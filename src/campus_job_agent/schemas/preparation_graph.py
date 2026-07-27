"""State and budgets for the v0.7 preparation subgraph."""

from typing import Annotated, Any, TypedDict

from pydantic import BaseModel, ConfigDict, Field

from campus_job_agent.schemas.candidate_graph import append_items, stable_union


class PreparationBudget(BaseModel):
    model_config = ConfigDict(frozen=True)
    max_plan_rounds: int = Field(default=3, ge=1)
    max_activities: int = Field(default=30, ge=1)
    max_llm_calls: int = Field(default=12, ge=0)
    max_plan_interrupts: int = Field(default=3, ge=0)


class PreparationCounter(BaseModel):
    plan_rounds: int = Field(default=0, ge=0)
    llm_calls: int = Field(default=0, ge=0)
    plan_interrupts: int = Field(default=0, ge=0)


class PreparationPlanGraphState(TypedDict, total=False):
    run_id: str
    thread_id: str
    user_id: str
    status: str
    target_decision_ids: list[str]
    candidate_profile_snapshot_id: str
    career_intent_snapshot_id: str
    comparison_set_id: str
    gap_assessment_ids: list[str]
    job_instance_profile_snapshot_ids: list[str]
    role_family_profile_snapshot_ids: list[str]
    constraints_id: str
    previous_plan_id: str | None
    excluded_activity_ids: Annotated[list[str], stable_union]
    activity_revisions: dict[str, dict[str, Any]]
    input_set_id: str
    objective_ids: Annotated[list[str], stable_union]
    activity_ids: Annotated[list[str], stable_union]
    priority_factor_ids: Annotated[list[str], stable_union]
    package_id: str | None
    learning_plan_id: str | None
    pending_interaction: dict[str, Any] | None
    resume_input: dict[str, Any] | None
    processed_response_ids: Annotated[list[str], stable_union]
    next_action: str | None
    budgets: dict[str, Any]
    counters: dict[str, Any]
    tool_results: Annotated[list[dict[str, Any]], append_items]
    llm_calls: Annotated[list[dict[str, Any]], append_items]
    trace: Annotated[list[dict[str, Any]], append_items]
    errors: Annotated[list[dict[str, Any]], append_items]
    report: dict[str, Any] | None
