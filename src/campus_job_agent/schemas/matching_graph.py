"""State and hard-budget contracts for the v0.6 matching subgraph."""

from typing import Annotated, Any, TypedDict

from pydantic import BaseModel, ConfigDict, Field

from campus_job_agent.schemas.candidate_graph import append_items, stable_union


class MatchingBudget(BaseModel):
    model_config = ConfigDict(frozen=True)
    max_match_rounds: int = Field(default=3, ge=1)
    max_explanation_calls: int = Field(default=10, ge=0)
    max_decision_interrupts: int = Field(default=3, ge=0)
    max_targets: int = Field(default=20, ge=1)


class MatchingCounter(BaseModel):
    match_rounds: int = Field(default=0, ge=0)
    explanation_calls: int = Field(default=0, ge=0)
    decision_interrupts: int = Field(default=0, ge=0)


class ProfileMatchingGraphState(TypedDict, total=False):
    run_id: str
    thread_id: str
    user_id: str
    status: str
    match_round: int
    match_budget_exhausted: bool
    output_dir: str | None
    input_set_id: str
    candidate_profile_snapshot_id: str
    career_intent_snapshot_id: str
    job_instance_profile_snapshot_ids: list[str]
    role_family_profile_snapshot_ids: list[str]
    qualification_assessment_ids: Annotated[list[str], stable_union]
    requirement_assessment_ids: Annotated[list[str], stable_union]
    preference_assessment_ids: Annotated[list[str], stable_union]
    gap_assessment_ids: Annotated[list[str], stable_union]
    comparison_set_id: str | None
    explanation_ids: Annotated[list[str], stable_union]
    pending_interaction: dict[str, Any] | None
    resume_input: dict[str, Any] | None
    processed_response_ids: Annotated[list[str], stable_union]
    target_decision_ids: Annotated[list[str], stable_union]
    intent_impact_assessment: dict[str, Any] | None
    rebuild_directive_id: str | None
    role_refresh_target_ids: list[str]
    next_action: str | None
    budgets: dict[str, Any]
    counters: dict[str, Any]
    tool_results: Annotated[list[dict[str, Any]], append_items]
    llm_calls: Annotated[list[dict[str, Any]], append_items]
    trace: Annotated[list[dict[str, Any]], append_items]
    errors: Annotated[list[dict[str, Any]], append_items]
    report: dict[str, Any] | None
