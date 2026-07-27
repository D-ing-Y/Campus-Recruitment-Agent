"""Deterministic v0.6 matching and decision metrics."""

from pydantic import BaseModel, Field


class ProfileMatchingEvalCase(BaseModel):
    name: str
    gold_hard_status: str | None = None
    predicted_hard_status: str | None = None
    coverage_expected: bool = False
    coverage_exact: bool = False
    gold_gap_types: list[str] = Field(default_factory=list)
    predicted_gap_types: list[str] = Field(default_factory=list)
    unknown_as_failure_count: int = Field(default=0, ge=0)
    explicit_outcome_count: int = Field(default=0, ge=0)
    traced_explicit_outcome_count: int = Field(default=0, ge=0)
    stability_expected: bool = False
    stable: bool = False
    preference_update_expected: bool = False
    preference_isolated: bool = False
    same_scope_expected: bool = False
    avoided_research: bool = False
    scope_change_expected: bool = False
    role_research_rerouted: bool = False
    stale_invalidation_expected: bool = False
    stale_invalidated: bool = False
    decision_resume_expected: bool = False
    decision_resumed: bool = False
    idempotency_violations: int = Field(default=0, ge=0)
    offer_probability_claims: int = Field(default=0, ge=0)
    llm_fact_mutations_accepted: int = Field(default=0, ge=0)
    max_loop_expected: bool = False
    max_loop_terminated: bool = False


class ProfileMatchingEvalReport(BaseModel):
    case_count: int
    hard_constraint_accuracy: float = Field(ge=0, le=1)
    coverage_calculation_accuracy: float = Field(ge=0, le=1)
    gap_label_accuracy: float = Field(ge=0, le=1)
    unknown_as_failure_count: int
    assessment_evidence_trace_rate: float = Field(ge=0, le=1)
    deterministic_output_stability_rate: float = Field(ge=0, le=1)
    preference_update_isolation_rate: float = Field(ge=0, le=1)
    same_scope_update_no_research_rate: float = Field(ge=0, le=1)
    search_scope_change_reroute_accuracy: float = Field(ge=0, le=1)
    stale_assessment_invalidation_rate: float = Field(ge=0, le=1)
    decision_interrupt_resume_success_rate: float = Field(ge=0, le=1)
    decision_idempotency_violation_count: int
    offer_probability_claim_count: int
    llm_fact_mutation_accept_count: int
    max_match_loop_termination_rate: float = Field(ge=0, le=1)


def evaluate_profile_matching(cases: list[ProfileMatchingEvalCase]) -> ProfileMatchingEvalReport:
    hard = [item for item in cases if item.gold_hard_status is not None]
    coverage = [item for item in cases if item.coverage_expected]
    gold_gap_count = sum(len(item.gold_gap_types) for item in cases)
    correct_gaps = sum(
        len(set(item.gold_gap_types) & set(item.predicted_gap_types))
        for item in cases
    )
    return ProfileMatchingEvalReport(
        case_count=len(cases),
        hard_constraint_accuracy=_ratio(sum(item.gold_hard_status == item.predicted_hard_status for item in hard), len(hard)),
        coverage_calculation_accuracy=_ratio(sum(item.coverage_exact for item in coverage), len(coverage)),
        gap_label_accuracy=_ratio(correct_gaps, gold_gap_count),
        unknown_as_failure_count=sum(item.unknown_as_failure_count for item in cases),
        assessment_evidence_trace_rate=_ratio(sum(item.traced_explicit_outcome_count for item in cases), sum(item.explicit_outcome_count for item in cases)),
        deterministic_output_stability_rate=_expected_rate(cases, "stability_expected", "stable"),
        preference_update_isolation_rate=_expected_rate(cases, "preference_update_expected", "preference_isolated"),
        same_scope_update_no_research_rate=_expected_rate(cases, "same_scope_expected", "avoided_research"),
        search_scope_change_reroute_accuracy=_expected_rate(cases, "scope_change_expected", "role_research_rerouted"),
        stale_assessment_invalidation_rate=_expected_rate(cases, "stale_invalidation_expected", "stale_invalidated"),
        decision_interrupt_resume_success_rate=_expected_rate(cases, "decision_resume_expected", "decision_resumed"),
        decision_idempotency_violation_count=sum(item.idempotency_violations for item in cases),
        offer_probability_claim_count=sum(item.offer_probability_claims for item in cases),
        llm_fact_mutation_accept_count=sum(item.llm_fact_mutations_accepted for item in cases),
        max_match_loop_termination_rate=_expected_rate(cases, "max_loop_expected", "max_loop_terminated"),
    )


def _expected_rate(cases: list[ProfileMatchingEvalCase], expected: str, result: str) -> float:
    selected = [item for item in cases if getattr(item, expected)]
    return _ratio(sum(bool(getattr(item, result)) for item in selected), len(selected))


def _ratio(numerator: int, denominator: int) -> float:
    return 1.0 if denominator == 0 else numerator / denominator


__all__ = ["ProfileMatchingEvalCase", "ProfileMatchingEvalReport", "evaluate_profile_matching"]
