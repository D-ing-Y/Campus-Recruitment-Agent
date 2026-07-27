"""Immutable v0.7 preparation-plan contracts."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, model_validator

from campus_job_agent.schemas.evidence import utc_now
from campus_job_agent.schemas.matching import canonical_hash


ActivityType = Literal[
    "resolve_uncertainty", "strengthen_evidence", "develop_capability",
    "prepare_application_asset", "written_exam_practice", "interview_practice",
    "portfolio_revision", "target_review",
]
PriorityBand = Literal["P0_blocker", "P1_core", "P2_transferable", "P3_bonus", "P4_deferred"]


class PreparationConstraints(BaseModel):
    model_config = ConfigDict(frozen=True)
    constraints_id: str = ""
    schema_version: Literal["v0.7"] = "v0.7"
    timezone: str = "Asia/Shanghai"
    horizon_start: date
    horizon_end: date
    weekly_hours: float = Field(default=15, ge=0)
    daily_max_hours: float = Field(default=4, ge=0)
    unavailable_dates: list[date] = Field(default_factory=list)
    preferred_activity_types: list[ActivityType] = Field(default_factory=list)
    excluded_activity_types: list[ActivityType] = Field(default_factory=list)
    session_minutes: int = Field(default=60, ge=15, le=480)
    confirmed: bool = True
    created_from_response_id: str | None = None

    @model_validator(mode="after")
    def validate_calendar(self) -> "PreparationConstraints":
        if self.horizon_end < self.horizon_start:
            raise ValueError("horizon_end cannot be before horizon_start")
        try:
            ZoneInfo(self.timezone)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("timezone must be a valid IANA timezone") from exc
        overlap = set(self.preferred_activity_types) & set(self.excluded_activity_types)
        if overlap:
            raise ValueError("preferred and excluded activity types cannot overlap")
        if self.daily_max_hours and self.session_minutes > self.daily_max_hours * 60:
            raise ValueError("session_minutes cannot exceed daily maximum")
        object.__setattr__(self, "unavailable_dates", sorted(set(self.unavailable_dates)))
        object.__setattr__(self, "preferred_activity_types", sorted(set(self.preferred_activity_types)))
        object.__setattr__(self, "excluded_activity_types", sorted(set(self.excluded_activity_types)))
        payload = self.model_dump(mode="json", exclude={"constraints_id"})
        digest = canonical_hash("preparation-constraints", payload)
        object.__setattr__(self, "constraints_id", self.constraints_id or f"prep-constraints:{digest[7:31]}")
        return self


class PreparationInputSet(BaseModel):
    model_config = ConfigDict(frozen=True)
    input_set_id: str = ""
    schema_version: Literal["v0.7"] = "v0.7"
    user_id: str
    target_decision_ids: list[str] = Field(min_length=1)
    candidate_profile_snapshot_id: str
    career_intent_snapshot_id: str
    comparison_set_id: str
    gap_assessment_ids: list[str] = Field(min_length=1)
    job_instance_profile_snapshot_ids: list[str] = Field(min_length=1)
    role_family_profile_snapshot_ids: list[str] = Field(default_factory=list)
    constraints_id: str
    planning_policy_version: str = "preparation_v1"
    snapshot_hashes: dict[str, str] = Field(default_factory=dict)
    canonical_input_hash: str = ""
    created_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def canonicalize(self) -> "PreparationInputSet":
        for field in (
            "target_decision_ids", "gap_assessment_ids", "job_instance_profile_snapshot_ids",
            "role_family_profile_snapshot_ids",
        ):
            object.__setattr__(self, field, sorted(set(getattr(self, field))))
        payload = self.model_dump(
            mode="json", exclude={"input_set_id", "canonical_input_hash", "created_at"}
        )
        digest = canonical_hash("preparation-input", payload)
        object.__setattr__(self, "canonical_input_hash", digest)
        object.__setattr__(self, "input_set_id", self.input_set_id or f"prep-input:{digest[7:31]}")
        return self


class PreparationObjective(BaseModel):
    objective_id: str
    schema_version: Literal["v0.7"] = "v0.7"
    objective_type: ActivityType
    title: str
    target_job_profile_ids: list[str] = Field(default_factory=list)
    gap_ids: list[str] = Field(default_factory=list)
    requirement_assessment_ids: list[str] = Field(default_factory=list)
    qualification_assessment_ids: list[str] = Field(default_factory=list)
    hiring_signal_ids: list[str] = Field(default_factory=list)
    application_asset_refs: list[str] = Field(default_factory=list)
    supporting_claim_ids: list[str] = Field(default_factory=list)
    addressability: Literal["addressable", "partially_addressable", "unaddressable", "unknown"]
    reason_codes: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def require_source_ref(self) -> "PreparationObjective":
        if not any((self.gap_ids, self.requirement_assessment_ids, self.qualification_assessment_ids,
                    self.hiring_signal_ids, self.application_asset_refs)):
            raise ValueError("objective requires a gap, requirement, qualification, signal or asset ref")
        return self


class ActivityDependency(BaseModel):
    activity_id: str
    depends_on_activity_id: str


class PreparationActivity(BaseModel):
    activity_id: str
    schema_version: Literal["v0.7"] = "v0.7"
    activity_type: ActivityType
    objective_ids: list[str] = Field(min_length=1)
    title: str = Field(min_length=1)
    description: str = Field(min_length=1)
    expected_outputs: list[str] = Field(min_length=1)
    completion_criteria: list[str] = Field(min_length=1)
    verification_method: Literal[
        "self_report_only", "artifact_required", "evidence_ingestion_required",
        "practice_result_required", "evaluator_feedback_required", "official_outcome_required",
    ]
    estimated_hours: float = Field(gt=0, le=200)
    splittable: bool = True
    minimum_session_minutes: int = Field(default=60, ge=15, le=480)
    deadline: date | None = None
    dependencies: list[str] = Field(default_factory=list)
    target_job_profile_ids: list[str] = Field(default_factory=list)
    gap_ids: list[str] = Field(default_factory=list)
    requirement_assessment_ids: list[str] = Field(default_factory=list)
    qualification_assessment_ids: list[str] = Field(default_factory=list)
    hiring_signal_ids: list[str] = Field(default_factory=list)
    supporting_claim_ids: list[str] = Field(default_factory=list)
    generation_source: Literal["deterministic_template", "llm_candidate", "user_requested"] = "deterministic_template"
    status: Literal["proposed", "scheduled", "active", "completed", "skipped", "blocked", "deferred", "cancelled"] = "proposed"
    deferred_reason: str | None = None

    @model_validator(mode="after")
    def validate_activity(self) -> "PreparationActivity":
        if self.activity_id in self.dependencies:
            raise ValueError("activity cannot depend on itself")
        text = " ".join([self.title, self.description, *self.expected_outputs, *self.completion_criteria]).lower()
        if "http://" in text or "https://" in text:
            raise ValueError("unapproved external resource URL")
        if any(token in text for token in ("guaranteed offer", "offer probability", "保证拿到offer", "必然掌握")):
            raise ValueError("activity contains a prohibited success or mastery claim")
        return self


class PriorityFactors(BaseModel):
    priority_factor_id: str
    activity_id: str
    priority_band: PriorityBand
    selected_target_count: int = Field(ge=0)
    role_importance_weight: float = Field(ge=0)
    hiring_signal_strength: float = Field(ge=0)
    transfer_target_count: int = Field(ge=0)
    deadline_urgency: float = Field(ge=0, le=1)
    improvability: Literal["high", "medium", "low", "unaddressable", "unknown"]
    estimated_effort_hours: float = Field(gt=0)
    sort_key: tuple[Any, ...]
    reason_codes: list[str] = Field(min_length=1)
    policy_version: str = "preparation_priority_v1"


class TargetPreparationSummary(BaseModel):
    addressable_hard_blockers_included: int = Field(default=0, ge=0)
    projected_core_coverage: float | None = Field(default=None, ge=0, le=1)
    coverage_target: float = Field(default=0.8, ge=0, le=1)
    required_application_assets_included: bool = True
    practice_minimum_included: bool = True


class MinimumPreparationPackage(BaseModel):
    package_id: str
    schema_version: Literal["v0.7"] = "v0.7"
    status: Literal["complete", "partial", "blocked", "unknown"]
    included_activity_ids: list[str] = Field(default_factory=list)
    deferred_activity_ids: list[str] = Field(default_factory=list)
    unaddressable_objective_ids: list[str] = Field(default_factory=list)
    deferred_reasons: dict[str, str] = Field(default_factory=dict)
    target_summaries: dict[str, TargetPreparationSummary] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    policy_version: str = "minimum_package_v1"


class ScheduledSession(BaseModel):
    session_id: str
    activity_id: str
    session_index: int = Field(ge=1)
    start_at: datetime
    end_at: datetime
    duration_minutes: int = Field(gt=0)

    @model_validator(mode="after")
    def validate_duration(self) -> "ScheduledSession":
        if self.end_at <= self.start_at:
            raise ValueError("session end must be after start")
        if int((self.end_at - self.start_at).total_seconds() / 60) != self.duration_minutes:
            raise ValueError("duration_minutes does not match timestamps")
        return self


class LearningPlan(BaseModel):
    learning_plan_id: str
    schema_version: Literal["v0.7"] = "v0.7"
    user_id: str
    input_set_id: str
    constraints_id: str
    package_id: str
    objective_ids: list[str]
    activity_ids: list[str]
    priority_factor_ids: list[str]
    schedule: list[ScheduledSession]
    schedule_hash: str
    status: Literal[
        "proposed", "accepted", "active", "completed", "partial", "blocked", "deferred",
        "stale", "superseded", "cancelled",
    ] = "proposed"
    previous_plan_id: str | None = None
    supersedes_plan_id: str | None = None
    change_reason_codes: list[str] = Field(default_factory=list)
    canonical_hash: str
    generated_at: datetime = Field(default_factory=utc_now)


class PlanProgressEvent(BaseModel):
    progress_event_id: str
    schema_version: Literal["v0.7"] = "v0.7"
    learning_plan_id: str
    activity_id: str
    status: Literal["not_started", "active", "completed", "completed_self_reported", "blocked", "skipped"]
    progress_percent: int = Field(ge=0, le=100)
    feedback_event_id: str
    evidence_artifact_ids: list[str] = Field(default_factory=list)
    reason_codes: list[str] = Field(default_factory=list)
    occurred_at: datetime = Field(default_factory=utc_now)


class PlanReviewRequest(BaseModel):
    request_id: str
    schema_version: Literal["v0.7"] = "v0.7"
    thread_id: str
    run_id: str
    user_id: str
    interaction_type: Literal["review_preparation_plan"] = "review_preparation_plan"
    reason: str
    input_set_id: str
    learning_plan_id: str
    package_id: str
    constraints_id: str
    allowed_activity_ids: list[str]
    allowed_actions: list[Literal[
        "accept_plan", "revise_constraints", "exclude_activities",
        "request_activity_revision", "defer_plan", "cancel",
    ]]
    warnings: list[str] = Field(default_factory=lambda: ["priority_is_not_success_probability"])
    created_at: datetime = Field(default_factory=utc_now)


class PlanReviewResponse(BaseModel):
    response_id: str
    schema_version: Literal["v0.7"] = "v0.7"
    request_id: str
    thread_id: str
    user_id: str
    action: Literal[
        "accept_plan", "revise_constraints", "exclude_activities",
        "request_activity_revision", "defer_plan", "cancel",
    ]
    constraints_patch: dict[str, Any] = Field(default_factory=dict)
    activity_ids: list[str] = Field(default_factory=list)
    activity_revision_requests: list[dict[str, Any]] = Field(default_factory=list)
    submitted_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def validate_payload(self) -> "PlanReviewResponse":
        if self.action == "revise_constraints" and not self.constraints_patch:
            raise ValueError("revise_constraints requires constraints_patch")
        if self.action == "exclude_activities" and not self.activity_ids:
            raise ValueError("exclude_activities requires activity_ids")
        if self.action == "request_activity_revision" and not self.activity_revision_requests:
            raise ValueError("request_activity_revision requires requests")
        return self
