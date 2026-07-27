from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from campus_job_agent.schemas import (
    FeedbackAttribution, FeedbackDiagnosis, FeedbackEvent, FeedbackInput, FeedbackObservation,
)
from campus_job_agent.workflows.feedback.candidates import (
    extract_feedback_with_fallback, validate_feedback_candidates,
)
from campus_job_agent.workflows.feedback.policy import (
    FeedbackPolicyError, assess_impact, build_attributions, extract_observations,
    propose_diagnoses, validate_diagnoses,
)


def _event(feedback_type="mock_interview", source_kind="evaluator_report"):
    return FeedbackEvent(
        feedback_event_id="feedback-1", user_id="owner", feedback_type=feedback_type,
        source_kind=source_kind, occurred_at=datetime.now(UTC), plan_id="plan-1", activity_id="activity-1",
        target_job_profile_ids=["role-1"], raw_artifact_ids=["artifact-1"],
        fragment_ids=["fragment-1"], canonical_event_hash="sha256:event",
    )


def _input(feedback_type="mock_interview", source_kind="evaluator_report", **updates):
    payload = {"feedback_type": feedback_type, "source_kind": source_kind,
               "occurred_at": datetime.now(UTC), "text": "explicit feedback"}
    payload.update(updates)
    if "structured" in updates:
        payload["text"] = None
    return FeedbackInput.model_validate(payload)


def _observation(kind="evaluator_comment", authority="evaluator_observed", value="explicit comment"):
    return FeedbackObservation(
        observation_id=f"observation-{kind}", feedback_event_id="feedback-1", observation_type=kind,
        value=value, outcome=value if "outcome" in kind else None, source_kind="evaluator_report",
        authority=authority, fragment_ids=["fragment-1"], confidence=1,
    )


def _diagnosis(scope="candidate_evidence", kind="candidate_evidence_gap", observations=None):
    return FeedbackDiagnosis(
        diagnosis_id=f"diagnosis-{scope}", feedback_event_id="feedback-1",
        observation_ids=observations or ["observation-evaluator_comment"], diagnosis_type=kind,
        subject_scope=scope, summary="inference", alternative_explanations=["alternative"],
        limitations=["single event"], confidence=0.7,
    )


@pytest.mark.parametrize("source_kind,expected", [
    ("self_reported", "self_reported"),
    ("evaluator_report", "evaluator_observed"),
    ("platform_result", "platform_reported"),
    ("official_result", "official_reported"),
    ("system_measurement", "system_measured"),
    ("imported_document", "unknown"),
])
def test_source_authority_is_deterministic(source_kind, expected):
    event = _event(source_kind=source_kind)
    value = _input(source_kind=source_kind)
    assert extract_observations(event, value, "text")[0].authority == expected


def test_structured_feedback_extracts_score_comment_and_outcome_separately():
    event = _event(feedback_type="written_exam", source_kind="platform_result")
    value = _input(feedback_type="written_exam", source_kind="platform_result",
                   structured={"score": 82, "outcome": "passed", "comment": "good reasoning"})
    kinds = {item.observation_type for item in extract_observations(event, value, "{}")}
    assert kinds == {"score", "platform_outcome", "evaluator_comment"}


def test_rejection_without_comment_produces_no_diagnosis():
    event = _event(feedback_type="application_outcome", source_kind="official_result")
    value = _input(feedback_type="application_outcome", source_kind="official_result", text="rejected")
    observations = extract_observations(event, value, "rejected")
    assert propose_diagnoses(event, value, observations) == []


def test_rejection_with_explicit_comment_can_propose_bounded_diagnosis():
    event = _event(feedback_type="interview", source_kind="evaluator_report")
    value = _input(feedback_type="interview", source_kind="evaluator_report",
                   structured={"outcome": "failed", "evaluator_comment": "answer omitted dataset construction"},
                   capability_id="ai.rag", suggested_scope="candidate_evidence")
    observations = extract_observations(event, value, "{}")
    diagnoses = propose_diagnoses(event, value, observations)
    assert len(diagnoses) == 1
    assert diagnoses[0].alternative_explanations and diagnoses[0].limitations


def test_task_progress_does_not_create_capability_diagnosis():
    event = _event(feedback_type="task_progress", source_kind="self_reported")
    value = _input(feedback_type="task_progress", source_kind="self_reported",
                   structured={"status": "completed"})
    observations = extract_observations(event, value, "{}")
    assert propose_diagnoses(event, value, observations) == []


@pytest.mark.parametrize("scope,expected", [
    ("candidate_evidence", "candidate_evidence_gap"),
    ("candidate_capability", "candidate_evidence_gap"),
    ("job_instance", "job_hiring_signal"),
    ("company_role", "company_role_signal"),
    ("role_family_candidate", "role_family_signal_candidate"),
    ("career_intent", "intent_signal"),
])
def test_explicit_comment_scope_mapping(scope, expected):
    event = _event()
    value = _input(structured={"evaluator_comment": "explicit"}, suggested_scope=scope)
    diagnosis = propose_diagnoses(event, value, extract_observations(event, value, "{}"))[0]
    assert diagnosis.subject_scope == scope
    assert diagnosis.diagnosis_type == expected


def test_single_question_is_job_instance_signal_only():
    event = _event(feedback_type="interview", source_kind="self_reported")
    value = _input(feedback_type="interview", source_kind="self_reported",
                   structured={"question_asked": "RAG evaluation?"})
    diagnosis = propose_diagnoses(event, value, extract_observations(event, value, "{}"))[0]
    assert diagnosis.subject_scope == "job_instance"
    assert diagnosis.diagnosis_type == "job_hiring_signal"


