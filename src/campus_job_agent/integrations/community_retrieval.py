"""External search and public community-detail retrieval providers.

The providers are deliberately transport-only.  They never classify a post or
decide whether evidence is sufficient; those decisions remain in the domain
tools and Graph.
"""

from __future__ import annotations

import asyncio
import json
import re
import threading
from dataclasses import asdict, dataclass, field
from html.parser import HTMLParser
from typing import Any, Awaitable, Callable
from urllib.parse import urlparse, urlunparse

import httpx


BRAVE_SEARCH_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"
NOWCODER_HOST = "www.nowcoder.com"
NOWCODER_DETAIL_PATHS = (
    re.compile(r"^/feed/main/detail/[A-Za-z0-9_-]+/?$"),
    re.compile(r"^/discuss/[0-9]+/?$"),
)
_FORBIDDEN_QUERY = re.compile(
    r"(?:https?://|\b(?:site|inurl|intitle|filetype|ext):|[\x00-\x1f\x7f])",
    re.IGNORECASE,
)


class CommunityRetrievalError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class BraveSearchConfig:
    endpoint: str = BRAVE_SEARCH_ENDPOINT
    timeout_seconds: float = 15.0


class BraveSearchClient:
    """Small official Brave Web Search client with no SDK dependency."""

    def __init__(
        self,
        config: BraveSearchConfig | None = None,
        *,
        request: Callable[..., httpx.Response] | None = None,
    ) -> None:
        self.config = config or BraveSearchConfig()
        self._request = request

    def search_nowcoder(
        self, *, keywords: list[str], limit: int, api_key: str
    ) -> tuple[bytes, dict[str, Any]]:
        if not api_key.strip():
            raise CommunityRetrievalError(
                "authentication_required", "Brave Search API key is missing"
            )
        query = build_brave_nowcoder_query(keywords)
        params = {
            "q": query,
            "count": max(1, min(int(limit), 20)),
            "safesearch": "moderate",
            "search_lang": "zh-hans",
        }
        headers = {
            "Accept": "application/json",
            "X-Subscription-Token": api_key.strip(),
        }
        if self._request is not None:
            response = self._request(
                self.config.endpoint, params=params, headers=headers,
                timeout=self.config.timeout_seconds,
            )
        else:
            with httpx.Client(timeout=self.config.timeout_seconds) as client:
                response = client.get(self.config.endpoint, params=params, headers=headers)
        if response.status_code in {401, 403}:
            raise CommunityRetrievalError(
                "authentication_required", "Brave Search authorization failed"
            )
        if response.status_code == 429:
            raise CommunityRetrievalError("rate_limited", "Brave Search rate limited")
        if response.status_code >= 500:
            raise CommunityRetrievalError(
                "external_dependency", "Brave Search is temporarily unavailable"
            )
        if response.status_code >= 400:
            raise CommunityRetrievalError(
                "failed", f"Brave Search returned HTTP {response.status_code}"
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise CommunityRetrievalError(
                "source_changed", "Brave Search returned invalid JSON"
            ) from exc
        if not isinstance(payload, dict):
            raise CommunityRetrievalError(
                "source_changed", "Brave Search response is not an object"
            )
        # Preserve the exact response bytes when supplied by the transport.
        raw = response.content or json.dumps(
            payload, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        metadata = {
            "provider": "brave_search",
            "endpoint": self.config.endpoint,
            "query": query,
            "count": params["count"],
        }
        return raw, metadata


def build_brave_nowcoder_query(keywords: list[str]) -> str:
    values = [re.sub(r"\s+", " ", str(value)).strip() for value in keywords]
    values = [value for value in values if value]
    if not values:
        raise ValueError("community search keywords are required")
    text = " ".join(values)
    if _FORBIDDEN_QUERY.search(text):
        raise ValueError("community query contains a forbidden operator or URL")
    if len(text) > 300:
        raise ValueError("community query is too long")
    return f"{text} site:nowcoder.com"


def canonical_nowcoder_detail_url(value: str) -> str | None:
    parsed = urlparse(str(value).strip())
    hostname = (parsed.hostname or "").casefold()
    if parsed.scheme != "https" or hostname not in {"nowcoder.com", NOWCODER_HOST}:
        return None
    if parsed.username or parsed.password or parsed.port not in {None, 443}:
        return None
    path = re.sub(r"/{2,}", "/", parsed.path).rstrip("/")
    if not any(pattern.fullmatch(path) for pattern in NOWCODER_DETAIL_PATHS):
        return None
    return urlunparse(("https", NOWCODER_HOST, path, "", "", ""))


@dataclass(frozen=True)
class CommunityFetchResult:
    requested_url: str
    final_url: str
    success: bool
    status_code: int | None
    html: str = ""
    cleaned_html: str = ""
    raw_markdown: str = ""
    fit_markdown: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    error_message: str = ""

    def raw_payload(self) -> bytes:
        return json.dumps(
            asdict(self), ensure_ascii=False, sort_keys=True
        ).encode("utf-8")


class Crawl4AICommunityFetcher:
    """In-process Crawl4AI batch fetcher; importing it remains optional."""

    def __init__(
        self,
        *,
        runner: Callable[[list[str], int], list[CommunityFetchResult]] | None = None,
    ) -> None:
        self._runner = runner

    def fetch_many(
        self, urls: list[str], *, max_concurrency: int = 2
    ) -> list[CommunityFetchResult]:
        canonical = [canonical_nowcoder_detail_url(value) for value in urls]
        if any(value is None for value in canonical):
            raise CommunityRetrievalError(
                "policy_blocked", "Nowcoder detail URL is outside the allowlist"
            )
        unique = list(dict.fromkeys(str(value) for value in canonical if value))
        if not unique:
            return []
        concurrency = max(1, min(int(max_concurrency), 2))
        if self._runner is not None:
            return self._runner(unique, concurrency)
        try:
            return _run_async(_crawl4ai_fetch(unique, concurrency))
        except ModuleNotFoundError as exc:
            raise CommunityRetrievalError(
                "adapter_required",
                "Crawl4AI is not installed; install the community optional extra",
            ) from exc


async def _crawl4ai_fetch(
    urls: list[str], max_concurrency: int
) -> list[CommunityFetchResult]:
    from crawl4ai import AsyncWebCrawler, BrowserConfig, CacheMode, CrawlerRunConfig
    from crawl4ai.async_dispatcher import SemaphoreDispatcher

    browser = BrowserConfig(headless=True, verbose=False)
    run = CrawlerRunConfig(
        cache_mode=CacheMode.ENABLED,
        check_robots_txt=True,
        stream=False,
        page_timeout=30_000,
        wait_for=(
            "js:() => ["
            + ",".join(json.dumps(value) for value in NOWCODER_BODY_SELECTORS)
            + "].some((selector) => document.querySelector(selector))"
        ),
        wait_for_timeout=15_000,
        delay_before_return_html=1.0,
        remove_overlay_elements=True,
        remove_forms=True,
        excluded_tags=["nav", "footer", "form"],
        exclude_external_links=True,
        exclude_external_images=True,
    )
    dispatcher = SemaphoreDispatcher(semaphore_count=max_concurrency)
    async with AsyncWebCrawler(config=browser) as crawler:
        raw_results = await crawler.arun_many(
            urls=urls, config=run, dispatcher=dispatcher
        )
    results: list[CommunityFetchResult] = []
    for item in raw_results:
        markdown = getattr(item, "markdown", None)
        results.append(CommunityFetchResult(
            requested_url=str(getattr(item, "url", "")),
            final_url=str(
                getattr(item, "redirected_url", None)
                or getattr(item, "url", "")
            ),
            success=bool(getattr(item, "success", False)),
            status_code=getattr(item, "status_code", None),
            html=str(getattr(item, "html", "") or ""),
            cleaned_html=str(getattr(item, "cleaned_html", "") or ""),
            raw_markdown=str(getattr(markdown, "raw_markdown", "") or ""),
            fit_markdown=str(getattr(markdown, "fit_markdown", "") or ""),
            metadata=dict(getattr(item, "metadata", None) or {}),
            error_message=str(getattr(item, "error_message", "") or ""),
        ))
    return results


def _run_async(awaitable: Awaitable[list[CommunityFetchResult]]) -> list[CommunityFetchResult]:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(awaitable)
    result: list[CommunityFetchResult] | None = None
    failure: BaseException | None = None

    def target() -> None:
        nonlocal result, failure
        try:
            result = asyncio.run(awaitable)
        except BaseException as exc:  # pragma: no cover - event-loop bridge
            failure = exc

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    thread.join()
    if failure is not None:
        raise failure
    return result or []


NOWCODER_BODY_SELECTORS: tuple[str, ...] = (
    "[data-post-content]",
    ".nc-post-content",
    ".post-content",
    ".post-content-box",
    ".moment-content",
    ".feed-content",
    ".discuss-main",
    "article",
)
NOWCODER_BODY_XPATHS: tuple[str, ...] = (
    "//*[@data-post-content]",
    "//*[contains(concat(' ', normalize-space(@class), ' '), ' nc-post-content ')]",
    "//*[contains(concat(' ', normalize-space(@class), ' '), ' post-content ')]",
    "//*[contains(concat(' ', normalize-space(@class), ' '), ' moment-content ')]",
    "//article",
)


def extract_nowcoder_main_body(
    *, html: str, cleaned_html: str = "", title: str | None = None
) -> tuple[str, str] | None:
    """Return the first selector with exactly one substantial body."""

    source = cleaned_html or html
    for selector in NOWCODER_BODY_SELECTORS:
        parser = _SelectorTextParser(selector)
        parser.feed(source)
        candidates = [
            value for value in (_normalize_body(item) for item in parser.values)
            if len(value) >= 80
        ]
        if len(candidates) != 1:
            continue
        body = candidates[0]
        clean_title = _normalize_body(title or "")
        if clean_title and clean_title not in body:
            body = f"{clean_title}\n{body}"
        return body, selector
    for xpath in NOWCODER_BODY_XPATHS:
        candidates = [
            value for value in (
                _normalize_body(item)
                for item in _xpath_text_candidates(source, xpath)
            )
            if len(value) >= 80
        ]
        if len(candidates) != 1:
            continue
        body = candidates[0]
        clean_title = _normalize_body(title or "")
        if clean_title and clean_title not in body:
            body = f"{clean_title}\n{body}"
        return body, f"xpath:{xpath}"
    return None


def _xpath_text_candidates(source: str, xpath: str) -> list[str]:
    if not source:
        return []
    try:
        from lxml import html as lxml_html
    except ModuleNotFoundError:
        return []
    try:
        root = lxml_html.fromstring(source)
        values = root.xpath(xpath)
    except (ValueError, TypeError):
        return []
    return [
        " ".join(value.itertext()) for value in values
        if hasattr(value, "itertext")
    ]


def classify_crawl4ai_result(result: CommunityFetchResult) -> str:
    text = " ".join((result.error_message, result.html[:20_000])).casefold()
    if result.status_code == 429:
        return "rate_limited"
    if not result.success and "robots" in text:
        return "robots_disallowed"
    if result.status_code in {401, 403}:
        return "authentication_required"
    if any(marker in text for marker in (
        "captcha", "验证码", "安全验证", "风险控制", "risk control",
    )):
        return "risk_controlled"
    if any(marker in text for marker in (
        "login", "登录后查看", "账号登录", "password",
    )):
        return "authentication_required"
    if result.status_code == 404:
        return "empty"
    if not result.success:
        return "failed"
    return "success"


class _SelectorTextParser(HTMLParser):
    _BREAK_TAGS = {"br", "p", "li", "div", "section", "article", "h1", "h2", "h3"}

    def __init__(self, selector: str) -> None:
        super().__init__(convert_charrefs=True)
        self.selector = selector
        self.depth = 0
        self.parts: list[str] = []
        self.values: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        mapping = {key: value or "" for key, value in attrs}
        if self.depth:
            self.depth += 1
        elif _matches_selector(tag, mapping, self.selector):
            self.depth = 1
            self.parts = []
        if self.depth and tag in self._BREAK_TAGS:
            self.parts.append("\n")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self.depth and tag in self._BREAK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if not self.depth:
            return
        if tag in self._BREAK_TAGS:
            self.parts.append("\n")
        self.depth -= 1
        if self.depth == 0:
            self.values.append("".join(self.parts))
            self.parts = []

    def handle_data(self, data: str) -> None:
        if self.depth:
            self.parts.append(data)


def _matches_selector(tag: str, attrs: dict[str, str], selector: str) -> bool:
    if selector == "article":
        return tag.casefold() == "article"
    if selector.startswith("."):
        return selector[1:] in attrs.get("class", "").split()
    if selector == "[data-post-content]":
        return "data-post-content" in attrs
    return False


def _normalize_body(value: str) -> str:
    lines = [re.sub(r"[ \t\f\v]+", " ", item).strip() for item in value.splitlines()]
    return "\n".join(item for item in lines if item).strip()


__all__ = [
    "BRAVE_SEARCH_ENDPOINT",
    "BraveSearchClient",
    "BraveSearchConfig",
    "CommunityFetchResult",
    "CommunityRetrievalError",
    "Crawl4AICommunityFetcher",
    "build_brave_nowcoder_query",
    "canonical_nowcoder_detail_url",
    "classify_crawl4ai_result",
    "extract_nowcoder_main_body",
]
