"""Recoverable v0.6 profile-matching LangGraph with comparison review."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any
from uuid import uuid4

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from campus_job_agent.schemas import (
    CareerIntent,
    ComparisonReviewRequest,
    ComparisonReviewResponse,
    ComparisonSet,
    GapAssessment,
    IntentConstraint,
    MatchingBudget,
    MatchingCounter,
    MatchingInputSet,
    ProfileMatchingGraphState,
    ProfileSnapshot,
    TargetDecision,
)
from campus_job_agent.schemas.matching import canonical_hash
from campus_job_agent.storage.base import EvidenceRepository, ProfileRepository
from campus_job_agent.workflows.candidate_profile.graph import open_sqlite_checkpointer
from campus_job_agent.workflows.profile_matching.explanation import ExplanationProvider, explain_with_fallback
from campus_job_agent.workflows.profile_matching.policy import MatchingRoutePolicy
from campus_job_agent.workflows.profile_matching.repository import SQLiteMatchingRepository
from campus_job_agent.workflows.profile_matching.service import (
    MatchingService,
    MatchingServiceError,
    assess_intent_impact,
    build_directive,
    project_search_scope,
    role_refresh_reason,
)


class ProfileMatchingWorkflowError(RuntimeError):
    pass


class ProfileMatchingGraphRuntime:
    def __init__(
        self,
        *,
        evidence_repository: EvidenceRepository,
        profile_repository: ProfileRepository,
        matching_repository: SQLiteMatchingRepository,
        checkpointer: Any,
        explanation_provider: ExplanationProvider | None = None,
    ) -> None:
        self.repository = matching_repository
        self.app = build_profile_matching_graph(
            evidence_repository=evidence_repository,
            profile_repository=profile_repository,
            matching_repository=matching_repository,
            checkpointer=checkpointer,
            explanation_provider=explanation_provider,
        )

    def invoke(self, state: ProfileMatchingGraphState) -> dict[str, Any]:
        thread_id = str(state.get("thread_id", "")).strip()
        if not thread_id:
            raise ValueError("thread_id is required")
        try:
            return self.app.invoke(state, {"configurable": {"thread_id": thread_id}})
        except sqlite3.Error as exc:
            raise ProfileMatchingWorkflowError(f"checkpoint_error: {exc}") from exc

    def resume(self, *, thread_id: str, response: ComparisonReviewResponse | dict[str, Any]) -> dict[str, Any]:
        payload = response.model_dump(mode="json") if isinstance(response, ComparisonReviewResponse) else response
        if str(payload.get("thread_id", "")) != thread_id:
            raise ValueError("resume thread_id does not match response thread_id")
        validated = ComparisonReviewResponse.model_validate(payload)
        current = self.app.get_state({"configurable": {"thread_id": thread_id}})
        values = dict(current.values or {})
        if not values.get("pending_interaction"):
            receipt = self.repository.get_response_result(validated.response_id)
            if receipt is None:
                raise ProfileMatchingWorkflowError("no pending comparison review exists")
            if receipt["payload_hash"] != _response_hash(validated):
                raise ProfileMatchingWorkflowError("idempotency_conflict")
            return values
        try:
            return self.app.invoke(Command(resume=payload), {"configurable": {"thread_id": thread_id}})
        except sqlite3.Error as exc:
            raise ProfileMatchingWorkflowError(f"checkpoint_error: {exc}") from exc

    def get_state(self, thread_id: str) -> Any:
        return self.app.get_state({"configurable": {"thread_id": thread_id}})


def create_profile_matching_state(
    *,
    thread_id: str,
    user_id: str,
    candidate_profile_snapshot_id: str,
    career_intent_snapshot_id: str,
    job_instance_profile_snapshot_ids: list[str],
    role_family_profile_snapshot_ids: list[str] | None = None,
    run_id: str | None = None,
    budgets: MatchingBudget | dict[str, Any] | None = None,
    output_dir: str | None = None,
) -> ProfileMatchingGraphState:
    budget = budgets if isinstance(budgets, MatchingBudget) else MatchingBudget.model_validate(budgets or {})
    if len(job_instance_profile_snapshot_ids) > budget.max_targets:
        raise ValueError("max_targets exceeded")
    return {
        "run_id": run_id or str(uuid4()), "thread_id": thread_id, "user_id": user_id,
        "status": "initialized", "match_round": 0, "match_budget_exhausted": False, "output_dir": output_dir,
        "input_set_id": "", "candidate_profile_snapshot_id": candidate_profile_snapshot_id,
        "career_intent_snapshot_id": career_intent_snapshot_id,
        "job_instance_profile_snapshot_ids": list(job_instance_profile_snapshot_ids),
        "role_family_profile_snapshot_ids": list(role_family_profile_snapshot_ids or []),
        "qualification_assessment_ids": [], "requirement_assessment_ids": [],
        "preference_assessment_ids": [], "gap_assessment_ids": [],
        "comparison_set_id": None, "explanation_ids": [], "pending_interaction": None,
        "resume_input": None, "processed_response_ids": [], "target_decision_ids": [],
        "intent_impact_assessment": None, "rebuild_directive_id": None,
        "role_refresh_target_ids": [], "next_action": None, "budgets": budget.model_dump(),
        "counters": MatchingCounter().model_dump(), "tool_results": [], "llm_calls": [],
        "trace": [], "errors": [], "report": None,
    }


def build_profile_matching_graph(
    *,
    evidence_repository: EvidenceRepository,
    profile_repository: ProfileRepository,
    matching_repository: SQLiteMatchingRepository,
    checkpointer: Any,
    explanation_provider: ExplanationProvider | None = None,
):
    nodes = _MatchingNodes(
        evidence_repository=evidence_repository,
        profile_repository=profile_repository,
        matching_repository=matching_repository,
        explanation_provider=explanation_provider,
    )
    graph = StateGraph(ProfileMatchingGraphState)
    for name in [
        "initialize_matching_run", "load_and_validate_snapshots",
        "evaluate_hard_qualifications", "align_capability_requirements",
        "compute_deterministic_coverage", "evaluate_preferences_and_uncertainty",
        "build_gap_assessments", "build_comparison_set", "explain_comparison",
        "route_matching_next_action", "plan_decision_interaction", "interrupt_for_decision",
        "validate_and_archive_decision", "route_user_decision", "persist_target_decisions",
        "handle_candidate_revision", "handle_intent_revision", "handle_role_refresh",
        "finalize_matching", "finalize_reroute",
    ]:
        graph.add_node(name, getattr(nodes, name))
    chain = [
        "initialize_matching_run", "load_and_validate_snapshots", "evaluate_hard_qualifications",
        "align_capability_requirements", "compute_deterministic_coverage",
        "evaluate_preferences_and_uncertainty", "build_gap_assessments",
        "build_comparison_set", "explain_comparison", "route_matching_next_action",
    ]
    graph.add_edge(START, chain[0])
    for left, right in zip(chain, chain[1:]):
        graph.add_edge(left, right)
    graph.add_conditional_edges("route_matching_next_action", lambda state: state["next_action"], {
        "review_user": "plan_decision_interaction",
        "role_refresh_required": "finalize_reroute",
        "complete": "finalize_matching", "complete_with_unknowns": "finalize_matching",
        "fail": "finalize_matching",
    })
    graph.add_edge("plan_decision_interaction", "interrupt_for_decision")
    graph.add_edge("interrupt_for_decision", "validate_and_archive_decision")
    graph.add_edge("validate_and_archive_decision", "route_user_decision")
    graph.add_conditional_edges("route_user_decision", lambda state: state["next_action"], {
        "persist_decisions": "persist_target_decisions",
        "candidate_profile_required": "handle_candidate_revision",
        "rematch_required": "handle_intent_revision",
        "role_research_required": "handle_intent_revision",
        "role_refresh_required": "handle_role_refresh",
        "complete": "finalize_matching", "cancel": "finalize_matching",
    })
    graph.add_edge("persist_target_decisions", "finalize_matching")
    graph.add_edge("handle_candidate_revision", "finalize_reroute")
    graph.add_conditional_edges("handle_intent_revision", lambda state: state["next_action"], {
        "rematch_required": "initialize_matching_run",
        "role_research_required": "finalize_reroute",
    })
    graph.add_edge("handle_role_refresh", "finalize_reroute")
    graph.add_edge("finalize_matching", END)
    graph.add_edge("finalize_reroute", END)
    return graph.compile(checkpointer=checkpointer)


class _MatchingNodes:
    def __init__(self, *, evidence_repository: EvidenceRepository, profile_repository: ProfileRepository,
                 matching_repository: SQLiteMatchingRepository, explanation_provider: ExplanationProvider | None) -> None:
        self.profile_repository = profile_repository
        self.repository = matching_repository
        self.service = MatchingService(
            profile_repository=profile_repository,
            evidence_repository=evidence_repository,
            matching_repository=matching_repository,
        )
        self.explanation_provider = explanation_provider
        self.route_policy = MatchingRoutePolicy()

    def initialize_matching_run(self, state: ProfileMatchingGraphState, config: RunnableConfig) -> dict[str, Any]:
        missing = [key for key in ["run_id", "thread_id", "user_id", "candidate_profile_snapshot_id", "career_intent_snapshot_id"] if not state.get(key)]
        if missing:
            raise ProfileMatchingWorkflowError("missing required fields: " + ", ".join(missing))
        if config.get("configurable", {}).get("thread_id") != state["thread_id"]:
            raise ProfileMatchingWorkflowError("configurable.thread_id must equal state.thread_id")
        budgets = MatchingBudget.model_validate(state.get("budgets", {}))
        counters = MatchingCounter.model_validate(state.get("counters", {}))
        if len(state.get("job_instance_profile_snapshot_ids", [])) > budgets.max_targets:
            raise ProfileMatchingWorkflowError("max_targets exceeded")
        if counters.match_rounds >= budgets.max_match_rounds:
            return {"next_action": "complete_with_unknowns", "match_budget_exhausted": True, "status": "running", "trace": [_trace("initialize_matching_run", counters, budget_exhausted=True)]}
        counters = counters.model_copy(update={"match_rounds": counters.match_rounds + 1})
        return {
            "status": "running", "match_round": counters.match_rounds,
            "counters": counters.model_dump(), "input_set_id": "", "comparison_set_id": None,
            "pending_interaction": None, "resume_input": None, "role_refresh_target_ids": [],
            "next_action": None, "match_budget_exhausted": False, "trace": [_trace("initialize_matching_run", counters)],
        }

    def load_and_validate_snapshots(self, state: ProfileMatchingGraphState) -> dict[str, Any]:
        try:
            input_set, _, _, jobs, _ = self.service.load_input_set(
                user_id=state["user_id"], candidate_snapshot_id=state["candidate_profile_snapshot_id"],
                intent_snapshot_id=state["career_intent_snapshot_id"],
                job_snapshot_ids=state["job_instance_profile_snapshot_ids"],
                family_snapshot_ids=state.get("role_family_profile_snapshot_ids", []),
            )
        except MatchingServiceError as exc:
            raise ProfileMatchingWorkflowError(str(exc)) from exc
        refresh = [snapshot_id for snapshot_id, role in jobs.items() if role_refresh_reason(role)]
        return {
            "input_set_id": input_set.input_set_id, "role_refresh_target_ids": refresh,
            "trace": [_trace("load_and_validate_snapshots", MatchingCounter.model_validate(state["counters"]), input_set_id=input_set.input_set_id, refresh_count=len(refresh))],
        }

    def evaluate_hard_qualifications(self, state: ProfileMatchingGraphState) -> dict[str, Any]:
        return {"trace": [_trace("evaluate_hard_qualifications", MatchingCounter.model_validate(state["counters"]))]}

    def align_capability_requirements(self, state: ProfileMatchingGraphState) -> dict[str, Any]:
        return {"trace": [_trace("align_capability_requirements", MatchingCounter.model_validate(state["counters"]))]}

    def compute_deterministic_coverage(self, state: ProfileMatchingGraphState) -> dict[str, Any]:
        return {"trace": [_trace("compute_deterministic_coverage", MatchingCounter.model_validate(state["counters"]))]}

    def evaluate_preferences_and_uncertainty(self, state: ProfileMatchingGraphState) -> dict[str, Any]:
        return {"trace": [_trace("evaluate_preferences_and_uncertainty", MatchingCounter.model_validate(state["counters"]))]}

    def build_gap_assessments(self, state: ProfileMatchingGraphState) -> dict[str, Any]:
        input_set, candidate, intent, jobs, _ = self.service.load_input_set(
            user_id=state["user_id"], candidate_snapshot_id=state["candidate_profile_snapshot_id"],
            intent_snapshot_id=state["career_intent_snapshot_id"],
            job_snapshot_ids=state["job_instance_profile_snapshot_ids"], family_snapshot_ids=state.get("role_family_profile_snapshot_ids", []),
        )
        assessments = [
            self.service.assess_job(input_set=input_set, candidate=candidate, intent=intent, job_snapshot_id=snapshot_id, role=role)
            for snapshot_id, role in jobs.items()
        ]
        qualification_ids = [item["assessment_item_id"] for assessment in assessments for item in assessment.qualification_assessments]
        requirement_ids = [item["assessment_item_id"] for assessment in assessments for item in assessment.requirement_assessments]
        preference_ids = [item["assessment_item_id"] for assessment in assessments for item in assessment.preference_assessments]
        return {
            "gap_assessment_ids": [item.assessment_id for item in assessments],
            "qualification_assessment_ids": qualification_ids, "requirement_assessment_ids": requirement_ids,
            "preference_assessment_ids": preference_ids,
            "trace": [_trace("build_gap_assessments", MatchingCounter.model_validate(state["counters"]), count=len(assessments))],
        }

    def build_comparison_set(self, state: ProfileMatchingGraphState) -> dict[str, Any]:
        input_set = self.repository.get(state["input_set_id"], MatchingInputSet, owner_id=state["user_id"])
        assessments = [self.repository.get(item, GapAssessment, owner_id=state["user_id"]) for item in state["gap_assessment_ids"]]
        current = [item for item in assessments if item is not None and item.input_set_id == state["input_set_id"]]
        if input_set is None or not current:
            raise ProfileMatchingWorkflowError("matching assessment persistence incomplete")
        comparison = self.service.build_comparison(input_set, current)
        return {"comparison_set_id": comparison.comparison_set_id, "trace": [_trace("build_comparison_set", MatchingCounter.model_validate(state["counters"]), comparison_set_id=comparison.comparison_set_id)]}

    def explain_comparison(self, state: ProfileMatchingGraphState) -> dict[str, Any]:
        comparison = self._comparison(state)
        assessments = self._assessment_map(comparison, state["user_id"])
        counters = MatchingCounter.model_validate(state["counters"])
        budgets = MatchingBudget.model_validate(state["budgets"])
        provider = self.explanation_provider if counters.explanation_calls < budgets.max_explanation_calls else None
        explanation, calls, error = explain_with_fallback(comparison, assessments, provider)
        if provider is not None:
            counters = counters.model_copy(update={"explanation_calls": counters.explanation_calls + 1})
        explanation = self.repository.save("match_explanation", explanation, owner_id=state["user_id"])
        update: dict[str, Any] = {
            "explanation_ids": [explanation.explanation_id], "counters": counters.model_dump(),
            "llm_calls": [item.model_dump(mode="json") if hasattr(item, "model_dump") else item for item in calls],
            "trace": [_trace("explain_comparison", counters, fallback=bool(error))],
        }
        if error:
            update["errors"] = [{"node": "explain_comparison", "error_type": "llm_fact_mutation", "message": error, "fatal": False, "fallback": "deterministic"}]
        return update

    def route_matching_next_action(self, state: ProfileMatchingGraphState) -> dict[str, Any]:
        refresh = state.get("role_refresh_target_ids", [])
        action = "complete_with_unknowns" if state.get("match_budget_exhausted") else self.route_policy.decide(
            budgets=MatchingBudget.model_validate(state["budgets"]),
            counters=MatchingCounter.model_validate(state["counters"]),
            has_fatal_error=any(item.get("fatal") for item in state.get("errors", [])),
            has_refresh_directive=bool(refresh),
        )
        update: dict[str, Any] = {"next_action": action, "trace": [_trace("route_matching_next_action", MatchingCounter.model_validate(state["counters"]), route=action)]}
        if action == "role_refresh_required":
            directive = build_directive(
                directive_type="role_refresh_required", run_id=state["run_id"], comparison_set_id=state["comparison_set_id"],
                reason_codes=["role_stale_or_identity_ambiguous"], required_input_refs=refresh, affected_job_profile_ids=refresh,
            )
            directive = self.repository.save("rebuild_directive", directive, owner_id=state["user_id"])
            update["rebuild_directive_id"] = directive.directive_id
        return update

    def plan_decision_interaction(self, state: ProfileMatchingGraphState) -> dict[str, Any]:
        comparison = self._comparison(state)
        request_id = _stable_id("hir-comparison", [state["thread_id"], state["match_round"], comparison.comparison_set_id])
        request = ComparisonReviewRequest(
            request_id=request_id, thread_id=state["thread_id"], run_id=state["run_id"], user_id=state["user_id"],
            reason="请审阅岗位比较结果并选择下一步", comparison_set_id=comparison.comparison_set_id,
            input_snapshot_refs={
                "candidate_profile_snapshot_id": state["candidate_profile_snapshot_id"],
                "career_intent_snapshot_id": state["career_intent_snapshot_id"],
                "job_instance_profile_snapshot_ids": state["job_instance_profile_snapshot_ids"],
            },
            target_summaries=[{
                "job_instance_profile_snapshot_id": item.job_instance_profile_snapshot_id,
                "gap_assessment_id": item.gap_assessment_id, "recommended_tier": item.recommended_tier,
            } for item in comparison.entries],
            allowed_target_ids=[item.job_instance_profile_snapshot_id for item in comparison.entries],
            allowed_actions=["select_targets", "defer_targets", "reject_targets", "revise_candidate", "revise_intent", "refresh_role", "confirm_and_finish", "cancel"],
        )
        counters = MatchingCounter.model_validate(state["counters"])
        counters = counters.model_copy(update={"decision_interrupts": counters.decision_interrupts + 1})
        return {"pending_interaction": request.model_dump(mode="json"), "status": "interrupted", "counters": counters.model_dump(), "trace": [_trace("plan_decision_interaction", counters, request_id=request_id)]}

    def interrupt_for_decision(self, state: ProfileMatchingGraphState) -> dict[str, Any]:
        response = interrupt(state["pending_interaction"])
        return {"resume_input": response, "status": "running"}

    def validate_and_archive_decision(self, state: ProfileMatchingGraphState) -> dict[str, Any]:
        request = ComparisonReviewRequest.model_validate(state["pending_interaction"])
        response = ComparisonReviewResponse.model_validate(state["resume_input"])
        for key in ["request_id", "thread_id", "user_id"]:
            if getattr(response, key) != getattr(request, key):
                raise ProfileMatchingWorkflowError(f"comparison response {key} mismatch")
        if response.action not in request.allowed_actions:
            raise ProfileMatchingWorkflowError("comparison action is not allowed")
        referenced = {item.job_instance_profile_snapshot_id for item in response.target_decisions} | set(response.role_refresh_target_ids)
        if not referenced.issubset(set(request.allowed_target_ids)):
            raise ProfileMatchingWorkflowError("invalid_decision_target")
        if not self._inputs_current(state):
            self._stale_current(state)
            raise ProfileMatchingWorkflowError("comparison_stale")
        return {"processed_response_ids": [response.response_id], "trace": [_trace("validate_and_archive_decision", MatchingCounter.model_validate(state["counters"]), action=response.action)]}

    def route_user_decision(self, state: ProfileMatchingGraphState) -> dict[str, Any]:
        response = ComparisonReviewResponse.model_validate(state["resume_input"])
        routes = {
            "select_targets": "persist_decisions", "defer_targets": "persist_decisions", "reject_targets": "persist_decisions",
            "revise_candidate": "candidate_profile_required", "revise_intent": "rematch_required",
            "refresh_role": "role_refresh_required", "confirm_and_finish": "complete", "cancel": "cancel",
        }
        return {"next_action": routes[response.action], "trace": [_trace("route_user_decision", MatchingCounter.model_validate(state["counters"]), route=routes[response.action])]}

    def persist_target_decisions(self, state: ProfileMatchingGraphState) -> dict[str, Any]:
        response = ComparisonReviewResponse.model_validate(state["resume_input"])
        decisions = []
        previous = self.repository.list("target_decision", TargetDecision, owner_id=state["user_id"])
        for item in response.target_decisions:
            supersedes = next((old.decision_id for old in reversed(previous) if old.job_instance_profile_snapshot_id == item.job_instance_profile_snapshot_id), None)
            digest = canonical_hash("target-decision", [state["comparison_set_id"], response.response_id, item.model_dump(mode="json")])
            decisions.append(TargetDecision(
                decision_id=f"target-decision:{digest[7:31]}", user_id=state["user_id"], comparison_set_id=state["comparison_set_id"],
                job_instance_profile_snapshot_id=item.job_instance_profile_snapshot_id, status=item.status,
                reason_codes=sorted(dict.fromkeys(item.reason_codes)), note=item.note,
                created_from_response_id=response.response_id, supersedes_decision_id=supersedes,
            ))
        saved = self.repository.save_decision_batch(
            decisions, owner_id=state["user_id"], response_id=response.response_id, payload_hash=_response_hash(response),
        )
        return {"target_decision_ids": [item.decision_id for item in saved], "pending_interaction": None, "resume_input": None, "next_action": "complete", "trace": [_trace("persist_target_decisions", MatchingCounter.model_validate(state["counters"]), count=len(saved))]}

    def handle_candidate_revision(self, state: ProfileMatchingGraphState) -> dict[str, Any]:
        response = ComparisonReviewResponse.model_validate(state["resume_input"])
        directive = build_directive(
            directive_type="candidate_profile_required", run_id=state["run_id"], comparison_set_id=state["comparison_set_id"],
            reason_codes=["candidate_revision_requested"], required_input_refs=[response.response_id],
            affected_job_profile_ids=state["job_instance_profile_snapshot_ids"],
        )
        directive = self.repository.save("rebuild_directive", directive, owner_id=state["user_id"])
        self._archive_nondecision_response(response, {"record_ids": [directive.directive_id]})
        self._stale_current(state)
        return {"rebuild_directive_id": directive.directive_id, "pending_interaction": None, "resume_input": None, "next_action": "candidate_profile_required"}

    def handle_intent_revision(self, state: ProfileMatchingGraphState) -> dict[str, Any]:
        response = ComparisonReviewResponse.model_validate(state["resume_input"])
        patch = dict((response.intent_revision or {}).get("requested_patch", response.intent_revision or {}))
        allowed = {"target_roles", "target_role_families", "locations", "graduation_year", "recruitment_type", "salary_min", "salary_max", "salary_unit", "industries", "companies", "company_types", "constraints"}
        if not set(patch).issubset(allowed):
            raise ProfileMatchingWorkflowError("intent revision contains unsupported fields")
        previous_snapshot = self.profile_repository.get_profile(state["career_intent_snapshot_id"])
        if previous_snapshot is None:
            raise ProfileMatchingWorkflowError("snapshot_not_found: career intent")
        old_intent = CareerIntent.model_validate(previous_snapshot.profile_data)
        if "constraints" in patch:
            patch["constraints"] = [IntentConstraint.model_validate(item) for item in patch["constraints"]]
        new_intent = old_intent.model_copy(update={**patch, "schema_version": "v0.6", "previous_snapshot_id": previous_snapshot.snapshot_id})
        changed_paths = [f"/{key}" for key in sorted(patch) if old_intent.model_dump(mode="json").get(key) != new_intent.model_dump(mode="json").get(key)]
        digest = canonical_hash("intent-revision", [previous_snapshot.snapshot_id, patch, response.response_id])
        new_snapshot_id = f"intent-snapshot:{digest[7:31]}"
        latest = self.profile_repository.get_latest_profile(previous_snapshot.subject_id, "career_intent")
        new_snapshot = ProfileSnapshot(
            snapshot_id=new_snapshot_id, subject_id=previous_snapshot.subject_id, profile_type="career_intent",
            version=(latest.version if latest else previous_snapshot.version) + 1, schema_version="v0.6",
            profile_data=new_intent.model_dump(mode="json"), supporting_claim_ids=old_intent.supporting_claim_ids,
        )
        new_snapshot = self.profile_repository.save_profile(new_snapshot)
        impact = assess_intent_impact(
            old_intent, new_intent, old_snapshot_id=previous_snapshot.snapshot_id,
            new_snapshot_id=new_snapshot.snapshot_id, changed_paths=changed_paths,
        )
        impact = self.repository.save("intent_impact", impact, owner_id=state["user_id"])
        directive_type = "rematch_required" if impact.impact != "role_research_required" else "role_research_required"
        directive = build_directive(
            directive_type=directive_type, run_id=state["run_id"], comparison_set_id=state["comparison_set_id"],
            reason_codes=impact.reason_codes, required_input_refs=[new_snapshot.snapshot_id],
            affected_job_profile_ids=state["job_instance_profile_snapshot_ids"],
            requested_scope=project_search_scope(new_intent, new_snapshot.snapshot_id).model_dump(mode="json") if directive_type == "role_research_required" else None,
        )
        directive = self.repository.save("rebuild_directive", directive, owner_id=state["user_id"])
        self._archive_nondecision_response(response, {"record_ids": [new_snapshot.snapshot_id, impact.impact_assessment_id, directive.directive_id]})
        self._stale_current(state, status="superseded")
        update: dict[str, Any] = {
            "career_intent_snapshot_id": new_snapshot.snapshot_id,
            "intent_impact_assessment": impact.model_dump(mode="json"),
            "rebuild_directive_id": directive.directive_id, "pending_interaction": None, "resume_input": None,
            "next_action": directive_type, "trace": [_trace("handle_intent_revision", MatchingCounter.model_validate(state["counters"]), impact=impact.impact)],
        }
        return update

    def handle_role_refresh(self, state: ProfileMatchingGraphState) -> dict[str, Any]:
        response = ComparisonReviewResponse.model_validate(state["resume_input"])
        directive = build_directive(
            directive_type="role_refresh_required", run_id=state["run_id"], comparison_set_id=state["comparison_set_id"],
            reason_codes=["user_requested_role_refresh"], required_input_refs=response.role_refresh_target_ids,
            affected_job_profile_ids=response.role_refresh_target_ids,
        )
        directive = self.repository.save("rebuild_directive", directive, owner_id=state["user_id"])
        self._archive_nondecision_response(response, {"record_ids": [directive.directive_id]})
        self._stale_current(state)
        return {"rebuild_directive_id": directive.directive_id, "pending_interaction": None, "resume_input": None, "next_action": "role_refresh_required"}

    def finalize_matching(self, state: ProfileMatchingGraphState) -> dict[str, Any]:
        action = state.get("next_action")
        if action == "cancel":
            status = "cancelled"
        elif action == "fail":
            status = "failed"
        elif action == "complete_with_unknowns":
            status = "completed_with_unknowns"
        else:
            status = "completed"
        if state.get("resume_input"):
            response = ComparisonReviewResponse.model_validate(state["resume_input"])
            self._archive_nondecision_response(response, {"record_ids": []})
        report = self._report(state, status)
        return {"status": status, "report": report, "pending_interaction": None, "resume_input": None, "trace": [_trace("finalize_matching", MatchingCounter.model_validate(state["counters"]), status=status)]}

    def finalize_reroute(self, state: ProfileMatchingGraphState) -> dict[str, Any]:
        report = self._report(state, "reroute_required")
        return {"status": "reroute_required", "report": report, "pending_interaction": None, "resume_input": None, "trace": [_trace("finalize_reroute", MatchingCounter.model_validate(state["counters"]), route=state.get("next_action"))]}

    def _comparison(self, state: ProfileMatchingGraphState) -> ComparisonSet:
        comparison = self.repository.get(str(state.get("comparison_set_id", "")), ComparisonSet, owner_id=state["user_id"])
        if comparison is None:
            raise ProfileMatchingWorkflowError("comparison not found")
        return comparison

    def _assessment_map(self, comparison: ComparisonSet, owner_id: str) -> dict[str, GapAssessment]:
        result = {}
        for entry in comparison.entries:
            assessment = self.repository.get(entry.gap_assessment_id, GapAssessment, owner_id=owner_id)
            if assessment is None:
                raise ProfileMatchingWorkflowError("gap assessment not found")
            result[assessment.assessment_id] = assessment
        return result

    def _inputs_current(self, state: ProfileMatchingGraphState) -> bool:
        for snapshot_id in [state["candidate_profile_snapshot_id"], state["career_intent_snapshot_id"], *state["job_instance_profile_snapshot_ids"]]:
            snapshot = self.profile_repository.get_profile(snapshot_id)
            if snapshot is None:
                return False
            latest = self.profile_repository.get_latest_profile(snapshot.subject_id, snapshot.profile_type)
            if latest is None or latest.snapshot_id != snapshot_id:
                return False
        comparison = self._comparison(state)
        return comparison.status == "current"

    def _stale_current(self, state: ProfileMatchingGraphState, status: str = "stale") -> None:
        comparison = self._comparison(state)
        if comparison.status == "current":
            self.repository.replace_lifecycle(comparison.comparison_set_id, ComparisonSet, status)
        for assessment in self._assessment_map(comparison, state["user_id"]).values():
            if assessment.status == "current":
                self.repository.replace_lifecycle(assessment.assessment_id, GapAssessment, status)

    def _archive_nondecision_response(self, response: ComparisonReviewResponse, result: dict[str, Any]) -> None:
        self.repository.save_response_result(response.response_id, _response_hash(response), result)

    def _report(self, state: ProfileMatchingGraphState, status: str) -> dict[str, Any]:
        comparison = self.repository.get(str(state.get("comparison_set_id", "")), ComparisonSet, owner_id=state["user_id"])
        rows = []
        if comparison:
            for entry in comparison.entries:
                assessment = self.repository.get(entry.gap_assessment_id, GapAssessment, owner_id=state["user_id"])
                if assessment:
                    rows.append({
                        "job_instance_profile_snapshot_id": entry.job_instance_profile_snapshot_id,
                        "hard_qualification": assessment.hard_constraint_status,
                        "core_capability_coverage": assessment.core_coverage,
                        "bonus_capability_coverage": assessment.bonus_coverage,
                        "preference_assessments": assessment.preference_assessments,
                        "gaps": [item.model_dump(mode="json") for item in assessment.gaps],
                        "evidence_refs": assessment.supporting_claim_ids,
                    })
        report = {
            "status": status, "comparison_set_id": state.get("comparison_set_id"),
            "input_snapshot_refs": {
                "candidate": state.get("candidate_profile_snapshot_id"), "intent": state.get("career_intent_snapshot_id"),
                "jobs": state.get("job_instance_profile_snapshot_ids", []),
            },
            "warning": "覆盖度不是 Offer 概率",
            "targets": rows, "target_decision_ids": state.get("target_decision_ids", []),
            "rebuild_directive_id": state.get("rebuild_directive_id"),
        }
        if state.get("output_dir"):
            root = Path(str(state["output_dir"])); root.mkdir(parents=True, exist_ok=True)
            (root / "profile_matching_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            lines = ["# 双画像匹配报告", "", "> 覆盖度不是 Offer 概率。", ""]
            for row in rows:
                lines.extend([
                    f"## {row['job_instance_profile_snapshot_id']}", "",
                    f"- 硬性资格：{row['hard_qualification']}",
                    f"- 核心能力覆盖：{json.dumps(row['core_capability_coverage'], ensure_ascii=False)}",
                    f"- 加分能力覆盖：{json.dumps(row['bonus_capability_coverage'], ensure_ascii=False)}",
                    f"- 偏好：{json.dumps(row['preference_assessments'], ensure_ascii=False)}",
                    f"- 四类差距：{json.dumps(row['gaps'], ensure_ascii=False)}",
                    f"- Evidence refs：{', '.join(row['evidence_refs']) or '无'}", "",
                ])
            (root / "profile_matching_report.md").write_text("\n".join(lines), encoding="utf-8")
        return report


def _response_hash(response: ComparisonReviewResponse) -> str:
    return canonical_hash("comparison-response", response.model_dump(mode="json", exclude={"submitted_at"}))


def _stable_id(prefix: str, payload: Any) -> str:
    return f"{prefix}:{canonical_hash(prefix, payload)[7:31]}"


def _trace(node: str, counters: MatchingCounter, **extra: Any) -> dict[str, Any]:
    return {"node": node, "counters": counters.model_dump(), **extra}


__all__ = [
    "ProfileMatchingGraphRuntime", "ProfileMatchingWorkflowError",
    "build_profile_matching_graph", "create_profile_matching_state", "open_sqlite_checkpointer",
]
