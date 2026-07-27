"""Deterministic feedback observation, causality, authority and impact policies."""

from __future__ import annotations

import json
from typing import Any, Iterable

from campus_job_agent.schemas import (
    FeedbackAttribution, FeedbackDiagnosis, FeedbackEvent, FeedbackImpactAssessment,
    FeedbackInput, FeedbackObservation,
)
from campus_job_agent.schemas.matching import canonical_hash


REJECTION_OUTCOMES = {"rejected", "no_offer", "failed", "fail", "拒绝", "未通过", "淘汰"}
AUTHORITY = {
    "self_reported": "self_reported", "evaluator_report": "evaluator_observed",
    "platform_result": "platform_reported", "official_result": "official_reported",
    "system_measurement": "system_measured", "imported_document": "unknown",
}


class FeedbackPolicyError(ValueError):
    pass


def extract_observations(event: FeedbackEvent, feedback_input: FeedbackInput,
                         fragment_text: str) -> list[FeedbackObservation]:
    data: dict[str, Any] = {}
    if feedback_input.structured is not None:
        data = feedback_input.structured
    authority = AUTHORITY[feedback_input.source_kind]
    rows: list[tuple[str, Any, str | None, float]] = []
    if event.feedback_type == "task_progress":
        status = data.get("status", fragment_text.strip())
        rows.append(("task_status", status, None, 1.0))
    if "score" in data:
        rows.append(("score", data["score"], None, 1.0))
    outcome = data.get("outcome")
    if outcome is None and event.feedback_type == "application_outcome":
        outcome = fragment_text.strip()
    if outcome is not None:
        kind = "official_outcome" if feedback_input.source_kind == "official_result" else "platform_outcome"
        rows.append((kind, outcome, str(outcome), 1.0))
    for key, kind in (("question_asked", "question_asked"), ("behavior", "behavior_observed"),
                      ("evaluator_comment", "evaluator_comment"), ("comment", "evaluator_comment"),
                      ("reflection", "user_reflection")):
        if data.get(key) not in (None, ""):
            rows.append((kind, data[key], None, 1.0))
    if not rows:
        if event.feedback_type == "user_reflection" or feedback_input.source_kind == "self_reported":
            rows.append(("user_reflection", fragment_text, None, 1.0))
        elif feedback_input.source_kind == "evaluator_report":
            rows.append(("evaluator_comment", fragment_text, None, 1.0))
        else:
            rows.append(("other", fragment_text, None, 0.8))
    result: list[FeedbackObservation] = []
    for index, (kind, value, row_outcome, confidence) in enumerate(rows):
        digest = canonical_hash("feedback-observation", [event.feedback_event_id, index, kind, value, row_outcome])
        result.append(FeedbackObservation(
            observation_id=f"observation:{digest[7:31]}", feedback_event_id=event.feedback_event_id,
            observation_type=kind, value=value, outcome=row_outcome, source_kind=event.source_kind,
            authority=authority, fragment_ids=event.fragment_ids, confidence=confidence,
        ))
    return result


