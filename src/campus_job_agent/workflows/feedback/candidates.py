"""Validation boundary for optional structured LLM feedback candidates."""

from __future__ import annotations

from typing import Any, Protocol

from campus_job_agent.schemas import FeedbackDiagnosis, FeedbackEvent, FeedbackObservation
from campus_job_agent.workflows.feedback.policy import AUTHORITY, validate_diagnoses


class FeedbackCandidateProvider(Protocol):
    def extract(self, payload: dict[str, Any]) -> tuple[dict[str, Any], list[Any]]: ...


def extract_feedback_with_fallback(provider: FeedbackCandidateProvider | None, *, payload: dict[str, Any],
                                   event: FeedbackEvent, deterministic_observations: list[FeedbackObservation],
                                   deterministic_diagnoses: list[FeedbackDiagnosis],
                                   fragment_texts: dict[str, str]) -> tuple[list[FeedbackObservation], list[FeedbackDiagnosis], list[Any], str | None]:
    if provider is None:
        return deterministic_observations, deterministic_diagnoses, [], None
    calls: list[Any] = []
    last_error: Exception | None = None
    for _ in range(2):
        try:
            candidate, records = provider.extract(payload)
            calls.extend(records)
            observations, diagnoses = validate_feedback_candidates(
                event=event, observation_candidates=candidate.get("observations", []),
                diagnosis_candidates=candidate.get("diagnoses", []), allowed_fragment_ids=set(event.fragment_ids),
                fragment_texts=fragment_texts,
            )
            return observations, diagnoses, calls, None
        except Exception as exc:  # one bounded structured retry
            last_error = exc
    return deterministic_observations, deterministic_diagnoses, calls, str(last_error)


def validate_feedback_candidates(*, event: FeedbackEvent,
                                 observation_candidates: list[FeedbackObservation | dict[str, Any]],
                                 diagnosis_candidates: list[FeedbackDiagnosis | dict[str, Any]],
                                 allowed_fragment_ids: set[str],
                                 fragment_texts: dict[str, str]) -> tuple[list[FeedbackObservation], list[FeedbackDiagnosis]]:
    observations = [item if isinstance(item, FeedbackObservation) else FeedbackObservation.model_validate(item)
                    for item in observation_candidates]
    if any(item.feedback_event_id != event.feedback_event_id or not set(item.fragment_ids).issubset(allowed_fragment_ids)
           for item in observations):
        raise ValueError("invalid_feedback_observation_reference")
    expected_authority = AUTHORITY[event.source_kind]
    for item in observations:
        if item.source_kind != event.source_kind or item.authority != expected_authority:
            raise ValueError("invalid_feedback_observation_authority")
        referenced_text = "\n".join(fragment_texts.get(fragment_id, "") for fragment_id in item.fragment_ids).lower()
        needle = str(item.value).strip().lower()
        if needle and needle not in referenced_text:
            raise ValueError("invalid_feedback_observation_not_in_fragment")
    diagnoses = [item if isinstance(item, FeedbackDiagnosis) else FeedbackDiagnosis.model_validate(item)
                 for item in diagnosis_candidates]
    for item in diagnoses:
        if item.subject_scope in {"job_instance", "company_role", "role_family_candidate"} \
                and not event.target_job_profile_ids:
            raise ValueError("invalid_feedback_diagnosis_scope")
        if item.subject_scope == "candidate_capability" and not (item.capability_id or event.capability_id):
            raise ValueError("invalid_feedback_diagnosis_scope")
    return observations, validate_diagnoses(event, observations, diagnoses)


__all__ = ["FeedbackCandidateProvider", "validate_feedback_candidates", "extract_feedback_with_fallback"]
