from langgraph.checkpoint.memory import InMemorySaver

from campus_job_agent.schemas import LearningPlan, MinimumPreparationPackage, PlanReviewResponse
import pytest

from campus_job_agent.workflows.preparation_plan import (
    PreparationPlanGraphRuntime, PreparationPlanWorkflowError, create_preparation_plan_state,
    open_sqlite_checkpointer,
)
from tests.helpers_v07 import seed_v07


def _runtime(tmp_path, **kwargs):
    data = seed_v07(tmp_path, **kwargs)
    runtime = PreparationPlanGraphRuntime(
        profile_repository=data["profiles"], matching_repository=data["matching"],
        preparation_repository=data["preparation"], checkpointer=InMemorySaver(),
    )
    return runtime, data


def _invoke(runtime, data, thread="prep-thread"):
    return runtime.invoke(create_preparation_plan_state(
        thread_id=thread, user_id="owner", target_decision_ids=[item.decision_id for item in data["decisions"]],
        candidate_profile_snapshot_id="candidate-s1", career_intent_snapshot_id="intent-s1",
        comparison_set_id="comparison-1", gap_assessment_ids=[item.assessment_id for item in data["gaps"]],
        job_instance_profile_snapshot_ids=data["job_ids"], constraints_id=data["constraints"].constraints_id,
    ))


def _request(result):
    return result["__interrupt__"][0].value


def test_selected_target_builds_plan_and_accepts_review(tmp_path):
    runtime, data = _runtime(tmp_path)
    interrupted = _invoke(runtime, data)
    request = _request(interrupted)
    assert request["interaction_type"] == "review_preparation_plan"
    plan = data["preparation"].get(interrupted["learning_plan_id"], LearningPlan, owner_id="owner")
    assert plan.schedule
    assert interrupted["report"]["schedule"]
    assert interrupted["report"]["priority_factors"]
    assert interrupted["report"]["activity_refs"]
    assert "priority_is_not_success_probability" in interrupted["report"]["warnings"]
    completed = runtime.resume(thread_id="prep-thread", response=PlanReviewResponse(
        response_id="accept", request_id=request["request_id"], thread_id="prep-thread",
        user_id="owner", action="accept_plan",
    ))
    assert completed["status"] == "completed"
    assert data["preparation"].get(plan.learning_plan_id, LearningPlan, owner_id="owner").status == "accepted"


def test_constraint_revision_creates_new_plan_and_supersedes_old(tmp_path):
    runtime, data = _runtime(tmp_path)
    interrupted = _invoke(runtime, data)
    old_plan_id = interrupted["learning_plan_id"]
    request = _request(interrupted)
    revised = runtime.resume(thread_id="prep-thread", response=PlanReviewResponse(
        response_id="revise", request_id=request["request_id"], thread_id="prep-thread", user_id="owner",
        action="revise_constraints", constraints_patch={"weekly_hours": 5},
    ))
    assert "__interrupt__" in revised
    assert revised["learning_plan_id"] != old_plan_id
    assert data["preparation"].get(old_plan_id, LearningPlan, owner_id="owner").status == "superseded"


def test_unaddressable_blocker_is_visible_and_not_fake_learning(tmp_path):
    runtime, data = _runtime(tmp_path, unaddressable=True)
    result = _invoke(runtime, data)
    package = data["preparation"].get(result["package_id"], MinimumPreparationPackage, owner_id="owner")
    assert package.status == "blocked"
    assert package.unaddressable_objective_ids
    assert "unaddressable_blocker_requires_target_review" in package.warnings


def test_no_selected_target_reroutes_without_generic_plan(tmp_path):
    runtime, data = _runtime(tmp_path)
    state = create_preparation_plan_state(
        thread_id="none", user_id="owner", target_decision_ids=[], candidate_profile_snapshot_id="candidate-s1",
        career_intent_snapshot_id="intent-s1", comparison_set_id="comparison-1",
        gap_assessment_ids=[item.assessment_id for item in data["gaps"]],
        job_instance_profile_snapshot_ids=data["job_ids"], constraints_id=data["constraints"].constraints_id,
    )
    result = runtime.invoke(state)
    assert result["status"] == "reroute_required"
    assert result["next_action"] == "target_selection_required"
    assert result.get("learning_plan_id") is None


def test_sqlite_checkpoint_restart_and_duplicate_response_are_idempotent(tmp_path):
    data = seed_v07(tmp_path)
    checkpoint = tmp_path / "preparation-checkpoints.sqlite3"
    with open_sqlite_checkpointer(checkpoint) as saver:
        runtime = PreparationPlanGraphRuntime(
            profile_repository=data["profiles"], matching_repository=data["matching"],
            preparation_repository=data["preparation"], checkpointer=saver,
        )
        interrupted = _invoke(runtime, data, thread="prep-restart")
        request = _request(interrupted)
    response = PlanReviewResponse(
        response_id="restart-response", request_id=request["request_id"], thread_id="prep-restart",
        user_id="owner", action="accept_plan",
    )
    with open_sqlite_checkpointer(checkpoint) as saver:
        restarted = PreparationPlanGraphRuntime(
            profile_repository=data["profiles"], matching_repository=data["matching"],
            preparation_repository=data["preparation"], checkpointer=saver,
        )
        completed = restarted.resume(thread_id="prep-restart", response=response)
        replay = restarted.resume(thread_id="prep-restart", response=response)
        assert completed["status"] == replay["status"] == "completed"
        with pytest.raises(PreparationPlanWorkflowError, match="idempotency_conflict"):
            restarted.resume(thread_id="prep-restart", response=response.model_copy(update={"action": "cancel"}))


def test_plan_round_budget_terminates_revision_loop(tmp_path):
    runtime, data = _runtime(tmp_path)
    state = create_preparation_plan_state(
        thread_id="budget", user_id="owner", target_decision_ids=[item.decision_id for item in data["decisions"]],
        candidate_profile_snapshot_id="candidate-s1", career_intent_snapshot_id="intent-s1",
        comparison_set_id="comparison-1", gap_assessment_ids=[item.assessment_id for item in data["gaps"]],
        job_instance_profile_snapshot_ids=data["job_ids"], constraints_id=data["constraints"].constraints_id,
        budgets={"max_plan_rounds": 1, "max_activities": 1, "max_llm_calls": 0, "max_plan_interrupts": 2},
    )
    interrupted = runtime.invoke(state)
    package = data["preparation"].get(interrupted["package_id"], MinimumPreparationPackage, owner_id="owner")
    assert package.status == "partial"
    assert "max_activity_budget_reached" in package.deferred_reasons.values()
    assert len({item.activity_id for item in data["preparation"].get(
        interrupted["learning_plan_id"], LearningPlan, owner_id="owner"
    ).schedule}) <= 1
    request = _request(interrupted)
    result = runtime.resume(thread_id="budget", response=PlanReviewResponse(
        response_id="budget-revise", request_id=request["request_id"], thread_id="budget", user_id="owner",
        action="revise_constraints", constraints_patch={"weekly_hours": 5},
    ))
    assert result["status"] == "partial"
    assert "__interrupt__" not in result
