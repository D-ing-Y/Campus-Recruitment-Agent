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
    max_tool_calls: int = Field(default=50, ge=1)


class RoleSearchCounter(BaseModel):
    query_rounds: int = Field(default=0, ge=0)
    queries: int = Field(default=0, ge=0)
    source_switches: int = Field(default=0, ge=0)
    official_verifications: int = Field(default=0, ge=0)
    documents: int = Field(default=0, ge=0)
    llm_calls: int = Field(default=0, ge=0)
    tool_calls: int = Field(default=0, ge=0)


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
    next_cursors: dict[str, str]
    pending_auth_source_id: str | None
    credential_refs: dict[str, str]
    source_batch_ids: Annotated[list[str], stable_union]
    source_run_receipts: Annotated[list[dict[str, Any]], append_items]
    raw_artifact_ids: Annotated[list[str], stable_union]
    extraction_ids: Annotated[list[str], stable_union]
    fragment_ids: Annotated[list[str], stable_union]
    normalized_job_ids: Annotated[list[str], stable_union]
    experience_record_ids: Annotated[list[str], stable_union]
    job_cluster_ids: Annotated[list[str], stable_union]
    official_verification_plan_ids: Annotated[list[str], stable_union]
    job_identity_link_ids: Annotated[list[str], stable_union]
    field_resolution_ids: Annotated[list[str], stable_union]
    official_status_by_cluster: dict[str, str]
    claim_ids: Annotated[list[str], stable_union]
    job_instance_profile_snapshot_ids: Annotated[list[str], stable_union]
    role_family_profile_snapshot_id: str | None
    coverage_assessment: dict[str, Any] | None
    coverage_gaps: list[dict[str, Any]]
    next_action: str | None
    pending_interaction: dict[str, Any] | None
    resume_input: dict[str, Any] | None
    last_auth_action: str | None
    budgets: dict[str, Any]
    counters: dict[str, Any]
    tool_results: Annotated[list[dict[str, Any]], append_items]
    llm_calls: Annotated[list[dict[str, Any]], append_items]
    trace: Annotated[list[dict[str, Any]], append_items]
    errors: Annotated[list[dict[str, Any]], append_items]
    report: dict[str, Any] | None
