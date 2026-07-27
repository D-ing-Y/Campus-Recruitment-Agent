from datetime import UTC, datetime

from langgraph.checkpoint.memory import InMemorySaver
import pytest

from campus_job_agent.schemas import (
    AttributionReviewResponse, FeedbackDiagnosis, FeedbackDirective, FeedbackEvent, FeedbackInput,
    PlanProgressEvent, ProfileSnapshot,
)
from campus_job_agent.storage import LocalBlobStore
from campus_job_agent.workflows.feedback import (
    FeedbackGraphRuntime, FeedbackReplanSaga, FeedbackServiceError, SQLiteFeedbackRepository, create_feedback_state,
    open_sqlite_checkpointer,
)
from campus_job_agent.schemas import LearningPlan
from tests.helpers_v07 import build_v07_plan, seed_v07


def _runtime(tmp_path, *, checkpointer=None):
    data = seed_v07(tmp_path)
    plan, _, activities, _ = build_v07_plan(data)
    feedback = SQLiteFeedbackRepository(tmp_path / "domain.sqlite3")
    runtime = FeedbackGraphRuntime(
        blob_store=LocalBlobStore(tmp_path / "blobs"), evidence_repository=data["profiles"],
        profile_repository=data["profiles"], matching_repository=data["matching"],
        preparation_repository=data["preparation"], feedback_repository=feedback,
        checkpointer=checkpointer or InMemorySaver(),
    )
    return runtime, data, feedback, plan, activities[0]


def _invoke(runtime, value, plan, activity, *, thread="feedback-thread", scope=None):
    value = value.model_copy(update={"suggested_scope": scope}) if scope else value
    return runtime.invoke(create_feedback_state(
        thread_id=thread, user_id="owner", feedback_input=value, allowed_path_roots=[],
        plan_id=plan.learning_plan_id, activity_id=activity.activity_id,
        target_job_profile_ids=["role-s1"], candidate_profile_snapshot_id="candidate-s1",
        career_intent_snapshot_id="intent-s1", comparison_set_id="comparison-1",
    ))


def _request(result):
    return result["__interrupt__"][0].value


def _confirm(request, *, action="confirm_attributions", response_id="feedback-response"):
    return AttributionReviewResponse(
        response_id=response_id, request_id=request["request_id"], thread_id=request["thread_id"],
        user_id=request["user_id"], action=action,
        attribution_ids=request["attribution_ids"] if action == "confirm_attributions" else [],
        diagnosis_ids=request["diagnosis_ids"] if action == "reject_diagnoses" else [],
    )


def test_task_completion_only_updates_progress(tmp_path):
    runtime, data, feedback, plan, activity = _runtime(tmp_path)
    result = _invoke(runtime, FeedbackInput(
        feedback_type="task_progress", source_kind="self_reported", occurred_at=datetime.now(UTC),
        structured={"status": "completed", "progress_percent": 100},
    ), plan, activity)
    assert result["status"] == "completed"
    assert len(data["preparation"].list("progress_event", PlanProgressEvent, owner_id="owner")) == 1
    assert result["feedback_claim_ids"] == []


def test_rejection_without_explanation_is_outcome_only(tmp_path):
    runtime, _, feedback, plan, activity = _runtime(tmp_path)
    result = _invoke(runtime, FeedbackInput(
        feedback_type="application_outcome", source_kind="official_result",
        occurred_at=datetime.now(UTC), text="rejected",
    ), plan, activity)
    assert result["status"] == "completed"
    assert feedback.list("feedback_diagnosis", FeedbackDiagnosis, owner_id="owner") == []
    assert result["feedback_claim_ids"] == []


def test_explicit_evaluator_signal_interrupts_then_creates_candidate_directives(tmp_path):
    runtime, _, feedback, plan, activity = _runtime(tmp_path)
    interrupted = _invoke(runtime, FeedbackInput(
        feedback_type="mock_interview", source_kind="evaluator_report", occurred_at=datetime.now(UTC),
        capability_id="programming.python",
        structured={"evaluator_comment": "The candidate demonstrated intermediate Python.",
                    "capability_level": "intermediate"},
    ), plan, activity, scope="candidate_capability")
    request = _request(interrupted)
    assert request["interaction_type"] == "confirm_feedback_attribution"
    assert interrupted["feedback_input"] is None
    result = runtime.resume(thread_id="feedback-thread", response=_confirm(request))
    assert result["status"] == "awaiting_rebuild"
    types = {feedback.get(item, FeedbackDirective, owner_id="owner").directive_type for item in result["directive_ids"]}
    assert {"candidate_profile_rebuild_required", "rematch_required", "replan_required"}.issubset(types)
    assert len(result["feedback_claim_ids"]) == 1
    assert result["report"]["attributions"][0]["status"] == "confirmed"
    assert result["report"]["impact"]["candidate_rebuild_required"] is True
    assert result["report"]["directives"]
    assert "raw_feedback_omitted_from_report" in result["report"]["warnings"]