def propose_diagnoses(event: FeedbackEvent, feedback_input: FeedbackInput,
                      observations: list[FeedbackObservation]) -> list[FeedbackDiagnosis]:
    comments = [item for item in observations if item.observation_type in {"evaluator_comment", "behavior_observed"}]
    outcomes = [item for item in observations if item.observation_type in {"platform_outcome", "official_outcome"}]
    if outcomes and not comments and _is_negative_outcome(outcomes[0].outcome or outcomes[0].value):
        return []
    if event.feedback_type == "task_progress" and all(item.observation_type == "task_status" for item in observations):
        return []
    diagnoses: list[FeedbackDiagnosis] = []
    structured = feedback_input.structured or {}
    if comments:
        scope = feedback_input.suggested_scope or "candidate_evidence"
        diagnosis_type = "candidate_capability_signal" if structured.get("capability_level") else "candidate_evidence_gap"
        if scope == "role_family_candidate":
            diagnosis_type = "role_family_signal_candidate"
        elif scope == "job_instance":
            diagnosis_type = "job_hiring_signal"
        elif scope == "company_role":
            diagnosis_type = "company_role_signal"
        elif scope == "career_intent":
            diagnosis_type = "intent_signal"
        diagnoses.append(_diagnosis(event, comments, diagnosis_type, scope, feedback_input.capability_id,
                                    "该明确评价可作为待确认的反馈信号。"))
    questions = [item for item in observations if item.observation_type == "question_asked"]
    if questions:
        diagnoses.append(_diagnosis(event, questions, "job_hiring_signal", "job_instance", None,
                                    "该问题只能表明本次岗位或公司考察信号。"))
    reflections = [item for item in observations if item.observation_type == "user_reflection"]
    if reflections and feedback_input.suggested_scope == "career_intent":
        diagnoses.append(_diagnosis(event, reflections, "intent_signal", "career_intent", None,
                                    "用户复盘表明可能需要复核求职意图。", claim_type="user_reported"))
    return diagnoses


def validate_diagnoses(event: FeedbackEvent, observations: list[FeedbackObservation],
                       diagnoses: Iterable[FeedbackDiagnosis]) -> list[FeedbackDiagnosis]:
    observation_map = {item.observation_id: item for item in observations}
    has_explicit_comment = any(item.observation_type in {"evaluator_comment", "behavior_observed"} for item in observations)
    has_negative_outcome = any(item.observation_type in {"platform_outcome", "official_outcome"}
                               and _is_negative_outcome(item.outcome or item.value) for item in observations)
    result: list[FeedbackDiagnosis] = []
    for diagnosis in diagnoses:
        if not set(diagnosis.observation_ids).issubset(observation_map):
            raise FeedbackPolicyError("feedback_scope_invalid: unknown observation")
        if has_negative_outcome and not has_explicit_comment and diagnosis.subject_scope in {"candidate_capability", "candidate_evidence"}:
            raise FeedbackPolicyError("feedback_causality_violation: rejection without explicit feedback")
        if event.feedback_type == "task_progress" and diagnosis.subject_scope in {"candidate_capability", "candidate_evidence"}:
            raise FeedbackPolicyError("feedback_causality_violation: task completion is not mastery")
        if diagnosis.subject_scope == "role_family_candidate" and diagnosis.diagnosis_type != "role_family_signal_candidate":
            raise FeedbackPolicyError("single_event_family_mutation_blocked")
        result.append(diagnosis)
    return result


def build_attributions(event: FeedbackEvent, observations: list[FeedbackObservation],
                       diagnoses: list[FeedbackDiagnosis], *, candidate_subject_ref: str | None) -> list[FeedbackAttribution]:
    if diagnoses:
        result = []
        for diagnosis in diagnoses:
            related = [item for item in observations if item.observation_id in diagnosis.observation_ids]
            subject_ref = candidate_subject_ref if diagnosis.subject_scope in {"candidate_capability", "candidate_evidence"} else (
                event.target_job_profile_ids[0] if event.target_job_profile_ids else None
            )
            requires = diagnosis.subject_scope not in {"plan_task", "unknown"}
            digest = canonical_hash("feedback-attribution", [event.feedback_event_id, diagnosis.diagnosis_id, diagnosis.subject_scope, subject_ref])
            result.append(FeedbackAttribution(
                attribution_id=f"attribution:{digest[7:31]}", feedback_event_id=event.feedback_event_id,
                observation_ids=diagnosis.observation_ids, diagnosis_ids=[diagnosis.diagnosis_id],
                subject_scope=diagnosis.subject_scope, subject_ref=subject_ref, capability_id=diagnosis.capability_id,
                target_job_profile_ids=event.target_job_profile_ids, authority=related[0].authority,
                requires_confirmation=requires, confirmation_status="pending" if requires else "not_required",
                reason_codes=["high_impact_feedback_attribution" if requires else "low_impact_observation"],
            ))
        return result
    scope = "plan_task" if event.feedback_type == "task_progress" else "unknown"
    digest = canonical_hash("feedback-attribution", [event.feedback_event_id, scope])
    return [FeedbackAttribution(
        attribution_id=f"attribution:{digest[7:31]}", feedback_event_id=event.feedback_event_id,
        observation_ids=[item.observation_id for item in observations], subject_scope=scope,
        subject_ref=event.activity_id if scope == "plan_task" else None,
        target_job_profile_ids=event.target_job_profile_ids, authority=observations[0].authority,
        requires_confirmation=False, confirmation_status="not_required", reason_codes=["outcome_or_progress_observation_only"],
    )]


