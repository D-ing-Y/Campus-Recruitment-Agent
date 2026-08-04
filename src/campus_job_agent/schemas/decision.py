"""v0.6 target-decision, intent-impact and rebuild contracts."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from campus_job_agent.schemas.evidence import utc_now


class TargetDecisionInput(BaseModel):
    job_instance_profile_snapshot_id: str
    status: Literal["selected", "deferred", "rejected"]
    reason_codes: list[str] = Field(default_factory=list)
    note: str | None = Field(default=None, max_length=500)


class TargetDecision(BaseModel):
    decision_id: str
    schema_version: Literal["v0.6"] = "v0.6"
    user_id: str
    comparison_set_id: str
    job_instance_profile_snapshot_id: str
    status: Literal["selected", "deferred", "rejected"]
    reason_codes: list[str] = Field(default_factory=list)
    note: str | None = Field(default=None, max_length=500)
    created_from_response_id: str
    supersedes_decision_id: str | None = None
    created_at: datetime = Field(default_factory=utc_now)


class IntentRevision(BaseModel):
    revision_id: str
    schema_version: Literal["v0.6"] = "v0.6"
    previous_intent_snapshot_id: str
    requested_patch: dict[str, Any]
    changed_paths: list[str]
    created_from_response_id: str


class IntentImpactAssessment(BaseModel):
    impact_assessment_id: str
    previous_intent_snapshot_id: str
    new_intent_snapshot_id: str
    changed_paths: list[str]
    search_scope_hash_before: str
    search_scope_hash_after: str
    impact: Literal["rematch_only", "role_research_required", "no_effect"]
    reason_codes: list[str] = Field(default_factory=list)
    policy_version: str = "intent_impact_v1"

    @model_validator(mode="after")
    def enforce_scope_impact(self) -> "IntentImpactAssessment":
        if self.search_scope_hash_before == self.search_scope_hash_after and self.impact == "role_research_required":
            raise ValueError("unchanged SearchScope cannot require role research")
        return self


class RebuildDirective(BaseModel):
    directive_id: str
    schema_version: Literal["v0.6"] = "v0.6"
    directive_type: Literal["candidate_profile_required", "rematch_required", "role_research_required", "role_refresh_required"]
    originating_run_id: str
    originating_comparison_set_id: str
    reason_codes: list[str] = Field(min_length=1)
    required_input_refs: list[str] = Field(default_factory=list)
    affected_job_profile_ids: list[str] = Field(default_factory=list)
    requested_scope: dict[str, Any] | None = None
    requested_scopes: list[dict[str, Any]] = Field(default_factory=list)
    status: Literal["pending", "consumed", "cancelled", "failed"] = "pending"
    created_at: datetime = Field(default_factory=utc_now)


class ComparisonReviewRequest(BaseModel):
    request_id: str
    schema_version: Literal["v0.6"] = "v0.6"
    thread_id: str
    run_id: str
    user_id: str
    interaction_type: Literal["review_comparison"] = "review_comparison"
    reason: str
    comparison_set_id: str
    input_snapshot_refs: dict[str, Any]
    target_summaries: list[dict[str, Any]]
    allowed_target_ids: list[str]
    allowed_actions: list[Literal[
        "select_targets", "defer_targets", "reject_targets", "revise_candidate",
        "revise_intent", "refresh_role", "confirm_and_finish", "cancel",
    ]]
    warnings: list[str] = Field(default_factory=lambda: ["coverage_is_not_offer_probability"])
    created_at: datetime = Field(default_factory=utc_now)


class ComparisonReviewResponse(BaseModel):
    response_id: str
    schema_version: Literal["v0.6"] = "v0.6"
    request_id: str
    thread_id: str
    user_id: str
    action: Literal[
        "select_targets", "defer_targets", "reject_targets", "revise_candidate",
        "revise_intent", "refresh_role", "confirm_and_finish", "cancel",
    ]
    target_decisions: list[TargetDecisionInput] = Field(default_factory=list)
    candidate_revision: dict[str, Any] | None = None
    intent_revision: dict[str, Any] | None = None
    role_refresh_target_ids: list[str] = Field(default_factory=list)
    submitted_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def validate_payload(self) -> "ComparisonReviewResponse":
        expected = {"select_targets": "selected", "defer_targets": "deferred", "reject_targets": "rejected"}
        if self.action in expected:
            if not self.target_decisions or any(item.status != expected[self.action] for item in self.target_decisions):
                raise ValueError(f"{self.action} requires matching target decision statuses")
        if self.action == "revise_candidate" and not self.candidate_revision:
            raise ValueError("revise_candidate requires candidate_revision")
        if self.action == "revise_intent" and not self.intent_revision:
            raise ValueError("revise_intent requires intent_revision")
        if self.action == "refresh_role" and not self.role_refresh_target_ids:
            raise ValueError("refresh_role requires target ids")
        return self
