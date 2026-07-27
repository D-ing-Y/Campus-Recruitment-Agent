"""Evidence-first v0.7 feedback contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from campus_job_agent.schemas.evidence import utc_now


FeedbackScope = Literal[
    "plan_task", "candidate_capability", "candidate_evidence", "job_instance",
    "company_role", "role_family_candidate", "career_intent", "unknown",
]


class FeedbackInput(BaseModel):
    feedback_type: Literal[
        "task_progress", "practice_result", "mock_interview", "written_exam", "interview",
        "application_outcome", "portfolio_review", "user_reflection", "other",
    ]
    source_kind: Literal[
        "self_reported", "evaluator_report", "platform_result", "official_result",
        "system_measurement", "imported_document",
    ]
    occurred_at: datetime
    text: str | None = None
    file_path: str | None = None
    structured: dict[str, Any] | None = None
    stage: str | None = None
    capability_id: str | None = None
    suggested_scope: FeedbackScope | None = None

    @model_validator(mode="after")
    def require_one_payload(self) -> "FeedbackInput":
        if sum(value is not None for value in (self.text, self.file_path, self.structured)) != 1:
            raise ValueError("feedback requires exactly one of text, file_path or structured")
        return self


class FeedbackEvent(BaseModel):
    feedback_event_id: str
    schema_version: Literal["v0.7"] = "v0.7"
    user_id: str
    feedback_type: str
    source_kind: str
    occurred_at: datetime
    plan_id: str | None = None
    activity_id: str | None = None
    target_job_profile_ids: list[str] = Field(default_factory=list)
    stage: str | None = None
    capability_id: str | None = None
    suggested_scope: FeedbackScope | None = None
    raw_artifact_ids: list[str] = Field(min_length=1)
    fragment_ids: list[str] = Field(default_factory=list)
    canonical_event_hash: str
    status: Literal[
        "received", "archived", "interpreted", "awaiting_confirmation", "processed",
        "completed_with_unknowns", "cancelled", "failed",
    ] = "archived"
    created_at: datetime = Field(default_factory=utc_now)


class FeedbackObservation(BaseModel):
    observation_id: str
    schema_version: Literal["v0.7"] = "v0.7"
    feedback_event_id: str
    observation_type: Literal[
        "task_status", "score", "question_asked", "behavior_observed", "evaluator_comment",
        "platform_outcome", "official_outcome", "user_reflection", "other",
    ]
    value: Any = None
    outcome: str | None = None
    source_kind: str
    authority: Literal[
        "self_reported", "system_measured", "evaluator_observed", "platform_reported",
        "official_reported", "unknown",
    ]
    fragment_ids: list[str] = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)
    extractor_version: str = "feedback_observation_v1"


class FeedbackDiagnosis(BaseModel):
    diagnosis_id: str
    schema_version: Literal["v0.7"] = "v0.7"
    feedback_event_id: str
    observation_ids: list[str] = Field(min_length=1)
    diagnosis_type: Literal[
        "candidate_capability_signal", "candidate_evidence_gap", "job_hiring_signal",
        "company_role_signal", "role_family_signal_candidate", "intent_signal",
        "plan_adjustment_signal", "unknown",
    ]
    subject_scope: FeedbackScope
    capability_id: str | None = None
    target_job_profile_ids: list[str] = Field(default_factory=list)
    summary: str
    alternative_explanations: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)
    claim_type: Literal["model_inference", "user_reported"] = "model_inference"
    status: Literal["proposed", "accepted", "rejected", "unknown"] = "proposed"
    extractor_version: str = "feedback_diagnosis_v1"

    @model_validator(mode="after")
    def require_uncertainty(self) -> "FeedbackDiagnosis":
        if self.diagnosis_type != "unknown" and (not self.alternative_explanations or not self.limitations):
            raise ValueError("non-unknown diagnosis requires alternatives and limitations")
        return self


class FeedbackAttribution(BaseModel):
    attribution_id: str
    schema_version: Literal["v0.7"] = "v0.7"
    feedback_event_id: str
    observation_ids: list[str] = Field(min_length=1)
    diagnosis_ids: list[str] = Field(default_factory=list)
    subject_scope: FeedbackScope
    subject_ref: str | None = None
    capability_id: str | None = None
    target_job_profile_ids: list[str] = Field(default_factory=list)
    authority: str
    requires_confirmation: bool
    confirmation_status: Literal[
        "not_required", "pending", "confirmed", "relabeled", "rejected", "unknown",
    ]
    confirmed_by_response_id: str | None = None
    reason_codes: list[str] = Field(min_length=1)


class FeedbackImpactAssessment(BaseModel):
    impact_assessment_id: str
    schema_version: Literal["v0.7"] = "v0.7"
    feedback_event_id: str
    accepted_attribution_ids: list[str] = Field(default_factory=list)
    progress_updates: list[str] = Field(default_factory=list)
    candidate_rebuild_required: bool = False
    role_instance_refresh_required: bool = False
    role_family_aggregation_candidate: bool = False
    intent_review_required: bool = False
    rematch_required_after_rebuild: bool = False
    replan_required: bool = False
    reason_codes: list[str] = Field(default_factory=list)
    policy_version: str = "feedback_impact_v1"


class FeedbackDirective(BaseModel):
    model_config = ConfigDict(frozen=True)
    directive_id: str
    schema_version: Literal["v0.7"] = "v0.7"
    directive_type: Literal[
        "candidate_profile_rebuild_required", "role_instance_refresh_required",
        "role_family_aggregation_candidate", "intent_review_required", "rematch_required",
        "replan_required",
    ]
    originating_feedback_event_id: str
    originating_plan_id: str | None = None
    reason_codes: list[str] = Field(min_length=1)
    required_input_refs: list[str] = Field(default_factory=list)
    affected_target_ids: list[str] = Field(default_factory=list)
    status: Literal["pending", "consumed", "resolved", "cancelled", "failed"] = "pending"
    resolved_refs: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)


class AttributionReviewRequest(BaseModel):
    request_id: str
    schema_version: Literal["v0.7"] = "v0.7"
    thread_id: str
    run_id: str
    user_id: str
    interaction_type: Literal["confirm_feedback_attribution"] = "confirm_feedback_attribution"
    feedback_event_id: str
    observation_ids: list[str]
    diagnosis_ids: list[str]
    attribution_ids: list[str]
    attribution_summaries: list[dict[str, Any]]
    allowed_scopes: list[FeedbackScope]
    allowed_actions: list[Literal[
        "confirm_attributions", "relabel_scope", "reject_diagnoses", "mark_unknown", "cancel",
    ]]
    created_at: datetime = Field(default_factory=utc_now)


class ScopeRelabel(BaseModel):
    attribution_id: str
    subject_scope: FeedbackScope
    subject_ref: str | None = None


class AttributionReviewResponse(BaseModel):
    response_id: str
    schema_version: Literal["v0.7"] = "v0.7"
    request_id: str
    thread_id: str
    user_id: str
    action: Literal[
        "confirm_attributions", "relabel_scope", "reject_diagnoses", "mark_unknown", "cancel",
    ]
    attribution_ids: list[str] = Field(default_factory=list)
    diagnosis_ids: list[str] = Field(default_factory=list)
    scope_relabels: list[ScopeRelabel] = Field(default_factory=list)
    submitted_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def validate_payload(self) -> "AttributionReviewResponse":
        if self.action == "confirm_attributions" and not self.attribution_ids:
            raise ValueError("confirm_attributions requires attribution_ids")
        if self.action == "relabel_scope" and not self.scope_relabels:
            raise ValueError("relabel_scope requires scope_relabels")
        if self.action == "reject_diagnoses" and not self.diagnosis_ids:
            raise ValueError("reject_diagnoses requires diagnosis_ids")
        return self


class DirectiveResolution(BaseModel):
    directive_id: str
    response_id: str
    user_id: str
    old_snapshot_ref: str | None = None
    resolved_refs: list[str] = Field(min_length=1)
    no_change: bool = False
