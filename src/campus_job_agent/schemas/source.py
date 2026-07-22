"""v0.5 source collection, normalization and verification contracts."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from campus_job_agent.schemas.evidence import utc_now


SourceChannel = Literal["recruitment_discovery", "employer_official", "experience"]
SourceType = Literal[
    "employer_official",
    "recruitment_platform",
    "community_experience",
    "fixture",
    "manual_import",
]
AccessStatus = Literal[
    "success",
    "empty",
    "authentication_required",
    "rate_limited",
    "source_changed",
    "robots_disallowed",
    "official_not_found",
    "official_unavailable",
    "identity_ambiguous",
    "adapter_required",
    "policy_blocked",
    "failed",
]


def canonical_hash(prefix: str, value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(f"{prefix}:{payload}".encode("utf-8")).hexdigest()


class SearchScope(BaseModel):
    scope_id: str = Field(default_factory=lambda: str(uuid4()))
    schema_version: Literal["v0.5"] = "v0.5"
    career_intent_snapshot_id: str | None = None
    target_role_queries: list[str] = Field(min_length=1)
    target_role_family: str
    locations: list[str] = Field(default_factory=list)
    graduation_year: str
    recruitment_type: Literal["autumn_campus", "spring_campus", "internship", "unknown"]
    industries: list[str] = Field(default_factory=list)
    companies: list[str] = Field(default_factory=list)
    hard_constraints: list[dict[str, Any]] = Field(default_factory=list)
    preferred_languages: list[str] = Field(default_factory=lambda: ["zh-CN"])
    created_at: datetime = Field(default_factory=utc_now)

    @field_validator("target_role_family", "graduation_year")
    @classmethod
    def non_empty_or_unknown(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("value must be non-empty or explicit unknown")
        return value

    def fingerprint(self) -> str:
        return canonical_hash("search-scope", self.model_dump(mode="json", exclude={"scope_id", "created_at"}))


class SourceCapabilities(BaseModel):
    source_id: str
    channel: SourceChannel
    source_type: SourceType
    adapter_version: str
    supports_keyword: bool = True
    supports_location: bool = False
    supports_company: bool = False
    supports_pagination: bool = False
    requires_auth: bool = False
    live_enabled: bool = False
    rate_limit_per_minute: int = Field(default=6, ge=1)


class SourceQuery(BaseModel):
    query_id: str = Field(default_factory=lambda: str(uuid4()))
    schema_version: Literal["v0.5"] = "v0.5"
    channel: SourceChannel
    source_id: str
    keywords: list[str] = Field(min_length=1)
    location: str | None = None
    company: str | None = None
    role_family: str
    graduation_year: str
    recruitment_type: str
    cursor: str | None = None
    page_size: int = Field(default=20, ge=1, le=100)
    parent_query_id: str | None = None
    change_reason: Literal[
        "initial_scope", "pagination", "synonym_expansion", "low_relevance",
        "low_recall", "authority_gap", "source_fallback",
    ] = "initial_scope"
    fingerprint: str = ""

    @model_validator(mode="after")
    def set_fingerprint(self) -> "SourceQuery":
        payload = self.model_dump(mode="json", exclude={"query_id", "fingerprint"})
        computed = canonical_hash("source-query", payload)
        if self.fingerprint and self.fingerprint != computed:
            raise ValueError("source query fingerprint does not match canonical content")
        self.fingerprint = computed
        return self


class PlannerIdentity(BaseModel):
    provider: str = "deterministic"
    model: str = "deterministic-role-query-v1"


class RoleQueryPlan(BaseModel):
    plan_id: str = Field(default_factory=lambda: str(uuid4()))
    schema_version: Literal["v0.5"] = "v0.5"
    scope_id: str
    queries: list[SourceQuery] = Field(default_factory=list)
    coverage_gap_ids: list[str] = Field(default_factory=list)
    planner: PlannerIdentity = Field(default_factory=PlannerIdentity)
    prompt_version: str = "role_query_planner_v1"
    created_at: datetime = Field(default_factory=utc_now)


class SourceDocument(BaseModel):
    source_document_id: str = Field(default_factory=lambda: str(uuid4()))
    schema_version: Literal["v0.5"] = "v0.5"
    source_id: str
    channel: SourceChannel
    query_id: str
    source_url: str
    document_kind: Literal[
        "search_page", "job_detail", "employer_job_detail", "official_search",
        "official_job_detail", "experience_search", "experience_post", "imported_snapshot",
    ]
    http_status: int | None = None
    published_at: datetime | None = None
    retrieved_at: datetime = Field(default_factory=utc_now)
    raw_artifact_id: str | None = None
    content_hash: str | None = None
    content_type: str = "text/html"
    access_status: AccessStatus = "success"
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def success_requires_raw(self) -> "SourceDocument":
        if self.access_status == "success" and (not self.raw_artifact_id or not self.content_hash):
            raise ValueError("successful source document requires archived raw artifact and hash")
        return self


class SourceBatch(BaseModel):
    batch_id: str = Field(default_factory=lambda: str(uuid4()))
    schema_version: Literal["v0.5"] = "v0.5"
    source_id: str
    channel: SourceChannel
    query_id: str
    cursor: str | None = None
    next_cursor: str | None = None
    documents: list[SourceDocument] = Field(default_factory=list)
    status: AccessStatus = "success"
    error_type: str | None = None
    retryable: bool = False
    needs_user_action: bool = False
    idempotency_key: str
    started_at: datetime = Field(default_factory=utc_now)
    completed_at: datetime = Field(default_factory=utc_now)


class SourceRunReceipt(BaseModel):
    source_run_id: str = Field(default_factory=lambda: str(uuid4()))
    schema_version: Literal["v0.5"] = "v0.5"
    run_id: str
    source_id: str
    channel: SourceChannel
    adapter_version: str
    query_ids: list[str] = Field(default_factory=list)
    received_count: int = Field(default=0, ge=0)
    archived_count: int = Field(default=0, ge=0)
    normalized_count: int = Field(default=0, ge=0)
    deduplicated_count: int = Field(default=0, ge=0)
    artifact_ids: list[str] = Field(default_factory=list)
    public_source_urls: list[str] = Field(default_factory=list)
    auth_used: bool = False
    status: Literal["completed", "partial", "failed", "interrupted"] = "completed"
    warnings: list[str] = Field(default_factory=list)
    started_at: datetime = Field(default_factory=utc_now)
    completed_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def reject_secrets(self) -> "SourceRunReceipt":
        payload = self.model_dump_json().lower()
        for marker in ("cookie:", "authorization:", "bearer ", "curl "):
            if marker in payload:
                raise ValueError("source receipt contains credential material")
        return self


class NormalizedJobPosting(BaseModel):
    job_posting_id: str = Field(default_factory=lambda: str(uuid4()))
    schema_version: Literal["v0.5"] = "v0.5"
    job_id: str | None = None
    company: str
    company_type: str = "unknown"
    role_title: str
    role_family: str
    city: str = "unknown"
    work_location_detail: str | None = None
    salary_min: float | None = None
    salary_max: float | None = None
    salary_unit: str | None = None
    salary_source: str = "unknown"
    job_description: str = ""
    requirements_raw: str = ""
    requirements_normalized: list[str] = Field(default_factory=list)
    degree_requirement: str | None = None
    major_requirement: str | None = None
    graduation_year: str = "unknown"
    recruitment_type: str = "unknown"
    application_deadline: datetime | None = None
    application_url: str | None = None
    source_url: str
    source_id: str
    source_type: SourceType
    source_date: datetime | None = None
    retrieved_at: datetime = Field(default_factory=utc_now)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    status: Literal["included", "deferred", "excluded_hard_scope", "expired", "closed", "unknown"] = "included"
    exclusion_code: str | None = None
    exclusion_evidence_fragment_ids: list[str] = Field(default_factory=list)
    raw_artifact_ids: list[str] = Field(min_length=1)
    supporting_fragment_ids: list[str] = Field(min_length=1)
    notes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_exclusion(self) -> "NormalizedJobPosting":
        if self.status == "excluded_hard_scope" and (not self.exclusion_code or not self.exclusion_evidence_fragment_ids):
            raise ValueError("hard-scope exclusion requires code and evidence")
        return self

    def exact_identity_key(self) -> str | None:
        discriminator = self.job_id or self.application_url
        if not discriminator:
            signature = canonical_hash("job-content", [self.job_description, self.requirements_raw])
            discriminator = signature if self.job_description or self.requirements_raw else None
        if not discriminator:
            return None
        return canonical_hash("job-exact", [
            normalize_text(self.company), normalize_text(self.role_title), normalize_text(self.city),
            self.recruitment_type, self.graduation_year, discriminator,
        ])


class ExperienceSignals(BaseModel):
    written_exam: list[str] = Field(default_factory=list)
    interview: list[str] = Field(default_factory=list)
    tech_stack: list[str] = Field(default_factory=list)
    project_preference: list[str] = Field(default_factory=list)
    salary: list[str] = Field(default_factory=list)
    work_context: list[str] = Field(default_factory=list)


class EvidenceQuote(BaseModel):
    text: str
    fragment_id: str


class ExperienceEvidenceRecord(BaseModel):
    experience_record_id: str = Field(default_factory=lambda: str(uuid4()))
    schema_version: Literal["v0.5"] = "v0.5"
    platform: str
    query_id: str
    content_type: str
    source_url: str
    title: str
    author_ref: str = "anonymous"
    published_at: datetime | None = None
    retrieved_at: datetime = Field(default_factory=utc_now)
    company: str | None = None
    role_title: str | None = None
    role_family: str | None = None
    city: str | None = None
    stage: str | None = None
    scope_level: Literal["job_instance", "company_role", "role_family", "company_only", "unknown"] = "unknown"
    signals: ExperienceSignals = Field(default_factory=ExperienceSignals)
    summary: str = ""
    evidence_quotes: list[EvidenceQuote] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    tags: list[str] = Field(default_factory=list)
    raw_artifact_id: str
    supporting_fragment_ids: list[str] = Field(min_length=1)
    notes: list[str] = Field(default_factory=list)


class JobPostingCluster(BaseModel):
    cluster_id: str = Field(default_factory=lambda: str(uuid4()))
    schema_version: Literal["v0.5"] = "v0.5"
    canonical_job_posting_id: str
    member_job_posting_ids: list[str] = Field(min_length=1)
    exact_key: str | None = None
    merge_method: Literal["same_source_url", "same_content_hash", "exact_normalized_key", "verified_fuzzy_candidate", "not_merged"]
    confidence: float = Field(ge=0.0, le=1.0)
    conflicts: list[dict[str, Any]] = Field(default_factory=list)
    source_ids: list[str] = Field(default_factory=list)
    fuzzy_candidate_ids: list[str] = Field(default_factory=list)


class OfficialVerificationPlan(BaseModel):
    verification_plan_id: str = Field(default_factory=lambda: str(uuid4()))
    schema_version: Literal["v0.5"] = "v0.5"
    job_cluster_id: str
    canonical_company: str
    candidate_role_title: str
    candidate_location: str | None = None
    candidate_recruitment_cycle: str | None = None
    candidate_application_ids: list[str] = Field(default_factory=list)
    official_domain_candidates: list[str] = Field(default_factory=list)
    official_entry_url_candidates: list[str] = Field(default_factory=list)
    allowed_domains: list[str] = Field(default_factory=list)
    max_pages: int = Field(default=10, ge=1, le=50)
    max_depth: int = Field(default=2, ge=0, le=5)
    created_reason: str = "verify_third_party_candidate"


class JobIdentityLink(BaseModel):
    job_identity_link_id: str = Field(default_factory=lambda: str(uuid4()))
    schema_version: Literal["v0.5"] = "v0.5"
    job_cluster_id: str
    official_job_posting_id: str | None = None
    status: Literal["candidate", "confirmed", "rejected", "identity_ambiguous", "official_not_found", "official_unavailable"]
    match_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    match_signals: dict[str, str] = Field(default_factory=dict)
    supporting_fragment_ids: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def confirmed_requires_strong_identity(self) -> "JobIdentityLink":
        if self.status == "confirmed":
            strong = sum(value in {"exact", "strong"} for value in self.match_signals.values())
            if not self.official_job_posting_id or strong < 4 or not self.supporting_fragment_ids:
                raise ValueError("confirmed identity link requires official record, evidence and four strong signals")
        return self


class FieldResolution(BaseModel):
    field_resolution_id: str = Field(default_factory=lambda: str(uuid4()))
    schema_version: Literal["v0.5"] = "v0.5"
    job_identity_link_id: str
    predicate: str
    chosen_claim_id: str | None = None
    conflicting_claim_ids: list[str] = Field(default_factory=list)
    resolution_status: Literal["resolved", "third_party_only", "official_only", "unresolved_conflict", "identity_ambiguous"]
    reason: str
    authority: Literal["primary", "allowed", "signal_only", "forbidden"]
    freshness: Literal["current", "recent", "historical", "expired", "unknown"] = "unknown"
    resolved_at: datetime = Field(default_factory=utc_now)


class OfficialSiteAdapterSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    spec_id: str = Field(default_factory=lambda: str(uuid4()))
    schema_version: Literal["v0.5"] = "v0.5"
    allowed_domains: list[str] = Field(min_length=1)
    entry_url_patterns: list[str] = Field(default_factory=list)
    document_kind_rules: list[dict[str, Any]] = Field(default_factory=list)
    selectors_or_jsonpaths: list[dict[str, Any]] = Field(default_factory=list)
    pagination_rules: list[dict[str, Any]] = Field(default_factory=list)
    stop_conditions: dict[str, int] = Field(default_factory=lambda: {"max_pages": 10, "max_depth": 2})
    status: Literal["candidate", "approved", "rejected"] = "candidate"

    def validate_against_plan(self, plan: OfficialVerificationPlan) -> None:
        if not set(self.allowed_domains).issubset(set(plan.allowed_domains)):
            raise ValueError("adapter spec expands the verification domain allowlist")
        if self.stop_conditions.get("max_pages", 0) > plan.max_pages or self.stop_conditions.get("max_depth", 0) > plan.max_depth:
            raise ValueError("adapter spec expands the verification budget")


class CredentialRef(BaseModel):
    credential_ref: str
    source_id: str
    credential_type: Literal["imported_curl", "cookie", "api_key_ref"]
    validated_at: datetime = Field(default_factory=utc_now)

    @field_validator("credential_ref")
    @classmethod
    def reference_only(cls, value: str) -> str:
        if not value.startswith("local-secret://"):
            raise ValueError("credential_ref must use local-secret://")
        return value


def normalize_text(value: str | None) -> str:
    return "".join((value or "").casefold().split())
