"""Deterministic v0.4 candidate-profile Graph metrics."""

from pydantic import BaseModel, Field


class CandidateProfileEvalCase(BaseModel):
    name: str
    predicted_action: str
    gold_action: str
    predicted_gap_ids: list[str] = Field(default_factory=list)
    gold_high_value_gap_ids: list[str] = Field(default_factory=list)
    question_count: int = Field(default=0, ge=0)
    actionable_question_count: int = Field(default=0, ge=0)
    redundant_question_count: int = Field(default=0, ge=0)
    interrupt_expected: bool = False
    interrupt_resumed: bool = False
    checkpoint_recovery_expected: bool = False
    checkpoint_recovered: bool = False
    factual_field_count: int = Field(default=0, ge=0)
    supported_factual_field_count: int = Field(default=0, ge=0)
    correction_expected: bool = False
    correction_traced: bool = False
    resume_idempotency_violations: int = Field(default=0, ge=0)
    max_loop_termination_expected: bool = False
    max_loop_terminated: bool = False


class CandidateProfileEvalReport(BaseModel):
    case_count: int
    candidate_route_accuracy: float = Field(ge=0.0, le=1.0)
    high_value_gap_recall: float = Field(ge=0.0, le=1.0)
    question_actionability_rate: float = Field(ge=0.0, le=1.0)
    redundant_question_rate: float = Field(ge=0.0, le=1.0)
    interrupt_resume_success_rate: float = Field(ge=0.0, le=1.0)
    checkpoint_recovery_rate: float = Field(ge=0.0, le=1.0)
    profile_evidence_coverage_rate: float = Field(ge=0.0, le=1.0)
    profile_correction_trace_rate: float = Field(ge=0.0, le=1.0)
    resume_idempotency_violation_count: int = Field(ge=0)
    max_loop_termination_rate: float = Field(ge=0.0, le=1.0)


def evaluate_candidate_profile(
    cases: list[CandidateProfileEvalCase],
) -> CandidateProfileEvalReport:
    route_correct = sum(
        item.predicted_action == item.gold_action for item in cases
    )
    gold_gaps = sum(len(item.gold_high_value_gap_ids) for item in cases)
    found_gaps = sum(
        len(set(item.predicted_gap_ids) & set(item.gold_high_value_gap_ids))
        for item in cases
    )
    questions = sum(item.question_count for item in cases)
    interrupt_cases = [item for item in cases if item.interrupt_expected]
    checkpoint_cases = [
        item for item in cases if item.checkpoint_recovery_expected
    ]
    correction_cases = [item for item in cases if item.correction_expected]
    loop_cases = [
        item for item in cases if item.max_loop_termination_expected
    ]
    return CandidateProfileEvalReport(
        case_count=len(cases),
        candidate_route_accuracy=_ratio(route_correct, len(cases)),
        high_value_gap_recall=_ratio(found_gaps, gold_gaps),
        question_actionability_rate=_ratio(
            sum(item.actionable_question_count for item in cases), questions
        ),
        redundant_question_rate=(
            0.0
            if questions == 0
            else sum(item.redundant_question_count for item in cases) / questions
        ),
        interrupt_resume_success_rate=_ratio(
            sum(item.interrupt_resumed for item in interrupt_cases),
            len(interrupt_cases),
        ),
        checkpoint_recovery_rate=_ratio(
            sum(item.checkpoint_recovered for item in checkpoint_cases),
            len(checkpoint_cases),
        ),
        profile_evidence_coverage_rate=_ratio(
            sum(item.supported_factual_field_count for item in cases),
            sum(item.factual_field_count for item in cases),
        ),
        profile_correction_trace_rate=_ratio(
            sum(item.correction_traced for item in correction_cases),
            len(correction_cases),
        ),
        resume_idempotency_violation_count=sum(
            item.resume_idempotency_violations for item in cases
        ),
        max_loop_termination_rate=_ratio(
            sum(item.max_loop_terminated for item in loop_cases),
            len(loop_cases),
        ),
    )


def _ratio(numerator: int, denominator: int) -> float:
    return 1.0 if denominator == 0 else numerator / denominator