def test_diagnosis_requires_alternatives_and_limitations():
    with pytest.raises(ValidationError):
        FeedbackDiagnosis(
            diagnosis_id="d", feedback_event_id="e", observation_ids=["o"],
            diagnosis_type="candidate_evidence_gap", subject_scope="candidate_evidence",
            summary="cause", confidence=0.8,
        )


def test_validator_blocks_rejection_to_capability_causality_jump():
    event = _event(feedback_type="application_outcome", source_kind="official_result")
    observation = _observation("official_outcome", "official_reported", "rejected").model_copy(
        update={"source_kind": "official_result"}
    )
    diagnosis = _diagnosis("candidate_capability", "candidate_capability_signal",
                           observations=[observation.observation_id])
    with pytest.raises(FeedbackPolicyError, match="causality_violation"):
        validate_diagnoses(event, [observation], [diagnosis])


def test_validator_blocks_task_completion_to_mastery_jump():
    event = _event(feedback_type="task_progress", source_kind="self_reported")
    observation = _observation("task_status", "self_reported", "completed")
    diagnosis = _diagnosis("candidate_capability", "candidate_capability_signal",
                           observations=[observation.observation_id])
    with pytest.raises(FeedbackPolicyError, match="task completion"):
        validate_diagnoses(event, [observation], [diagnosis])


def test_validator_blocks_direct_family_mutation_semantics():
    event = _event()
    observation = _observation()
    diagnosis = _diagnosis("role_family_candidate", "job_hiring_signal")
    with pytest.raises(FeedbackPolicyError, match="family_mutation"):
        validate_diagnoses(event, [observation], [diagnosis])


def test_high_impact_attribution_requires_confirmation_without_authority_upgrade():
    event = _event()
    observation = _observation(authority="self_reported")
    diagnosis = _diagnosis()
    attribution = build_attributions(event, [observation], [diagnosis],
                                     candidate_subject_ref="candidate:owner")[0]
    assert attribution.requires_confirmation
    assert attribution.confirmation_status == "pending"
    assert attribution.authority == "self_reported"


def test_outcome_only_attribution_is_unknown_and_needs_no_confirmation():
    event = _event(feedback_type="application_outcome", source_kind="official_result")
    observation = _observation("official_outcome", "official_reported", "rejected")
    attribution = build_attributions(event, [observation], [], candidate_subject_ref="candidate:owner")[0]
    assert attribution.subject_scope == "unknown"
    assert attribution.confirmation_status == "not_required"


@pytest.mark.parametrize("scope,flags", [
    ("plan_task", (False, False, False, False)),
    ("candidate_evidence", (True, False, False, False)),
    ("job_instance", (False, True, False, False)),
    ("company_role", (False, True, False, False)),
    ("role_family_candidate", (False, False, True, False)),
    ("career_intent", (False, False, False, True)),
    ("unknown", (False, False, False, False)),
])
def test_feedback_impact_route_truth_table(scope, flags):
    event = _event()
    attribution = FeedbackAttribution(
        attribution_id=f"a-{scope}", feedback_event_id=event.feedback_event_id,
        observation_ids=["o"], diagnosis_ids=[] if scope in {"plan_task", "unknown"} else ["d"],
        subject_scope=scope, authority="evaluator_observed", requires_confirmation=False,
        confirmation_status="not_required", reason_codes=["fixture"],
    )
    impact = assess_impact(event, [attribution], ["progress"] if scope == "plan_task" else [])
    assert (impact.candidate_rebuild_required, impact.role_instance_refresh_required,
            impact.role_family_aggregation_candidate, impact.intent_review_required) == flags


def test_llm_candidate_reference_outside_fragment_set_is_rejected():
    event = _event()
    observation = _observation().model_copy(update={"fragment_ids": ["fabricated"]})
    with pytest.raises(ValueError, match="reference"):
        validate_feedback_candidates(event=event, observation_candidates=[observation],
                                     diagnosis_candidates=[], allowed_fragment_ids={"fragment-1"},
                                     fragment_texts={"fragment-1": "explicit comment"})
    elevated = _observation(authority="official_reported")
    with pytest.raises(ValueError, match="authority"):
        validate_feedback_candidates(event=event, observation_candidates=[elevated],
                                     diagnosis_candidates=[], allowed_fragment_ids={"fragment-1"},
                                     fragment_texts={"fragment-1": "explicit comment"})
    invented = _observation(value="invented fact")
    with pytest.raises(ValueError, match="not_in_fragment"):
        validate_feedback_candidates(event=event, observation_candidates=[invented],
                                     diagnosis_candidates=[], allowed_fragment_ids={"fragment-1"},
                                     fragment_texts={"fragment-1": "explicit comment"})


class _CausalityProvider:
    def __init__(self):
        self.calls = 0

    def extract(self, payload):
        self.calls += 1
        return payload, []


def test_invalid_llm_causality_retries_then_falls_back_to_outcome_observation():
    event = _event(feedback_type="application_outcome", source_kind="official_result").model_copy(
        update={"capability_id": "programming.python"}
    )
    observation = _observation("official_outcome", "official_reported", "rejected").model_copy(
        update={"source_kind": "official_result"}
    )
    diagnosis = _diagnosis("candidate_capability", "candidate_capability_signal",
                           observations=[observation.observation_id])
    provider = _CausalityProvider()
    observations, diagnoses, _, error = extract_feedback_with_fallback(
        provider, payload={"observations": [observation.model_dump(mode="json")],
                           "diagnoses": [diagnosis.model_dump(mode="json")]},
        event=event, deterministic_observations=[observation], deterministic_diagnoses=[],
        fragment_texts={"fragment-1": "rejected"},
    )
    assert provider.calls == 2
    assert observations == [observation]
    assert diagnoses == []
    assert "causality_violation" in error
