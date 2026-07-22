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

from campus_job_agent.schemas import (
    EvidenceArtifact,
    OfficialVerificationPlan,
    SourceBatch,
    SourceCapabilities,
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
    ) -> None:
        self.source_id = source_id
        self.fixture_pages = fixture_pages
        self.blob_store = blob_store
        self.evidence_repository = evidence_repository
        self.role_repository = role_repository
        self.owner_id = owner_id
        self.capabilities = SourceCapabilities(
            source_id=source_id,
            channel=channel,
            source_type=source_type,
            adapter_version=f"{source_id}_fixture_v1",
            supports_location=True,
            supports_company=True,
            supports_pagination=True,
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
            supports_company=True, supports_pagination=True, requires_auth=requires_auth,
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

    def build_url(self, query: SourceQuery) -> str:
        raise NotImplementedError

    def request_headers(self, credential_headers: dict[str, str]) -> dict[str, str]:
        return {"Accept": "text/html,application/json;q=0.9,*/*;q=0.8", **credential_headers}

    def classify_response(self, response: httpx.Response) -> AccessStatus:
        return _classify_http_response(response)

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


class ZhaopinJobsAdapter(_HttpAdapter):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(
            source_id="zhaopin_jobs", channel="recruitment_discovery",
            source_type="recruitment_platform", requires_auth=False,
            allowed_domains={"sou.zhaopin.com", "www.zhaopin.com"},
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


class NowcoderExperienceAdapter(_HttpAdapter):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(source_id="nowcoder_experience", channel="experience", source_type="community_experience", requires_auth=True, allowed_domains={"www.nowcoder.com"}, **kwargs)

    def build_url(self, query: SourceQuery) -> str:
        from urllib.parse import quote
        return f"https://www.nowcoder.com/search/all?query={quote(' '.join(query.keywords))}"


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
                      "http_status": http_status, "adapter_version": adapter_version, "access_status": access_status, "warnings": []},
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
