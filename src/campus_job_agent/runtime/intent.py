"""CareerIntentGraph application boundary for the production CLI."""

from __future__ import annotations

import hashlib
import json
from typing import Any, TYPE_CHECKING
from uuid import uuid4

from campus_job_agent.runtime.models import ArtifactEntry, ErrorEvent, LLMCallReceipt, ObjectRef, RunEvent
from campus_job_agent.schemas import IntentReviewResponse, SearchScope
from campus_job_agent.workflows.career_intent.graph import create_career_intent_state, response_hash

if TYPE_CHECKING:
    from campus_job_agent.runtime.factory import Runtime


class IntentApplicationError(RuntimeError):
    error_type = "contract_violation"


class IntentApplicationService:
    def __init__(self, runtime: "Runtime") -> None:
        self.runtime = runtime

    def create(self, *, session_id: str, raw_text: str) -> dict[str, Any]:
        session = self.runtime.session_service.status(session_id)
        if session.status != "active" or session.pending_request is not None:
            raise IntentApplicationError("CareerIntent create requires an active session without a pending request")
        text = raw_text.strip()
        if not text:
            raise IntentApplicationError("raw career intent is required")
        candidate_snapshot_id = session.current_refs.get("candidate_profile_snapshot_id")
        if not isinstance(candidate_snapshot_id, str):
            raise IntentApplicationError("current Candidate snapshot is required")
        if session.current_stage != "intent":
            raise IntentApplicationError("CareerIntent create requires the intent session stage")
        thread_id = f"thread-{uuid4()}"
        manifest = self.runtime.artifact_writer.initialize_run(
            session_id=session.session_id, thread_id=thread_id,
            workflow="career_intent", command="intent.create",
            parent_run_id=session.latest_run_id,
            input_refs={
                "candidate_profile_snapshot_id": candidate_snapshot_id,
                "raw_intent_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                "raw_intent_character_count": len(text),
            },
        )
        state = create_career_intent_state(
            run_id=manifest.run_id, session_id=session.session_id,
            thread_id=thread_id, user_id=session.user_id,
            candidate_profile_snapshot_id=candidate_snapshot_id, raw_text=text,
        )
        try:
            with self.runtime.open_workflow("intent") as workflow:
                result = workflow.invoke(state)
            return self._finish(session=session, manifest=manifest, result=result, trace_start=0, llm_start=0)
        except Exception as exc:
            self._fail(manifest.run_id, exc)
            raise

    def resume(
        self, *, session_id: str, response: IntentReviewResponse
    ) -> dict[str, Any]:
        session = self.runtime.session_service.status(session_id)
        existing = self.runtime.intent_repository.get_response_result(response.response_id)
        if existing is not None:
            if existing["owner_id"] != session.user_id or existing["payload_hash"] != response_hash(response):
                raise IntentApplicationError("idempotency_conflict")
            return self._duplicate_payload(session)
        if not session.latest_run_id or not session.pending_request:
            raise IntentApplicationError("session has no pending CareerIntent review")
        parent = self.runtime.artifact_writer.load_manifest(session.latest_run_id)
        manifest = self.runtime.artifact_writer.initialize_run(
            session_id=session.session_id, thread_id=parent.thread_id,
            workflow="career_intent", command="intent.resume",
            parent_run_id=parent.run_id,
            input_refs={
                "response_id": response.response_id,
                "request_id": response.request_id,
                "action": response.action,
            },
        )
        try:
            with self.runtime.open_workflow("intent") as workflow:
                previous = dict(workflow.get_state(parent.thread_id).values or {})
                result = workflow.resume(
                    thread_id=parent.thread_id, response=response, run_id=manifest.run_id
                )
            return self._finish(
                session=session, manifest=manifest, result=result,
                trace_start=len(previous.get("trace", [])),
                llm_start=len(previous.get("llm_calls", [])),
            )
        except Exception as exc:
            self._fail(manifest.run_id, exc)
            raise

    def _finish(
        self, *, session: Any, manifest: Any, result: dict[str, Any],
        trace_start: int, llm_start: int,
    ) -> dict[str, Any]:
        self._append_events(manifest, result.get("trace", [])[trace_start:])
        self._append_llm(manifest.run_id, result.get("llm_calls", [])[llm_start:])
        pending = _pending_request(result)
        status = "interrupted" if pending else str(result.get("status") or "failed")
        snapshot_id = result.get("career_intent_snapshot_id")
        scope_id = result.get("search_scope_id")
        scope_ids = list(result.get("search_scope_ids") or ([scope_id] if scope_id else []))
        handoff_id = result.get("handoff_id")
        handoff_ids = list(result.get("handoff_ids") or ([handoff_id] if handoff_id else []))
        next_action = (
            "intent.resume" if status == "interrupted"
            else "role.research" if status == "completed"
            else "intent.create" if status == "cancelled"
            else "inspect.run"
        )
        session = self._update_session(
            session=session, run_id=manifest.run_id, status=status,
            pending=pending, snapshot_id=snapshot_id, handoff_ids=handoff_ids,
        )
        self._index_objects(
            run_id=manifest.run_id, owner=session.user_id,
            artifact_id=result.get("raw_artifact_id"), snapshot_id=snapshot_id,
            scope_ids=scope_ids,
        )
        for value in handoff_ids:
            handoff = self.runtime.session_repository.get_handoff(value, user_id=session.user_id)
            self.runtime.artifact_writer.append_handoff(handoff)
        metrics = self._metrics(result, status)
        self.runtime.artifact_writer.write_state(manifest.run_id, {
            "status": status, "raw_artifact_id": result.get("raw_artifact_id"),
            "raw_fragment_id": result.get("raw_fragment_id"),
            "draft_id": (result.get("draft") or {}).get("draft_id"),
            "validation_receipt_id": result.get("validation_receipt_id"),
            "pending_request": pending,
            "career_intent_snapshot_id": snapshot_id,
            "search_scope_id": scope_id, "search_scope_ids": scope_ids,
            "handoff_id": handoff_id, "handoff_ids": handoff_ids,
            "metrics": metrics,
        })
        self.runtime.artifact_writer.write_report(
            manifest.run_id,
            "\n".join([
                "# CareerIntent Workflow", "", f"- status: `{status}`",
                f"- next action: `{next_action}`",
                f"- snapshot: `{snapshot_id or 'none'}`",
                f"- search scopes: `{', '.join(scope_ids) or 'none'}`",
                f"- handoffs: `{', '.join(handoff_ids) or 'none'}`",
            ]),
        )
        terminal = self.runtime.artifact_writer.finish_run(
            manifest.run_id,
            status=status if status in {"completed", "interrupted", "cancelled", "failed"} else "failed",
            next_action=next_action,
            output_refs={
                "career_intent_snapshot_id": snapshot_id,
                "search_scope_id": scope_id, "search_scope_ids": scope_ids,
                "handoff_id": handoff_id, "handoff_ids": handoff_ids,
            },
            pending_request_id=(pending or {}).get("request_id"),
            pending_handoff_ids=handoff_ids,
        )
        return {
            "schema_version": "v0.7.1", "command": manifest.command,
            "run_id": manifest.run_id, "session_id": session.session_id,
            "session_version": session.session_version, "thread_id": manifest.thread_id,
            "status": status, "next_action": next_action,
            "output_refs": {
                "career_intent_snapshot_id": snapshot_id,
                "search_scope_id": scope_id, "search_scope_ids": scope_ids,
                "handoff_id": handoff_id, "handoff_ids": handoff_ids,
            },
            "pending_request": pending, "artifact_paths": terminal.artifact_paths,
            "metrics": metrics, "deduplicated": False, "warnings": [], "errors": [],
        }

    def _update_session(
        self, *, session: Any, run_id: str, status: str,
        pending: dict[str, Any] | None, snapshot_id: str | None, handoff_ids: list[str],
    ) -> Any:
        if status == "interrupted":
            return self.runtime.session_repository.update_navigation(
                session.session_id, expected_version=session.session_version,
                operation="intent_interrupted", status="interrupted",
                current_stage="intent", pending_request=str(pending["request_id"]),
                latest_run_id=run_id,
            )
        if status == "completed" and snapshot_id:
            snapshot = self.runtime.profile_repository.get_profile(snapshot_id)
            if snapshot is None:
                raise IntentApplicationError("CareerIntent snapshot was not persisted")
            previous = session.current_refs.get("career_intent_snapshot_id")
            predecessors = [str(previous)] if previous and str(previous) != snapshot_id else []
            self.runtime.session_repository.register_ref(ObjectRef(
                object_id=snapshot_id, object_type="career_intent_snapshot",
                owner_id=session.user_id, schema_version=snapshot.schema_version,
                predecessor_ids=predecessors,
                successor_of=predecessors[0] if predecessors else None,
                canonical_hash=_hash(snapshot.profile_data),
            ))
            session = self.runtime.session_repository.set_current_ref(
                session.session_id, key="career_intent_snapshot_id", object_id=snapshot_id,
                expected_version=session.session_version,
            )
            pending_handoffs = list(dict.fromkeys([*session.pending_handoff_ids, *handoff_ids]))
            return self.runtime.session_repository.update_navigation(
                session.session_id, expected_version=session.session_version,
                operation="intent_completed", status="active", current_stage="role",
                pending_request=None, pending_handoff_ids=pending_handoffs,
                latest_run_id=run_id,
            )
        return self.runtime.session_repository.update_navigation(
            session.session_id, expected_version=session.session_version,
            operation=f"intent_{status}", status="cancelled" if status == "cancelled" else "failed",
            current_stage="intent", pending_request=None, latest_run_id=run_id,
        )

    def _append_events(self, manifest: Any, traces: list[dict[str, Any]]) -> None:
        for item in traces:
            node = str(item.get("node", "unknown"))
            self.runtime.artifact_writer.append_event(RunEvent(
                run_id=manifest.run_id, session_id=manifest.session_id,
                thread_id=manifest.thread_id, event_type="node_started",
                workflow="career_intent", node=node, status="running",
            ))
            self.runtime.artifact_writer.append_event(RunEvent(
                run_id=manifest.run_id, session_id=manifest.session_id,
                thread_id=manifest.thread_id, event_type="node_finished",
                workflow="career_intent", node=node, status="completed",
                output_refs={key: value for key, value in item.items() if key.endswith("_id")},
                duration_ms=max(1, int(item.get("duration_ms", 1))),
            ))

    def _append_llm(self, run_id: str, calls: list[dict[str, Any]]) -> None:
        for call in calls:
            self.runtime.artifact_writer.append_llm_call(LLMCallReceipt(
                run_id=run_id, provider=str(call.get("provider", "unknown")),
                model=str(call.get("model", "unknown")),
                prompt_version=str(call.get("prompt_version", "unknown")),
                schema_version_used=str(call.get("schema_version", "unknown")),
                request_hash=str(call.get("cache_key", "unknown")),
                status="success" if call.get("status") == "success" else "failed",
                retry_count=int(call.get("retry_count", 0)), cache_hit=bool(call.get("cache_hit")),
                latency_ms=int(call.get("duration_ms", 0)),
                validation_result=str(call.get("error_type") or "accepted"),
                fallback=call.get("fallback_reason"), integration=call.get("integration"),
                requested_strategy=call.get("requested_strategy"),
                effective_strategy=call.get("effective_strategy"),
                capabilities=call.get("capabilities"),
            ))

    def _index_objects(
        self, *, run_id: str, owner: str, artifact_id: str | None,
        snapshot_id: str | None, scope_ids: list[str],
    ) -> None:
        base = [
            ("career_intent_evidence", artifact_id, f"repository://evidence/artifacts/{artifact_id}", "private"),
            ("career_intent_snapshot", snapshot_id, f"repository://profiles/{snapshot_id}", "internal"),
        ]
        base.extend(
            ("search_scope", scope_id, f"repository://intent/search-scopes/{scope_id}", "internal")
            for scope_id in scope_ids
        )
        for logical_type, object_id, locator, sensitivity in base:
            if object_id:
                self.runtime.artifact_writer.add_artifact(run_id, ArtifactEntry(
                    logical_type=logical_type, object_id=str(object_id), locator=locator,
                    owner=owner, sensitivity=sensitivity,  # type: ignore[arg-type]
                ))

    @staticmethod
    def _metrics(result: dict[str, Any], status: str) -> dict[str, int | float | None]:
        has_raw = bool(result.get("raw_artifact_id") and result.get("raw_fragment_id"))
        draft = result.get("draft") or {}
        constraints = draft.get("constraints", [])
        traced = sum(bool(item.get("source_ref")) for item in constraints)
        completed = status == "completed"
        return {
            "raw_intent_evidence_trace_rate": 1.0 if has_raw else 0.0,
            "confirmed_constraint_trace_rate": (
                (1.0 if not constraints else round(traced / len(constraints), 6))
                if completed else None
            ),
            "search_scope_projection_accuracy": (
                1.0 if completed and (result.get("search_scope_ids") or result.get("search_scope_id")) else 0.0
            ) if completed else None,
            "duplicate_confirmation_write_count": 0,
        }

    def _duplicate_payload(self, session: Any) -> dict[str, Any]:
        return {
            "schema_version": "v0.7.1", "command": "intent.resume",
            "run_id": session.latest_run_id, "session_id": session.session_id,
            "session_version": session.session_version,
            "status": "completed" if session.current_refs.get("career_intent_snapshot_id") else "interrupted",
            "next_action": "role.research" if session.current_refs.get("career_intent_snapshot_id") else "intent.resume",
            "output_refs": {
                "career_intent_snapshot_id": session.current_refs.get("career_intent_snapshot_id"),
            },
            "pending_request": None, "artifact_paths": {},
            "metrics": {"duplicate_confirmation_write_count": 0},
            "deduplicated": True, "warnings": ["duplicate_response_reused"], "errors": [],
        }

    def _fail(self, run_id: str, exc: Exception) -> None:
        try:
            from campus_job_agent.llm import LLMProviderError, StructuredOutputError

            if isinstance(exc, StructuredOutputError):
                self._append_llm(
                    run_id,
                    [item.model_dump(mode="json") for item in exc.call_records],
                )
                error_type = (
                    "llm_unavailable"
                    if exc.error_type in {
                        "provider_error", "network_timeout", "rate_limited", "auth_required"
                    }
                    else "llm_invalid_output"
                )
                retryable = exc.retryable
            elif isinstance(exc, LLMProviderError):
                error_type, retryable = "llm_unavailable", True
            elif "checkpoint" in str(exc).casefold():
                error_type, retryable = "checkpoint_failure", True
            elif isinstance(exc, OSError):
                error_type, retryable = "storage_failure", True
            else:
                error_type, retryable = "internal_error", False
            self.runtime.artifact_writer.append_error(ErrorEvent(
                run_id=run_id, workflow="career_intent", error_type=error_type,
                message=str(exc), retryable=retryable,  # type: ignore[arg-type]
                recovery_hint="inspect the failed CareerIntent run and checkpoint",
            ))
            self.runtime.artifact_writer.finish_run(
                run_id, status="failed", next_action="inspect.run", reason_codes=[error_type]
            )
        except Exception:
            pass


def _pending_request(result: dict[str, Any]) -> dict[str, Any] | None:
    pending = result.get("pending_interaction")
    if isinstance(pending, dict):
        return pending
    interrupts = result.get("__interrupt__", [])
    if interrupts:
        value = getattr(interrupts[0], "value", None)
        return dict(value) if isinstance(value, dict) else None
    return None


def _hash(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


__all__ = ["IntentApplicationService", "IntentApplicationError"]