def assess_impact(event: FeedbackEvent, attributions: list[FeedbackAttribution],
                  progress_ids: list[str]) -> FeedbackImpactAssessment:
    accepted = [item for item in attributions if item.confirmation_status in {"not_required", "confirmed", "relabeled"}]
    scopes = {item.subject_scope for item in accepted}
    candidate = bool(scopes & {"candidate_capability", "candidate_evidence"})
    role = bool(scopes & {"job_instance", "company_role"})
    family = "role_family_candidate" in scopes
    intent = "career_intent" in scopes
    reasons = []
    if progress_ids:
        reasons.append("plan_progress_updated_without_capability_upgrade")
    if candidate:
        reasons.append("new_candidate_feedback_claim")
    if role:
        reasons.append("job_or_company_signal_requires_role_refresh")
    if family:
        reasons.append("single_event_requires_family_aggregation_policy")
    if intent:
        reasons.append("career_intent_review_required")
    digest = canonical_hash("feedback-impact", [event.feedback_event_id, sorted(item.attribution_id for item in accepted), progress_ids])
    return FeedbackImpactAssessment(
        impact_assessment_id=f"feedback-impact:{digest[7:31]}", feedback_event_id=event.feedback_event_id,
        accepted_attribution_ids=[item.attribution_id for item in accepted], progress_updates=progress_ids,
        candidate_rebuild_required=candidate, role_instance_refresh_required=role,
        role_family_aggregation_candidate=family, intent_review_required=intent,
        rematch_required_after_rebuild=candidate or role or intent, replan_required=bool(progress_ids) or candidate or role or intent,
        reason_codes=reasons,
    )


def _diagnosis(event: FeedbackEvent, observations: list[FeedbackObservation], diagnosis_type: str,
               scope: str, capability_id: str | None, summary: str,
               claim_type: str = "model_inference") -> FeedbackDiagnosis:
    ids = [item.observation_id for item in observations]
    digest = canonical_hash("feedback-diagnosis", [event.feedback_event_id, ids, diagnosis_type, scope, capability_id])
    return FeedbackDiagnosis(
        diagnosis_id=f"diagnosis:{digest[7:31]}", feedback_event_id=event.feedback_event_id,
        observation_ids=ids, diagnosis_type=diagnosis_type, subject_scope=scope,
        capability_id=capability_id, target_job_profile_ids=event.target_job_profile_ids,
        summary=summary, alternative_explanations=["样本为单次反馈", "情境或时间限制可能影响表现"],
        limitations=["不能单独确认长期能力等级或招聘因果"], confidence=0.7,
        claim_type=claim_type,
    )


def _is_negative_outcome(value: Any) -> bool:
    normalized = str(value).strip().lower().replace(" ", "_")
    return normalized in REJECTION_OUTCOMES or any(token in normalized for token in ("reject", "no_offer", "failed", "拒绝", "未通过", "淘汰"))


__all__ = ["FeedbackPolicyError", "extract_observations", "propose_diagnoses", "validate_diagnoses",
           "build_attributions", "assess_impact"]
