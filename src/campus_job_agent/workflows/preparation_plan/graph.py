"""Recoverable v0.7 PreparationPlanGraph with deterministic review loops."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any
from uuid import uuid4

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from campus_job_agent.schemas import (
    LearningPlan, MinimumPreparationPackage, PlanReviewRequest, PlanReviewResponse,
    PreparationBudget, PreparationConstraints, PreparationCounter, PreparationInputSet,
    PreparationPlanGraphState,
)
from campus_job_agent.schemas.matching import canonical_hash
from campus_job_agent.storage.base import ProfileRepository
from campus_job_agent.workflows.candidate_profile.graph import open_sqlite_checkpointer
from campus_job_agent.workflows.preparation_plan.report import build_plan_report
from campus_job_agent.workflows.preparation_plan.repository import SQLitePreparationRepository
from campus_job_agent.workflows.preparation_plan.service import PreparationService, PreparationServiceError
from campus_job_agent.workflows.profile_matching.repository import SQLiteMatchingRepository


class PreparationPlanWorkflowError(RuntimeError):
    pass


class PreparationPlanGraphRuntime:
    def __init__(self, *, profile_repository: ProfileRepository,
                 matching_repository: SQLiteMatchingRepository,
                 preparation_repository: SQLitePreparationRepository, checkpointer: Any) -> None:
        self.repository = preparation_repository
        self.app = build_preparation_plan_graph(
            profile_repository=profile_repository, matching_repository=matching_repository,
            preparation_repository=preparation_repository, checkpointer=checkpointer,
        )

    def invoke(self, state: PreparationPlanGraphState) -> dict[str, Any]:
        thread_id = str(state.get("thread_id", "")).strip()
        if not thread_id:
            raise ValueError("thread_id is required")
        try:
            return self.app.invoke(state, {"configurable": {"thread_id": thread_id}})
        except sqlite3.Error as exc:
            raise PreparationPlanWorkflowError(f"checkpoint_error: {exc}") from exc

    def resume(self, *, thread_id: str, response: PlanReviewResponse | dict[str, Any]) -> dict[str, Any]:
        payload = response.model_dump(mode="json") if isinstance(response, PlanReviewResponse) else response
        validated = PlanReviewResponse.model_validate(payload)
        if validated.thread_id != thread_id:
            raise ValueError("resume thread_id does not match response thread_id")
        current = self.app.get_state({"configurable": {"thread_id": thread_id}})
        values = dict(current.values or {})
        if not values.get("pending_interaction"):
            receipt = self.repository.get_response_result(validated.response_id)
            if receipt is None:
                raise PreparationPlanWorkflowError("no pending plan review exists")
            if receipt["payload_hash"] != _response_hash(validated):
                raise PreparationPlanWorkflowError("idempotency_conflict")
            return values
        return self.app.invoke(Command(resume=payload), {"configurable": {"thread_id": thread_id}})

    def get_state(self, thread_id: str) -> Any:
        return self.app.get_state({"configurable": {"thread_id": thread_id}})


def create_preparation_plan_state(*, thread_id: str, user_id: str,
                                  target_decision_ids: list[str], candidate_profile_snapshot_id: str,
                                  career_intent_snapshot_id: str, comparison_set_id: str,
                                  gap_assessment_ids: list[str], job_instance_profile_snapshot_ids: list[str],
                                  constraints_id: str, role_family_profile_snapshot_ids: list[str] | None = None,
                                  previous_plan_id: str | None = None, run_id: str | None = None,
                                  budgets: PreparationBudget | dict[str, Any] | None = None) -> PreparationPlanGraphState:
    budget = budgets if isinstance(budgets, PreparationBudget) else PreparationBudget.model_validate(budgets or {})
    return {
        "run_id": run_id or str(uuid4()), "thread_id": thread_id, "user_id": user_id,
        "status": "initialized", "target_decision_ids": list(target_decision_ids),
        "candidate_profile_snapshot_id": candidate_profile_snapshot_id,
        "career_intent_snapshot_id": career_intent_snapshot_id, "comparison_set_id": comparison_set_id,
        "gap_assessment_ids": list(gap_assessment_ids),
        "job_instance_profile_snapshot_ids": list(job_instance_profile_snapshot_ids),
        "role_family_profile_snapshot_ids": list(role_family_profile_snapshot_ids or []),
        "constraints_id": constraints_id, "previous_plan_id": previous_plan_id,
        "excluded_activity_ids": [], "activity_revisions": {}, "input_set_id": "",
        "objective_ids": [], "activity_ids": [], "priority_factor_ids": [], "package_id": None,
        "learning_plan_id": None, "pending_interaction": None, "resume_input": None,
        "processed_response_ids": [], "next_action": None, "budgets": budget.model_dump(),
        "counters": PreparationCounter().model_dump(), "tool_results": [], "llm_calls": [],
        "trace": [], "errors": [], "report": None,
    }


def build_preparation_plan_graph(*, profile_repository: ProfileRepository,
                                 matching_repository: SQLiteMatchingRepository,
                                 preparation_repository: SQLitePreparationRepository,
                                 checkpointer: Any):
    nodes = _PreparationNodes(profile_repository, matching_repository, preparation_repository)
    graph = StateGraph(PreparationPlanGraphState)
    for name in (
        "initialize_preparation_run", "load_and_validate_selected_targets", "derive_preparation_objectives",
        "generate_activity_candidates", "validate_activity_candidates", "compute_priority_factors",
        "build_minimum_preparation_package", "schedule_activities", "project_learning_plan",
        "route_plan_next_action", "plan_review_interaction", "interrupt_for_plan_review",
        "validate_plan_review", "apply_plan_review", "finalize_plan", "finalize_reroute",
    ):
        graph.add_node(name, getattr(nodes, name))
    graph.add_edge(START, "initialize_preparation_run")
    graph.add_edge("initialize_preparation_run", "load_and_validate_selected_targets")
    graph.add_conditional_edges("load_and_validate_selected_targets", lambda state: state["next_action"], {
        "plan": "derive_preparation_objectives", "target_selection_required": "finalize_reroute",
        "rematch_required": "finalize_reroute", "partial": "finalize_plan", "fail": "finalize_plan",
    })
    chain = ["derive_preparation_objectives", "generate_activity_candidates", "validate_activity_candidates",
             "compute_priority_factors", "build_minimum_preparation_package", "schedule_activities",
             "project_learning_plan", "route_plan_next_action"]
    for left, right in zip(chain, chain[1:]):
        graph.add_edge(left, right)
    graph.add_conditional_edges("route_plan_next_action", lambda state: state["next_action"], {
        "review_user": "plan_review_interaction", "complete": "finalize_plan",
        "partial": "finalize_plan", "blocked": "finalize_plan", "fail": "finalize_plan",
    })
    graph.add_edge("plan_review_interaction", "interrupt_for_plan_review")
    graph.add_edge("interrupt_for_plan_review", "validate_plan_review")
    graph.add_edge("validate_plan_review", "apply_plan_review")
    graph.add_conditional_edges("apply_plan_review", lambda state: state["next_action"], {
        "revise_constraints": "initialize_preparation_run", "revise_activities": "initialize_preparation_run",
        "complete": "finalize_plan", "defer": "finalize_plan", "cancel": "finalize_plan",
    })
    graph.add_edge("finalize_plan", END)
    graph.add_edge("finalize_reroute", END)
    return graph.compile(checkpointer=checkpointer)


class _PreparationNodes:
    def __init__(self, profiles: ProfileRepository, matching: SQLiteMatchingRepository,
                 repository: SQLitePreparationRepository) -> None:
        self.repository = repository
        self.service = PreparationService(profile_repository=profiles, matching_repository=matching,
                                          preparation_repository=repository)
        self._loaded: dict[str, tuple[Any, ...]] = {}

    def initialize_preparation_run(self, state: PreparationPlanGraphState, config: RunnableConfig) -> dict[str, Any]:
        if config.get("configurable", {}).get("thread_id") != state.get("thread_id"):
            raise PreparationPlanWorkflowError("configurable.thread_id must equal state.thread_id")
        counters = PreparationCounter.model_validate(state.get("counters", {}))
        budgets = PreparationBudget.model_validate(state.get("budgets", {}))
        if counters.plan_rounds >= budgets.max_plan_rounds:
            return {"status": "partial", "next_action": "partial", "trace": [_trace("initialize_preparation_run", "budget_exhausted")]}
        counters = counters.model_copy(update={"plan_rounds": counters.plan_rounds + 1})
        return {"status": "running", "next_action": None, "counters": counters.model_dump(),
                "pending_interaction": None, "resume_input": None,
                "trace": [_trace("initialize_preparation_run", "running", round=counters.plan_rounds)]}

    def load_and_validate_selected_targets(self, state: PreparationPlanGraphState) -> dict[str, Any]:
        if state.get("next_action") == "partial":
            return {"next_action": "partial"}
        try:
            loaded = self.service.load_and_validate_input(
                user_id=state["user_id"], target_decision_ids=state.get("target_decision_ids", []),
                candidate_snapshot_id=state["candidate_profile_snapshot_id"],
                intent_snapshot_id=state["career_intent_snapshot_id"], comparison_set_id=state["comparison_set_id"],
                gap_assessment_ids=state["gap_assessment_ids"], job_snapshot_ids=state["job_instance_profile_snapshot_ids"],
                family_snapshot_ids=state.get("role_family_profile_snapshot_ids", []), constraints_id=state["constraints_id"],
            )
        except PreparationServiceError as exc:
            reason = str(exc)
            route = "target_selection_required" if "target_selection_required" in reason else "rematch_required" if "stale" in reason else "fail"
            return {"next_action": route, "errors": [{"node": "load_and_validate_selected_targets", "error_type": reason.split(":")[0], "message": reason, "fatal": route == "fail"}],
                    "trace": [_trace("load_and_validate_selected_targets", route)]}
        input_set = loaded[0]
        self._loaded[state["thread_id"]] = loaded
        return {"input_set_id": input_set.input_set_id, "next_action": "plan",
                "trace": [_trace("load_and_validate_selected_targets", "valid", input_set_id=input_set.input_set_id)]}

    def derive_preparation_objectives(self, state: PreparationPlanGraphState) -> dict[str, Any]:
        return {"trace": [_trace("derive_preparation_objectives", "deterministic")]}

    def generate_activity_candidates(self, state: PreparationPlanGraphState) -> dict[str, Any]:
        return {"trace": [_trace("generate_activity_candidates", "deterministic_fallback")]}

    def validate_activity_candidates(self, state: PreparationPlanGraphState) -> dict[str, Any]:
        return {"trace": [_trace("validate_activity_candidates", "valid")]}

    def compute_priority_factors(self, state: PreparationPlanGraphState) -> dict[str, Any]:
        return {"trace": [_trace("compute_priority_factors", "deterministic")]}

    def build_minimum_preparation_package(self, state: PreparationPlanGraphState) -> dict[str, Any]:
        return {"trace": [_trace("build_minimum_preparation_package", "deterministic")]}

    def schedule_activities(self, state: PreparationPlanGraphState) -> dict[str, Any]:
        return {"trace": [_trace("schedule_activities", "deterministic")]}

    def project_learning_plan(self, state: PreparationPlanGraphState) -> dict[str, Any]:
        loaded = self._loaded.get(state["thread_id"])
        if loaded is None:
            loaded = self.service.load_and_validate_input(
                user_id=state["user_id"], target_decision_ids=state["target_decision_ids"],
                candidate_snapshot_id=state["candidate_profile_snapshot_id"], intent_snapshot_id=state["career_intent_snapshot_id"],
                comparison_set_id=state["comparison_set_id"], gap_assessment_ids=state["gap_assessment_ids"],
                job_snapshot_ids=state["job_instance_profile_snapshot_ids"], family_snapshot_ids=state.get("role_family_profile_snapshot_ids", []),
                constraints_id=state["constraints_id"],
            )
        input_set, assessments, roles, constraints = loaded
        previous = state.get("learning_plan_id") or state.get("previous_plan_id")
        plan, objectives, activities, factors = self.service.build_plan(
            input_set=input_set, assessments=assessments, roles=roles, constraints=constraints,
            excluded_activity_ids=set(state.get("excluded_activity_ids", [])), previous_plan_id=previous,
            change_reason_codes=["plan_review_revision"] if previous else [],
            activity_revisions=state.get("activity_revisions", {}),
            max_activities=PreparationBudget.model_validate(state["budgets"]).max_activities,
        )
        package = self.repository.get(plan.package_id, MinimumPreparationPackage, owner_id=state["user_id"])
        return {"objective_ids": [item.objective_id for item in objectives],
                "activity_ids": [item.activity_id for item in activities],
                "priority_factor_ids": [item.priority_factor_id for item in factors], "package_id": plan.package_id,
                "learning_plan_id": plan.learning_plan_id,
                "report": build_plan_report(plan, package, factors, activities),
                "trace": [_trace("project_learning_plan", plan.status, plan_id=plan.learning_plan_id,
                                  schedule_hash=plan.schedule_hash)]}

    def route_plan_next_action(self, state: PreparationPlanGraphState) -> dict[str, Any]:
        counters = PreparationCounter.model_validate(state["counters"])
        budgets = PreparationBudget.model_validate(state["budgets"])
        route = "review_user" if counters.plan_interrupts < budgets.max_plan_interrupts else "partial"
        return {"next_action": route, "trace": [_trace("route_plan_next_action", route)]}

    def plan_review_interaction(self, state: PreparationPlanGraphState) -> dict[str, Any]:
        plan = self.repository.get(state["learning_plan_id"], LearningPlan, owner_id=state["user_id"])
        request_id = _stable_id("hir-plan", [state["thread_id"], plan.learning_plan_id])
        request = PlanReviewRequest(
            request_id=request_id, thread_id=state["thread_id"], run_id=state["run_id"], user_id=state["user_id"],
            reason="请确认准备计划的时间约束和活动安排", input_set_id=state["input_set_id"],
            learning_plan_id=plan.learning_plan_id, package_id=plan.package_id, constraints_id=plan.constraints_id,
            allowed_activity_ids=plan.activity_ids,
            allowed_actions=["accept_plan", "revise_constraints", "exclude_activities", "request_activity_revision", "defer_plan", "cancel"],
        )
        counters = PreparationCounter.model_validate(state["counters"])
        counters = counters.model_copy(update={"plan_interrupts": counters.plan_interrupts + 1})
        return {"pending_interaction": request.model_dump(mode="json"), "status": "interrupted",
                "counters": counters.model_dump(), "trace": [_trace("plan_review_interaction", "interrupted", request_id=request_id)]}

    def interrupt_for_plan_review(self, state: PreparationPlanGraphState) -> dict[str, Any]:
        return {"resume_input": interrupt(state["pending_interaction"]), "status": "running"}

    def validate_plan_review(self, state: PreparationPlanGraphState) -> dict[str, Any]:
        request = PlanReviewRequest.model_validate(state["pending_interaction"])
        response = PlanReviewResponse.model_validate(state["resume_input"])
        for key in ("request_id", "thread_id", "user_id"):
            if getattr(request, key) != getattr(response, key):
                raise PreparationPlanWorkflowError(f"plan review {key} mismatch")
        if response.action not in request.allowed_actions:
            raise PreparationPlanWorkflowError("plan review action is not allowed")
        if not set(response.activity_ids).issubset(set(request.allowed_activity_ids)):
            raise PreparationPlanWorkflowError("invalid_activity_reference")
        revision_ids = {str(item.get("activity_id")) for item in response.activity_revision_requests}
        if not revision_ids.issubset(set(request.allowed_activity_ids)):
            raise PreparationPlanWorkflowError("invalid_activity_reference")
        return {"processed_response_ids": [response.response_id], "trace": [_trace("validate_plan_review", response.action)]}

    def apply_plan_review(self, state: PreparationPlanGraphState) -> dict[str, Any]:
        response = PlanReviewResponse.model_validate(state["resume_input"])
        plan = self.repository.get(state["learning_plan_id"], LearningPlan, owner_id=state["user_id"])
        update: dict[str, Any] = {"pending_interaction": None, "resume_input": None}
        if response.action == "accept_plan":
            self.repository.replace_lifecycle(plan.learning_plan_id, LearningPlan, "accepted")
            route = "complete"
        elif response.action == "revise_constraints":
            allowed = {"timezone", "horizon_start", "horizon_end", "weekly_hours", "daily_max_hours",
                       "unavailable_dates", "preferred_activity_types", "excluded_activity_types", "session_minutes"}
            if not set(response.constraints_patch).issubset(allowed):
                raise PreparationPlanWorkflowError("constraints patch contains unsupported fields")
            old = self.repository.get(state["constraints_id"], PreparationConstraints, owner_id=state["user_id"])
            new = PreparationConstraints.model_validate({**old.model_dump(mode="json"), **response.constraints_patch,
                                                         "constraints_id": "", "created_from_response_id": response.response_id})
            new = self.service.save_constraints(new, owner_id=state["user_id"])
            update["constraints_id"] = new.constraints_id
            route = "revise_constraints"
        elif response.action == "exclude_activities":
            update["excluded_activity_ids"] = response.activity_ids
            route = "revise_activities"
        elif response.action == "request_activity_revision":
            revisions = dict(state.get("activity_revisions", {}))
            for item in response.activity_revision_requests:
                activity_id = str(item["activity_id"])
                revisions[activity_id] = {key: value for key, value in item.items() if key != "activity_id"}
            update["activity_revisions"] = revisions
            route = "revise_activities"
        elif response.action == "defer_plan":
            self.repository.replace_lifecycle(plan.learning_plan_id, LearningPlan, "deferred")
            route = "defer"
        else:
            self.repository.replace_lifecycle(plan.learning_plan_id, LearningPlan, "cancelled")
            route = "cancel"
        self.repository.save_response_result(response.response_id, _response_hash(response),
                                             {"record_ids": [state["learning_plan_id"]], "route": route})
        update["next_action"] = route
        update["trace"] = [_trace("apply_plan_review", route)]
        return update

    def finalize_plan(self, state: PreparationPlanGraphState) -> dict[str, Any]:
        route = state.get("next_action")
        status = {"complete": "completed", "partial": "partial", "blocked": "blocked",
                  "defer": "deferred", "cancel": "cancelled", "fail": "failed"}.get(route, "completed")
        return {"status": status, "trace": [_trace("finalize_plan", status)]}

    def finalize_reroute(self, state: PreparationPlanGraphState) -> dict[str, Any]:
        return {"status": "reroute_required", "trace": [_trace("finalize_reroute", state.get("next_action"))],
                "report": {"status": "reroute_required", "next_action": state.get("next_action"),
                           "warnings": ["no_generic_plan_without_selected_target"]}}


def _trace(node: str, outcome: str, **details: Any) -> dict[str, Any]:
    return {"node": node, "outcome": outcome, **details}


def _stable_id(prefix: str, payload: Any) -> str:
    return f"{prefix}:{canonical_hash(prefix, payload)[7:31]}"


def _response_hash(response: PlanReviewResponse) -> str:
    return canonical_hash("plan-review-response", response.model_dump(mode="json", exclude={"submitted_at"}))


__all__ = ["PreparationPlanGraphRuntime", "PreparationPlanWorkflowError", "build_preparation_plan_graph",
           "create_preparation_plan_state", "open_sqlite_checkpointer"]
