"""Bounded optional LLM activity candidate validation and deterministic fallback."""

from __future__ import annotations

from typing import Any, Protocol

from campus_job_agent.schemas import PreparationActivity, PreparationObjective
from campus_job_agent.workflows.preparation_plan.policy import validate_dependency_dag


class ActivityCandidateProvider(Protocol):
    def generate(self, payload: dict[str, Any]) -> tuple[list[PreparationActivity | dict[str, Any]], list[Any]]: ...


def generate_activities_with_fallback(provider: ActivityCandidateProvider | None, *, payload: dict[str, Any],
                                      deterministic: list[PreparationActivity], objectives: list[PreparationObjective],
                                      allowed_target_ids: set[str], allowed_gap_ids: set[str],
                                      allowed_signal_ids: set[str], allowed_claim_ids: set[str]) -> tuple[list[PreparationActivity], list[Any], str | None]:
    if provider is None:
        return deterministic, [], None
    calls: list[Any] = []
    last_error: Exception | None = None
    for _ in range(2):
        try:
            candidates, records = provider.generate(payload)
            calls.extend(records)
            return validate_activity_candidates(
                candidates, objectives=objectives, allowed_target_ids=allowed_target_ids,
                allowed_gap_ids=allowed_gap_ids, allowed_signal_ids=allowed_signal_ids,
                allowed_claim_ids=allowed_claim_ids,
            ), calls, None
        except Exception as exc:  # bounded structured retry, then safe template
            last_error = exc
    return deterministic, calls, str(last_error)


def validate_activity_candidates(candidates: list[PreparationActivity | dict[str, Any]], *,
                                 objectives: list[PreparationObjective], allowed_target_ids: set[str],
                                 allowed_gap_ids: set[str], allowed_signal_ids: set[str],
                                 allowed_claim_ids: set[str]) -> list[PreparationActivity]:
    objective_ids = {item.objective_id for item in objectives}
    result: list[PreparationActivity] = []
    for raw in candidates:
        if isinstance(raw, dict) and any(key in raw for key in ("priority_band", "priority_factors", "schedule", "package_status")):
            raise ValueError("llm_priority_mutation")
        item = raw if isinstance(raw, PreparationActivity) else PreparationActivity.model_validate(raw)
        if not set(item.objective_ids).issubset(objective_ids):
            raise ValueError("invalid_activity_reference: objective")
        if not set(item.target_job_profile_ids).issubset(allowed_target_ids):
            raise ValueError("invalid_activity_reference: target")
        if not set(item.gap_ids).issubset(allowed_gap_ids):
            raise ValueError("invalid_activity_reference: gap")
        if not set(item.hiring_signal_ids).issubset(allowed_signal_ids):
            raise ValueError("invalid_activity_reference: signal")
        if not set(item.supporting_claim_ids).issubset(allowed_claim_ids):
            raise ValueError("invalid_activity_reference: claim")
        result.append(item)
    validate_dependency_dag(result)
    return result


__all__ = ["ActivityCandidateProvider", "validate_activity_candidates", "generate_activities_with_fallback"]
