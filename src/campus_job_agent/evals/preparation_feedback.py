"""v0.7 fixed-set metric aggregation without success-probability scoring."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from pydantic import BaseModel, Field


RATE_METRICS = [
    "preparation_priority_accuracy", "minimum_package_rule_accuracy",
    "schedule_capacity_feasibility_rate", "dependency_valid_rate",
    "unaddressable_blocker_visibility_rate", "preparation_evidence_trace_rate",
    "deterministic_schedule_stability_rate", "plan_interrupt_resume_success_rate",
    "raw_before_feedback_interpret_rate", "feedback_scope_accuracy",
    "observation_diagnosis_separation_rate", "feedback_impact_route_accuracy",
    "feedback_claim_trace_rate", "feedback_replan_chain_success_rate", "max_loop_termination_rate",
]
COUNT_METRICS = [
    "rejection_causality_hallucination_count", "task_completion_capability_upgrade_count",
    "single_event_role_family_mutation_count", "feedback_idempotency_violation_count",
    "llm_priority_or_causality_mutation_accept_count",
]


class V07EvalCaseResult(BaseModel):
    case_id: str
    checks: dict[str, bool] = Field(default_factory=dict)
    violation_counts: dict[str, int] = Field(default_factory=dict)
    expected_scope: str | None = None
    predicted_scope: str | None = None


def aggregate_v07_metrics(results: list[V07EvalCaseResult]) -> dict[str, Any]:
    numerators: dict[str, int] = defaultdict(int)
    denominators: dict[str, int] = defaultdict(int)
    counts: dict[str, int] = defaultdict(int)
    confusion: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for result in results:
        for metric, passed in result.checks.items():
            if metric not in RATE_METRICS:
                raise ValueError(f"unknown v0.7 rate metric: {metric}")
            denominators[metric] += 1
            numerators[metric] += int(passed)
        for metric, value in result.violation_counts.items():
            if metric not in COUNT_METRICS:
                raise ValueError(f"unknown v0.7 count metric: {metric}")
            counts[metric] += value
        if result.expected_scope is not None:
            confusion[result.expected_scope][result.predicted_scope or "missing"] += 1
    metrics = {
        metric: (round(numerators[metric] / denominators[metric], 6) if denominators[metric] else None)
        for metric in RATE_METRICS
    }
    metrics.update({metric: counts[metric] for metric in COUNT_METRICS})
    metrics["feedback_scope_confusion_matrix"] = {
        expected: dict(sorted(predicted.items())) for expected, predicted in sorted(confusion.items())
    }
    metrics["case_count"] = len(results)
    metrics["passed_case_count"] = sum(
        all(result.checks.values()) and not any(result.violation_counts.values())
        and (result.expected_scope is None or result.expected_scope == result.predicted_scope)
        for result in results
    )
    return metrics


__all__ = ["COUNT_METRICS", "RATE_METRICS", "V07EvalCaseResult", "aggregate_v07_metrics"]
