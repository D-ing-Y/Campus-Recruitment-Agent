"""Fixture and opt-in live SourceAdapter implementations."""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import parse_qs, urlparse
from uuid import NAMESPACE_URL, uuid5

import httpx

from campus_job_agent.integrations.community_retrieval import (
    BRAVE_SEARCH_ENDPOINT,
    BraveSearchClient,
    CommunityRetrievalError,
    Crawl4AICommunityFetcher,
    canonical_nowcoder_detail_url,
    classify_crawl4ai_result,
    extract_nowcoder_main_body,
)
from campus_job_agent.integrations.social_media import SocialBridgeError
from campus_job_agent.schemas import (
    EvidenceArtifact,
    EvidenceFragment,
    OfficialVerificationPlan,
    SourceBatch,
    SourceCapabilities,
    SourceDetailRequest,
    SourceDocument,
    SourceQuery,
)
from campus_job_agent.schemas.evidence import utc_now
from campus_job_agent.schemas.source import AccessStatus
from campus_job_agent.sources.repository import SQLiteRoleRepository
from campus_job_agent.storage.base import BlobStore, EvidenceRepository


class RecruitmentDiscoveryAdapter(Protocol):
    source_id: str
    capabilities: SourceCapabilities
    def collect(self, query: SourceQuery, credential_ref: str | None = None) -> SourceBatch: ...


class ExperienceSourceAdapter(Protocol):
    source_id: str
    capabilities: SourceCapabilities
    def collect(self, query: SourceQuery, credential_ref: str | None = None) -> SourceBatch: ...


class OfficialCareerAdapter(Protocol):
    source_id: str
    capabilities: SourceCapabilities
    def verify(self, plan: OfficialVerificationPlan, credential_ref: str | None = None) -> SourceBatch: ...


class SourceDetailAdapter(Protocol):
    source_id: str
    capabilities: SourceCapabilities
    def fetch_detail(self, request: SourceDetailRequest, credential_ref: str | None = None) -> SourceBatch: ...


class SourceAdapterRegistry:
    def __init__(self) -> None:
        self._adapters: dict[str, Any] = {}

    def register(self, adapter: Any) -> None:
        self._adapters[adapter.source_id] = adapter

    def get(self, source_id: str) -> Any | None:
        return self._adapters.get(source_id)

    def capabilities(self) -> dict[str, dict[str, Any]]:
        return {key: value.capabilities.model_dump(mode="json") for key, value in self._adapters.items()}


