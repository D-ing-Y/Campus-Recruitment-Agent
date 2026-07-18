"""v0.4 candidate-profile graph, sufficiency and version contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal, TypedDict
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from campus_job_agent.schemas.evidence import utc_now


GapCategory = Literal[
    "education",
    "experience",
    "capability",
    "responsibility_boundary",
    "evidence_quality",
    "conflict",
]
GapAction = Literal[
    "read_more",
    "ask_user",
    "request_more_materials",
    "keep_unknown",
]
NextAction = Literal[
    "read_more",
    "ask_user",
    "request_more_materials",
    "finalize_with_unknowns",
    "complete",
    "fail",
]
DimensionResult = Literal["sufficient", "partial", "insufficient", "unknown"]


def stable_union(left: list[Any], right: list[Any]) -> list[Any]:
    """Append unique JSON-like values while retaining their first occurrence."""

    result = list(left or [])
    for item in right or []:
        if item not in result:
            result.append(item)
    return result


def append_items(left: list[Any], right: list[Any]) -> list[Any]:
    return list(left or []) + list(right or [])


class InformationGap(BaseModel):
    gap_id: str
    target_path: str
    category: GapCategory
    description: str
    importance: float = Field(ge=0.0, le=1.0)
    uncertainty: float = Field(ge=0.0, le=1.0)
    answerability: float = Field(ge=0.0, le=1.0)
    evidence_cost: float = Field(ge=0.0, le=1.0)
    information_value: float = Field(default=0.0, ge=-1.0, le=1.0)
    preferred_action: GapAction
    related_claim_ids: list[str] = Field(default_factory=list)
    related_artifact_ids: list[str] = Field(default_factory=list)
    status: Literal["open", "resolved", "skipped", "expired"] = "open"

    @model_validator(mode="after")
    def compute_information_value(self) -> "InformationGap":
        # The model may suggest the components, but deterministic code owns the
        # final value and clamps it to the schema range.
        value = self.importance * self.uncertainty * self.answerability
        value -= self.evidence_cost
        self.information_value = round(max(-1.0, min(1.0, value)), 6)
        return self


class EvaluatorIdentity(BaseModel):
    provider: str
    model: str


class SufficiencyAssessment(BaseModel):
    assessment_id: str = Field(default_factory=lambda: str(uuid4()))
    schema_version: Literal["v0.4"] = "v0.4"
    candidate_id: str
    profile_snapshot_id: str | None = None
    is_sufficient: bool
    dimension_results: dict[str, DimensionResult]
    information_gaps: list[InformationGap] = Field(default_factory=list)
    blocking_conflict_ids: list[str] = Field(default_factory=list)
    recommended_action: NextAction
    reason: str
    confidence: float = Field(ge=0.0, le=1.0)
    evaluator: EvaluatorIdentity = Field(
        default_factory=lambda: EvaluatorIdentity(
            provider="deterministic", model="deterministic-sufficiency-v1"
        )
    )
    prompt_version: str = "candidate_sufficiency_v1"
    created_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def require_all_dimensions(self) -> "SufficiencyAssessment":
        required = {
            "education",
            "experience",
            "capability",
            "responsibility_boundary",
            "evidence_quality",
        }
        missing = required - self.dimension_results.keys()
        if missing:
            raise ValueError(
                "dimension_results is missing: " + ", ".join(sorted(missing))
            )
        return self


class QuestionItem(BaseModel):
    question_id: str
    gap_id: str
    target_path: str
    prompt: str
    reason: str
    answer_type: Literal["free_text", "short_text", "boolean", "choice"] = "free_text"
    required: bool = False
    related_claim_ids: list[str] = Field(default_factory=list)


class QuestionPlan(BaseModel):
    plan_id: str
    schema_version: Literal["v0.4"] = "v0.4"
    assessment_id: str
    questions: list[QuestionItem] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)


class ProfileCorrection(BaseModel):
    correction_id: str
    candidate_id: str
    target_path: str
    operation: Literal["add", "replace", "remove", "mark_unknown"]
    new_value: Any = None
    reason: str
    supersedes_claim_ids: list[str] = Field(default_factory=list)
    response_artifact_id: str | None = None
    created_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def validate_operation(self) -> "ProfileCorrection":
        if self.operation in {"add", "replace"} and self.new_value is None:
            raise ValueError(f"{self.operation} correction requires new_value")
        if (
            self.operation in {"replace", "remove", "mark_unknown"}
            and not self.supersedes_claim_ids
        ):
            raise ValueError(
                f"{self.operation} correction requires supersedes_claim_ids"
            )
        return self


class ProfileVersionDiff(BaseModel):
    from_snapshot_id: str
    to_snapshot_id: str
    added_paths: list[str] = Field(default_factory=list)
    removed_paths: list[str] = Field(default_factory=list)
    changed_paths: list[str] = Field(default_factory=list)
    resolved_gap_ids: list[str] = Field(default_factory=list)
    new_conflicts: list[str] = Field(default_factory=list)
    resolved_conflicts: list[str] = Field(default_factory=list)


class BudgetState(BaseModel):
    model_config = ConfigDict(frozen=True)

    max_profile_rounds: int = Field(default=3, ge=1)
    max_questions_per_interrupt: int = Field(default=3, ge=1)
    max_llm_calls: int = Field(default=12, ge=0)
    max_tool_calls: int = Field(default=30, ge=1)


class CounterState(BaseModel):
    profile_rounds: int = Field(default=0, ge=0)
    interaction_rounds: int = Field(default=0, ge=0)
    llm_calls: int = Field(default=0, ge=0)
    tool_calls: int = Field(default=0, ge=0)


class CandidateProfileGraphState(TypedDict, total=False):
    run_id: str
    thread_id: str
    user_id: str
    candidate_id: str
    status: str
    allowed_path_roots: list[str]

    input_paths: list[str]
    pending_artifact_ids: Annotated[list[str], stable_union]
    active_artifact_ids: Annotated[list[str], stable_union]
    processed_artifact_ids: Annotated[list[str], stable_union]
    fragment_ids: Annotated[list[str], stable_union]
    processed_fragment_ids: Annotated[list[str], stable_union]
    claim_ids: Annotated[list[str], stable_union]
    unsupported_artifact_ids: Annotated[list[str], stable_union]

    candidate_profile_snapshot_id: str | None
    sufficiency_assessment: dict[str, Any] | None
    information_gaps: list[dict[str, Any]]
    question_plan: dict[str, Any] | None
    next_action: str | None

    pending_interaction: dict[str, Any] | None
    resume_input: dict[str, Any] | None
    processed_response_ids: Annotated[list[str], stable_union]
    skipped_gap_ids: Annotated[list[str], stable_union]
    asked_question_keys: Annotated[list[str], stable_union]
    last_human_action: str | None

    budgets: dict[str, Any]
    counters: dict[str, Any]
    tool_results: Annotated[list[dict[str, Any]], append_items]
    llm_calls: Annotated[list[dict[str, Any]], append_items]
    trace: Annotated[list[dict[str, Any]], append_items]
    errors: Annotated[list[dict[str, Any]], append_items]
    report: dict[str, Any] | None
