"""CandidateProfileGraph application boundary for the production CLI."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, TYPE_CHECKING
from uuid import uuid4

from campus_job_agent.evidence import CandidatePredicateError, parse_candidate_predicate
from campus_job_agent.runtime.models import (
    ArtifactEntry,
    ErrorEvent,
    LLMCallReceipt,
    ObjectRef,
    RunEvent,
)
from campus_job_agent.schemas import HumanInteractionResponse
from campus_job_agent.tools.candidate_profile import canonical_response_payload
from campus_job_agent.workflows.candidate_profile import create_candidate_profile_state

if TYPE_CHECKING:
    from campus_job_agent.runtime.factory import Runtime


class CandidateApplicationError(RuntimeError):
    error_type = "contract_violation"


class CandidateApplicationService:
    def __init__(self, runtime: "Runtime") -> None:
        self.runtime = runtime

    def build(
        self,
        *,
        session_id: str,
        candidate_id: str,
        input_paths: list[str],
        budgets: dict[str, int] | None = None,
    ) -> dict[str, Any]:
        session = self.runtime.session_service.status(session_id)
        if not candidate_id.strip():
            raise CandidateApplicationError("candidate_id is required")
        resolved_paths = [str(Path(value).expanduser().resolve()) for value in input_paths]
        missing = [value for value in resolved_paths if not Path(value).is_file()]
        if missing:
            raise CandidateApplicationError(f"candidate input does not exist: {missing[0]}")
        thread_id = f"thread-{uuid4()}"
        manifest = self.runtime.artifact_writer.initialize_run(
            session_id=session.session_id,
            thread_id=thread_id,
            workflow="candidate_profile",
            command="candidate.build",
            parent_run_id=session.latest_run_id,
            input_refs={
                "candidate_id": candidate_id,
                "material_count": len(resolved_paths),
                "material_names": [Path(value).name for value in resolved_paths],
            },
        )
        state = create_candidate_profile_state(
            run_id=manifest.run_id,
            thread_id=thread_id,
            user_id=session.user_id,
            candidate_id=candidate_id,
            input_paths=resolved_paths,
            allowed_path_roots=[str(Path(value).parent) for value in resolved_paths] or [str(Path.cwd())],
            budgets=budgets,
        )
        try:
            with self.runtime.open_workflow("candidate") as workflow:
                result = workflow.invoke(state)
            return self._finish(
                session=session, manifest=manifest, result=result,
                trace_start=0, llm_start=0,
            )
        except Exception as exc:
            self._fail(manifest.run_id, exc)
            raise

    def resume(
        self, *, session_id: str, response: HumanInteractionResponse
    ) -> dict[str, Any]:
        session = self.runtime.session_service.status(session_id)
        existing = self.runtime.evidence_repository.get_response_receipt(response.response_id)
        if existing is not None:
            payload_hash = hashlib.sha256(canonical_response_payload(response)).hexdigest()
            if existing.get("payload_hash") != payload_hash:
                raise CandidateApplicationError(
                    "idempotency_conflict: response_id has a different payload"
                )
            return self._duplicate_payload(session)
        if not session.latest_run_id or not session.pending_request:
            raise CandidateApplicationError("session has no pending Candidate interaction")
        missing_uploads = [
            value for value in response.file_paths if not Path(value).is_file()
        ]
        if missing_uploads:
            raise CandidateApplicationError(
                f"candidate upload does not exist: {missing_uploads[0]}"
            )
        parent = self.runtime.artifact_writer.load_manifest(session.latest_run_id)
        thread_id = parent.thread_id
        manifest = self.runtime.artifact_writer.initialize_run(
            session_id=session.session_id,
            thread_id=thread_id,
            workflow="candidate_profile",
            command="candidate.resume",
            parent_run_id=parent.run_id,
            input_refs={
                "candidate_response_id": response.response_id,
                "request_id": response.request_id,
                "action": response.action,
            },
        )
        try:
            with self.runtime.open_workflow("candidate") as workflow:
                previous = workflow.get_state(thread_id)
                previous_values = dict(previous.values or {})
                workflow.authorize_upload_paths(thread_id, response.file_paths)
                result = workflow.resume(
                    thread_id=thread_id, response=response, run_id=manifest.run_id
                )
            return self._finish(
                session=session, manifest=manifest, result=result,
                trace_start=len(previous_values.get("trace", [])),
                llm_start=len(previous_values.get("llm_calls", [])),
            )
        except Exception as exc:
            self._fail(manifest.run_id, exc)
            raise

    def _finish(
        self,
        *,
        session: Any,
        manifest: Any,
        result: dict[str, Any],
        trace_start: int,
        llm_start: int,
    ) -> dict[str, Any]:
        self._append_events(manifest, result.get("trace", [])[trace_start:])
        self._append_llm(manifest.run_id, result.get("llm_calls", [])[llm_start:])
        self._append_errors(manifest.run_id, result.get("errors", []))
        status = str(result.get("status") or "failed")
        pending = _pending_request(result)
        snapshot_id = result.get("candidate_profile_snapshot_id")
        metrics = self._metrics(manifest.run_id, result, snapshot_id)
        output_refs = {
            "candidate_profile_snapshot_id": snapshot_id,
            "claim_ids": list(result.get("claim_ids", [])),
        }
        next_action = (
            "candidate.resume" if status == "interrupted"
            else "intent.create" if status in {"completed", "completed_with_unknowns"}
            else "candidate.build" if status == "cancelled"
            else "inspect.run"
        )
        session = self._update_session(
            session, manifest.run_id, status, pending, snapshot_id
        )
        self._index_objects(manifest.run_id, result, snapshot_id, session.user_id)
        safe_state = {
            "candidate_id": result.get("candidate_id"),
            "status": status,
            "active_artifact_ids": result.get("active_artifact_ids", []),
            "fragment_ids": result.get("fragment_ids", []),
            "fragment_processing": result.get("fragment_processing", {}),
            "claim_ids": result.get("claim_ids", []),
            "validation_receipts": result.get("validation_receipts", []),
            "candidate_profile_snapshot_id": snapshot_id,
            "pending_request": pending,
            "metrics": metrics,
        }
        self.runtime.artifact_writer.write_state(manifest.run_id, safe_state)
        self.runtime.artifact_writer.write_report(
            manifest.run_id,
            _report(status, next_action, snapshot_id, metrics),
        )
        terminal = self.runtime.artifact_writer.finish_run(
            manifest.run_id,
            status=status if status in {
                "completed", "completed_with_unknowns", "interrupted", "cancelled", "failed"
            } else "failed",
            next_action=next_action,
            output_refs=output_refs,
            pending_request_id=pending.get("request_id") if pending else None,
            reason_codes=_reason_codes(result),
        )
        return {
            "schema_version": "v0.7.1",
            "command": manifest.command,
            "run_id": manifest.run_id,
            "session_id": session.session_id,
            "session_version": session.session_version,
            "thread_id": manifest.thread_id,
            "status": status,
            "next_action": next_action,
            "output_refs": output_refs,
            "pending_request": pending,
            "artifact_paths": terminal.artifact_paths,
            "metrics": metrics,
            "deduplicated": False,
            "warnings": [],
            "errors": result.get("errors", []),
        }

    def _update_session(
        self, session: Any, run_id: str, status: str,
        pending: dict[str, Any] | None, snapshot_id: str | None,
    ) -> Any:
        if status == "interrupted":
            request_id = str((pending or {}).get("request_id", ""))
            return self.runtime.session_repository.update_navigation(
                session.session_id, expected_version=session.session_version,
                operation="candidate_interrupted", status="interrupted",
                pending_request=request_id, latest_run_id=run_id,
            )
        if status in {"completed", "completed_with_unknowns"} and snapshot_id:
            snapshot = self.runtime.profile_repository.get_profile(snapshot_id)
            if snapshot is None:
                raise CandidateApplicationError("Candidate snapshot was not persisted")
            previous = session.current_refs.get("candidate_profile_snapshot_id")
            predecessor_ids = [str(previous)] if previous and str(previous) != snapshot_id else []
            self.runtime.session_repository.register_ref(ObjectRef(
                object_id=snapshot_id,
                object_type="candidate_profile_snapshot",
                owner_id=session.user_id,
                schema_version=snapshot.schema_version,
                predecessor_ids=predecessor_ids,
                successor_of=predecessor_ids[0] if predecessor_ids else None,
                canonical_hash=_hash(snapshot.profile_data),
            ))
            session = self.runtime.session_repository.set_current_ref(
                session.session_id,
                key="candidate_profile_snapshot_id",
                object_id=snapshot_id,
                expected_version=session.session_version,
            )
            return self.runtime.session_repository.update_navigation(
                session.session_id, expected_version=session.session_version,
                operation="candidate_completed", status="active",
                current_stage="intent", pending_request=None, latest_run_id=run_id,
            )
        return self.runtime.session_repository.update_navigation(
            session.session_id, expected_version=session.session_version,
            operation=f"candidate_{status}",
            status="cancelled" if status == "cancelled" else "failed",
            pending_request=None, latest_run_id=run_id,
        )

    def _append_events(self, manifest: Any, traces: list[dict[str, Any]]) -> None:
        for trace in traces:
            node = str(trace.get("node", "unknown"))
            self.runtime.artifact_writer.append_event(RunEvent(
                run_id=manifest.run_id, session_id=manifest.session_id,
                thread_id=manifest.thread_id, event_type="node_started",
                workflow="candidate_profile", node=node, status="running",
            ))
            status = str(trace.get("status", "success"))
            event_status = (
                "failed" if status == "failed"
                else status if status in {"interrupted", "cancelled", "completed_with_unknowns"}
                else "completed"
            )
            self.runtime.artifact_writer.append_event(RunEvent(
                run_id=manifest.run_id, session_id=manifest.session_id,
                thread_id=manifest.thread_id, event_type="node_finished",
                workflow="candidate_profile", node=node, status=event_status,
                output_refs={
                    "snapshot_id": trace.get("snapshot_id"),
                    "request_id": trace.get("request_id"),
                    "response_id": trace.get("response_id"),
                },
                counts=trace.get("output_counts", {}),
                route=trace.get("route"),
                duration_ms=max(1, int(trace.get("duration_ms") or 1)),
                reason_codes=trace.get("reason_codes", []),
            ))

    def _append_llm(self, run_id: str, calls: list[dict[str, Any]]) -> None:
        for call in calls:
            usage = call.get("usage")
            token_usage = (
                {str(key): int(value) for key, value in usage.items() if isinstance(value, int)}
                if isinstance(usage, dict) else None
            )
            self.runtime.artifact_writer.append_llm_call(LLMCallReceipt(
                run_id=run_id,
                provider=str(call.get("provider", "unknown")),
                model=str(call.get("model", "unknown")),
                prompt_version=str(call.get("prompt_version", "unknown")),
                schema_version_used=str(call.get("schema_version", "unknown")),
                request_hash=str(call.get("cache_key", "unknown")),
                status=(
                    "fallback" if call.get("status") == "success" and call.get("fallback_reason")
                    else "success" if call.get("status") == "success" else "failed"
                ),
                retry_count=int(call.get("retry_count", 0)),
                cache_hit=bool(call.get("cache_hit")),
                token_usage=token_usage,
                latency_ms=int(call.get("duration_ms", 0)),
                validation_result=str(call.get("error_type") or "accepted"),
                fallback=call.get("fallback_reason"),
                integration=call.get("integration"),
                requested_strategy=call.get("requested_strategy"),
                effective_strategy=call.get("effective_strategy"),
                capabilities=call.get("capabilities"),
            ))

    def _append_errors(self, run_id: str, errors: list[dict[str, Any]]) -> None:
        for item in errors:
            error_type = _error_type(str(item.get("error_type", "internal_error")))
            self.runtime.artifact_writer.append_error(ErrorEvent(
                run_id=run_id, workflow="candidate_profile",
                node=item.get("node"), error_type=error_type,
                message=str(item.get("message", error_type)),
                retryable=bool(item.get("retryable")),
                recovery_hint=(
                    "inspect validation receipts and retry with corrected evidence"
                    if error_type in {"contract_violation", "llm_invalid_output"}
                    else "inspect the run and retry from the recorded next action"
                ),
            ))

    def _index_objects(
        self, run_id: str, result: dict[str, Any], snapshot_id: str | None, owner: str
    ) -> None:
        for artifact_id in result.get("active_artifact_ids", []):
            self.runtime.artifact_writer.add_artifact(run_id, ArtifactEntry(
                logical_type="evidence_artifact", object_id=str(artifact_id),
                locator=f"repository://evidence/artifacts/{artifact_id}",
                owner=owner, sensitivity="private",
            ))
        for claim_id in result.get("claim_ids", []):
            self.runtime.artifact_writer.add_artifact(run_id, ArtifactEntry(
                logical_type="candidate_claim", object_id=str(claim_id),
                locator=f"repository://evidence/claims/{claim_id}",
                owner=owner,
            ))
        if snapshot_id:
            self.runtime.artifact_writer.add_artifact(run_id, ArtifactEntry(
                logical_type="candidate_profile_snapshot", object_id=str(snapshot_id),
                locator=f"repository://profiles/{snapshot_id}", owner=owner,
            ))

    def _metrics(
        self, run_id: str, result: dict[str, Any], snapshot_id: str | None
    ) -> dict[str, int | float]:
        receipts = self.runtime.evidence_repository.list_validation_receipts(run_id=run_id)
        accepted = [item for item in receipts if item.status in {"accepted", "duplicate"}]
        expected_receipts = [
            item for item in result.get("validation_receipts", [])
            if str(item.get("run_id")) == run_id
        ]
        supported = 0
        for item in accepted:
            try:
                parse_candidate_predicate(str(item.predicate), allow_legacy=False)
            except CandidatePredicateError:
                continue
            supported += 1
        snapshot = self.runtime.profile_repository.get_profile(snapshot_id) if snapshot_id else None
        projected = set(snapshot.supporting_claim_ids if snapshot else [])
        active_claims = self.runtime.evidence_repository.list_active_claims(
            str(result.get("candidate_id", ""))
        )
        accepted_ids = {item.claim_id for item in active_claims}
        reference_valid = sum(
            bool(item.persisted_claim_id)
            and self.runtime.evidence_repository.get_claim(str(item.persisted_claim_id)) is not None
            and all(
                self.runtime.evidence_repository.get_fragment(fragment_id) is not None
                for fragment_id in item.fragment_ids
            )
            for item in accepted
        )
        artifacts = [
            self.runtime.evidence_repository.get_artifact(value)
            for value in result.get("active_artifact_ids", [])
        ]
        fragments = [
            self.runtime.evidence_repository.get_fragment(value)
            for value in result.get("fragment_ids", [])
        ]
        return {
            "artifact_archive_success_rate": _rate(
                sum(item is not None for item in artifacts), len(artifacts)
            ),
            "fragment_locator_trace_rate": _rate(sum(item is not None and bool(item.locator) for item in fragments), len(fragments)),
            "model_item_receipt_rate": _rate(len(receipts), len(expected_receipts)),
            "accepted_claim_reference_valid_rate": _rate(reference_valid, len(accepted)),
            "accepted_candidate_predicate_supported_rate": _rate(supported, len(accepted)),
            "accepted_claim_projection_rate": _rate(len(accepted_ids & projected), len(accepted_ids)),
            "rejected_claim_reason_coverage_rate": _rate(
                sum(bool(item.reason_codes) for item in receipts if item.status == "rejected"),
                sum(item.status == "rejected" for item in receipts),
            ),
            "candidate_snapshot_trace_rate": 1.0 if snapshot is not None else 0.0,
            "duplicate_resume_write_count": 0,
            "silent_unprojected_active_claim_count": len(accepted_ids - projected),
        }

    def _duplicate_payload(self, session: Any) -> dict[str, Any]:
        snapshot_id = session.current_refs.get("candidate_profile_snapshot_id")
        return {
            "schema_version": "v0.7.1", "command": "candidate.resume",
            "run_id": session.latest_run_id, "session_id": session.session_id,
            "session_version": session.session_version, "status": "completed",
            "next_action": "intent.create",
            "output_refs": {"candidate_profile_snapshot_id": snapshot_id},
            "pending_request": None, "artifact_paths": {},
            "metrics": {"duplicate_resume_write_count": 0},
            "deduplicated": True, "warnings": ["duplicate_response_reused"], "errors": [],
        }

    def _fail(self, run_id: str, exc: Exception) -> None:
        try:
            self.runtime.artifact_writer.append_error(ErrorEvent(
                run_id=run_id, workflow="candidate_profile",
                error_type="internal_error", message=str(exc), retryable=False,
                recovery_hint="inspect the failed run and Candidate checkpoint",
            ))
            self.runtime.artifact_writer.finish_run(
                run_id, status="failed", next_action="inspect.run",
                reason_codes=["internal_error"],
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


def _error_type(value: str) -> str:
    return {
        "validation_error": "contract_violation",
        "idempotency_conflict": "stale_input",
        "llm_output_error": "llm_invalid_output",
        "storage_error": "storage_failure",
        "checkpoint_error": "checkpoint_failure",
        "budget_exhausted": "budget_exhausted",
        "permission_denied": "permission_denied",
    }.get(value, "internal_error")


def _reason_codes(result: dict[str, Any]) -> list[str]:
    return list(dict.fromkeys(
        str(item.get("error_type"))
        for item in result.get("errors", [])
        if item.get("error_type")
    ))


def _rate(numerator: int, denominator: int) -> float:
    return 1.0 if denominator == 0 else round(numerator / denominator, 6)


def _hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _report(
    status: str, next_action: str, snapshot_id: str | None,
    metrics: dict[str, int | float],
) -> str:
    lines = [
        "# Candidate Workflow", "", f"- status: `{status}`",
        f"- next action: `{next_action}`", f"- snapshot: `{snapshot_id or 'none'}`",
        "", "## Metrics", "",
    ]
    lines.extend(f"- {key}: `{value}`" for key, value in sorted(metrics.items()))
    return "\n".join(lines) + "\n"