class _FixtureAdapter:
    def __init__(
        self,
        *,
        source_id: str,
        channel: str,
        source_type: str,
        fixture_pages: dict[str, list[dict[str, Any]]],
        blob_store: BlobStore,
        evidence_repository: EvidenceRepository,
        role_repository: SQLiteRoleRepository,
        owner_id: str,
        requires_auth: bool = False,
        detail_pages: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        self.source_id = source_id
        self.fixture_pages = fixture_pages
        self.blob_store = blob_store
        self.evidence_repository = evidence_repository
        self.role_repository = role_repository
        self.owner_id = owner_id
        self.detail_pages = detail_pages or {}
        self.capabilities = SourceCapabilities(
            source_id=source_id,
            channel=channel,
            source_type=source_type,
            adapter_version=f"{source_id}_fixture_v1",
            supports_location=True,
            supports_company=True,
            supports_pagination=True,
            supports_detail_fetch=True,
            requires_auth=requires_auth,
        )

    def collect(self, query: SourceQuery, credential_ref: str | None = None) -> SourceBatch:
        if self.capabilities.requires_auth and not credential_ref:
            return self._status_batch(query, "authentication_required", needs_user_action=True)
        key = _batch_key(self.source_id, query.fingerprint, query.cursor, self.capabilities.adapter_version)
        existing = self.role_repository.get_batch(key)
        if existing is not None and not (existing.status == "authentication_required" and credential_ref):
            return existing
        page_key = query.cursor or "first"
        payloads = self.fixture_pages.get(page_key, [])
        if query.channel == "employer_official" and query.company:
            payloads = [item for item in payloads if str(item.get("company", "")).strip() == query.company.strip()]
        documents: list[SourceDocument] = []
        for index, payload in enumerate(payloads):
            raw = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
            documents.append(
                _archive_document(
                    raw=raw,
                    owner_id=self.owner_id,
                    source_id=self.source_id,
                    channel=query.channel,
                    query_id=query.query_id,
                    source_url=str(payload.get("source_url") or f"fixture://{self.source_id}/{page_key}/{index}"),
                    document_kind=str(payload.get("document_kind") or _default_kind(query.channel)),
                    content_type="application/json",
                    adapter_version=self.capabilities.adapter_version,
                    blob_store=self.blob_store,
                    evidence_repository=self.evidence_repository,
                )
            )
        next_cursor = "page-2" if page_key == "first" and "page-2" in self.fixture_pages else None
        batch = SourceBatch(
            batch_id=str(uuid5(NAMESPACE_URL, key)), source_id=self.source_id,
            channel=query.channel, query_id=query.query_id, cursor=query.cursor,
            next_cursor=next_cursor, documents=documents,
            status="success" if documents else "empty", idempotency_key=key,
        )
        return self.role_repository.save_batch(batch)

    def _status_batch(self, query: SourceQuery, status: str, *, needs_user_action: bool = False) -> SourceBatch:
        key = _batch_key(self.source_id, query.fingerprint, query.cursor, self.capabilities.adapter_version)
        return self.role_repository.save_batch(SourceBatch(
            batch_id=str(uuid5(NAMESPACE_URL, key)), source_id=self.source_id,
            channel=query.channel, query_id=query.query_id, cursor=query.cursor,
            status=status, error_type=status, retryable=status in {"rate_limited", "network_timeout"},
            needs_user_action=needs_user_action, idempotency_key=key,
        ))

    def fetch_detail(
        self, request: SourceDetailRequest, credential_ref: str | None = None,
    ) -> SourceBatch:
        if request.source_id != self.source_id or request.channel != self.capabilities.channel:
            raise ValueError("detail request does not match fixture adapter")
        key = hashlib.sha256(
            f"{request.idempotency_key}:{self.capabilities.adapter_version}".encode()
        ).hexdigest()
        existing = self.role_repository.get_batch(key)
        if existing is not None and not (
            existing.status == "authentication_required" and credential_ref
        ):
            return existing
        if self.capabilities.requires_auth and not credential_ref:
            return self.role_repository.save_batch(SourceBatch(
                batch_id=str(uuid5(NAMESPACE_URL, key)), source_id=self.source_id,
                channel=request.channel, query_id=request.query_id,
                status="authentication_required", error_type="authentication_required",
                needs_user_action=True, idempotency_key=key,
            ))
        payload = self.detail_pages.get(request.detail_url)
        if payload is None:
            payload = next((
                item
                for values in self.fixture_pages.values()
                for item in values
                if str(item.get("source_url") or item.get("detail_url") or "") == request.detail_url
            ), None)
        if payload is None:
            return self.role_repository.save_batch(SourceBatch(
                batch_id=str(uuid5(NAMESPACE_URL, key)), source_id=self.source_id,
                channel=request.channel, query_id=request.query_id,
                status="empty", error_type="detail_not_found", idempotency_key=key,
            ))
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        document = _archive_document(
            raw=raw, owner_id=self.owner_id, source_id=self.source_id,
            channel=request.channel, query_id=request.query_id,
            source_url=request.detail_url,
            document_kind=request.expected_document_kind,
            content_type="application/json", adapter_version=self.capabilities.adapter_version,
            blob_store=self.blob_store, evidence_repository=self.evidence_repository,
        )
        return self.role_repository.save_batch(SourceBatch(
            batch_id=str(uuid5(NAMESPACE_URL, key)), source_id=self.source_id,
            channel=request.channel, query_id=request.query_id,
            documents=[document], status="success", idempotency_key=key,
        ))


class FixtureRecruitmentAdapter(_FixtureAdapter):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(channel="recruitment_discovery", source_type="fixture", **kwargs)


class FixtureExperienceAdapter(_FixtureAdapter):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(channel="experience", source_type="fixture", **kwargs)


class FixtureOfficialAdapter(_FixtureAdapter):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(channel="employer_official", source_type="fixture", **kwargs)

    def verify(self, plan: OfficialVerificationPlan, credential_ref: str | None = None) -> SourceBatch:
        query = SourceQuery(
            query_id=f"official:{plan.verification_plan_id}", channel="employer_official",
            source_id=self.source_id, keywords=[plan.candidate_role_title],
            company=plan.canonical_company, location=plan.candidate_location,
            role_family="official_verification", graduation_year="unknown",
            recruitment_type=plan.candidate_recruitment_cycle or "unknown",
        )
        return self.collect(query, credential_ref)


class _HttpAdapter:
    def __init__(
        self, *, source_id: str, channel: str, source_type: str, blob_store: BlobStore,
        evidence_repository: EvidenceRepository, role_repository: SQLiteRoleRepository,
        owner_id: str, live_enabled: bool = False, requires_auth: bool = False,
        allowed_domains: set[str] | None = None, credential_resolver: Any | None = None,
        timeout_seconds: float = 10.0, max_retries: int = 1, rate_limit_per_minute: int = 6,
        robots_allowed: bool = True, follow_redirects: bool = False,
    ) -> None:
        self.source_id = source_id
        self.blob_store = blob_store
        self.evidence_repository = evidence_repository
        self.role_repository = role_repository
        self.owner_id = owner_id
        self.allowed_domains = allowed_domains or set()
        self.credential_resolver = credential_resolver
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.robots_allowed = robots_allowed
        self.follow_redirects = follow_redirects
        self._last_request_at = 0.0
        self.capabilities = SourceCapabilities(
            source_id=source_id, channel=channel, source_type=source_type,
            adapter_version=f"{source_id}_v1", supports_location=True,
            supports_company=True, supports_pagination=True, supports_detail_fetch=True,
            requires_auth=requires_auth,
            live_enabled=live_enabled, rate_limit_per_minute=rate_limit_per_minute,
        )

    def collect(self, query: SourceQuery, credential_ref: str | None = None) -> SourceBatch:
        key = _batch_key(self.source_id, query.fingerprint, query.cursor, self.capabilities.adapter_version)
        existing = self.role_repository.get_batch(key)
        if existing is not None and not (existing.status == "authentication_required" and credential_ref):
            return existing
        if not self.capabilities.live_enabled:
            return self._error_batch(query, key, "policy_blocked", False)
        if not self.robots_allowed:
            return self._error_batch(query, key, "robots_disallowed", False)
        if self.capabilities.requires_auth and not credential_ref:
            return self._error_batch(query, key, "authentication_required", False, True)
        try:
            url = self.build_url(query)
            _assert_allowed_url(url, self.allowed_domains)
            headers = {}
            if credential_ref and self.credential_resolver:
                headers = self.credential_resolver(credential_ref, source_id=self.source_id)
            headers = self.request_headers(headers)
            response = self._request(url, headers)
            _assert_allowed_url(str(response.url), self.allowed_domains)
            preliminary_status = _classify_http_metadata(response)
            document = _archive_document(
                raw=response.content, owner_id=self.owner_id, source_id=self.source_id,
                channel=query.channel, query_id=query.query_id, source_url=str(response.url),
                document_kind=_default_kind(query.channel), content_type=response.headers.get("content-type", "text/html"),
                adapter_version=self.capabilities.adapter_version, blob_store=self.blob_store,
                evidence_repository=self.evidence_repository, http_status=response.status_code,
                access_status=preliminary_status,
            )
            status = self.classify_response(response)
            if status != "success":
                document = document.model_copy(update={"access_status": status})
                return self._error_batch(
                    query, key, status, status in {"rate_limited", "network_timeout"},
                    status == "authentication_required", documents=[document],
                )
            return self.role_repository.save_batch(SourceBatch(
                batch_id=str(uuid5(NAMESPACE_URL, key)), source_id=self.source_id,
                channel=query.channel, query_id=query.query_id, cursor=query.cursor,
                documents=[document], status="success", idempotency_key=key,
            ))
        except httpx.TimeoutException:
            return self._error_batch(query, key, "network_timeout", True)
        except ValueError:
            return self._error_batch(query, key, "policy_blocked", False)
        except Exception:
            return self._error_batch(query, key, "failed", False)

    def fetch_detail(
        self, request: SourceDetailRequest, credential_ref: str | None = None,
    ) -> SourceBatch:
        if request.source_id != self.source_id or request.channel != self.capabilities.channel:
            raise ValueError("detail request does not match source adapter")
        key = hashlib.sha256(
            f"{request.idempotency_key}:{self.capabilities.adapter_version}".encode()
        ).hexdigest()
        existing = self.role_repository.get_batch(key)
        if existing is not None and not (
            existing.status == "authentication_required" and credential_ref
        ):
            return existing
        if not self.capabilities.live_enabled:
            return self._detail_error_batch(request, key, "policy_blocked", False)
        if not self.robots_allowed:
            return self._detail_error_batch(request, key, "robots_disallowed", False)
        if self.capabilities.requires_auth and not credential_ref:
            return self._detail_error_batch(
                request, key, "authentication_required", False, True
            )
        try:
            _assert_allowed_url(request.detail_url, self.allowed_domains)
            headers: dict[str, str] = {}
            if credential_ref and self.credential_resolver:
                headers = self.credential_resolver(credential_ref, source_id=self.source_id)
            response = self._request(request.detail_url, self.request_headers(headers))
            _assert_allowed_url(str(response.url), self.allowed_domains)
            preliminary = _classify_http_metadata(response)
            document = _archive_document(
                raw=response.content, owner_id=self.owner_id, source_id=self.source_id,
                channel=request.channel, query_id=request.query_id,
                source_url=str(response.url),
                document_kind=request.expected_document_kind,
                content_type=response.headers.get("content-type", "text/html"),
                adapter_version=self.capabilities.adapter_version,
                blob_store=self.blob_store, evidence_repository=self.evidence_repository,
                http_status=response.status_code, access_status=preliminary,
            )
            status = self.classify_detail_response(response, request)
            if status != "success":
                document = document.model_copy(update={"access_status": status})
                return self._detail_error_batch(
                    request, key, status, status in {"rate_limited", "network_timeout"},
                    status == "authentication_required", [document],
                )
            return self.role_repository.save_batch(SourceBatch(
                batch_id=str(uuid5(NAMESPACE_URL, key)), source_id=self.source_id,
                channel=request.channel, query_id=request.query_id,
                documents=[document], status="success", idempotency_key=key,
            ))
        except httpx.TimeoutException:
            return self._detail_error_batch(request, key, "network_timeout", True)
        except ValueError:
            return self._detail_error_batch(request, key, "policy_blocked", False)
        except Exception:
            return self._detail_error_batch(request, key, "failed", False)

    def build_url(self, query: SourceQuery) -> str:
        raise NotImplementedError

    def request_headers(self, credential_headers: dict[str, str]) -> dict[str, str]:
        return {"Accept": "text/html,application/json;q=0.9,*/*;q=0.8", **credential_headers}

    def classify_response(self, response: httpx.Response) -> AccessStatus:
        return _classify_http_response(response)

    def classify_detail_response(
        self, response: httpx.Response, request: SourceDetailRequest,
    ) -> AccessStatus:
        return self.classify_response(response)

    def _request(self, url: str, headers: dict[str, str]) -> httpx.Response:
        minimum_interval = 60.0 / self.capabilities.rate_limit_per_minute
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < minimum_interval:
            time.sleep(minimum_interval - elapsed)
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                with httpx.Client(timeout=self.timeout_seconds, follow_redirects=self.follow_redirects) as client:
                    response = client.get(url, headers=headers)
                self._last_request_at = time.monotonic()
                if response.status_code in {429, 500, 502, 503, 504} and attempt < self.max_retries:
                    continue
                return response
            except httpx.TimeoutException as exc:
                last_error = exc
        assert last_error is not None
        raise last_error

    def _error_batch(
        self, query: SourceQuery, key: str, status: str, retryable: bool,
        needs_user_action: bool = False, documents: list[SourceDocument] | None = None,
    ) -> SourceBatch:
        batch_status = status if status in {"empty", "authentication_required", "rate_limited", "source_changed", "robots_disallowed", "official_not_found", "official_unavailable", "identity_ambiguous", "adapter_required", "policy_blocked"} else "failed"
        return self.role_repository.save_batch(SourceBatch(
            batch_id=str(uuid5(NAMESPACE_URL, key)), source_id=self.source_id,
            channel=query.channel, query_id=query.query_id, cursor=query.cursor,
            documents=documents or [], status=batch_status, error_type=status, retryable=retryable,
            needs_user_action=needs_user_action, idempotency_key=key,
        ))

    def _detail_error_batch(
        self, request: SourceDetailRequest, key: str, status: str, retryable: bool,
        needs_user_action: bool = False, documents: list[SourceDocument] | None = None,
    ) -> SourceBatch:
        batch_status = status if status in {
            "empty", "authentication_required", "rate_limited", "source_changed",
            "robots_disallowed", "official_not_found", "official_unavailable",
            "adapter_required", "policy_blocked",
        } else "failed"
        return self.role_repository.save_batch(SourceBatch(
            batch_id=str(uuid5(NAMESPACE_URL, key)), source_id=self.source_id,
            channel=request.channel, query_id=request.query_id,
            documents=documents or [], status=batch_status, error_type=status,
            retryable=retryable, needs_user_action=needs_user_action,
            idempotency_key=key,
        ))


class ZhaopinJobsAdapter(_HttpAdapter):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(
            source_id="zhaopin_jobs", channel="recruitment_discovery",
            source_type="recruitment_platform", requires_auth=False,
            allowed_domains={"sou.zhaopin.com", "www.zhaopin.com", "jobs.zhaopin.com"},
            follow_redirects=True, **kwargs,
        )

    def build_url(self, query: SourceQuery) -> str:
        from urllib.parse import urlencode
        params = {
            "jl": query.location or "全国",
            "kw": " ".join(query.keywords),
            "p": query.cursor or "1",
        }
        return f"https://sou.zhaopin.com/?{urlencode(params)}"

    def request_headers(self, credential_headers: dict[str, str]) -> dict[str, str]:
        return {
            "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
            "Referer": "https://www.zhaopin.com/",
            "User-Agent": "campus-job-agent/0.5 read-only-source-adapter",
            **credential_headers,
        }

    def classify_response(self, response: httpx.Response) -> AccessStatus:
        metadata = _classify_http_metadata(response)
        if metadata != "success":
            return metadata
        final_url = str(response.url).casefold()
        text = response.text[:500_000]
        lowered = text.casefold()
        if "passport.zhaopin.com" in final_url:
            return "authentication_required"
        if any(marker in lowered for marker in ("访问过于频繁", "请求过于频繁", "too many requests")):
            return "rate_limited"
        if any(marker in text for marker in ("暂无符合条件的职位", "没有找到相关职位")):
            return "empty"
        if "/jobdetail/" in lowered or "joblist-box__item" in lowered or '"jobname"' in lowered:
            return "success"
        if any(marker in lowered for marker in ("验证码", "安全验证", "captcha", "passport.zhaopin.com")):
            return "authentication_required"
        return "source_changed"

    def classify_detail_response(
        self, response: httpx.Response, request: SourceDetailRequest,
    ) -> AccessStatus:
        status = self.classify_response(response)
        if status != "success":
            return status
        lowered = response.text[:500_000].casefold()
        return "success" if (
            "/jobdetail/" in str(response.url).casefold()
            or "jobdetaildata" in lowered
            or '"@type":"jobposting"' in lowered.replace(" ", "")
        ) else "source_changed"


class BraveNowcoderExperienceAdapter:
    """Brave discovery plus in-process Crawl4AI public-detail retrieval."""

    source_id = "nowcoder_experience"

    def __init__(
        self, *, blob_store: BlobStore, evidence_repository: EvidenceRepository,
        role_repository: SQLiteRoleRepository, owner_id: str,
        live_enabled: bool = False, credential_resolver: Any | None = None,
        search_client: BraveSearchClient | None = None,
        detail_fetcher: Crawl4AICommunityFetcher | None = None, **_: Any,
    ) -> None:
        self.blob_store = blob_store
        self.evidence_repository = evidence_repository
        self.role_repository = role_repository
        self.owner_id = owner_id
        self.credential_resolver = credential_resolver
        self.search_client = search_client or BraveSearchClient()
        self.detail_fetcher = detail_fetcher or Crawl4AICommunityFetcher()
        self.capabilities = SourceCapabilities(
            source_id=self.source_id, channel="experience",
            source_type="community_experience",
            adapter_version="brave_search_crawl4ai_v1",
            supports_keyword=True, supports_company=True,
            supports_pagination=False, supports_detail_fetch=True,
            requires_auth=True, authorization_mode="credential_ref",
            live_enabled=live_enabled, rate_limit_per_minute=6,
        )

    def collect(
        self, query: SourceQuery, credential_ref: str | None = None,
    ) -> SourceBatch:
        if query.source_id != self.source_id or query.channel != "experience":
            raise ValueError("query does not match Brave Nowcoder adapter")
        key = _batch_key(
            self.source_id, query.fingerprint, query.cursor,
            self.capabilities.adapter_version,
        )
        existing = self.role_repository.get_batch(key)
        if existing is not None and existing.status not in {
            "authentication_required", "rate_limited", "adapter_required",
        }:
            return existing
        if not self.capabilities.live_enabled:
            return self._error(query.query_id, key, "policy_blocked")
        if not credential_ref or self.credential_resolver is None:
            return self._error(
                query.query_id, key, "authentication_required",
                needs_user_action=True,
            )
        try:
            try:
                credential = self.credential_resolver(
                    credential_ref, source_id=self.source_id
                )
            except ValueError:
                return self._error(
                    query.query_id, key, "authentication_required",
                    needs_user_action=True,
                )
            raw, request_metadata = self.search_client.search_nowcoder(
                keywords=query.keywords, limit=query.page_size,
                api_key=str(credential.get("api_key") or ""),
            )
            document = _archive_document(
                raw=raw, owner_id=self.owner_id, source_id=self.source_id,
                channel="experience", query_id=query.query_id,
                source_url=BRAVE_SEARCH_ENDPOINT,
                document_kind="experience_search",
                content_type="application/json",
                adapter_version=self.capabilities.adapter_version,
                blob_store=self.blob_store,
                evidence_repository=self.evidence_repository,
                artifact_metadata={"request": request_metadata},
            )
            count = _brave_nowcoder_candidate_count(raw)
            return self.role_repository.save_batch(SourceBatch(
                batch_id=str(uuid5(NAMESPACE_URL, key)),
                source_id=self.source_id, channel="experience",
                query_id=query.query_id, documents=[document],
                status="success" if count else "empty",
                error_type=None if count else "search_empty",
                idempotency_key=key,
            ))
        except CommunityRetrievalError as exc:
            return self._error(
                query.query_id, key, exc.code,
                needs_user_action=exc.code == "authentication_required",
            )
        except ValueError:
            return self._error(query.query_id, key, "policy_blocked")
        except httpx.TimeoutException:
            return self._error(query.query_id, key, "network_timeout")
        except Exception:
            return self._error(query.query_id, key, "failed")

    def fetch_detail(
        self, request: SourceDetailRequest, credential_ref: str | None = None,
    ) -> SourceBatch:
        return self.fetch_details(
            [request], credential_ref=credential_ref, max_concurrency=1
        )[0]

    def fetch_details(
        self, requests: list[SourceDetailRequest], *,
        credential_ref: str | None = None, max_concurrency: int = 2,
    ) -> list[SourceBatch]:
        validated = [SourceDetailRequest.model_validate(item) for item in requests]
        if any(
            item.source_id != self.source_id or item.channel != "experience"
            for item in validated
        ):
            raise ValueError("detail request does not match Brave Nowcoder adapter")
        batches: dict[str, SourceBatch] = {}
        pending: list[SourceDetailRequest] = []
        keys: dict[str, str] = {}
        for request in validated:
            key = hashlib.sha256(
                f"{request.idempotency_key}:{self.capabilities.adapter_version}".encode()
            ).hexdigest()
            keys[request.detail_request_id] = key
            existing = self.role_repository.get_batch(key)
            if existing is not None and existing.status not in {
                "rate_limited", "adapter_required", "failed",
            }:
                batches[request.detail_request_id] = existing
            else:
                pending.append(request)
        if not self.capabilities.live_enabled:
            for item in pending:
                batches[item.detail_request_id] = self._detail_error(
                    item, keys[item.detail_request_id], "policy_blocked"
                )
            return [batches[item.detail_request_id] for item in validated]
        canonical_urls: list[str] = []
        for item in pending:
            canonical = canonical_nowcoder_detail_url(item.detail_url)
            if canonical is None:
                batches[item.detail_request_id] = self._detail_error(
                    item, keys[item.detail_request_id], "policy_blocked"
                )
            else:
                canonical_urls.append(canonical)
        fetchable = [
            item for item in pending if item.detail_request_id not in batches
        ]
        if fetchable:
            try:
                fetched = self.detail_fetcher.fetch_many(
                    canonical_urls, max_concurrency=max_concurrency
                )
            except CommunityRetrievalError as exc:
                for item in fetchable:
                    batches[item.detail_request_id] = self._detail_error(
                        item, keys[item.detail_request_id], exc.code,
                        needs_user_action=exc.code == "authentication_required",
                    )
            else:
                by_requested = {
                    canonical_nowcoder_detail_url(item.requested_url): item
                    for item in fetched
                }
                for item in fetchable:
                    canonical = canonical_nowcoder_detail_url(item.detail_url)
                    result = by_requested.get(canonical)
                    if result is None:
                        batches[item.detail_request_id] = self._detail_error(
                            item, keys[item.detail_request_id], "failed"
                        )
                        continue
                    final_url = canonical_nowcoder_detail_url(result.final_url)
                    if final_url is None:
                        document = _archive_document(
                            raw=result.raw_payload(), owner_id=self.owner_id,
                            source_id=self.source_id, channel="experience",
                            query_id=item.query_id, source_url=str(canonical),
                            document_kind="experience_post",
                            content_type="application/json",
                            adapter_version=self.capabilities.adapter_version,
                            blob_store=self.blob_store,
                            evidence_repository=self.evidence_repository,
                            http_status=result.status_code,
                            access_status="policy_blocked",
                            artifact_metadata={
                                "requested_url": canonical,
                                "redirect_validation": "rejected",
                            },
                        )
                        batches[item.detail_request_id] = self._detail_error(
                            item, keys[item.detail_request_id], "policy_blocked",
                            documents=[document],
                        )
                        continue
                    status = classify_crawl4ai_result(result)
                    document = _archive_document(
                        raw=result.raw_payload(), owner_id=self.owner_id,
                        source_id=self.source_id, channel="experience",
                        query_id=item.query_id, source_url=final_url,
                        document_kind="experience_post",
                        content_type="application/json",
                        adapter_version=self.capabilities.adapter_version,
                        blob_store=self.blob_store,
                        evidence_repository=self.evidence_repository,
                        http_status=result.status_code, access_status=(
                            status if status in {
                                "success", "empty", "authentication_required",
                                "rate_limited", "robots_disallowed",
                                "risk_controlled", "failed",
                            } else "failed"
                        ),
                        artifact_metadata={"requested_url": canonical},
                    )
                    if status == "success":
                        main_body = extract_nowcoder_main_body(
                            html=result.html, cleaned_html=result.cleaned_html,
                            title=str(result.metadata.get("title") or ""),
                        )
                        if main_body is None:
                            status = "source_changed"
                            document = document.model_copy(
                                update={"access_status": "source_changed"}
                            )
                        else:
                            _save_nowcoder_body_fragment(
                                document=document, body=main_body[0],
                                selector=main_body[1],
                                repository=self.evidence_repository,
                            )
                    if status != "success":
                        batches[item.detail_request_id] = self._detail_error(
                            item, keys[item.detail_request_id], status,
                            retryable=status in {"rate_limited", "network_timeout"},
                            needs_user_action=status == "authentication_required",
                            documents=[document],
                        )
                    else:
                        batches[item.detail_request_id] = self.role_repository.save_batch(
                            SourceBatch(
                                batch_id=str(uuid5(
                                    NAMESPACE_URL, keys[item.detail_request_id]
                                )),
                                source_id=self.source_id, channel="experience",
                                query_id=item.query_id, documents=[document],
                                status="success",
                                idempotency_key=keys[item.detail_request_id],
                            )
                        )
        return [batches[item.detail_request_id] for item in validated]

    def _error(
        self, query_id: str, key: str, status: str, *,
        needs_user_action: bool = False,
    ) -> SourceBatch:
        allowed = {
            "empty", "authentication_required", "rate_limited",
            "adapter_required", "policy_blocked", "source_changed",
        }
        value = status if status in allowed else "failed"
        return self.role_repository.save_batch(SourceBatch(
            batch_id=str(uuid5(NAMESPACE_URL, key)), source_id=self.source_id,
            channel="experience", query_id=query_id, status=value,
            error_type=status, retryable=status in {
                "rate_limited", "network_timeout", "external_dependency",
            }, needs_user_action=needs_user_action, idempotency_key=key,
        ))

    def _detail_error(
        self, request: SourceDetailRequest, key: str, status: str, *,
        retryable: bool = False, needs_user_action: bool = False,
        documents: list[SourceDocument] | None = None,
    ) -> SourceBatch:
        allowed = {
            "empty", "authentication_required", "rate_limited",
            "adapter_required", "policy_blocked", "source_changed",
            "robots_disallowed", "risk_controlled",
        }
        value = status if status in allowed else "failed"
        return self.role_repository.save_batch(SourceBatch(
            batch_id=str(uuid5(NAMESPACE_URL, key)), source_id=self.source_id,
            channel="experience", query_id=request.query_id,
            documents=documents or [], status=value, error_type=status,
            retryable=retryable, needs_user_action=needs_user_action,
            idempotency_key=key,
        ))


class XiaohongshuExperienceAdapter:
    """Read-only adapter over the project-owned MediaCrawler bridge service."""

    source_id = "xiaohongshu_experience"

    def __init__(
        self, *, bridge_client: Any | None, blob_store: BlobStore,
        evidence_repository: EvidenceRepository, role_repository: SQLiteRoleRepository,
        owner_id: str, live_enabled: bool = False, **_: Any,
    ) -> None:
        self.bridge_client = bridge_client
        self.blob_store = blob_store
        self.evidence_repository = evidence_repository
        self.role_repository = role_repository
        self.owner_id = owner_id
        self.capabilities = SourceCapabilities(
            source_id=self.source_id, channel="experience",
            source_type="community_experience", adapter_version="xiaohongshu_sidecar_v2",
            supports_keyword=True, supports_company=True, supports_pagination=False,
            supports_detail_fetch=True, requires_auth=True,
            authorization_mode="external_session", live_enabled=live_enabled,
            rate_limit_per_minute=3,
        )

    def collect(self, query: SourceQuery, credential_ref: str | None = None) -> SourceBatch:
        key = _batch_key(self.source_id, query.fingerprint, query.cursor, self.capabilities.adapter_version)
        existing = self.role_repository.get_batch(key)
        if existing is not None and existing.status not in {
            "authentication_required", "risk_controlled", "adapter_required",
        }:
            return existing
        if not self.capabilities.live_enabled:
            return self._error(query.query_id, key, "policy_blocked")
        if self.bridge_client is None:
            return self._error(query.query_id, key, "adapter_required")
        try:
            payload = self.bridge_client.search_posts(
                keywords=" ".join(query.keywords), limit=query.page_size,
            )
            raw = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
            source_url = f"mediacrawler://xhs/search/{hashlib.sha256(raw).hexdigest()[:24]}"
            document = _archive_document(
                raw=raw, owner_id=self.owner_id, source_id=self.source_id,
                channel="experience", query_id=query.query_id, source_url=source_url,
                document_kind="experience_search", content_type="application/json",
                adapter_version=self.capabilities.adapter_version, blob_store=self.blob_store,
                evidence_repository=self.evidence_repository,
            )
            return self.role_repository.save_batch(SourceBatch(
                batch_id=str(uuid5(NAMESPACE_URL, key)), source_id=self.source_id,
                channel="experience", query_id=query.query_id, documents=[document],
                status="success" if payload.get("candidates") else "empty",
                idempotency_key=key,
            ))
        except SocialBridgeError as exc:
            return self._error(
                query.query_id, key, exc.code,
                needs_user_action=exc.code in {"authentication_required", "risk_controlled"},
            )

    def fetch_detail(
        self, request: SourceDetailRequest, credential_ref: str | None = None,
    ) -> SourceBatch:
        if request.source_id != self.source_id or request.channel != "experience":
            raise ValueError("detail request does not match xiaohongshu adapter")
        key = hashlib.sha256(
            f"{request.idempotency_key}:{self.capabilities.adapter_version}".encode()
        ).hexdigest()
        existing = self.role_repository.get_batch(key)
        if existing is not None and existing.status not in {
            "authentication_required", "risk_controlled", "adapter_required",
        }:
            return existing
        if not self.capabilities.live_enabled:
            return self._error(request.query_id, key, "policy_blocked")
        if self.bridge_client is None:
            return self._error(request.query_id, key, "adapter_required")
        if not request.external_locator_ref:
            return self._error(request.query_id, key, "unsupported_input")
        try:
            payload = self.bridge_client.fetch_post_detail(
                candidate_ref=request.external_locator_ref
            )
            raw = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
            document = _archive_document(
                raw=raw, owner_id=self.owner_id, source_id=self.source_id,
                channel="experience", query_id=request.query_id,
                source_url=str(payload["canonical_url"]), document_kind="experience_post",
                content_type="application/json", adapter_version=self.capabilities.adapter_version,
                blob_store=self.blob_store, evidence_repository=self.evidence_repository,
            )
            return self.role_repository.save_batch(SourceBatch(
                batch_id=str(uuid5(NAMESPACE_URL, key)), source_id=self.source_id,
                channel="experience", query_id=request.query_id, documents=[document],
                status="success", idempotency_key=key,
            ))
        except SocialBridgeError as exc:
            return self._error(
                request.query_id, key, exc.code,
                needs_user_action=exc.code in {"authentication_required", "risk_controlled"},
            )

    def authorization_status(self) -> str:
        if not self.capabilities.live_enabled or self.bridge_client is None:
            return "adapter_required"
        try:
            return str(self.bridge_client.auth_status().get("status"))
        except SocialBridgeError as exc:
            return exc.code

    def _error(
        self, query_id: str, key: str, status: str, *, needs_user_action: bool = False,
    ) -> SourceBatch:
        allowed = {
            "empty", "authentication_required", "risk_controlled", "adapter_required",
            "policy_blocked", "unsupported_input", "rate_limited",
        }
        value = status if status in allowed else "failed"
        return self.role_repository.save_batch(SourceBatch(
            batch_id=str(uuid5(NAMESPACE_URL, key)), source_id=self.source_id,
            channel="experience", query_id=query_id, status=value,
            error_type=status, needs_user_action=needs_user_action,
            retryable=status in {"rate_limited", "network_timeout"}, idempotency_key=key,
        ))


class OfficialCareersAdapter(_HttpAdapter):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(source_id="official_careers", channel="employer_official", source_type="employer_official", requires_auth=False, **kwargs)

    def build_url(self, query: SourceQuery) -> str:
        if not query.keywords or not query.keywords[0].startswith("http"):
            raise ValueError("official adapter requires an approved entry URL as first keyword")
        return query.keywords[0]

    def verify(self, plan: OfficialVerificationPlan, credential_ref: str | None = None) -> SourceBatch:
        self.allowed_domains = set(plan.allowed_domains)
        if not plan.official_entry_url_candidates:
            query = SourceQuery(
                query_id=f"official:{plan.verification_plan_id}", channel="employer_official", source_id=self.source_id,
                keywords=["https://invalid.local/"], company=plan.canonical_company,
                role_family="official_verification", graduation_year="unknown", recruitment_type="unknown",
            )
            key = _batch_key(self.source_id, query.fingerprint, None, self.capabilities.adapter_version)
            return self._error_batch(query, key, "official_not_found", False)
        query = SourceQuery(
            query_id=f"official:{plan.verification_plan_id}", channel="employer_official", source_id=self.source_id,
            keywords=[plan.official_entry_url_candidates[0]], company=plan.canonical_company,
            location=plan.candidate_location, role_family="official_verification",
            graduation_year="unknown", recruitment_type=plan.candidate_recruitment_cycle or "unknown",
        )
        return self.collect(query, credential_ref)


class MeituanOfficialCareersAdapter(_HttpAdapter):
    """Read-only adapter for Meituan's public official job-detail API."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(
            source_id="official_careers_meituan", channel="employer_official",
            source_type="employer_official", requires_auth=False,
            allowed_domains={"zhaopin.meituan.com"}, **kwargs,
        )

    def build_url(self, query: SourceQuery) -> str:
        if not query.keywords:
            raise ValueError("Meituan official adapter requires a public detail URL")
        parsed = urlparse(query.keywords[0])
        job_union_ids = parse_qs(parsed.query).get("jobUnionId", [])
        if parsed.hostname != "zhaopin.meituan.com" or parsed.path != "/web/position/detail" or len(job_union_ids) != 1:
            raise ValueError("Meituan official adapter requires an approved position detail URL")
        return query.keywords[0]

    def _request(self, url: str, headers: dict[str, str]) -> httpx.Response:
        parsed = urlparse(url)
        job_union_id = parse_qs(parsed.query)["jobUnionId"][0]
        api_url = "https://zhaopin.meituan.com/api/official/job/getJobDetail"
        with httpx.Client(timeout=self.timeout_seconds, follow_redirects=False) as client:
            response = client.post(
                api_url,
                headers={"Accept": "application/json", "Content-Type": "application/json", **headers},
                json={"jobUnionId": job_union_id},
            )
        self._last_request_at = time.monotonic()
        return response

    def classify_response(self, response: httpx.Response) -> AccessStatus:
        metadata = _classify_http_metadata(response)
        if metadata != "success":
            return metadata
        try:
            payload = response.json()
        except ValueError:
            return "source_changed"
        data = payload.get("data") if isinstance(payload, dict) else None
        if payload.get("status") == 1 and isinstance(data, dict) and data.get("jobUnionId"):
            return "success"
        return "official_not_found" if payload.get("status") == 1 else "source_changed"

    def verify(self, plan: OfficialVerificationPlan, credential_ref: str | None = None) -> SourceBatch:
        if "zhaopin.meituan.com" not in plan.allowed_domains or not plan.official_entry_url_candidates:
            raise ValueError("Meituan official verification plan is outside the adapter allowlist")
        query = SourceQuery(
            query_id=f"official:{plan.verification_plan_id}", channel="employer_official",
            source_id=self.source_id, keywords=[plan.official_entry_url_candidates[0]],
            company=plan.canonical_company, location=plan.candidate_location,
            role_family="official_verification", graduation_year="unknown",
            recruitment_type=plan.candidate_recruitment_cycle or "unknown",
        )
        return self.collect(query, credential_ref)


def _archive_document(
    *, raw: bytes, owner_id: str, source_id: str, channel: str, query_id: str,
    source_url: str, document_kind: str, content_type: str, adapter_version: str,
    blob_store: BlobStore, evidence_repository: EvidenceRepository, http_status: int | None = 200,
    access_status: AccessStatus = "success",
    artifact_metadata: dict[str, Any] | None = None,
) -> SourceDocument:
    digest = hashlib.sha256(raw).hexdigest()
    existing = evidence_repository.find_artifact_by_hash(digest, owner_id)
    if existing is None:
        artifact_id = str(uuid5(NAMESPACE_URL, f"source:{owner_id}:{digest}"))
        raw_uri = blob_store.put(f"sources/{hashlib.sha256(owner_id.encode()).hexdigest()[:24]}/{artifact_id}/raw", raw)
        artifact = evidence_repository.save_artifact(EvidenceArtifact(
            artifact_id=artifact_id, owner_id=owner_id,
            source_type={"recruitment_discovery": "recruitment_platform", "employer_official": "employer_official", "experience": "community_experience"}[channel],
            content_type=content_type, source_url=source_url, original_name=f"{source_id}-{document_kind}",
            raw_uri=raw_uri, content_hash=digest, parser_name=None, parser_version=None,
            metadata={"source_id": source_id, "channel": channel, "query_id": query_id, "document_kind": document_kind,
                      "http_status": http_status, "adapter_version": adapter_version, "access_status": access_status,
                      "warnings": [], **dict(artifact_metadata or {})},
        ))
    else:
        artifact = existing
    return SourceDocument(
        source_document_id=str(uuid5(NAMESPACE_URL, f"source-document:{source_id}:{query_id}:{source_url}:{digest}")),
        source_id=source_id, channel=channel, query_id=query_id, source_url=source_url,
        document_kind=document_kind, http_status=http_status, retrieved_at=utc_now(),
        raw_artifact_id=artifact.artifact_id, content_hash=digest, content_type=content_type,
        access_status=access_status,
    )


def _batch_key(source_id: str, fingerprint: str, cursor: str | None, adapter_version: str) -> str:
    return hashlib.sha256(f"{source_id}:{fingerprint}:{cursor or ''}:{adapter_version}".encode()).hexdigest()


def _default_kind(channel: str) -> str:
    return {"recruitment_discovery": "search_page", "employer_official": "official_search", "experience": "experience_search"}[channel]


def _brave_nowcoder_candidate_count(raw: bytes) -> int:
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return 0
    web = payload.get("web") if isinstance(payload, dict) else None
    results = web.get("results") if isinstance(web, dict) else None
    if not isinstance(results, list):
        return 0
    return sum(
        canonical_nowcoder_detail_url(str(item.get("url") or "")) is not None
        for item in results if isinstance(item, dict)
    )


def _save_nowcoder_body_fragment(
    *, document: SourceDocument, body: str, selector: str,
    repository: EvidenceRepository,
) -> EvidenceFragment:
    if not document.raw_artifact_id:
        raise ValueError("raw-before-parse: Nowcoder detail artifact is missing")
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
    return repository.save_fragment(EvidenceFragment(
        fragment_id=str(uuid5(
            NAMESPACE_URL,
            f"nowcoder-main-body:{document.raw_artifact_id}:{digest}",
        )),
        artifact_id=document.raw_artifact_id,
        locator_type="css_selector_and_char_range",
        locator={"selector": selector, "start": 0, "end": len(body)},
        text=body, text_hash=digest,
        metadata={
            "source_id": document.source_id,
            "source_document_id": document.source_document_id,
            "document_kind": document.document_kind,
            "parser_version": "nowcoder_main_body_v1",
        },
    ))


def _assert_allowed_url(url: str, allowed_domains: set[str]) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.hostname not in allowed_domains:
        raise ValueError("URL is outside the adapter allowlist")


def _classify_http_response(response: httpx.Response) -> AccessStatus:
    metadata_status = _classify_http_metadata(response)
    if metadata_status != "success":
        return metadata_status
    text = response.text[:5000].casefold()
    if "captcha" in text or "验证码" in text or "login" in text and "password" in text:
        return "authentication_required"
    return "success"


def _classify_http_metadata(response: httpx.Response) -> AccessStatus:
    if response.headers.get("x-source-changed", "").casefold() == "true":
        return "source_changed"
    if 300 <= response.status_code < 400:
        return "official_unavailable"
    if response.status_code in {401, 403}:
        return "authentication_required"
    if response.status_code == 429:
        return "rate_limited"
    if response.status_code == 404:
        return "official_not_found"
    if response.status_code >= 500:
        return "official_unavailable"
    return "success" if response.status_code < 400 else "failed"
