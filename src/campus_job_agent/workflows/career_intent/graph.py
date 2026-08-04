"""Interruptible CareerIntent intake graph with evidence and typed handoff boundaries."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Annotated, Any, TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from campus_job_agent.schemas import (
    CareerIntent,
    CareerIntentCandidate,
    CareerIntentDraft,
    IntentConfirmationRecord,
    IntentReviewRequest,
    IntentReviewResponse,
    IntentValidationReceipt,
    ProfileSnapshot,
    Provenance,
    SearchScope,
    stable_intent_id,
)
from campus_job_agent.schemas.candidate_graph import append_items
from campus_job_agent.storage.base import EvidenceRepository, ProfileRepository
from campus_job_agent.workflows.career_intent.extractor import IntentCandidateExtractor
from campus_job_agent.workflows.career_intent.ingestion import IntentEvidenceIngestor
from campus_job_agent.workflows.career_intent.policy import apply_revision, publish_intent, validate_candidate
from campus_job_agent.workflows.career_intent.repository import SQLiteIntentRepository
from campus_job_agent.workflows.profile_matching.service import project_search_scopes

if TYPE_CHECKING:
    from campus_job_agent.runtime.sessions import SQLiteSessionRepository


class CareerIntentWorkflowError(RuntimeError):
    pass


class CareerIntentState(TypedDict, total=False):
    run_id: str
    session_id: str
    thread_id: str
    user_id: str
    candidate_profile_snapshot_id: str
    raw_text: str | None
    raw_artifact_id: str | None
    raw_fragment_id: str | None
    candidate: dict[str, Any] | None
    draft: dict[str, Any] | None
    validation_receipt_id: str | None
    pending_interaction: dict[str, Any] | None
    resume_input: dict[str, Any] | None
    processed_response: dict[str, Any] | None
    response_artifact_id: str | None
    response_fragment_id: str | None
    confirmed_intent: dict[str, Any] | None
    career_intent_snapshot_id: str | None
    search_scope_id: str | None
    search_scope_ids: list[str]
    handoff_id: str | None
    handoff_ids: list[str]
    status: str
    next_action: str | None
    llm_calls: Annotated[list[dict[str, Any]], append_items]
    trace: Annotated[list[dict[str, Any]], append_items]
    errors: Annotated[list[dict[str, Any]], append_items]


class CareerIntentGraphRuntime:
    def __init__(
        self, *, ingestor: IntentEvidenceIngestor, extractor: IntentCandidateExtractor,
        evidence_repository: EvidenceRepository, profile_repository: ProfileRepository,
        intent_repository: SQLiteIntentRepository,
        session_repository: SQLiteSessionRepository, checkpointer: Any,
    ) -> None:
        self.repository = intent_repository
        self.app = build_career_intent_graph(
            ingestor=ingestor, extractor=extractor,
            evidence_repository=evidence_repository,
            profile_repository=profile_repository,
            intent_repository=intent_repository,
            session_repository=session_repository,
            checkpointer=checkpointer,
        )

    def invoke(self, state: CareerIntentState) -> dict[str, Any]:
        thread_id = str(state.get("thread_id", "")).strip()
        if not thread_id:
            raise ValueError("thread_id is required")
        try:
            return self.app.invoke(state, {"configurable": {"thread_id": thread_id}})
        except sqlite3.Error as exc:
            raise CareerIntentWorkflowError(f"checkpoint_error: {exc}") from exc

    def resume(
        self, *, thread_id: str, response: IntentReviewResponse | dict[str, Any],
        run_id: str | None = None,
    ) -> dict[str, Any]:
        payload = response.model_dump(mode="json") if isinstance(response, IntentReviewResponse) else response
        validated = IntentReviewResponse.model_validate(payload)
        if validated.thread_id != thread_id:
            raise CareerIntentWorkflowError("resume thread_id does not match response")
        current = self.app.get_state({"configurable": {"thread_id": thread_id}})
        values = dict(current.values or {})
        if not values.get("pending_interaction"):
            receipt = self.repository.get_response_result(validated.response_id)
            if receipt is None:
                raise CareerIntentWorkflowError("no pending CareerIntent review exists")
            if receipt["payload_hash"] != response_hash(validated):
                raise CareerIntentWorkflowError("idempotency_conflict")
            return values
        if run_id is not None:
            self.app.update_state(
                {"configurable": {"thread_id": thread_id}}, {"run_id": run_id}
            )
        try:
            return self.app.invoke(
                Command(resume=validated.model_dump(mode="json")),
                {"configurable": {"thread_id": thread_id}},
            )
        except sqlite3.Error as exc:
            raise CareerIntentWorkflowError(f"checkpoint_error: {exc}") from exc

    def get_state(self, thread_id: str) -> Any:
        return self.app.get_state({"configurable": {"thread_id": thread_id}})


def create_career_intent_state(
    *, run_id: str, session_id: str, thread_id: str, user_id: str,
    candidate_profile_snapshot_id: str, raw_text: str,
) -> CareerIntentState:
    return {
        "run_id": run_id, "session_id": session_id, "thread_id": thread_id,
        "user_id": user_id,
        "candidate_profile_snapshot_id": candidate_profile_snapshot_id,
        "raw_text": raw_text, "raw_artifact_id": None, "raw_fragment_id": None,
        "candidate": None, "draft": None, "validation_receipt_id": None,
        "pending_interaction": None, "resume_input": None,
        "processed_response": None,
        "response_artifact_id": None, "response_fragment_id": None,
        "confirmed_intent": None, "career_intent_snapshot_id": None,
        "search_scope_id": None, "search_scope_ids": [], "handoff_id": None,
        "handoff_ids": [],
        "status": "initialized", "next_action": None,
        "llm_calls": [], "trace": [], "errors": [],
    }


def build_career_intent_graph(
    *, ingestor: IntentEvidenceIngestor, extractor: IntentCandidateExtractor,
    evidence_repository: EvidenceRepository, profile_repository: ProfileRepository,
    intent_repository: SQLiteIntentRepository,
    session_repository: SQLiteSessionRepository, checkpointer: Any,
):
    nodes = _Nodes(
        ingestor=ingestor, extractor=extractor,
        evidence_repository=evidence_repository,
        profile_repository=profile_repository,
        intent_repository=intent_repository,
        session_repository=session_repository,
    )
    graph = StateGraph(CareerIntentState)
    graph.add_node("validate_context", nodes.validate_context)
    graph.add_node("archive_raw_intent", nodes.archive_raw_intent)
    graph.add_node("extract_structured_candidate", nodes.extract_structured_candidate)
    graph.add_node("validate_candidate", nodes.validate_candidate)
    graph.add_node("plan_confirmation", nodes.plan_confirmation)
    graph.add_node("interrupt_for_confirmation", nodes.interrupt_for_confirmation)
    graph.add_node("apply_confirmation", nodes.apply_confirmation)
    graph.add_node("persist_intent_snapshot", nodes.persist_intent_snapshot)
    graph.add_node("project_search_scope", nodes.project_search_scope)
    graph.add_node("emit_role_research_handoff", nodes.emit_role_research_handoff)
    graph.add_node("finalize", nodes.finalize)
    graph.add_edge(START, "validate_context")
    graph.add_edge("validate_context", "archive_raw_intent")
    graph.add_edge("archive_raw_intent", "extract_structured_candidate")
    graph.add_edge("extract_structured_candidate", "validate_candidate")
    graph.add_edge("validate_candidate", "plan_confirmation")
    graph.add_edge("plan_confirmation", "interrupt_for_confirmation")
    graph.add_edge("interrupt_for_confirmation", "apply_confirmation")
    graph.add_conditional_edges(
        "apply_confirmation", lambda state: state["next_action"],
        {"review": "plan_confirmation", "publish": "persist_intent_snapshot", "cancel": "finalize"},
    )
    graph.add_edge("persist_intent_snapshot", "project_search_scope")
    graph.add_edge("project_search_scope", "emit_role_research_handoff")
    graph.add_edge("emit_role_research_handoff", "finalize")
    graph.add_edge("finalize", END)
    return graph.compile(checkpointer=checkpointer)


class _Nodes:
    def __init__(
        self, *, ingestor: IntentEvidenceIngestor, extractor: IntentCandidateExtractor,
        evidence_repository: EvidenceRepository, profile_repository: ProfileRepository,
        intent_repository: SQLiteIntentRepository,
        session_repository: SQLiteSessionRepository,
    ) -> None:
        self.ingestor = ingestor
        self.extractor = extractor
        self.evidence = evidence_repository
        self.profiles = profile_repository
        self.intents = intent_repository
        self.sessions = session_repository

    def validate_context(self, state: CareerIntentState, config: RunnableConfig) -> dict[str, Any]:
        required = ("run_id", "session_id", "thread_id", "user_id", "candidate_profile_snapshot_id")
        missing = [key for key in required if not str(state.get(key, "")).strip()]
        if missing:
            raise CareerIntentWorkflowError(f"missing required state fields: {', '.join(missing)}")
        configured = str(config.get("configurable", {}).get("thread_id", ""))
        if configured != state["thread_id"]:
            raise CareerIntentWorkflowError("configurable.thread_id mismatch")
        session = self.sessions.get(state["session_id"], user_id=state["user_id"])
        if session.current_refs.get("candidate_profile_snapshot_id") != state["candidate_profile_snapshot_id"]:
            raise CareerIntentWorkflowError("current Candidate snapshot is required before intent intake")
        candidate_snapshot = self.profiles.get_profile(state["candidate_profile_snapshot_id"])
        if (
            candidate_snapshot is None
            or candidate_snapshot.profile_type != "candidate"
            or candidate_snapshot.subject_id != state["user_id"]
        ):
            raise CareerIntentWorkflowError("current Candidate snapshot is missing or owner-mismatched")
        return {"status": "running", "trace": [trace("validate_context", state)]}

    def archive_raw_intent(self, state: CareerIntentState) -> dict[str, Any]:
        artifact, fragment = self.ingestor.archive_text(
            owner_id=state["user_id"], text=str(state.get("raw_text") or "")
        )
        return {
            "raw_text": None,
            "raw_artifact_id": artifact.artifact_id,
            "raw_fragment_id": fragment.fragment_id,
            "trace": [trace("archive_raw_intent", state, artifact_id=artifact.artifact_id)],
        }

    def extract_structured_candidate(self, state: CareerIntentState) -> dict[str, Any]:
        fragment = self.evidence.get_fragment(str(state["raw_fragment_id"]))
        if fragment is None:
            raise CareerIntentWorkflowError("raw intent fragment was not persisted")
        candidate, calls = self.extractor.extract(fragment)
        return {
            "candidate": candidate.model_dump(mode="json"),
            "llm_calls": [item.model_dump(mode="json") for item in calls],
            "trace": [trace("extract_structured_candidate", state)],
        }

    def validate_candidate(self, state: CareerIntentState) -> dict[str, Any]:
        fragment = self.evidence.get_fragment(str(state["raw_fragment_id"]))
        if fragment is None:
            raise CareerIntentWorkflowError("raw intent fragment not found")
        draft = validate_candidate(
            candidate=CareerIntentCandidate.model_validate(state["candidate"]),
            user_id=state["user_id"], artifact_id=str(state["raw_artifact_id"]),
            fragment_id=fragment.fragment_id, raw_text=fragment.text,
        )
        draft = self.intents.save("intent_draft", draft, owner_id=state["user_id"])
        reasons = [*draft.validation_issues, *[f"unresolved:{item}" for item in draft.unresolved_fields]]
        receipt = IntentValidationReceipt(
            receipt_id=stable_intent_id("intent-validation", [state["run_id"], draft.draft_id]),
            run_id=state["run_id"], draft_id=draft.draft_id,
            status="accepted" if not reasons else "needs_confirmation",
            reason_codes=reasons, fragment_ids=draft.source_fragment_ids,
        )
        receipt = self.intents.save("validation_receipt", receipt, owner_id=state["user_id"])
        return {
            "draft": draft.model_dump(mode="json"),
            "validation_receipt_id": receipt.receipt_id,
            "trace": [trace("validate_candidate", state, draft_id=draft.draft_id)],
        }

    def plan_confirmation(self, state: CareerIntentState) -> dict[str, Any]:
        draft = CareerIntentDraft.model_validate(state["draft"])
        request = IntentReviewRequest(
            request_id=stable_intent_id("request-intent", [
                state["thread_id"], draft.draft_id, draft.revision,
                draft.unresolved_fields, draft.validation_issues,
            ]),
            thread_id=state["thread_id"], run_id=state["run_id"], user_id=state["user_id"],
            draft_id=draft.draft_id,
            summary=draft_summary(draft),
            unresolved_fields=draft.unresolved_fields,
            validation_issues=draft.validation_issues,
            allowed_actions=["confirm", "revise", "cancel"],
        )
        return {
            "pending_interaction": request.model_dump(mode="json"),
            "resume_input": None, "status": "interrupted", "next_action": "review",
            "trace": [trace("plan_confirmation", state, request_id=request.request_id)],
        }

    def interrupt_for_confirmation(self, state: CareerIntentState) -> dict[str, Any]:
        response = interrupt(state["pending_interaction"])
        return {
            "resume_input": response, "status": "running",
            "trace": [trace("interrupt_for_confirmation", state)],
        }

    def apply_confirmation(self, state: CareerIntentState) -> dict[str, Any]:
        request = IntentReviewRequest.model_validate(state["pending_interaction"])
        response = IntentReviewResponse.model_validate(state["resume_input"])
        if response.request_id != request.request_id:
            raise CareerIntentWorkflowError("request_id does not match pending review")
        if response.thread_id != request.thread_id or response.user_id != request.user_id:
            raise CareerIntentWorkflowError("CareerIntent response identity mismatch")
        existing = self.intents.get_response_result(response.response_id)
        payload_hash = response_hash(response)
        if existing is not None:
            if existing["owner_id"] != response.user_id or existing["payload_hash"] != payload_hash:
                raise CareerIntentWorkflowError("idempotency_conflict")
            return existing["state_update"]
        artifact, fragment = self.ingestor.archive_response(
            owner_id=response.user_id, response_payload=response.model_dump(mode="json")
        )
        draft = CareerIntentDraft.model_validate(state["draft"])
        common = {
            "pending_interaction": None, "resume_input": None,
            "processed_response": response.model_dump(mode="json"),
            "response_artifact_id": artifact.artifact_id,
            "response_fragment_id": fragment.fragment_id,
        }
        if response.action == "cancel":
            update = {**common, "status": "cancelled", "next_action": "cancel",
                      "trace": [trace("apply_confirmation", state, response_id=response.response_id)]}
            self._save_response(response, update, artifact.artifact_id, fragment.fragment_id, "cancelled", draft)
            return update
        if response.action == "revise":
            revised = apply_revision(draft, response.patch, response_fragment_id=fragment.fragment_id)  # type: ignore[arg-type]
            revised = self.intents.save("intent_draft", revised, owner_id=state["user_id"])
            update = {**common, "draft": revised.model_dump(mode="json"), "status": "running",
                      "next_action": "review", "trace": [trace("apply_confirmation", state, response_id=response.response_id)]}
            self._save_response(response, update, artifact.artifact_id, fragment.fragment_id, "revised", revised)
            return update
        if draft.unresolved_fields or draft.validation_issues:
            update = {**common, "status": "running", "next_action": "review",
                      "trace": [trace("apply_confirmation", state, response_id=response.response_id)]}
            self._save_response(response, update, artifact.artifact_id, fragment.fragment_id, "needs_confirmation", draft)
            return update
        intent = publish_intent(
            draft, response_id=response.response_id,
            previous_snapshot_id=_latest_snapshot_id(self.profiles, state["user_id"]),
        )
        return {
            **common, "confirmed_intent": intent.model_dump(mode="json"),
            "status": "running", "next_action": "publish",
            "trace": [trace("apply_confirmation", state, response_id=response.response_id)],
        }

    def persist_intent_snapshot(self, state: CareerIntentState) -> dict[str, Any]:
        intent = CareerIntent.model_validate(state["confirmed_intent"])
        canonical = intent.model_dump(mode="json", exclude={"updated_at"})
        snapshot_id = stable_intent_id("intent-snapshot", [state["user_id"], canonical])
        existing = self.profiles.get_profile(snapshot_id)
        if existing is None:
            latest = self.profiles.get_latest_profile(state["user_id"], "career_intent")
            snapshot = ProfileSnapshot(
                snapshot_id=snapshot_id, subject_id=state["user_id"], profile_type="career_intent",
                version=(latest.version + 1) if latest else 1, schema_version="v0.7.1",
                profile_data=intent.model_dump(mode="json"), supporting_claim_ids=[],
                provenance=Provenance(
                    provider="human_confirmed", model="career_intent_policy_v1",
                    prompt_version="career_intent_extractor_v1", schema_version="v0.7.1",
                ),
            )
            existing = self.profiles.save_profile(snapshot)
        return {
            "career_intent_snapshot_id": existing.snapshot_id,
            "trace": [trace("persist_intent_snapshot", state, snapshot_id=existing.snapshot_id)],
        }

    def project_search_scope(self, state: CareerIntentState) -> dict[str, Any]:
        snapshot = self.profiles.get_profile(str(state["career_intent_snapshot_id"]))
        if snapshot is None:
            raise CareerIntentWorkflowError("CareerIntent snapshot not found")
        intent = CareerIntent.model_validate(snapshot.profile_data)
        scopes = [
            self.intents.save(
                "search_scope", scope, owner_id=state["user_id"],
                idempotency_key=scope.fingerprint(),
            )
            for scope in project_search_scopes(intent, snapshot.snapshot_id)
        ]
        scope_ids = [scope.scope_id for scope in scopes]
        return {
            "search_scope_id": scope_ids[0] if len(scope_ids) == 1 else None,
            "search_scope_ids": scope_ids,
            "trace": [trace("project_search_scope", state, scope_ids=scope_ids)],
        }

    def emit_role_research_handoff(self, state: CareerIntentState) -> dict[str, Any]:
        from campus_job_agent.runtime.models import Handoff

        handoffs = []
        for scope_id in state.get("search_scope_ids", []):
            handoff_id = stable_intent_id("handoff", [
                state["session_id"], state["career_intent_snapshot_id"], scope_id,
            ])
            handoffs.append(self.sessions.save_handoff(Handoff(
                handoff_id=handoff_id, session_id=state["session_id"], user_id=state["user_id"],
                handoff_type="role_research_required", origin_run_id=state["run_id"],
                origin_object_refs={"career_intent_snapshot_id": state["career_intent_snapshot_id"]},
                required_input_refs={
                    "career_intent_snapshot_id": state["career_intent_snapshot_id"],
                    "search_scope_id": scope_id,
                },
                handler_version="role_research_handoff_v2",
            )))
        if not handoffs:
            raise CareerIntentWorkflowError("CareerIntent produced no SearchScope handoff")
        handoff_ids = [handoff.handoff_id for handoff in handoffs]
        return {
            "handoff_id": handoff_ids[0] if len(handoff_ids) == 1 else None,
            "handoff_ids": handoff_ids,
            "trace": [trace("emit_role_research_handoff", state, handoff_ids=handoff_ids)],
        }

    def finalize(self, state: CareerIntentState) -> dict[str, Any]:
        if state.get("next_action") == "cancel" or state.get("status") == "cancelled":
            return {"status": "cancelled", "next_action": "intent.create", "trace": [trace("finalize", state)]}
        response = IntentReviewResponse.model_validate(state["processed_response"])
        update = {
            "status": "completed", "next_action": "role.research", "pending_interaction": None,
            "processed_response": None,
            "trace": [trace("finalize", state)],
        }
        artifact_id = str(state.get("response_artifact_id") or "")
        fragment_id = str(state.get("response_fragment_id") or "")
        draft = CareerIntentDraft.model_validate(state["draft"])
        self._save_response(response, update, artifact_id, fragment_id, "confirmed", draft,
                            snapshot_id=str(state["career_intent_snapshot_id"]),
                            search_scope_id=str(state["search_scope_id"]) if state.get("search_scope_id") else None,
                            search_scope_ids=list(state.get("search_scope_ids", [])))
        return update

    def _save_response(
        self, response: IntentReviewResponse, update: dict[str, Any], artifact_id: str,
        fragment_id: str, status: str, draft: CareerIntentDraft,
        snapshot_id: str | None = None, search_scope_id: str | None = None,
        search_scope_ids: list[str] | None = None,
    ) -> None:
        record = IntentConfirmationRecord(
            confirmation_id=stable_intent_id("intent-confirmation", [response.response_id, status]),
            response_id=response.response_id, request_id=response.request_id,
            user_id=response.user_id, draft_id=draft.draft_id,
            response_artifact_id=artifact_id, response_fragment_id=fragment_id,
            status=status, snapshot_id=snapshot_id, search_scope_id=search_scope_id,
            search_scope_ids=list(search_scope_ids or ([search_scope_id] if search_scope_id else [])),
        )
        self.intents.save("confirmation", record, owner_id=response.user_id)
        self.intents.save_response_result(
            response_id=response.response_id, owner_id=response.user_id,
            payload_hash=response_hash(response), result={
                "response_payload": response.model_dump(mode="json"),
                "state_update": update,
            },
        )


def response_hash(response: IntentReviewResponse) -> str:
    payload = response.model_dump(mode="json")
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def draft_summary(draft: CareerIntentDraft) -> dict[str, Any]:
    return {
        "target_roles": draft.target_roles,
        "target_role_families": draft.target_role_families,
        "constraints": [
            {"key": item.key, "value": item.value, "kind": item.kind,
             "affects_search_scope": item.affects_search_scope, "status": item.status}
            for item in draft.constraints
        ],
    }


def trace(node: str, state: CareerIntentState, **refs: Any) -> dict[str, Any]:
    now = datetime.now(UTC).isoformat()
    return {
        "node": node, "status": "success", "started_at": now, "ended_at": now,
        "duration_ms": 1, "run_id": state.get("run_id"), "thread_id": state.get("thread_id"),
        **refs,
    }


def _latest_snapshot_id(repository: ProfileRepository, user_id: str) -> str | None:
    latest = repository.get_latest_profile(user_id, "career_intent")
    return latest.snapshot_id if latest else None


__all__ = [
    "CareerIntentGraphRuntime", "CareerIntentWorkflowError", "CareerIntentState",
    "create_career_intent_state", "build_career_intent_graph", "response_hash",
]
