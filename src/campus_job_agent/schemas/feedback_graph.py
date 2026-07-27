"""State and budgets for the v0.7 feedback subgraph."""

from typing import Annotated, Any, TypedDict

from pydantic import BaseModel, ConfigDict, Field

from campus_job_agent.schemas.candidate_graph import append_items, stable_union


class FeedbackBudget(BaseModel):
    model_config = ConfigDict(frozen=True)
    max_feedback_items: int = Field(default=20, ge=1)
    max_feedback_interrupts: int = Field(default=3, ge=0)
    max_llm_calls: int = Field(default=12, ge=0)


class FeedbackCounter(BaseModel):
    feedback_items: int = Field(default=0, ge=0)
    feedback_interrupts: int = Field(default=0, ge=0)
    llm_calls: int = Field(default=0, ge=0)


class FeedbackGraphState(TypedDict, total=False):
    run_id: str
    thread_id: str
    user_id: str
    status: str
    allowed_path_roots: list[str]
    plan_id: str | None
    activity_id: str | None
    candidate_profile_snapshot_id: str | None
    career_intent_snapshot_id: str | None
    comparison_set_id: str | None
    target_job_profile_ids: list[str]
    feedback_input: dict[str, Any] | None
    feedback_event_id: str | None
    raw_artifact_ids: Annotated[list[str], stable_union]
    fragment_ids: Annotated[list[str], stable_union]
    observation_ids: Annotated[list[str], stable_union]
    diagnosis_ids: Annotated[list[str], stable_union]
    attribution_ids: Annotated[list[str], stable_union]
    feedback_claim_ids: Annotated[list[str], stable_union]
    progress_event_ids: Annotated[list[str], stable_union]
    impact_assessment_id: str | None
    directive_ids: Annotated[list[str], stable_union]
    resolved_snapshot_refs: dict[str, list[str]]
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

