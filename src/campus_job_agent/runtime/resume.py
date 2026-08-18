"""ResumeEvidenceGraph application boundary for the production CLI."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from campus_job_agent.runtime.models import ArtifactEntry, ErrorEvent, LLMCallReceipt, ObjectRef, RunEvent
from campus_job_agent.schemas import ResumeReviewResponse, resume_response_hash
from campus_job_agent.workflows.resume_evidence import create_resume_evidence_state

if TYPE_CHECKING:
    from campus_job_agent.runtime.factory import Runtime


class ResumeApplicationError(RuntimeError):
    error_type = "contract_violation"


class ResumeApplicationService:
    def __init__(self, runtime: "Runtime") -> None:
        self.runtime = runtime

    def import_pdf(
        self, *, session_id: str, candidate_id: str, input_path: str,
        reparse: bool = False,
    ) -> dict[str, Any]:
        session = self.runtime.session_service.status(session_id)
        if session.pending_request is not None or session.status != "active":
            raise ResumeApplicationError("resume import requires an active session without a pending request")
        if session.current_stage != "candidate":
            raise ResumeApplicationError("resume import requires the candidate session stage")
        path = Path(input_path).expanduser().resolve()
        if path.suffix.lower() != ".pdf" or not path.is_file():
            raise ResumeApplicationError("resume input must be an existing PDF")
        if not candidate_id.strip():
            raise ResumeApplicationError("candidate_id is required")
        thread_id = f"thread-{uuid4()}"
        manifest = self.runtime.artifact_writer.initialize_run(
            session_id=session.session_id, thread_id=thread_id,
            workflow="resume_evidence", command="resume.import",
            parent_run_id=session.latest_run_id,
            input_refs={
                "candidate_id": candidate_id, "file_extension": path.suffix.lower(),
                "file_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "reparse": reparse,
            },
        )
        state = create_resume_evidence_state(
            run_id=manifest.run_id, session_id=session.session_id,
            thread_id=thread_id, user_id=session.user_id,
            candidate_id=candidate_id, input_path=str(path),
            allowed_path_roots=[str(path.parent)],
            force_reparse=reparse,
        )
        try:
            with self.runtime.open_workflow("resume") as workflow:
                result = workflow.invoke(state)
            return self._finish(session, manifest, result, 0, 0)
        except Exception as exc:
            self._fail(session, manifest.run_id, exc)
            raise

    def resume(
        self, *, session_id: str, response: ResumeReviewResponse
    ) -> dict[str, Any]:
        session = self.runtime.session_service.status(session_id)
        existing = self.runtime.evidence_repository.get_resume_review_receipt(response.response_id)
        if existing is not None:
            if (
                self.runtime.evidence_repository.get_resume_review_payload_hash(
                    response.response_id
                )
                != resume_response_hash(response)
            ):
                raise ResumeApplicationError(
                    "idempotency_conflict: response_id payload differs"
                )
            return self._duplicate_payload(session)
        if not session.latest_run_id or not session.pending_request:
            raise ResumeApplicationError("session has no pending resume review")
        parent = self.runtime.artifact_writer.load_manifest(session.latest_run_id)
        if parent.workflow != "resume_evidence":
            raise ResumeApplicationError("legacy_session_incompatible")
        manifest = self.runtime.artifact_writer.initialize_run(
            session_id=session.session_id, thread_id=parent.thread_id,
            workflow="resume_evidence", command="resume.resume",
            parent_run_id=parent.run_id,
            input_refs={
                "response_id": response.response_id,
                "request_id": response.request_id, "action": response.action,
            },
        )
        try:
            with self.runtime.open_workflow("resume") as workflow:
                previous = dict(workflow.get_state(parent.thread_id).values or {})
                result = workflow.resume(
                    thread_id=parent.thread_id, response=response, run_id=manifest.run_id
                )
            return self._finish(
                session, manifest, result,
                len(previous.get("trace", [])), len(previous.get("llm_calls", [])),
            )
        except Exception as exc:
            self._fail(session, manifest.run_id, exc)
            raise

    def review_view(self, request: dict[str, Any]) -> dict[str, Any]:
        draft = self.runtime.evidence_repository.get_resume_draft(str(request.get("draft_id", "")))
        if draft is None:
            raise ResumeApplicationError("resume draft not found")
        section = str(request["section"])
        value: Any = getattr(draft.data, section)
        if request.get("target_kind") == "record":
            value = next(
                item for item in value if item.record_id == request.get("record_id")
            )
        if hasattr(value, "model_dump"):
            value = value.model_dump(mode="json")
        elif isinstance(value, list):
            value = [item.model_dump(mode="json") for item in value]
        prefix = f"/{section}"
        if request.get("target_kind") == "record":
            record_index = next(
                index
                for index, item in enumerate(getattr(draft.data, section))
                if item.record_id == request.get("record_id")
            )
            prefix = f"/{section}/{record_index}/"
        refs = [
            ref
            for path, values in draft.field_sources.items()
            if path == prefix or path.startswith(prefix)
            for ref in values
        ]
        pages = sorted({ref.page_number for ref in refs})
        excerpts = []
        seen: set[tuple[str, int | None, int | None]] = set()
        for ref in refs:
            key = (ref.fragment_id, ref.start_offset, ref.end_offset)
            if key in seen:
                continue
            seen.add(key)
            fragment = self.runtime.evidence_repository.get_fragment(ref.fragment_id)
            if fragment is not None:
                start = ref.start_offset
                end = ref.end_offset
                if (
                    start is None or end is None or start < 0
                    or end <= start or end > len(fragment.text)
                ):
                    excerpt = " ".join(fragment.text.split())[:240]
                else:
                    context_start = max(0, start - 80)
                    context_end = min(len(fragment.text), end + 80)
                    excerpt = " ".join(
                        fragment.text[context_start:context_end].split()
                    )
                excerpts.append({
                    "page": ref.page_number,
                    "text": excerpt[:320],
                })
            if len(excerpts) >= 3:
                break
        return {
            "request": request, "section": section,
            "target_kind": request["target_kind"], "value": value,
            "source_pages": pages, "source_excerpts": excerpts,
            "draft_revision": draft.revision,
        }

    def _finish(
        self, session: Any, manifest: Any, result: dict[str, Any],
        trace_start: int, llm_start: int,
    ) -> dict[str, Any]:
        self._append_events(manifest, result.get("trace", [])[trace_start:])
        self._append_llm(manifest.run_id, result.get("llm_calls", [])[llm_start:])
        pending = _pending_request(result)
        status = "interrupted" if pending else str(result.get("status") or "failed")
        evidence_id = result.get("resume_evidence_id")
        next_action = (
            "resume.resume" if status == "interrupted"
            else "candidate.build" if status == "completed"
            else "resume.import" if status == "cancelled" else "inspect.run"
        )
        session = self._update_session(
            session, manifest.run_id, status, pending, evidence_id
        )
        if result.get("artifact_id"):
            self.runtime.artifact_writer.add_artifact(manifest.run_id, ArtifactEntry(
                logical_type="resume_pdf", object_id=str(result["artifact_id"]),
                locator=f"repository://evidence/artifacts/{result['artifact_id']}",
                owner=session.user_id, sensitivity="private",
            ))
        if evidence_id:
            self.runtime.artifact_writer.add_artifact(manifest.run_id, ArtifactEntry(
                logical_type="resume_evidence_snapshot", object_id=str(evidence_id),
                locator=f"repository://resume-evidence/{evidence_id}",
                owner=session.user_id, sensitivity="private",
            ))
        safe_state = {
            "status": status, "artifact_id": result.get("artifact_id"),
            "fragment_ids": result.get("extraction_fragment_ids", []),
            "draft_id": result.get("draft_id"),
            "resume_evidence_id": evidence_id,
            "pending_request": pending,
        }
        personal_values: list[str] = []
        draft = self.runtime.evidence_repository.get_resume_draft(
            str(result.get("draft_id") or "")
        )
        if draft is not None:
            personal_values = [
                value for value in draft.data.personal_information.model_dump().values()
                if isinstance(value, str) and value.strip()
            ]
        candidate_id = (
            draft.candidate_id if draft is not None
            else str(result.get("candidate_id") or "")
        )
        metrics = {
            "pre_confirmation_claim_count": max(
                0,
                len(self.runtime.evidence_repository.list_claims(
                    candidate_id
                )) - int(
                    draft.candidate_claim_count_at_import
                    if draft is not None
                    else result.get("candidate_claim_count_at_start", 0)
                ),
            ),
            "duplicate_review_write_count": 0,
            "unconfirmed_field_persisted_count": (
                sum(
                    1
                    for path, value in _resume_leaf_values(draft).items()
                    if value not in (None, "")
                    and draft.section_statuses.get(path.split("/", 2)[1]) == "pending"
                )
                if evidence_id and draft is not None else 0
            ),
            "pii_leak_count": _pii_leak_count(safe_state, personal_values),
            "resume_snapshot_created": int(bool(evidence_id)),
        }
        self.runtime.artifact_writer.write_state(
            manifest.run_id, {**safe_state, "metrics": metrics}
        )
        self.runtime.artifact_writer.write_report(
            manifest.run_id,
            "\n".join([
                "# Resume Evidence Workflow", "", f"- status: `{status}`",
                f"- next action: `{next_action}`",
                f"- resume evidence: `{evidence_id or 'none'}`",
            ]) + "\n",
        )
        terminal = self.runtime.artifact_writer.finish_run(
            manifest.run_id,
            status=status if status in {"completed", "interrupted", "cancelled", "failed"} else "failed",
            next_action=next_action,
            output_refs={"resume_evidence_id": evidence_id, "draft_id": result.get("draft_id")},
            pending_request_id=(pending or {}).get("request_id"),
        )
        return {
            "schema_version": "v0.7.1", "command": manifest.command,
            "run_id": manifest.run_id, "session_id": session.session_id,
            "session_version": session.session_version,
            "thread_id": manifest.thread_id, "status": status,
            "next_action": next_action,
            "output_refs": {
                "resume_evidence_id": evidence_id,
                "draft_id": result.get("draft_id"),
            },
            "pending_request": pending, "artifact_paths": terminal.artifact_paths,
            "metrics": metrics, "deduplicated": False,
            "warnings": [], "errors": result.get("errors", []),
        }

    def _update_session(
        self, session: Any, run_id: str, status: str,
        pending: dict[str, Any] | None, evidence_id: str | None,
    ) -> Any:
        if status == "interrupted":
            return self.runtime.session_repository.update_navigation(
                session.session_id, expected_version=session.session_version,
                operation="resume_evidence_interrupted", status="interrupted",
                current_stage="candidate", pending_request=str(pending["request_id"]),
                latest_run_id=run_id,
            )
        if status == "completed" and evidence_id:
            snapshot = self.runtime.evidence_repository.get_resume_evidence_snapshot(evidence_id)
            if snapshot is None:
                raise ResumeApplicationError("ResumeEvidence snapshot was not persisted")
            previous = session.current_refs.get("resume_evidence_snapshot_id")
            predecessors = [str(previous)] if previous and str(previous) != evidence_id else []
            self.runtime.session_repository.register_ref(ObjectRef(
                object_id=evidence_id, object_type="resume_evidence_snapshot",
                owner_id=session.user_id, schema_version=snapshot.schema_version,
                predecessor_ids=predecessors,
                successor_of=predecessors[0] if predecessors else None,
                canonical_hash=_hash(snapshot.data.model_dump(mode="json")),
            ))
            session = self.runtime.session_repository.set_current_ref(
                session.session_id, key="resume_evidence_snapshot_id",
                object_id=evidence_id, expected_version=session.session_version,
            )
            return self.runtime.session_repository.update_navigation(
                session.session_id, expected_version=session.session_version,
                operation="resume_evidence_completed", status="active",
                current_stage="candidate", pending_request=None,
                latest_run_id=run_id,
            )
        return self.runtime.session_repository.update_navigation(
            session.session_id, expected_version=session.session_version,
            operation=f"resume_evidence_{status}",
            status="cancelled" if status == "cancelled" else "failed",
            pending_request=None, latest_run_id=run_id,
        )

    def _append_events(self, manifest: Any, traces: list[dict[str, Any]]) -> None:
        for item in traces:
            node = str(item.get("node", "unknown"))
            self.runtime.artifact_writer.append_event(RunEvent(
                run_id=manifest.run_id, session_id=manifest.session_id,
                thread_id=manifest.thread_id, event_type="node_finished",
                workflow="resume_evidence", node=node,
                status="interrupted" if node == "plan_review" else "completed",
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
                retry_count=int(call.get("retry_count", 0)),
                cache_hit=bool(call.get("cache_hit")),
                latency_ms=int(call.get("duration_ms", 0)),
                validation_result=str(call.get("error_type") or "accepted"),
                fallback=call.get("fallback_reason"), integration=call.get("integration"),
                requested_strategy=call.get("requested_strategy"),
                effective_strategy=call.get("effective_strategy"),
                capabilities=call.get("capabilities"),
            ))

    def _duplicate_payload(self, session: Any) -> dict[str, Any]:
        evidence_id = session.current_refs.get("resume_evidence_snapshot_id")
        return {
            "schema_version": "v0.7.1", "command": "resume.resume",
            "run_id": session.latest_run_id, "session_id": session.session_id,
            "session_version": session.session_version,
            "status": "completed" if evidence_id else "interrupted",
            "next_action": "candidate.build" if evidence_id else "resume.resume",
            "output_refs": {"resume_evidence_id": evidence_id},
            "pending_request": None, "artifact_paths": {},
            "metrics": {"duplicate_review_write_count": 0},
            "deduplicated": True, "warnings": ["duplicate_response_reused"],
            "errors": [],
        }

    def _fail(self, session: Any, run_id: str, exc: Exception) -> None:
        calls = [
            item.model_dump(mode="json")
            for item in getattr(exc, "call_records", [])
            if hasattr(item, "model_dump")
        ]
        try:
            self._append_llm(run_id, calls)
        except Exception:
            pass
        source_error_type = str(
            getattr(exc, "error_type", "internal_error")
        )
        error_type = _resume_error_type(source_error_type)
        try:
            self.runtime.artifact_writer.append_error(ErrorEvent(
                run_id=run_id, workflow="resume_evidence",
                error_type=error_type,
                message=_resume_failure_message(error_type),
                retryable=bool(getattr(exc, "retryable", False)),
                recovery_hint="inspect the ResumeEvidence run and checkpoint",
            ))
            self.runtime.artifact_writer.finish_run(
                run_id, status="failed", next_action="inspect.run",
                reason_codes=[error_type],
            )
        except Exception:
            pass
        try:
            current = self.runtime.session_service.status(session.session_id)
            self.runtime.session_repository.update_navigation(
                current.session_id,
                expected_version=current.session_version,
                operation="resume_evidence_failed",
                status=current.status,
                current_stage=current.current_stage,
                pending_request=current.pending_request,
                latest_run_id=run_id,
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


def _resume_error_type(value: str) -> str:
    return {
        "unsupported_input": "invalid_input",
        "validation_error": "contract_violation",
        "schema_validation_error": "llm_invalid_output",
        "json_parse_error": "llm_invalid_output",
        "unsupported_capability": "llm_unavailable",
        "provider_error": "llm_unavailable",
        "network_timeout": "llm_unavailable",
        "auth_required": "auth_required",
        "rate_limited": "rate_limited",
        "storage_error": "storage_failure",
    }.get(value, value if value in {
        "invalid_input", "contract_violation", "permission_denied", "not_found",
        "stale_input", "auth_required", "rate_limited", "source_changed",
        "adapter_required", "llm_invalid_output", "llm_unavailable",
        "storage_failure", "checkpoint_failure", "budget_exhausted",
        "internal_error",
    } else "internal_error")


def _resume_failure_message(error_type: str) -> str:
    return {
        "llm_invalid_output": "model returned invalid structured resume output",
        "llm_unavailable": "configured model provider is unavailable",
        "auth_required": "configured model provider authorization failed",
        "rate_limited": "configured model provider rate limit exceeded",
        "invalid_input": "resume input could not be processed",
        "storage_failure": "resume evidence storage failed",
        "checkpoint_failure": "resume workflow checkpoint failed",
    }.get(error_type, "resume evidence workflow failed")


def _hash(value: Any) -> str:
    return "sha256:" + hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


def _pii_leak_count(value: Any, personal_values: list[str]) -> int:
    serialized = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return sum(serialized.count(personal) for personal in set(personal_values))


def _resume_leaf_values(draft: Any) -> dict[str, Any]:
    from campus_job_agent.workflows.resume_evidence.policy import leaf_values

    return leaf_values(draft.data)


__all__ = ["ResumeApplicationError", "ResumeApplicationService"]