def test_job_question_creates_job_signal_not_family_mutation(tmp_path):
    runtime, _, feedback, plan, activity = _runtime(tmp_path)
    interrupted = _invoke(runtime, FeedbackInput(
        feedback_type="interview", source_kind="self_reported", occurred_at=datetime.now(UTC),
        structured={"question_asked": "How do you evaluate RAG?"},
    ), plan, activity)
    request = _request(interrupted)
    result = runtime.resume(thread_id="feedback-thread", response=_confirm(request))
    types = {feedback.get(item, FeedbackDirective, owner_id="owner").directive_type for item in result["directive_ids"]}
    assert "role_instance_refresh_required" in types
    assert "role_family_aggregation_candidate" not in types


def test_user_rejects_diagnosis_preserves_observation_without_claim(tmp_path):
    runtime, _, _, plan, activity = _runtime(tmp_path)
    interrupted = _invoke(runtime, FeedbackInput(
        feedback_type="mock_interview", source_kind="evaluator_report", occurred_at=datetime.now(UTC),
        text="The answer omitted dataset construction.", capability_id="ai.rag",
    ), plan, activity, scope="candidate_evidence")
    request = _request(interrupted)
    result = runtime.resume(thread_id="feedback-thread", response=_confirm(request, action="reject_diagnoses"))
    assert result["status"] == "completed_with_unknowns"
    assert result["observation_ids"]
    assert result["feedback_claim_ids"] == []


def test_duplicate_event_reuses_event_and_artifact(tmp_path):
    runtime, data, feedback, plan, activity = _runtime(tmp_path)
    value = FeedbackInput(feedback_type="application_outcome", source_kind="official_result",
                          occurred_at=datetime(2026, 8, 1, tzinfo=UTC), text="rejected")
    first = _invoke(runtime, value, plan, activity, thread="duplicate-one")
    second = _invoke(runtime, value, plan, activity, thread="duplicate-two")
    assert first["feedback_event_id"] == second["feedback_event_id"]
    assert feedback.count("feedback_event", owner_id="owner") == 1
    assert len(data["profiles"].list_fragments(first["raw_artifact_ids"][0])) == 1


def test_feedback_candidate_snapshot_rematch_replan_saga(tmp_path):
    runtime, data, feedback, plan, activity = _runtime(tmp_path)
    interrupted = _invoke(runtime, FeedbackInput(
        feedback_type="mock_interview", source_kind="evaluator_report", occurred_at=datetime.now(UTC),
        capability_id="Python", structured={
            "evaluator_comment": "The candidate demonstrated intermediate Python in a scored exercise.",
            "capability_level": "intermediate",
        },
    ), plan, activity, scope="candidate_capability")
    request = _request(interrupted)
    awaiting = runtime.resume(thread_id="feedback-thread", response=_confirm(request, response_id="saga-confirm"))
    saga = FeedbackReplanSaga(
        evidence_repository=data["profiles"], profile_repository=data["profiles"],
        matching_repository=data["matching"], preparation_repository=data["preparation"],
        feedback_repository=feedback, feedback_service=runtime.service,
    )
    result = saga.run_candidate_feedback_replan(
        user_id="owner", feedback_event_id=awaiting["feedback_event_id"],
        old_candidate_snapshot_id="candidate-s1", old_plan_id=plan.learning_plan_id,
    )
    assert result["new_candidate_snapshot_id"] != "candidate-s1"
    assert result["new_plan_id"] != plan.learning_plan_id
    assert data["preparation"].get(plan.learning_plan_id, LearningPlan, owner_id="owner").status == "superseded"
    assert all(feedback.get(item, FeedbackDirective, owner_id="owner").status == "resolved"
               for item in result["resolved_directive_ids"])


