"""WP3.1 RoleProfileGraph application boundary for the production CLI."""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from campus_job_agent.runtime.models import (
    ArtifactEntry,
    ErrorEvent,
    LLMCallReceipt,
    ObjectRef,
    RunEvent,
)
from campus_job_agent.schemas import (
    CommunityEvidenceSegment,
    CompanyReputationProfile,
    JobDemandProfile,
    JobReputationProfile,
    RoleAuthorizationResponseReceipt,
    RoleFamilyDemandProfile,
    RoleIntelligenceBundle,
    SearchScope,
    SourceDocument,
)
from campus_job_agent.schemas.role_intelligence import (
    INTERVIEW_SEGMENT_TYPES,
    REPUTATION_SEGMENT_TYPES,
)
from campus_job_agent.workflows.role_profile import create_role_profile_state

if TYPE_CHECKING:
    from campus_job_agent.runtime.factory import Runtime


class RoleApplicationError(RuntimeError):
    error_type = "contract_violation"


class RoleApplicationService:
    def __init__(self, runtime: "Runtime") -> None:
        self.runtime = runtime

    def research(self, *, session_id: str, handoff_id: str) -> dict[str, Any]:
        session = self.runtime.session_service.status(session_id)
        if session.status != "active" or session.pending_request is not None:
            raise RoleApplicationError(
                "role research requires an active session without a pending request"
            )
        if session.current_stage != "role":
            raise RoleApplicationError("role research requires the role session stage")
        if handoff_id not in session.pending_handoff_ids:
            raise RoleApplicationError("handoff is not pending for this session")
        handoff, scope = self._load_handoff(session, handoff_id)
        thread_id = f"thread-{uuid4()}"
        manifest = self.runtime.artifact_writer.initialize_run(
            session_id=session.session_id,
            thread_id=thread_id,
            workflow="role_profile",
            command="role.research",
            parent_run_id=session.latest_run_id,
            input_refs={
                "handoff_id": handoff.handoff_id,
                "career_intent_snapshot_id": handoff.required_input_refs.get(
                    "career_intent_snapshot_id"
                ),
                "search_scope_id": scope.scope_id,
            },
        )
        enabled = [
            source_id
            for source_id in (
                "zhaopin_jobs", "nowcoder_experience", "xiaohongshu_experience",
            )
            if self.runtime.source_adapter_registry.get(source_id) is not None
        ]
        state = create_role_profile_state(
            run_id=manifest.run_id,
            thread_id=thread_id,
            user_id=session.user_id,
            search_scope=scope,
            enabled_source_ids=enabled,
            source_capabilities=self.runtime.source_adapter_registry.capabilities(),
            credential_refs=self._default_credential_refs(enabled),
        )
        try:
            with self.runtime.open_workflow("role") as workflow:
                result = workflow.invoke(state)
            return self._finish(
                session=session,
                handoff_id=handoff_id,
                manifest=manifest,
                result=result,
                trace_start=0,
                llm_start=0,
            )
        except Exception as exc:
            self._fail(manifest.run_id, exc)
            raise

    def resume(
        self,
        *,
        session_id: str,
        action: str,
        response_id: str,
        credential_ref: str | None = None,
    ) -> dict[str, Any]:
        session = self.runtime.session_service.status(session_id)
        normalized_action = action.replace("-", "_")
        if normalized_action not in {"authorized", "skip_source", "cancel"}:
            raise RoleApplicationError("unsupported role resume action")
        receipt = self.runtime.role_repository.get(
            response_id, RoleAuthorizationResponseReceipt
        )
        payload_hash = _hash({
            "action": normalized_action,
            "credential_ref": credential_ref,
        })
        if receipt is not None:
            if receipt.user_id != session.user_id or receipt.payload_hash != payload_hash:
                raise RoleApplicationError("idempotency_conflict")
            return self._duplicate_payload(session, receipt)
        if not session.latest_run_id or not session.pending_request:
            raise RoleApplicationError("session has no pending role source authorization")
        parent = self.runtime.artifact_writer.load_manifest(session.latest_run_id)
        if parent.workflow != "role_profile":
            raise RoleApplicationError("legacy_session_incompatible")
        handoff_id = str(parent.input_refs.get("handoff_id") or "")
        if not handoff_id:
            raise RoleApplicationError("role run has no typed handoff input")
        manifest = self.runtime.artifact_writer.initialize_run(
            session_id=session.session_id,
            thread_id=parent.thread_id,
            workflow="role_profile",
            command="role.resume",
            parent_run_id=parent.run_id,
            input_refs={
                "handoff_id": handoff_id,
                "response_id": response_id,
                "action": normalized_action,
            },
        )
        try:
            with self.runtime.open_workflow("role") as workflow:
                previous = dict(workflow.get_state(parent.thread_id).values or {})
                if previous.get("workflow_version") != "wp3.1.1":
                    raise RoleApplicationError("legacy_session_incompatible")
                pending = previous.get("pending_interaction")
                if not isinstance(pending, dict):
                    raise RoleApplicationError(
                        "session has no pending role source authorization"
                    )
                response = {
                    "response_id": response_id,
                    "request_id": pending["request_id"],
                    "thread_id": parent.thread_id,
                    "user_id": session.user_id,
                    "source_id": pending["source_id"],
                    "action": normalized_action,
                }
                if normalized_action == "authorized":
                    source_id = str(pending["source_id"])
                    capability = self.runtime.source_adapter_registry.capabilities().get(
                        source_id, {}
                    )
                    requires_ref = capability.get("authorization_mode") == "credential_ref"
                    if requires_ref and not credential_ref:
                        raise RoleApplicationError(
                            "authorized action requires --credential-ref"
                        )
                    if credential_ref:
                        response["credential_ref"] = credential_ref
                elif credential_ref is not None:
                    raise RoleApplicationError(
                        "--credential-ref is only valid with authorized"
                    )
                result = workflow.resume(
                    thread_id=parent.thread_id, response=response
                )
            payload = self._finish(
                session=session,
                handoff_id=handoff_id,
                manifest=manifest,
                result=result,
                trace_start=len(previous.get("trace", [])),
                llm_start=len(previous.get("llm_calls", [])),
            )
            saved = RoleAuthorizationResponseReceipt(
                response_id=response_id,
                request_id=str(response["request_id"]),
                thread_id=parent.thread_id,
                user_id=session.user_id,
                source_id=str(response["source_id"]),
                action=normalized_action,  # type: ignore[arg-type]
                payload_hash=payload_hash,
                result_status=str(payload["status"]),
                role_intelligence_bundle_id=payload["output_refs"].get(
                    "role_intelligence_bundle_id"
                ),
            )
            self.runtime.role_repository.save(
                "role_authorization_response_receipt",
                saved,
                idempotency_key=f"role-authorization-response:{response_id}",
            )
            return payload
        except Exception as exc:
            self._fail(manifest.run_id, exc)
            raise

    def show(self, bundle_id: str) -> dict[str, Any]:
        bundle = self.runtime.role_repository.get(bundle_id, RoleIntelligenceBundle)
        if bundle is None:
            raise KeyError(f"RoleIntelligenceBundle not found: {bundle_id}")
        family = self.runtime.role_repository.get(
            bundle.role_family_demand_profile_id, RoleFamilyDemandProfile
        )
        jobs = [
            item
            for value in bundle.job_demand_profile_ids
            if (item := self.runtime.role_repository.get(value, JobDemandProfile)) is not None
        ]
        job_reputation = [
            item
            for value in bundle.job_reputation_profile_ids
            if (
                item := self.runtime.role_repository.get(
                    value, JobReputationProfile
                )
            ) is not None
        ]
        company_reputation = [
            item
            for value in bundle.company_reputation_profile_ids
            if (
                item := self.runtime.role_repository.get(
                    value, CompanyReputationProfile
                )
            ) is not None
        ]
        return {
            "bundle": bundle.model_dump(mode="json"),
            "role_family_demand": (
                family.model_dump(mode="json") if family is not None else None
            ),
            "job_demands": [item.model_dump(mode="json") for item in jobs],
            "job_reputation": [
                item.model_dump(mode="json") for item in job_reputation
            ],
            "company_reputation": [
                item.model_dump(mode="json") for item in company_reputation
            ],
        }

    def _load_handoff(self, session: Any, handoff_id: str):
        handoff = self.runtime.session_repository.get_handoff(
            handoff_id, user_id=session.user_id
        )
        if handoff.session_id != session.session_id:
            raise RoleApplicationError("handoff session mismatch")
        if handoff.handoff_type != "role_research_required":
            raise RoleApplicationError("role research requires role_research_required")
        if handoff.status not in {"pending", "failed_retryable"}:
            raise RoleApplicationError("handoff is not available for role research")
        scope_id = handoff.required_input_refs.get("search_scope_id")
        intent_id = handoff.required_input_refs.get("career_intent_snapshot_id")
        if not isinstance(scope_id, str) or not isinstance(intent_id, str):
            raise RoleApplicationError("handoff is missing typed SearchScope input")
        if session.current_refs.get("career_intent_snapshot_id") != intent_id:
            raise RoleApplicationError("handoff references a stale CareerIntent snapshot")
        scope = self.runtime.intent_repository.get(
            scope_id, SearchScope, owner_id=session.user_id
        )
        if scope is None or scope.career_intent_snapshot_id != intent_id:
            raise RoleApplicationError("handoff SearchScope is missing or stale")
        return handoff, scope

    def _default_credential_refs(self, enabled: list[str]) -> dict[str, str]:
        refs: dict[str, str] = {}
        for source_id in enabled:
            ref = f"local-secret://{source_id}/default"
            try:
                self.runtime.credential_resolver.validate_ref(
                    ref, source_id=source_id
                )
            except ValueError:
                continue
            refs[source_id] = ref
        return refs

    def _finish(
        self,
        *,
        session: Any,
        handoff_id: str,
        manifest: Any,
        result: dict[str, Any],
        trace_start: int,
        llm_start: int,
    ) -> dict[str, Any]:
        self._append_events(manifest, result.get("trace", [])[trace_start:])
        self._append_llm(manifest.run_id, result.get("llm_calls", [])[llm_start:])
        all_errors = [
            *result.get("errors", []),
            *result.get("recruitment_errors", []),
            *result.get("community_errors", []),
        ]
        self._append_errors(manifest.run_id, all_errors)
        pending = _pending_request(result)
        status = "interrupted" if pending else str(result.get("status") or "failed")
        bundle_id = result.get("role_intelligence_bundle_id")
        output_refs = {
            "role_intelligence_bundle_id": bundle_id,
            "job_demand_profile_ids": list(result.get("job_demand_profile_ids", [])),
            "role_family_demand_profile_id": result.get(
                "role_family_demand_profile_id"
            ),
            "job_reputation_profile_ids": list(
                result.get("job_reputation_profile_ids", [])
            ),
            "company_reputation_profile_ids": list(
                result.get("company_reputation_profile_ids", [])
            ),
        }
        session = self._update_session(
            session=session,
            run_id=manifest.run_id,
            handoff_id=handoff_id,
            status=status,
            pending=pending,
            bundle_id=bundle_id,
        )
        next_action = (
            "role.resume"
            if status == "interrupted"
            else "role.research"
            if status == "cancelled"
            else "role.research"
            if session.current_stage == "role" and session.pending_handoff_ids
            else "match.run"
            if status in {"completed", "completed_with_unknowns"} and bundle_id
            else "session.resume"
        )
        self._index_objects(manifest.run_id, session.user_id, result)
        metrics = self._metrics(result)
        safe_state = {
            "status": status,
            "handoff_id": handoff_id,
            "pending_request": pending,
            "output_refs": output_refs,
            "missing_sections": list(result.get("missing_sections", [])),
            "counts": {
                "recruitment_search_documents": len(
                    result.get("recruitment_search_document_ids", [])
                ),
                "recruitment_detail_documents": len(
                    result.get("recruitment_detail_document_ids", [])
                ),
                "community_search_documents": len(
                    result.get("community_search_document_ids", [])
                ),
                "community_detail_documents": len(
                    result.get("community_detail_document_ids", [])
                ),
                "community_segments": len(
                    result.get("community_evidence_segment_ids", [])
                ),
            },
            "metrics": metrics,
        }
        self.runtime.artifact_writer.write_state(manifest.run_id, safe_state)
        self.runtime.artifact_writer.write_report(
            manifest.run_id,
            "\n".join([
                "# WP3.1 Role Intelligence", "",
                f"- status: `{status}`",
                f"- next action: `{next_action}`",
                f"- bundle: `{bundle_id or 'none'}`",
                f"- missing sections: `{', '.join(result.get('missing_sections', [])) or 'none'}`",
                f"- job demand profiles: `{len(result.get('job_demand_profile_ids', []))}`",
                f"- interview segments: `{metrics['interview_segment_count']}`",
                f"- reputation segments: `{metrics['reputation_segment_count']}`",
            ]),
        )
        terminal = self.runtime.artifact_writer.finish_run(
            manifest.run_id,
            status=(
                status
                if status in {
                    "completed", "completed_with_unknowns", "interrupted",
                    "cancelled", "failed",
                }
                else "failed"
            ),
            next_action=next_action,
            output_refs=output_refs,
            pending_request_id=(pending or {}).get("request_id"),
            pending_handoff_ids=session.pending_handoff_ids,
            reason_codes=list(result.get("missing_sections", [])),
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
            "warnings": list(result.get("missing_sections", [])),
            "errors": [],
        }

    def _update_session(
        self,
        *,
        session: Any,
        run_id: str,
        handoff_id: str,
        status: str,
        pending: dict[str, Any] | None,
        bundle_id: str | None,
    ) -> Any:
        if status == "interrupted":
            return self.runtime.session_repository.update_navigation(
                session.session_id,
                expected_version=session.session_version,
                operation="role_interrupted",
                status="interrupted",
                current_stage="role",
                pending_request=str(pending["request_id"]),
                latest_run_id=run_id,
            )
        if status in {"completed", "completed_with_unknowns"} and bundle_id:
            bundle = self.runtime.role_repository.get(
                bundle_id, RoleIntelligenceBundle
            )
            if bundle is None:
                raise RoleApplicationError(
                    "RoleIntelligenceBundle was not persisted"
                )
            handoff = self.runtime.session_repository.get_handoff(
                handoff_id, user_id=session.user_id
            )
            previous = session.current_refs.get("role_intelligence_bundle_ids", [])
            previous_ids = list(previous) if isinstance(previous, list) else []
            origins = [
                str(item)
                for item in handoff.origin_object_refs.values()
                if isinstance(item, str)
            ]
            predecessors = list(dict.fromkeys([*previous_ids, *origins]))
            self.runtime.session_repository.register_ref(ObjectRef(
                object_id=bundle.bundle_id,
                object_type="role_intelligence_bundle",
                owner_id=session.user_id,
                schema_version=bundle.schema_version,
                predecessor_ids=predecessors,
                canonical_hash=_hash(bundle.model_dump(mode="json")),
            ))
            session = self.runtime.session_repository.set_current_ref(
                session.session_id,
                key="role_intelligence_bundle_ids",
                object_id=bundle.bundle_id,
                expected_version=session.session_version,
            )
            self.runtime.session_repository.resolve_handoff(
                handoff_id,
                resolved_refs={"role_intelligence_bundle_id": bundle.bundle_id},
                user_id=session.user_id,
            )
            remaining = [
                value for value in session.pending_handoff_ids if value != handoff_id
            ]
            return self.runtime.session_repository.update_navigation(
                session.session_id,
                expected_version=session.session_version,
                operation="role_completed",
                status="active",
                current_stage="role" if remaining else "matching",
                pending_request=None,
                pending_handoff_ids=remaining,
                latest_run_id=run_id,
            )
        if status == "cancelled":
            return self.runtime.session_repository.update_navigation(
                session.session_id,
                expected_version=session.session_version,
                operation="role_cancelled",
                status="active",
                current_stage="role",
                pending_request=None,
                latest_run_id=run_id,
            )
        return self.runtime.session_repository.update_navigation(
            session.session_id,
            expected_version=session.session_version,
            operation="role_failed",
            status="failed",
            current_stage="role",
            pending_request=None,
            latest_run_id=run_id,
        )

    def _append_events(self, manifest: Any, traces: list[dict[str, Any]]) -> None:
        for item in traces:
            node = str(item.get("node", "unknown"))
            self.runtime.artifact_writer.append_event(RunEvent(
                run_id=manifest.run_id,
                session_id=manifest.session_id,
                thread_id=manifest.thread_id,
                event_type="node_started",
                workflow="role_profile",
                node=node,
                status="running",
            ))
            self.runtime.artifact_writer.append_event(RunEvent(
                run_id=manifest.run_id,
                session_id=manifest.session_id,
                thread_id=manifest.thread_id,
                event_type="node_finished",
                workflow="role_profile",
                node=node,
                status="completed",
                counts={
                    key: value
                    for key, value in (item.get("counters") or {}).items()
                    if isinstance(value, (int, float))
                },
                duration_ms=max(1, int(item.get("duration_ms", 1))),
            ))

    def _append_llm(self, run_id: str, calls: list[dict[str, Any]]) -> None:
        for call in calls:
            self.runtime.artifact_writer.append_llm_call(LLMCallReceipt(
                run_id=run_id,
                provider=str(call.get("provider", "unknown")),
                model=str(call.get("model", "unknown")),
                prompt_version=str(call.get("prompt_version", "unknown")),
                schema_version_used=str(call.get("schema_version", "v0.7.1")),
                request_hash=str(call.get("cache_key", "unknown")),
                status="success" if call.get("status") == "success" else "failed",
                retry_count=int(call.get("retry_count", 0)),
                cache_hit=bool(call.get("cache_hit")),
                latency_ms=int(call.get("duration_ms", 0)),
                validation_result=str(call.get("error_type") or "accepted"),
                fallback=call.get("fallback_reason"),
                integration=call.get("integration"),
                requested_strategy=call.get("requested_strategy"),
                effective_strategy=call.get("effective_strategy"),
                capabilities=call.get("capabilities"),
            ))

    def _append_errors(self, run_id: str, values: list[dict[str, Any]]) -> None:
        seen: set[tuple[str, str]] = set()
        for item in values:
            error_type = str(item.get("error_type") or "internal_error")
            message = str(item.get("message") or error_type)
            key = (error_type, message)
            if key in seen:
                continue
            seen.add(key)
            mapped = error_type if error_type in {
                "auth_required", "rate_limited", "source_changed",
                "adapter_required", "llm_invalid_output", "llm_unavailable",
                "storage_failure", "checkpoint_failure", "budget_exhausted",
            } else "contract_violation"
            self.runtime.artifact_writer.append_error(ErrorEvent(
                run_id=run_id,
                workflow="role_profile",
                node=item.get("node"),
                error_type=mapped,  # type: ignore[arg-type]
                message=message,
                retryable=bool(item.get("retryable")),
                recovery_hint="inspect the safe role run receipt and resume or retry",
            ))

    def _index_objects(
        self, run_id: str, owner_id: str, result: dict[str, Any]
    ) -> None:
        groups = {
            "job_demand_profile": result.get("job_demand_profile_ids", []),
            "role_family_demand_profile": [
                result.get("role_family_demand_profile_id")
            ],
            "job_reputation_profile": result.get(
                "job_reputation_profile_ids", []
            ),
            "company_reputation_profile": result.get(
                "company_reputation_profile_ids", []
            ),
            "role_intelligence_bundle": [
                result.get("role_intelligence_bundle_id")
            ],
            "raw_source_evidence": result.get("raw_artifact_ids", []),
        }
        for logical_type, values in groups.items():
            for value in values:
                if not value:
                    continue
                locator = (
                    f"repository://evidence/artifacts/{value}"
                    if logical_type == "raw_source_evidence"
                    else f"repository://role/{logical_type}/{value}"
                )
                self.runtime.artifact_writer.add_artifact(
                    run_id,
                    ArtifactEntry(
                        logical_type=logical_type,
                        object_id=str(value),
                        locator=locator,
                        owner=owner_id,
                        sensitivity="private" if logical_type == "raw_source_evidence" else "internal",
                    ),
                )

    def _metrics(self, result: dict[str, Any]) -> dict[str, int | float]:
        profile_ids = list(result.get("job_demand_profile_ids", []))
        source_documents: list[SourceDocument] = []
        for profile_id in profile_ids:
            profile = self.runtime.role_repository.get(profile_id, JobDemandProfile)
            if profile is None:
                continue
            source_documents.extend(
                document
                for source_id in profile.source_document_ids
                if (
                    document := self.runtime.role_repository.get(
                        source_id, SourceDocument
                    )
                ) is not None
            )
        detail_count = len(source_documents)
        detail_traced = sum(
            item.document_kind == "job_detail" and bool(item.raw_artifact_id)
            for item in source_documents
        )
        segments = [
            item
            for value in result.get("community_evidence_segment_ids", [])
            if (
                item := self.runtime.role_repository.get(
                    value, CommunityEvidenceSegment
                )
            ) is not None
        ]
        accepted = [item for item in segments if item.validation_status == "accepted"]
        segment_traced = sum(
            self.runtime.evidence_repository.get_fragment(item.fragment_id) is not None
            for item in accepted
        )
        crossover = sum(
            (
                item.segment_type in INTERVIEW_SEGMENT_TYPES
                and item.usage != "demand_assessment"
            )
            or (
                item.segment_type in REPUTATION_SEGMENT_TYPES
                and item.usage not in {"reputation_job", "reputation_company"}
            )
            for item in accepted
        )
        return {
            "search_only_projection_count": sum(
                item.document_kind != "job_detail" for item in source_documents
            ),
            "detail_artifact_trace_rate": (
                round(detail_traced / detail_count, 6) if detail_count else 0.0
            ),
            "community_segment_trace_rate": (
                round(segment_traced / len(accepted), 6) if accepted else 0.0
            ),
            "community_usage_crossover_count": crossover,
            "interview_segment_count": sum(
                item.segment_type in INTERVIEW_SEGMENT_TYPES for item in accepted
            ),
            "reputation_segment_count": sum(
                item.segment_type in REPUTATION_SEGMENT_TYPES for item in accepted
            ),
        }

    def _duplicate_payload(
        self, session: Any, receipt: RoleAuthorizationResponseReceipt
    ) -> dict[str, Any]:
        return {
            "schema_version": "v0.7.1",
            "command": "role.resume",
            "run_id": session.latest_run_id,
            "session_id": session.session_id,
            "session_version": session.session_version,
            "status": receipt.result_status,
            "next_action": (
                "role.research"
                if session.current_stage == "role" and session.pending_handoff_ids
                else "match.run"
            ),
            "output_refs": {
                "role_intelligence_bundle_id": receipt.role_intelligence_bundle_id,
            },
            "pending_request": None,
            "artifact_paths": {},
            "metrics": {},
            "deduplicated": True,
            "warnings": ["duplicate_response_reused"],
            "errors": [],
        }

    def _fail(self, run_id: str, exc: Exception) -> None:
        try:
            error_type = (
                "checkpoint_failure"
                if "checkpoint" in str(exc).casefold()
                else "storage_failure"
                if isinstance(exc, OSError)
                else "contract_violation"
            )
            self.runtime.artifact_writer.append_error(ErrorEvent(
                run_id=run_id,
                workflow="role_profile",
                error_type=error_type,  # type: ignore[arg-type]
                message=str(exc),
                retryable=error_type in {"checkpoint_failure", "storage_failure"},
                recovery_hint="inspect the role run and retry from the current session state",
            ))
            self.runtime.artifact_writer.finish_run(
                run_id,
                status="failed",
                next_action="session.resume",
                reason_codes=[error_type],
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


__all__ = ["RoleApplicationError", "RoleApplicationService"]
