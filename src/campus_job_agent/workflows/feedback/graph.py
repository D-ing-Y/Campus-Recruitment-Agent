"""Recoverable raw-first v0.7 FeedbackGraph."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any
from uuid import uuid4

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from campus_job_agent.schemas import (
    AttributionReviewRequest, AttributionReviewResponse, FeedbackAttribution, FeedbackBudget,
    FeedbackCounter, FeedbackDiagnosis, FeedbackDirective, FeedbackEvent, FeedbackGraphState,
    FeedbackImpactAssessment, FeedbackInput, FeedbackObservation, PlanProgressEvent,
)
from campus_job_agent.schemas.matching import canonical_hash
from campus_job_agent.storage.base import BlobStore, EvidenceRepository, ProfileRepository
from campus_job_agent.workflows.candidate_profile.graph import open_sqlite_checkpointer
from campus_job_agent.workflows.feedback.ingestion import FeedbackIngestionError, FeedbackIngestor
from campus_job_agent.workflows.feedback.report import build_feedback_report
from campus_job_agent.workflows.feedback.repository import SQLiteFeedbackRepository
from campus_job_agent.workflows.feedback.service import FeedbackService, FeedbackServiceError
from campus_job_agent.workflows.preparation_plan.repository import SQLitePreparationRepository
from campus_job_agent.workflows.profile_matching.repository import SQLiteMatchingRepository


class FeedbackWorkflowError(RuntimeError):
    pass


class FeedbackGraphRuntime:
    def __init__(self, *, blob_store: BlobStore, evidence_repository: EvidenceRepository,
                 profile_repository: ProfileRepository, matching_repository: SQLiteMatchingRepository,
                 preparation_repository: SQLitePreparationRepository,
                 feedback_repository: SQLiteFeedbackRepository, checkpointer: Any) -> None:
        self.repository = feedback_repository
        ingestor = FeedbackIngestor(blob_store=blob_store, evidence_repository=evidence_repository,
                                    feedback_repository=feedback_repository)
        self.service = FeedbackService(
            ingestor=ingestor, evidence_repository=evidence_repository, profile_repository=profile_repository,
            feedback_repository=feedback_repository, preparation_repository=preparation_repository,
            matching_repository=matching_repository,
        )
        self.app = build_feedback_graph(service=self.service, feedback_repository=feedback_repository,
                                        checkpointer=checkpointer)

    def invoke(self, state: FeedbackGraphState) -> dict[str, Any]:
        thread_id = str(state.get("thread_id", "")).strip()
        if not thread_id:
            raise ValueError("thread_id is required")
        checkpoint_state = dict(state)
        counters = FeedbackCounter.model_validate(checkpoint_state.get("counters", {}))
        budgets = FeedbackBudget.model_validate(checkpoint_state.get("budgets", {}))
        if counters.feedback_items < budgets.max_feedback_items and not checkpoint_state.get("feedback_event_id"):
            value = FeedbackInput.model_validate(checkpoint_state.get("feedback_input"))
            try:
                event, artifact, fragments = self.service.ingest(
                    owner_id=checkpoint_state["user_id"], feedback_input=value,
                    allowed_path_roots=checkpoint_state.get("allowed_path_roots", []),
                    plan_id=checkpoint_state.get("plan_id"), activity_id=checkpoint_state.get("activity_id"),
                    target_job_profile_ids=checkpoint_state.get("target_job_profile_ids", []),
                )
            except FeedbackIngestionError as exc:
                raise FeedbackWorkflowError(str(exc)) from exc
            checkpoint_state.update({
                "feedback_event_id": event.feedback_event_id,
                "raw_artifact_ids": [artifact.artifact_id],
                "fragment_ids": [item.fragment_id for item in fragments],
                "feedback_input": None,
            })
        else:
            # Budget-exhausted runs must not checkpoint an input they intentionally ignore.
            checkpoint_state["feedback_input"] = None
        try:
            return self.app.invoke(checkpoint_state, {"configurable": {"thread_id": thread_id}})
        except sqlite3.Error as exc:
            raise FeedbackWorkflowError(f"checkpoint_error: {exc}") from exc

    def resume(self, *, thread_id: str, response: AttributionReviewResponse | dict[str, Any]) -> dict[str, Any]:
        payload = response.model_dump(mode="json") if isinstance(response, AttributionReviewResponse) else response
        validated = AttributionReviewResponse.model_validate(payload)
        if validated.thread_id != thread_id:
            raise ValueError("resume thread_id does not match response thread_id")
        current = self.app.get_state({"configurable": {"thread_id": thread_id}})
        values = dict(current.values or {})
        if not values.get("pending_interaction"):
            receipt = self.repository.get_response_result(validated.response_id)
            if receipt is None:
                raise FeedbackWorkflowError("no pending attribution review exists")
            if receipt["payload_hash"] != _response_hash(validated):
                raise FeedbackWorkflowError("idempotency_conflict")
            return values
        return self.app.invoke(Command(resume=payload), {"configurable": {"thread_id": thread_id}})

    def resolve(self, *, thread_id: str, resolutions: list[dict[str, Any]]) -> dict[str, Any]:
        values = dict(self.app.get_state({"configurable": {"thread_id": thread_id}}).values or {})
        if values.get("status") != "awaiting_rebuild":
            raise FeedbackWorkflowError("no awaiting rebuild state exists")
        resolved = dict(values.get("resolved_snapshot_refs", {}))
        for item in resolutions:
            directive = self.service.resolve_directive(
                user_id=values["user_id"], directive_id=item["directive_id"], response_id=item["response_id"],
                resolved_refs=list(item["resolved_refs"]), old_snapshot_ref=item.get("old_snapshot_ref"),
                no_change=bool(item.get("no_change", False)),
            )
            resolved[directive.directive_id] = directive.resolved_refs
        directives = [self.repository.get(item, FeedbackDirective, owner_id=values["user_id"])
                      for item in values.get("directive_ids", [])]
        required = [item for item in directives if item is not None and item.directive_type in {
            "candidate_profile_rebuild_required", "role_instance_refresh_required", "intent_review_required",
            "role_family_aggregation_candidate", "rematch_required", "replan_required",
        }]
        status = "completed" if required and all(item.status == "resolved" for item in required) else "awaiting_rebuild"
        report = dict(values.get("report") or {})
        report["version_chain_refs"] = dict(sorted(resolved.items()))
        report["directives"] = [{
            "directive_id": item.directive_id,
            "directive_type": item.directive_type,
            "status": item.status,
            "reason_codes": item.reason_codes,
            "affected_target_ids": item.affected_target_ids,
            "resolved_refs": item.resolved_refs,
        } for item in directives if item is not None]
        update = {"resolved_snapshot_refs": resolved, "status": status,
                  "next_action": "complete" if status == "completed" else "await_external_rebuild",
                  "report": report}
        # Only checkpoint the fields changed by resolution. Re-applying the full state would
        # feed trace/error lists through their reducers and duplicate historical entries.
        self.app.update_state({"configurable": {"thread_id": thread_id}}, update)
        return {**values, **update}

    def get_state(self, thread_id: str) -> Any:
        return self.app.get_state({"configurable": {"thread_id": thread_id}})


def create_feedback_state(*, thread_id: str, user_id: str, feedback_input: FeedbackInput | dict[str, Any],
                          allowed_path_roots: list[str], plan_id: str | None = None,
                          activity_id: str | None = None, target_job_profile_ids: list[str] | None = None,
                          candidate_profile_snapshot_id: str | None = None,
                          career_intent_snapshot_id: str | None = None,
                          comparison_set_id: str | None = None, run_id: str | None = None,
                          budgets: FeedbackBudget | dict[str, Any] | None = None) -> FeedbackGraphState:
    budget = budgets if isinstance(budgets, FeedbackBudget) else FeedbackBudget.model_validate(budgets or {})
    value = feedback_input if isinstance(feedback_input, FeedbackInput) else FeedbackInput.model_validate(feedback_input)
    return {
        "run_id": run_id or str(uuid4()), "thread_id": thread_id, "user_id": user_id,
        "status": "initialized", "allowed_path_roots": [str(Path(item).resolve()) for item in allowed_path_roots],
        "plan_id": plan_id, "activity_id": activity_id,
        "candidate_profile_snapshot_id": candidate_profile_snapshot_id,
        "career_intent_snapshot_id": career_intent_snapshot_id, "comparison_set_id": comparison_set_id,
        "target_job_profile_ids": list(target_job_profile_ids or []),
        "feedback_input": value.model_dump(mode="json"), "feedback_event_id": None,
        "raw_artifact_ids": [], "fragment_ids": [], "observation_ids": [], "diagnosis_ids": [],
        "attribution_ids": [], "feedback_claim_ids": [], "progress_event_ids": [],
        "impact_assessment_id": None, "directive_ids": [], "resolved_snapshot_refs": {},
        "pending_interaction": None, "resume_input": None, "processed_response_ids": [],
        "next_action": None, "budgets": budget.model_dump(), "counters": FeedbackCounter().model_dump(),
        "tool_results": [], "llm_calls": [], "trace": [], "errors": [], "report": None,
    }


def build_feedback_graph(*, service: FeedbackService, feedback_repository: SQLiteFeedbackRepository,
                         checkpointer: Any):
    nodes = _FeedbackNodes(service, feedback_repository)
    graph = StateGraph(FeedbackGraphState)
    for name in (
        "initialize_feedback_run", "ingest_and_archive_feedback", "extract_feedback_observations",
        "propose_feedback_diagnoses", "validate_feedback_attributions", "route_feedback_confirmation",
        "plan_attribution_interaction", "interrupt_for_attribution", "validate_attribution_response",
        "apply_attribution_response", "persist_feedback_claims_and_progress", "assess_feedback_impact",
        "build_feedback_directives", "route_feedback_next_action", "await_external_rebuild", "finalize_feedback",
    ):
        graph.add_node(name, getattr(nodes, name))
    chain = ["initialize_feedback_run", "ingest_and_archive_feedback", "extract_feedback_observations",
             "propose_feedback_diagnoses", "validate_feedback_attributions", "route_feedback_confirmation"]
    graph.add_edge(START, chain[0])
    for left, right in zip(chain, chain[1:]):
        graph.add_edge(left, right)
    graph.add_conditional_edges("route_feedback_confirmation", lambda state: state["next_action"], {
        "confirm_attribution": "plan_attribution_interaction", "persist": "persist_feedback_claims_and_progress",
        "complete_with_unknowns": "finalize_feedback", "fail": "finalize_feedback",
    })
    graph.add_edge("plan_attribution_interaction", "interrupt_for_attribution")
    graph.add_edge("interrupt_for_attribution", "validate_attribution_response")
    graph.add_edge("validate_attribution_response", "apply_attribution_response")
    graph.add_conditional_edges("apply_attribution_response", lambda state: state["next_action"], {
        "persist": "persist_feedback_claims_and_progress", "complete_with_unknowns": "finalize_feedback",
        "cancel": "finalize_feedback",
    })
    graph.add_edge("persist_feedback_claims_and_progress", "assess_feedback_impact")
    graph.add_edge("assess_feedback_impact", "build_feedback_directives")
    graph.add_edge("build_feedback_directives", "route_feedback_next_action")
    graph.add_conditional_edges("route_feedback_next_action", lambda state: state["next_action"], {
        "await_external_rebuild": "await_external_rebuild", "complete": "finalize_feedback",
        "complete_with_unknowns": "finalize_feedback",
    })
    graph.add_edge("await_external_rebuild", END)
    graph.add_edge("finalize_feedback", END)
    return graph.compile(checkpointer=checkpointer)


class _FeedbackNodes:
    def __init__(self, service: FeedbackService, repository: SQLiteFeedbackRepository) -> None:
        self.service = service
        self.repository = repository

    def initialize_feedback_run(self, state: FeedbackGraphState, config: RunnableConfig) -> dict[str, Any]:
        if config.get("configurable", {}).get("thread_id") != state.get("thread_id"):
            raise FeedbackWorkflowError("configurable.thread_id must equal state.thread_id")
        counters = FeedbackCounter.model_validate(state.get("counters", {}))
        budgets = FeedbackBudget.model_validate(state.get("budgets", {}))
        if counters.feedback_items >= budgets.max_feedback_items:
            return {"status": "completed_with_unknowns", "next_action": "complete_with_unknowns",
                    "trace": [_trace("initialize_feedback_run", "budget_exhausted")]}
        counters = counters.model_copy(update={"feedback_items": counters.feedback_items + 1})
        return {"status": "running", "counters": counters.model_dump(),
                "trace": [_trace("initialize_feedback_run", "running")]}

    def ingest_and_archive_feedback(self, state: FeedbackGraphState) -> dict[str, Any]:
        if state.get("next_action") == "complete_with_unknowns":
            return {"next_action": "complete_with_unknowns"}
        if state.get("feedback_event_id"):
            return {"feedback_input": None,
                    "trace": [_trace("ingest_and_archive_feedback", "already_archived",
                                      event_id=state["feedback_event_id"])]}
        value = FeedbackInput.model_validate(state["feedback_input"])
        try:
            event, artifact, fragments = self.service.ingest(
                owner_id=state["user_id"], feedback_input=value,
                allowed_path_roots=state.get("allowed_path_roots", []), plan_id=state.get("plan_id"),
                activity_id=state.get("activity_id"), target_job_profile_ids=state.get("target_job_profile_ids", []),
            )
        except FeedbackIngestionError as exc:
            raise FeedbackWorkflowError(str(exc)) from exc
        return {"feedback_event_id": event.feedback_event_id, "raw_artifact_ids": [artifact.artifact_id],
                "fragment_ids": [item.fragment_id for item in fragments], "feedback_input": None,
                "trace": [_trace("ingest_and_archive_feedback", "archived", event_id=event.feedback_event_id,
                                  artifact_count=1, fragment_count=len(fragments))]}

    def extract_feedback_observations(self, state: FeedbackGraphState) -> dict[str, Any]:
        if state.get("next_action") == "complete_with_unknowns":
            return {"trace": [_trace("extract_feedback_observations", "budget_exhausted")]}
        event = self._event(state)
        observations, diagnoses, attributions = self.service.interpret(
            event=event, candidate_snapshot_id=state.get("candidate_profile_snapshot_id"),
        )
        return {"observation_ids": [item.observation_id for item in observations],
                "diagnosis_ids": [item.diagnosis_id for item in diagnoses],
                "attribution_ids": [item.attribution_id for item in attributions],
                "trace": [_trace("extract_feedback_observations", "validated", observation_count=len(observations))]}

    def propose_feedback_diagnoses(self, state: FeedbackGraphState) -> dict[str, Any]:
        return {"trace": [_trace("propose_feedback_diagnoses", "deterministic", count=len(state.get("diagnosis_ids", [])))]}

    def validate_feedback_attributions(self, state: FeedbackGraphState) -> dict[str, Any]:
        return {"trace": [_trace("validate_feedback_attributions", "valid", count=len(state.get("attribution_ids", [])))]}

    def route_feedback_confirmation(self, state: FeedbackGraphState) -> dict[str, Any]:
        if state.get("next_action") == "complete_with_unknowns":
            return {"next_action": "complete_with_unknowns"}
        attributions = self._attributions(state)
        pending = any(item.requires_confirmation and item.confirmation_status == "pending" for item in attributions)
        counters = FeedbackCounter.model_validate(state["counters"])
        budgets = FeedbackBudget.model_validate(state["budgets"])
        route = "confirm_attribution" if pending and counters.feedback_interrupts < budgets.max_feedback_interrupts else (
            "complete_with_unknowns" if pending else "persist"
        )
        return {"next_action": route, "trace": [_trace("route_feedback_confirmation", route)]}

    def plan_attribution_interaction(self, state: FeedbackGraphState) -> dict[str, Any]:
        event = self._event(state)
        attributions = self._attributions(state)
        observations = {item.observation_id: item for item in self._observations(state)}
        diagnoses = {item.diagnosis_id: item for item in self._diagnoses(state)}
        pending = [item for item in attributions if item.confirmation_status == "pending"]
        request_id = _stable_id("hir-attribution", [state["thread_id"], event.feedback_event_id,
                                                     [item.attribution_id for item in pending]])
        summaries = []
        for item in pending:
            observation = observations[item.observation_ids[0]]
            diagnosis = diagnoses[item.diagnosis_ids[0]]
            summaries.append({"attribution_id": item.attribution_id, "observation_id": observation.observation_id,
                              "evidence_excerpt": str(observation.value)[:160], "diagnosis_id": diagnosis.diagnosis_id,
                              "diagnosis_summary": diagnosis.summary, "subject_scope": item.subject_scope,
                              "authority": item.authority, "alternative_explanations": diagnosis.alternative_explanations,
                              "limitations": diagnosis.limitations})
        request = AttributionReviewRequest(
            request_id=request_id, thread_id=state["thread_id"], run_id=state["run_id"], user_id=state["user_id"],
            feedback_event_id=event.feedback_event_id,
            observation_ids=[item for attribution in pending for item in attribution.observation_ids],
            diagnosis_ids=[item for attribution in pending for item in attribution.diagnosis_ids],
            attribution_ids=[item.attribution_id for item in pending], attribution_summaries=summaries,
            allowed_scopes=["plan_task", "candidate_capability", "candidate_evidence", "job_instance",
                            "company_role", "role_family_candidate", "career_intent", "unknown"],
            allowed_actions=["confirm_attributions", "relabel_scope", "reject_diagnoses", "mark_unknown", "cancel"],
        )
        counters = FeedbackCounter.model_validate(state["counters"])
        counters = counters.model_copy(update={"feedback_interrupts": counters.feedback_interrupts + 1})
        return {"pending_interaction": request.model_dump(mode="json"), "status": "interrupted",
                "counters": counters.model_dump(), "trace": [_trace("plan_attribution_interaction", "interrupted", request_id=request_id)]}

    def interrupt_for_attribution(self, state: FeedbackGraphState) -> dict[str, Any]:
        return {"resume_input": interrupt(state["pending_interaction"]), "status": "running"}

    def validate_attribution_response(self, state: FeedbackGraphState) -> dict[str, Any]:
        request = AttributionReviewRequest.model_validate(state["pending_interaction"])
        response = AttributionReviewResponse.model_validate(state["resume_input"])
        for key in ("request_id", "thread_id", "user_id"):
            if getattr(request, key) != getattr(response, key):
                raise FeedbackWorkflowError(f"attribution response {key} mismatch")
        if response.action not in request.allowed_actions:
            raise FeedbackWorkflowError("attribution action is not allowed")
        if not set(response.attribution_ids).issubset(request.attribution_ids) \
                or not set(response.diagnosis_ids).issubset(request.diagnosis_ids):
            raise FeedbackWorkflowError("feedback_scope_invalid")
        if any(item.attribution_id not in request.attribution_ids or item.subject_scope not in request.allowed_scopes
               for item in response.scope_relabels):
            raise FeedbackWorkflowError("feedback_scope_invalid")
        return {"processed_response_ids": [response.response_id], "trace": [_trace("validate_attribution_response", response.action)]}

    def apply_attribution_response(self, state: FeedbackGraphState) -> dict[str, Any]:
        response = AttributionReviewResponse.model_validate(state["resume_input"])
        if response.action == "cancel":
            route = "cancel"
        else:
            self.service.apply_attribution_response(
                event=self._event(state), response_id=response.response_id, action=response.action,
                attribution_ids=response.attribution_ids or state["attribution_ids"], diagnosis_ids=response.diagnosis_ids,
                relabels=[item.model_dump(mode="json") for item in response.scope_relabels],
            )
            route = "complete_with_unknowns" if response.action in {"reject_diagnoses", "mark_unknown"} else "persist"
        self.repository.save_response_result(response.response_id, _response_hash(response),
                                             {"record_ids": state["attribution_ids"], "route": route})
        return {"pending_interaction": None, "resume_input": None, "next_action": route,
                "trace": [_trace("apply_attribution_response", route)]}

    def persist_feedback_claims_and_progress(self, state: FeedbackGraphState) -> dict[str, Any]:
        event = self._event(state)
        claims, progress = self.service.persist_claims_and_progress(
            event=event, attributions=self._attributions(state),
        )
        return {"feedback_claim_ids": [item.claim_id for item in claims],
                "progress_event_ids": [item.progress_event_id for item in progress], "feedback_input": None,
                "trace": [_trace("persist_feedback_claims_and_progress", "persisted",
                                  claim_count=len(claims), progress_count=len(progress))]}

    def assess_feedback_impact(self, state: FeedbackGraphState) -> dict[str, Any]:
        progress = [self.service.preparation.get(item, PlanProgressEvent, owner_id=state["user_id"])
                    for item in state.get("progress_event_ids", [])]
        impact = self.service.assess_and_save_impact(self._event(state), self._attributions(state),
                                                     [item for item in progress if item is not None])
        return {"impact_assessment_id": impact.impact_assessment_id,
                "trace": [_trace("assess_feedback_impact", "deterministic", impact_id=impact.impact_assessment_id)]}

    def build_feedback_directives(self, state: FeedbackGraphState) -> dict[str, Any]:
        impact = self.repository.get(state["impact_assessment_id"], FeedbackImpactAssessment, owner_id=state["user_id"])
        directives = self.service.create_directives(self._event(state), impact, state.get("feedback_claim_ids", []))
        return {"directive_ids": [item.directive_id for item in directives],
                "report": build_feedback_report(event_id=state["feedback_event_id"],
                                                observation_ids=state["observation_ids"], diagnosis_ids=state["diagnosis_ids"],
                                                attributions=self._attributions(state), claim_ids=state["feedback_claim_ids"],
                                                progress_ids=state["progress_event_ids"], impact=impact, directives=directives),
                "trace": [_trace("build_feedback_directives", "built", directive_count=len(directives))]}

    def route_feedback_next_action(self, state: FeedbackGraphState) -> dict[str, Any]:
        directives = [self.repository.get(item, FeedbackDirective, owner_id=state["user_id"])
                      for item in state.get("directive_ids", [])]
        external = any(item is not None and item.directive_type in {
            "candidate_profile_rebuild_required", "role_instance_refresh_required", "intent_review_required",
            "role_family_aggregation_candidate",
        } for item in directives)
        route = "await_external_rebuild" if external else "complete"
        return {"next_action": route, "trace": [_trace("route_feedback_next_action", route)]}

    def await_external_rebuild(self, state: FeedbackGraphState) -> dict[str, Any]:
        return {"status": "awaiting_rebuild", "trace": [_trace("await_external_rebuild", "awaiting_rebuild")]}

    def finalize_feedback(self, state: FeedbackGraphState) -> dict[str, Any]:
        route = state.get("next_action")
        status = "cancelled" if route == "cancel" else "failed" if route == "fail" else (
            "completed_with_unknowns" if route == "complete_with_unknowns" else "completed"
        )
        if state.get("feedback_event_id"):
            self.repository.replace_lifecycle(state["feedback_event_id"], FeedbackEvent,
                                              "cancelled" if status == "cancelled" else "completed_with_unknowns" if status == "completed_with_unknowns" else "processed")
        return {"status": status, "feedback_input": None, "trace": [_trace("finalize_feedback", status)]}

    def _event(self, state: FeedbackGraphState) -> FeedbackEvent:
        event = self.repository.get(state["feedback_event_id"], FeedbackEvent, owner_id=state["user_id"])
        if event is None:
            raise FeedbackWorkflowError("feedback event missing")
        return event

    def _observations(self, state: FeedbackGraphState) -> list[FeedbackObservation]:
        return [item for item in (self.repository.get(value, FeedbackObservation, owner_id=state["user_id"])
                                  for value in state.get("observation_ids", [])) if item is not None]

    def _diagnoses(self, state: FeedbackGraphState) -> list[FeedbackDiagnosis]:
        return [item for item in (self.repository.get(value, FeedbackDiagnosis, owner_id=state["user_id"])
                                  for value in state.get("diagnosis_ids", [])) if item is not None]

    def _attributions(self, state: FeedbackGraphState) -> list[FeedbackAttribution]:
        return [item for item in (self.repository.get(value, FeedbackAttribution, owner_id=state["user_id"])
                                  for value in state.get("attribution_ids", [])) if item is not None]


def _trace(node: str, outcome: str, **details: Any) -> dict[str, Any]:
    return {"node": node, "outcome": outcome, **details}


def _stable_id(prefix: str, payload: Any) -> str:
    return f"{prefix}:{canonical_hash(prefix, payload)[7:31]}"


def _response_hash(response: AttributionReviewResponse) -> str:
    return canonical_hash("attribution-review-response", response.model_dump(mode="json", exclude={"submitted_at"}))


__all__ = ["FeedbackGraphRuntime", "FeedbackWorkflowError", "build_feedback_graph", "create_feedback_state",
           "open_sqlite_checkpointer"]
