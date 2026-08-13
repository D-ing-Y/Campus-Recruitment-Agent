"""v0.5 role-profile graph state, coverage and reducer contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal, TypedDict
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from campus_job_agent.schemas.candidate_graph import append_items, stable_union
from campus_job_agent.schemas.evidence import utc_now


RoleNextAction = Literal[
    "search_more", "change_query", "change_source", "verify_official",
    "await_user_auth", "finalize_with_unknowns", "complete", "fail",
]


class RoleSearchBudget(BaseModel):
    model_config = ConfigDict(frozen=True)
    max_query_rounds: int = Field(default=3, ge=1)
    max_queries: int = Field(default=12, ge=1)
    max_source_switches: int = Field(default=2, ge=0)
    max_official_verifications: int = Field(default=20, ge=0)
    max_documents: int = Field(default=60, ge=1)
    max_llm_calls: int = Field(default=20, ge=0)
    # WP3's family-membership, experience-scope and detail-evidence gates are
    # deterministic tool calls too.  A complete authenticated fixture run now
    # needs 58 calls, so keep the default above that valid baseline while
    # preserving the same hard-budget behaviour for explicitly smaller limits.
    max_tool_calls: int = Field(default=80, ge=1)
    max_recruitment_detail_documents: int = Field(default=20, ge=1)
    max_community_groups: int = Field(default=10, ge=0)
    max_community_queries_per_group: int = Field(default=12, ge=0, le=12)
    max_community_rounds_per_source: int = Field(default=3, ge=1, le=3)
    max_community_sources_per_purpose: int = Field(default=2, ge=1, le=2)
    community_target_documents_per_purpose: int = Field(default=3, ge=1, le=5)
    community_target_clusters_per_purpose: int = Field(default=3, ge=3, le=5)
    max_community_detail_documents_per_query: int = Field(default=3, ge=0, le=10)


class RoleSearchCounter(BaseModel):
    query_rounds: int = Field(default=0, ge=0)
    queries: int = Field(default=0, ge=0)
    source_switches: int = Field(default=0, ge=0)
    official_verifications: int = Field(default=0, ge=0)
    documents: int = Field(default=0, ge=0)
    llm_calls: int = Field(default=0, ge=0)
    tool_calls: int = Field(default=0, ge=0)
    recruitment_searches: int = Field(default=0, ge=0)
    recruitment_details: int = Field(default=0, ge=0)
    community_searches: int = Field(default=0, ge=0)
    community_details: int = Field(default=0, ge=0)


class RoleCoverageGap(BaseModel):
    gap_id: str
    category: Literal[
        "job_count", "company_diversity", "field_completeness", "source_authority",
        "source_diversity", "freshness", "experience_signal", "official_verification",
        "identity_ambiguity", "conflict", "query_relevance",
    ]
    description: str
    importance: float = Field(ge=0.0, le=1.0)
    uncertainty: float = Field(ge=0.0, le=1.0)
    retrievability: float = Field(ge=0.0, le=1.0)
    collection_cost: float = Field(ge=0.0, le=1.0)
    information_value: float = Field(default=0.0, ge=-1.0, le=1.0)
    preferred_action: Literal["search_more", "change_query", "change_source", "verify_official", "await_user_auth", "keep_unknown"]
    target_channel: str | None = None
    target_source_ids: list[str] = Field(default_factory=list)
    related_query_ids: list[str] = Field(default_factory=list)
    status: Literal["open", "resolved", "skipped", "expired"] = "open"

    @model_validator(mode="after")
    def compute_information_value(self) -> "RoleCoverageGap":
        self.information_value = round(max(-1.0, min(1.0, self.importance * self.uncertainty * self.retrievability - self.collection_cost)), 6)
        return self


class CoverageEvaluatorIdentity(BaseModel):
    provider: str = "deterministic"
    model: str = "deterministic-role-coverage-v1"


class RoleCoverageAssessment(BaseModel):
    assessment_id: str = Field(default_factory=lambda: str(uuid4()))
    schema_version: Literal["v0.5"] = "v0.5"
    scope_id: str
    role_family_profile_snapshot_id: str | None = None
    is_sufficient: bool
    dimension_results: dict[str, Literal["sufficient", "partial", "insufficient", "unknown"]]
    coverage_gaps: list[RoleCoverageGap] = Field(default_factory=list)
    recommended_action: RoleNextAction
    reason: str
    confidence: float = Field(ge=0.0, le=1.0)
    evaluator: CoverageEvaluatorIdentity = Field(default_factory=CoverageEvaluatorIdentity)
    prompt_version: str = "role_coverage_v1"
    created_at: datetime = Field(default_factory=utc_now)


class RoleProfileGraphState(TypedDict, total=False):
    workflow_version: str
    run_id: str
    thread_id: str
    user_id: str
    status: str
    output_dir: str | None
    career_intent_snapshot_id: str | None
    search_scope: dict[str, Any]
    query_plan: dict[str, Any] | None
    pending_queries: list[dict[str, Any]]
    completed_query_ids: Annotated[list[str], stable_union]
    query_history: Annotated[list[dict[str, Any]], append_items]
    enabled_source_ids: list[str]
    skipped_source_ids: Annotated[list[str], stable_union]
    source_capabilities: dict[str, dict[str, Any]]
    official_domains: dict[str, list[str]]
    user_requested_official_job_ids: list[str]
    next_cursors: dict[str, str]
    pending_auth_source_id: str | None
    pending_auth_requirement: dict[str, Any] | None
    credential_refs: dict[str, str]
    browser_profile_refs: dict[str, str]
    source_batch_ids: Annotated[list[str], stable_union]
    recruitment_search_document_ids: Annotated[list[str], stable_union]
    recruitment_detail_candidate_ids: Annotated[list[str], stable_union]
    recruitment_detail_request_ids: Annotated[list[str], stable_union]
    recruitment_detail_document_ids: Annotated[list[str], stable_union]
    source_run_receipts: Annotated[list[dict[str, Any]], append_items]
    raw_artifact_ids: Annotated[list[str], stable_union]
    extraction_ids: Annotated[list[str], stable_union]
    fragment_ids: Annotated[list[str], stable_union]
    normalized_job_ids: Annotated[list[str], stable_union]
    role_family_membership_ids: Annotated[list[str], stable_union]
    experience_record_ids: Annotated[list[str], stable_union]
    job_cluster_ids: Annotated[list[str], stable_union]
    company_role_group_ids: Annotated[list[str], stable_union]
    community_search_plan_ids: Annotated[list[str], stable_union]
    community_attempt_queue: list[dict[str, Any]]
    community_attempt_index: int
    community_current_query: dict[str, Any] | None
    community_current_group_id: str | None
    community_current_purpose: str | None
    community_current_source_id: str | None
    community_current_source_priority: int | None
    community_current_round: int | None
    community_current_search_document_ids: list[str]
    community_current_candidate_ids: list[str]
    community_current_detail_document_ids: list[str]
    community_current_evidence_document_ids: list[str]
    community_current_evidence_segment_ids: list[str]
    community_current_diagnostic_ids: list[str]
    community_attempt_receipt_ids: Annotated[list[str], stable_union]
    community_search_diagnostic_ids: Annotated[list[str], stable_union]
    community_coverage_ids: Annotated[list[str], stable_union]
    community_content_cluster_ids: Annotated[list[str], stable_union]
    community_source_evaluation_ids: Annotated[list[str], stable_union]
    community_source_evaluation_by_lane: dict[str, str]
    community_decision_receipt_ids: Annotated[list[str], stable_union]
    community_decision_by_purpose: dict[str, str]
    community_source_allocations_by_purpose: dict[str, dict[str, float]]
    community_proposed_keywords_by_purpose: dict[str, list[str]]
    community_accepted_document_ids_by_scope: dict[str, list[str]]
    community_exhausted_source_ids_by_scope: dict[str, list[str]]
    community_sufficient_scope_keys: Annotated[list[str], stable_union]
    community_last_query_by_lane: dict[str, str]
    community_route: str | None
    community_skip_current_source: bool
    community_query_group_map: dict[str, str]
    community_query_intended_types: dict[str, list[str]]
    community_search_document_ids: Annotated[list[str], stable_union]
    community_post_candidate_ids: Annotated[list[str], stable_union]
    community_detail_request_ids: Annotated[list[str], stable_union]
    community_detail_document_ids: Annotated[list[str], stable_union]
    community_evidence_document_ids: Annotated[list[str], stable_union]
    community_evidence_segment_ids: Annotated[list[str], stable_union]
    community_classification_receipt_ids: Annotated[list[str], stable_union]
    experience_scope_link_ids: Annotated[list[str], stable_union]
    official_verification_plan_ids: Annotated[list[str], stable_union]
    job_identity_link_ids: Annotated[list[str], stable_union]
    role_detail_evidence_receipt_ids: Annotated[list[str], stable_union]
    eligible_job_cluster_ids: Annotated[list[str], stable_union]
    field_resolution_ids: Annotated[list[str], stable_union]
    official_status_by_cluster: dict[str, str]
    official_escalation_receipt_ids: Annotated[list[str], stable_union]
    claim_ids: Annotated[list[str], stable_union]
    job_instance_profile_snapshot_ids: Annotated[list[str], stable_union]
    role_family_profile_snapshot_id: str | None
    job_demand_profile_ids: Annotated[list[str], stable_union]
    role_family_demand_profile_id: str | None
    job_reputation_profile_ids: Annotated[list[str], stable_union]
    company_reputation_profile_ids: Annotated[list[str], stable_union]
    role_intelligence_bundle_id: str | None
    missing_sections: list[str]
    coverage_assessment: dict[str, Any] | None
    coverage_gaps: list[dict[str, Any]]
    next_action: str | None
    pending_interaction: dict[str, Any] | None
    resume_input: dict[str, Any] | None
    last_auth_action: str | None
    budgets: dict[str, Any]
    counters: dict[str, Any]
    # Tool payloads are persisted in domain repositories.  The checkpoint only
    # keeps the latest small diagnostic batch; appending extracted document
    # records would duplicate full page text at every graph step.
    tool_results: list[dict[str, Any]]
    llm_calls: Annotated[list[dict[str, Any]], append_items]
    trace: Annotated[list[dict[str, Any]], append_items]
    errors: Annotated[list[dict[str, Any]], append_items]
    recruitment_errors: Annotated[list[dict[str, Any]], append_items]
    community_errors: Annotated[list[dict[str, Any]], append_items]
    report: dict[str, Any] | None