def test_feedback_sqlite_checkpoint_restart_resumes_attribution(tmp_path):
    data = seed_v07(tmp_path)
    plan, _, activities, _ = build_v07_plan(data)
    feedback = SQLiteFeedbackRepository(tmp_path / "domain.sqlite3")
    checkpoint = tmp_path / "feedback-checkpoints.sqlite3"
    with open_sqlite_checkpointer(checkpoint) as saver:
        runtime = FeedbackGraphRuntime(
            blob_store=LocalBlobStore(tmp_path / "blobs"), evidence_repository=data["profiles"],
            profile_repository=data["profiles"], matching_repository=data["matching"],
            preparation_repository=data["preparation"], feedback_repository=feedback, checkpointer=saver,
        )
        interrupted = _invoke(runtime, FeedbackInput(
            feedback_type="mock_interview", source_kind="evaluator_report", occurred_at=datetime.now(UTC),
            text="The answer omitted evaluation data.", capability_id="ai.rag",
        ), plan, activities[0], thread="feedback-restart", scope="candidate_evidence")
        request = _request(interrupted)
    with open_sqlite_checkpointer(checkpoint) as saver:
        restarted = FeedbackGraphRuntime(
            blob_store=LocalBlobStore(tmp_path / "blobs"), evidence_repository=data["profiles"],
            profile_repository=data["profiles"], matching_repository=data["matching"],
            preparation_repository=data["preparation"], feedback_repository=feedback, checkpointer=saver,
        )
        result = restarted.resume(thread_id="feedback-restart", response=_confirm(
            request, response_id="restart-attribution-response"
        ))
        assert result["status"] == "awaiting_rebuild"


def test_feedback_budget_exhaustion_stops_without_archiving_fake_event(tmp_path):
    runtime, _, feedback, plan, activity = _runtime(tmp_path)
    state = create_feedback_state(
        thread_id="feedback-budget", user_id="owner",
        feedback_input=FeedbackInput(feedback_type="other", source_kind="self_reported",
                                     occurred_at=datetime.now(UTC), text="ignored"),
        allowed_path_roots=[], plan_id=plan.learning_plan_id, activity_id=activity.activity_id,
        budgets={"max_feedback_items": 1, "max_feedback_interrupts": 1, "max_llm_calls": 0},
    )
    state["counters"] = {"feedback_items": 1, "feedback_interrupts": 0, "llm_calls": 0}
    result = runtime.invoke(state)
    assert result["status"] == "completed_with_unknowns"
    assert result.get("feedback_event_id") is None
    assert feedback.count("feedback_event", owner_id="owner") == 0


def test_awaiting_rebuild_accepts_valid_candidate_successor_and_is_idempotent(tmp_path):
    runtime, data, feedback, plan, activity = _runtime(tmp_path)
    interrupted = _invoke(runtime, FeedbackInput(
        feedback_type="mock_interview", source_kind="evaluator_report", occurred_at=datetime.now(UTC),
        text="Explicit evidence gap.", capability_id="ai.rag",
    ), plan, activity, scope="candidate_evidence")
    request = _request(interrupted)
    awaiting = runtime.resume(thread_id="feedback-thread", response=_confirm(request, response_id="resolve-confirm"))
    candidate_directive = next(
        feedback.get(item, FeedbackDirective, owner_id="owner") for item in awaiting["directive_ids"]
        if feedback.get(item, FeedbackDirective, owner_id="owner").directive_type == "candidate_profile_rebuild_required"
    )
    old = data["profiles"].get_profile("candidate-s1")
    data["profiles"].save_profile(ProfileSnapshot(
        snapshot_id="candidate-other-subject", subject_id="candidate:other", profile_type="candidate", version=2,
        profile_data={**old.profile_data, "owner_id": "owner"}, supporting_claim_ids=old.supporting_claim_ids,
    ))
    with pytest.raises(FeedbackServiceError, match="snapshot successor"):
        runtime.resolve(thread_id="feedback-thread", resolutions=[{
            "directive_id": candidate_directive.directive_id, "response_id": "wrong-subject-resolution",
            "resolved_refs": ["candidate-other-subject"], "old_snapshot_ref": "candidate-s1",
        }])
    data["profiles"].save_profile(old.model_copy(update={
        "snapshot_id": "candidate-wrong-owner", "version": 2,
        "profile_data": {**old.profile_data, "owner_id": "other"},
    }))
    with pytest.raises(FeedbackServiceError, match="snapshot owner"):
        runtime.resolve(thread_id="feedback-thread", resolutions=[{
            "directive_id": candidate_directive.directive_id, "response_id": "wrong-owner-resolution",
            "resolved_refs": ["candidate-wrong-owner"], "old_snapshot_ref": "candidate-s1",
        }])
    data["profiles"].save_profile(old.model_copy(update={"snapshot_id": "candidate-s3", "version": 3}))
    resolution = {
        "directive_id": candidate_directive.directive_id, "response_id": "candidate-resolution",
        "resolved_refs": ["candidate-s3"], "old_snapshot_ref": "candidate-s1",
    }
    first = runtime.resolve(thread_id="feedback-thread", resolutions=[resolution])
    second = runtime.resolve(thread_id="feedback-thread", resolutions=[resolution])
    assert first["status"] == second["status"] == "awaiting_rebuild"
    assert first["resolved_snapshot_refs"][candidate_directive.directive_id] == ["candidate-s3"]
    assert first["report"]["version_chain_refs"][candidate_directive.directive_id] == ["candidate-s3"]
