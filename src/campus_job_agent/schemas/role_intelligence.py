"""WP3.1 demand/reputation evidence and projection contracts."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from campus_job_agent.schemas.evidence import utc_now
from campus_job_agent.schemas.role import FamilyRequirementAggregate, Qualification, RoleRequirement
from campus_job_agent.schemas.source import canonical_hash, normalize_text


CommunityDocumentType = Literal[
    "interview_experience", "employment_experience", "mixed", "unknown"
]
CommunitySegmentType = Literal[
    "written_exam", "interview_process", "interview_question", "recruiter_feedback",
    "project_preference", "work_intensity", "management", "team_atmosphere",
    "compensation", "growth", "stability", "work_content", "other_reputation", "unknown",
]
CommunityUsage = Literal[
    "demand_assessment", "reputation_job", "reputation_company", "excluded"
]
CommunityEvidencePurpose = Literal["interview_experience", "employment_experience"]
CommunityRelaxationLevel = Literal["exact_role", "role_family", "company_only"]
CommunitySearchOutcome = Literal[
    "post_candidates_found", "non_post_cards_only", "search_empty",
    "parser_changed", "authentication_required", "risk_controlled", "failed",
]

INTERVIEW_SEGMENT_TYPES = {
    "written_exam", "interview_process", "interview_question", "recruiter_feedback",
    "project_preference",
}
REPUTATION_SEGMENT_TYPES = {
    "work_intensity", "management", "team_atmosphere", "compensation", "growth",
    "stability", "work_content", "other_reputation",
}


class JobDetailCandidate(BaseModel):
    candidate_id: str
    schema_version: Literal["v0.7.1"] = "v0.7.1"
    source_id: str
    query_id: str
    search_document_id: str
    search_artifact_id: str
    supporting_fragment_id: str
    detail_url: str
    platform_job_id: str | None = None
    company_hint: str | None = None
    role_title_hint: str | None = None
    location_hint: str | None = None


class CommunityPostCandidate(BaseModel):
    candidate_id: str
    schema_version: Literal["v0.7.1"] = "v0.7.1"
    source_id: str
    query_id: str
    search_document_id: str
    search_artifact_id: str
    supporting_fragment_id: str
    detail_url: str
    external_locator_ref: str | None = None
    platform_post_id: str | None = None
    title_hint: str | None = None
    company_hint: str | None = None
    role_family_hint: str | None = None
    intended_document_types: list[CommunityDocumentType] = Field(default_factory=list)


class CompanyRoleGroup(BaseModel):
    group_id: str
    schema_version: Literal["v0.7.1"] = "v0.7.1"
    search_scope_id: str
    company_key: str
    company_display_name: str
    company_aliases: list[str] = Field(default_factory=list)
    company_search_term: str | None = None
    verified_company_aliases: list[str] = Field(default_factory=list)
    company_alias_policy_version: str = "company_alias_v1"
    role_family_id: str
    job_instance_ids: list[str] = Field(min_length=1)
    exact_role_terms: list[str] = Field(default_factory=list)
    status: Literal["active", "insufficient_identity", "excluded"] = "active"
    created_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def validate_search_identity(self) -> "CompanyRoleGroup":
        aliases = sorted({item.strip() for item in self.verified_company_aliases if item.strip()})
        search_term = (self.company_search_term or self.company_display_name).strip()
        allowed = {normalize_text(self.company_display_name), *(normalize_text(item) for item in aliases)}
        if normalize_text(search_term) not in allowed:
            raise ValueError("company search term must be the display name or a verified alias")
        self.company_search_term = search_term
        self.verified_company_aliases = aliases
        self.company_aliases = sorted({
            *(item.strip() for item in self.company_aliases if item.strip()),
            self.company_display_name.strip(), *aliases,
        })
        return self


class CommunitySearchQuery(BaseModel):
    query_id: str
    query_kind: Literal[
        "company_exact_role", "company_role_family", "company_reputation",
        "generic_family_interview",
    ]
    query_text: str
    intended_document_types: list[CommunityDocumentType]
    source_ids: list[str] = Field(min_length=1)
    source_id: str | None = None
    evidence_purpose: CommunityEvidencePurpose | None = None
    round_index: Literal[1, 2, 3] = 1
    relaxation_level: CommunityRelaxationLevel | None = None
    parent_query_id: str | None = None
    source_priority: Literal[1, 2] = 1
    search_budget: int = Field(default=1, ge=1, le=10)
    detail_budget: int = Field(default=3, ge=1, le=10)
    expansion_reason: str

    @model_validator(mode="after")
    def normalize_wp311_fields(self) -> "CommunitySearchQuery":
        explicit_source_id = self.source_id
        source_id = self.source_id or self.source_ids[0]
        if self.source_id and any(item != self.source_id for item in self.source_ids):
            raise ValueError("new community query must bind exactly one source")
        purpose = self.evidence_purpose
        if purpose is None:
            purpose = (
                "employment_experience"
                if "employment_experience" in self.intended_document_types
                else "interview_experience"
            )
        relaxation = self.relaxation_level
        if relaxation is None:
            relaxation = {
                "company_exact_role": "exact_role",
                "company_role_family": "role_family",
                "company_reputation": "company_only",
                "generic_family_interview": "role_family",
            }[self.query_kind]
        self.source_id = source_id
        if explicit_source_id is not None:
            self.source_ids = [source_id]
        self.evidence_purpose = purpose
        self.relaxation_level = relaxation
        return self


class CommunitySearchPlan(BaseModel):
    plan_id: str
    schema_version: Literal["v0.7.1"] = "v0.7.1"
    company_role_group_id: str
    queries: list[CommunitySearchQuery] = Field(default_factory=list)
    status: Literal["planned", "running", "completed", "partially_blocked", "blocked"] = "planned"


class CommunitySearchAttemptReceipt(BaseModel):
    attempt_id: str
    schema_version: Literal["v0.7.1"] = "v0.7.1"
    company_role_group_id: str
    query_id: str
    source_id: str
    evidence_purpose: CommunityEvidencePurpose
    round_index: Literal[1, 2, 3]
    relaxation_level: CommunityRelaxationLevel
    status: Literal["completed", "empty", "blocked", "failed", "budget_exhausted"]
    discovered_candidate_ids: list[str] = Field(default_factory=list)
    detail_document_ids: list[str] = Field(default_factory=list)
    accepted_document_ids: list[str] = Field(default_factory=list)
    diagnostic_id: str | None = None
    reason_codes: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)


class CommunitySearchDiagnostic(BaseModel):
    diagnostic_id: str
    schema_version: Literal["v0.7.1"] = "v0.7.1"
    source_id: str
    query_id: str
    outcome: CommunitySearchOutcome
    raw_record_count: int = Field(default=0, ge=0)
    post_candidate_count: int = Field(default=0, ge=0)
    non_post_record_count: int = Field(default=0, ge=0)
    parser_signature: str
    reason_codes: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def validate_counts(self) -> "CommunitySearchDiagnostic":
        if self.post_candidate_count + self.non_post_record_count > self.raw_record_count:
            raise ValueError("classified search records cannot exceed raw record count")
        if self.outcome == "post_candidates_found" and self.post_candidate_count == 0:
            raise ValueError("post candidate outcome requires at least one candidate")
        return self


class CommunitySourceEvaluation(BaseModel):
    evaluation_id: str
    schema_version: Literal["v0.7.1"] = "v0.7.1"
    run_id: str
    source_id: str
    evidence_purpose: CommunityEvidencePurpose
    sampled_detail_count: int = Field(default=0, ge=0, le=2)
    relevant_detail_count: int = Field(default=0, ge=0)
    valid_body_count: int = Field(default=0, ge=0)
    scope_hit_count: int = Field(default=0, ge=0)
    accepted_segment_count: int = Field(default=0, ge=0)
    duplicate_detail_count: int = Field(default=0, ge=0)
    failed_detail_count: int = Field(default=0, ge=0)
    relevance_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    valid_body_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    scope_hit_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    duplicate_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    failure_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    latency_ms: int = Field(default=0, ge=0)
    search_cost_units: float = Field(default=0.0, ge=0.0)
    reason_codes: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def validate_sample_counts(self) -> "CommunitySourceEvaluation":
        for value in (
            self.relevant_detail_count, self.valid_body_count,
            self.scope_hit_count, self.duplicate_detail_count,
            self.failed_detail_count,
        ):
            if value > self.sampled_detail_count:
                raise ValueError("community source metric exceeds sampled details")
        return self


class CommunityContentCluster(BaseModel):
    cluster_id: str
    schema_version: Literal["v0.7.1"] = "v0.7.1"
    company_role_group_id: str
    evidence_purpose: CommunityEvidencePurpose
    representative_document_id: str
    member_document_ids: list[str] = Field(min_length=1)
    member_segment_ids: list[str] = Field(default_factory=list)
    source_ids: list[str] = Field(min_length=1)
    merge_methods: list[Literal[
        "not_merged", "canonical_url", "platform_post_id", "body_hash",
        "shingle_jaccard", "semantic_segment_receipt",
    ]] = Field(default_factory=lambda: ["not_merged"])
    max_similarity: float = Field(default=1.0, ge=0.0, le=1.0)
    semantic_decision_receipt_id: str | None = None
    created_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def validate_cluster_members(self) -> "CommunityContentCluster":
        self.member_document_ids = list(dict.fromkeys(self.member_document_ids))
        self.member_segment_ids = list(dict.fromkeys(self.member_segment_ids))
        self.source_ids = sorted(set(self.source_ids))
        if self.representative_document_id not in self.member_document_ids:
            raise ValueError("cluster representative must be a member")
        if "semantic_segment_receipt" in self.merge_methods and not self.semantic_decision_receipt_id:
            raise ValueError("semantic merge requires a decision receipt")
        return self


class CommunitySearchDecisionReceipt(BaseModel):
    decision_id: str
    schema_version: Literal["v0.7.1"] = "v0.7.1"
    run_id: str
    evidence_purpose: CommunityEvidencePurpose
    source_evaluation_ids: list[str] = Field(default_factory=list)
    ranked_source_ids: list[str] = Field(default_factory=list)
    budget_allocation: dict[str, float] = Field(default_factory=dict)
    missing_topics: list[str] = Field(default_factory=list, max_length=10)
    proposed_keywords: list[str] = Field(default_factory=list, max_length=10)
    semantic_duplicate_segment_groups: list[list[str]] = Field(
        default_factory=list, max_length=20
    )
    cluster_ids: list[str] = Field(default_factory=list)
    verdict: Literal["sufficient", "insufficient"]
    hard_floor_met: bool
    provider: str
    model: str
    prompt_version: str = "community_search_decision_v1"
    reason_codes: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def validate_bounded_decision(self) -> "CommunitySearchDecisionReceipt":
        ranked = list(dict.fromkeys(self.ranked_source_ids))
        if ranked != self.ranked_source_ids:
            raise ValueError("ranked community sources must be unique")
        if set(self.budget_allocation) - set(ranked):
            raise ValueError("budget allocation contains an unranked source")
        total = sum(self.budget_allocation.values())
        if self.budget_allocation and abs(total - 1.0) > 1e-6:
            raise ValueError("community source allocation must sum to one")
        if any(value not in {0.0, 0.3, 0.7, 1.0} for value in self.budget_allocation.values()):
            raise ValueError("community source allocation must use policy weights")
        if self.verdict == "sufficient" and not self.hard_floor_met:
            raise ValueError("LLM cannot override the independent-cluster hard floor")
        for keyword in self.proposed_keywords:
            if len(keyword) > 80 or re.search(
                r"https?://|\b(?:site|inurl|intitle|filetype):", keyword,
                re.IGNORECASE,
            ):
                raise ValueError("proposed community keyword exceeds policy")
        return self


class CommunityEvidenceCoverage(BaseModel):
    coverage_id: str
    schema_version: Literal["v0.7.1"] = "v0.7.1"
    company_role_group_id: str
    evidence_purpose: CommunityEvidencePurpose
    target_document_count: int = Field(default=3, ge=1, le=10)
    accepted_document_ids: list[str] = Field(default_factory=list)
    independent_document_count: int = Field(default=0, ge=0)
    target_cluster_count: int = Field(default=3, ge=1, le=10)
    accepted_cluster_ids: list[str] = Field(default_factory=list)
    independent_cluster_count: int = Field(default=0, ge=0)
    decision_receipt_id: str | None = None
    attempted_query_ids: list[str] = Field(default_factory=list)
    exhausted_source_ids: list[str] = Field(default_factory=list)
    status: Literal["sufficient", "insufficient", "blocked", "budget_exhausted"]
    next_action: Literal[
        "next_round", "switch_source", "next_purpose", "next_group", "complete",
        "finalize_partial",
    ]
    reason_codes: list[str] = Field(default_factory=list)
    assessed_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def validate_count(self) -> "CommunityEvidenceCoverage":
        if self.independent_document_count != len(set(self.accepted_document_ids)):
            raise ValueError("independent document count must match unique accepted documents")
        if self.independent_cluster_count != len(set(self.accepted_cluster_ids)):
            raise ValueError("independent cluster count must match unique accepted clusters")
        if self.status == "sufficient" and self.independent_cluster_count < self.target_cluster_count:
            raise ValueError("sufficient coverage requires target cluster count")
        return self


class CommunityExtractionSegment(BaseModel):
    """LLM-visible output: quote and hints only, never internal evidence IDs."""

    model_config = ConfigDict(extra="forbid")
    quote: str = Field(min_length=2, max_length=2000)
    segment_type: CommunitySegmentType
    scope_level: Literal["job_instance", "company_role", "role_family", "company_only", "unknown"]
    company: str | None = None
    role_title: str | None = None
    polarity: Literal["favorable", "mixed", "unfavorable", "unknown"] = "unknown"
    limited_summary: str = Field(default="", max_length=300)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)


class CommunityExtractionBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document_type: CommunityDocumentType
    segments: list[CommunityExtractionSegment] = Field(default_factory=list, max_length=30)


class CommunityDocumentClassificationReceipt(BaseModel):
    receipt_id: str
    schema_version: Literal["v0.7.1"] = "v0.7.1"
    source_document_id: str
    artifact_id: str
    document_type: CommunityDocumentType
    accepted_segment_ids: list[str] = Field(default_factory=list)
    rejected_segment_count: int = Field(default=0, ge=0)
    reason_codes: list[str] = Field(default_factory=list)
    provider: str
    model: str
    prompt_version: str
    created_at: datetime = Field(default_factory=utc_now)


class RoleAuthorizationResponseReceipt(BaseModel):
    """Idempotency receipt for one role-source authorization response."""

    response_id: str
    schema_version: Literal["v0.7.1"] = "v0.7.1"
    request_id: str
    thread_id: str
    user_id: str
    source_id: str
    action: Literal["authorized", "skip_source", "cancel"]
    payload_hash: str
    result_status: str
    role_intelligence_bundle_id: str | None = None
    created_at: datetime = Field(default_factory=utc_now)


class CommunityEvidenceDocument(BaseModel):
    document_id: str
    schema_version: Literal["v0.7.1"] = "v0.7.1"
    artifact_id: str
    source_document_id: str
    source_id: str
    detail_url: str
    retrieved_at: datetime
    published_at: datetime | None = None
    author_fingerprint: str | None = None
    document_type: CommunityDocumentType
    company_key: str | None = None
    role_family_id: str | None = None
    job_instance_id: str | None = None
    classification_receipt_id: str


class CommunityEvidenceSegment(BaseModel):
    segment_id: str
    schema_version: Literal["v0.7.1"] = "v0.7.1"
    document_id: str
    fragment_id: str
    quote_start: int = Field(ge=0)
    quote_end: int = Field(gt=0)
    quote_hash: str
    segment_type: CommunitySegmentType
    usage: CommunityUsage
    company_key: str | None = None
    role_family_id: str | None = None
    job_instance_id: str | None = None
    polarity: Literal["favorable", "mixed", "unfavorable", "unknown"] = "unknown"
    limited_summary: str = ""
    scope_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    classification_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    validation_status: Literal["accepted", "rejected", "ambiguous"]
    reason_codes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_usage_and_quote(self) -> "CommunityEvidenceSegment":
        if self.quote_end <= self.quote_start:
            raise ValueError("community quote range is invalid")
        if len(self.quote_hash) != 64 or any(ch not in "0123456789abcdef" for ch in self.quote_hash):
            raise ValueError("community quote hash must be SHA-256")
        if self.validation_status == "accepted":
            if self.segment_type in INTERVIEW_SEGMENT_TYPES and self.usage != "demand_assessment":
                raise ValueError("interview segment has invalid usage")
            if self.segment_type in REPUTATION_SEGMENT_TYPES and self.usage not in {"reputation_job", "reputation_company"}:
                raise ValueError("employment segment has invalid usage")
            if self.segment_type == "unknown" or self.usage == "excluded":
                raise ValueError("unknown or excluded segment cannot be accepted")
            if self.usage == "reputation_job" and not self.company_key:
                raise ValueError("job reputation requires a company scope")
            if self.usage == "reputation_company" and not self.company_key:
                raise ValueError("company reputation requires a company scope")
        return self


class JobDemandRequirements(BaseModel):
    responsibilities: list[RoleRequirement] = Field(default_factory=list)
    qualifications: list[Qualification] = Field(default_factory=list)
    capabilities: list[RoleRequirement] = Field(default_factory=list)
    preferred_qualifications: list[RoleRequirement] = Field(default_factory=list)
    work_context: list[RoleRequirement] = Field(default_factory=list)


class AssessmentSignal(BaseModel):
    signal_id: str
    topic: str
    stage: str | None = None
    observation: Literal["observed", "frequent", "insufficient_sample", "disputed"]
    sample_count: int = Field(ge=1)
    independent_source_count: int = Field(ge=1)
    segment_ids: list[str] = Field(min_length=1)


class JobDemandProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    profile_id: str
    schema_version: Literal["v0.7.1"] = "v0.7.1"
    job_instance_id: str
    company_key: str
    role_family_id: str
    search_scope_id: str
    jd_requirements: JobDemandRequirements
    assessment_signals: list[AssessmentSignal] = Field(default_factory=list)
    source_document_ids: list[str] = Field(min_length=1)
    official_escalation_receipt_id: str | None = None
    published_at: datetime = Field(default_factory=utc_now)


class DemandDenominator(BaseModel):
    accepted_job_count: int = Field(ge=0)
    accepted_interview_document_count: int = Field(ge=0)


class RoleFamilyDemandProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    profile_id: str
    schema_version: Literal["v0.7.1"] = "v0.7.1"
    role_family_id: str
    search_scope_id: str
    member_job_profile_ids: list[str] = Field(default_factory=list)
    common_requirements: list[FamilyRequirementAggregate] = Field(default_factory=list)
    differentiating_requirements: list[FamilyRequirementAggregate] = Field(default_factory=list)
    assessment_signals: list[AssessmentSignal] = Field(default_factory=list)
    denominator: DemandDenominator
    conflicts: list[dict[str, object]] = Field(default_factory=list)
    published_at: datetime = Field(default_factory=utc_now)


class RoleDistribution(BaseModel):
    role_family_id: str
    sample_count: int = Field(ge=1)


class ReputationDimension(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dimension: str
    polarity: Literal["favorable", "mixed", "unfavorable", "unknown"]
    sample_status: Literal["insufficient_sample", "observed", "sufficient", "disputed"]
    sample_count: int = Field(ge=1)
    independent_source_count: int = Field(ge=1)
    role_distribution: list[RoleDistribution] = Field(default_factory=list)
    earliest_published_at: datetime | None = None
    latest_published_at: datetime | None = None
    supporting_segment_ids: list[str] = Field(default_factory=list)
    contradicting_segment_ids: list[str] = Field(default_factory=list)
    limited_summary: str = ""


class JobReputationProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    profile_id: str
    schema_version: Literal["v0.7.1"] = "v0.7.1"
    company_key: str
    role_family_id: str
    job_instance_ids: list[str] = Field(default_factory=list)
    dimensions: list[ReputationDimension] = Field(default_factory=list)
    source_document_ids: list[str] = Field(default_factory=list)
    published_at: datetime = Field(default_factory=utc_now)


class CompanyReputationProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    profile_id: str
    schema_version: Literal["v0.7.1"] = "v0.7.1"
    company_key: str
    covered_role_families: list[str] = Field(default_factory=list)
    dimensions: list[ReputationDimension] = Field(default_factory=list)
    source_document_ids: list[str] = Field(default_factory=list)
    published_at: datetime = Field(default_factory=utc_now)


class OfficialEscalationReceipt(BaseModel):
    receipt_id: str
    schema_version: Literal["v0.7.1"] = "v0.7.1"
    job_instance_id: str
    required: Literal[False] = False
    trigger: Literal[
        "cross_platform_conflict", "suspected_stale_or_closed", "missing_critical_fields",
        "user_priority_request", "not_required",
    ]
    status: Literal[
        "not_requested", "verified", "unavailable", "adapter_required", "conflicting"
    ] = "not_requested"
    official_document_ids: list[str] = Field(default_factory=list)
    reason_codes: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)


class RoleIntelligenceBundle(BaseModel):
    model_config = ConfigDict(extra="forbid")
    bundle_id: str
    schema_version: Literal["v0.7.1"] = "v0.7.1"
    search_scope_id: str
    role_family_demand_profile_id: str
    job_demand_profile_ids: list[str] = Field(default_factory=list)
    job_reputation_profile_ids: list[str] = Field(default_factory=list)
    company_reputation_profile_ids: list[str] = Field(default_factory=list)
    raw_evidence_refs: list[str] = Field(default_factory=list)
    source_receipt_ids: list[str] = Field(default_factory=list)
    missing_sections: list[str] = Field(default_factory=list)
    evidence_cutoff: datetime = Field(default_factory=utc_now)
    created_at: datetime = Field(default_factory=utc_now)


def stable_role_id(prefix: str, payload: object) -> str:
    return f"{prefix}:{canonical_hash(prefix, payload)[:24]}"


def quote_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


__all__ = [
    "AssessmentSignal", "CommunityDocumentClassificationReceipt",
    "CommunityContentCluster", "CommunityEvidenceDocument", "CommunityEvidenceSegment", "CommunityExtractionBatch",
    "CommunityEvidenceCoverage", "CommunityEvidencePurpose", "CommunityExtractionSegment",
    "CommunitySearchDecisionReceipt", "CommunitySourceEvaluation",
    "CommunityPostCandidate", "CommunityRelaxationLevel", "CommunitySearchAttemptReceipt",
    "CommunitySearchPlan", "CommunitySearchQuery", "CompanyReputationProfile", "CompanyRoleGroup",
    "DemandDenominator", "INTERVIEW_SEGMENT_TYPES", "JobDemandProfile",
    "JobDemandRequirements", "JobDetailCandidate", "JobReputationProfile",
    "OfficialEscalationReceipt", "REPUTATION_SEGMENT_TYPES", "ReputationDimension",
    "RoleAuthorizationResponseReceipt", "RoleFamilyDemandProfile",
    "RoleIntelligenceBundle", "quote_hash", "stable_role_id",
]
