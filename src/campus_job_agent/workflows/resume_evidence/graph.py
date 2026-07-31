"""Deterministic, interruptible PDF-to-structured-resume evidence graph."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from campus_job_agent.evidence import ArtifactIngestor
from campus_job_agent.schemas import (
    EvidenceArtifact,
    EvidenceFragment,
    PdfExtractionDiagnostics,
    Provenance,
    ResumeDraft,
    ResumeEvidenceGraphState,
    ResumeReviewRequest,
    ResumeReviewResponse,
    canonical_hash,
    resume_response_hash,
)
from campus_job_agent.schemas.evidence import utc_now
from campus_job_agent.storage.base import BlobStore, EvidenceRepository
from campus_job_agent.tools.candidate_profile import CreateFragmentsTool, ExtractPdfTextTool
from campus_job_agent.workflows.resume_evidence.extractor import ResumeEvidenceExtractor
from campus_job_agent.workflows.resume_evidence.policy import (
    apply_resume_review,
    build_resume_draft,
    missing_source_paths,
    next_review_target,
    publish_resume_evidence,
)


class ResumeEvidenceWorkflowError(RuntimeError):
    def __init__(
        self, message: str, *, error_type: str = "contract_violation",
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.error_type = error_type
        self.retryable = retryable


class ResumeEvidenceGraphRuntime:
    def __init__(
        self, *, blob_store: BlobStore, repository: EvidenceRepository,
        extractor: ResumeEvidenceExtractor, checkpointer: Any,
    ) -> None:
        self.repository = repository
        self.app = build_resume_evidence_graph(
            blob_store=blob_store, repository=repository,
            extractor=extractor, checkpointer=checkpointer,
        )

    def invoke(self, state: ResumeEvidenceGraphState) -> dict[str, Any]:
        thread_id = str(state.get("thread_id", "")).strip()
        if not thread_id:
            raise ResumeEvidenceWorkflowError("thread_id is required")
        try:
            return self.app.invoke(state, {"configurable": {"thread_id": thread_id}})
        except sqlite3.Error as exc:
            raise ResumeEvidenceWorkflowError(f"checkpoint_error: {exc}") from exc
        except ValueError as exc:
            raise ResumeEvidenceWorkflowError(str(exc)) from exc

    def resume(
        self, *, thread_id: str, response: ResumeReviewResponse | dict[str, Any],
        run_id: str | None = None,
    ) -> dict[str, Any]:
        validated = ResumeReviewResponse.model_validate(
            response.model_dump(mode="json")
            if isinstance(response, ResumeReviewResponse) else response
        )
        if validated.thread_id != thread_id:
            raise ResumeEvidenceWorkflowError("resume thread_id does not match response")
        current = self.app.get_state({"configurable": {"thread_id": thread_id}})
        values = dict(current.values or {})
        prior = self.repository.get_resume_review_receipt(validated.response_id)
        if prior is not None:
            payload_hash = resume_response_hash(validated)
            if self.repository.get_resume_review_payload_hash(validated.response_id) != payload_hash:
                raise ResumeEvidenceWorkflowError(
                    "idempotency_conflict: response_id payload differs"
                )
            return values
        if not values.get("pending_interaction"):
            raise ResumeEvidenceWorkflowError("no pending resume review exists")
        try:
            request = ResumeReviewRequest.model_validate(values["pending_interaction"])
            draft = self.repository.get_resume_draft(request.draft_id)
            if draft is None:
                raise ValueError("resume draft was not persisted")
            fragments = [
                item
                for item in (
                    self.repository.get_fragment(str(fragment_id))
                    for fragment_id in values.get("extraction_fragment_ids", [])
                )
                if item is not None
            ]
            # Validate against the current checkpoint before consuming the
            # LangGraph interrupt. Invalid input must not poison resumability.
            apply_resume_review(
                draft=draft, request=request, response=validated,
                fragments=fragments,
            )
        except ValueError as exc:
            raise ResumeEvidenceWorkflowError(str(exc)) from exc
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
            raise ResumeEvidenceWorkflowError(f"checkpoint_error: {exc}") from exc
        except ValueError as exc:
            raise ResumeEvidenceWorkflowError(str(exc)) from exc

    def get_state(self, thread_id: str) -> Any:
        return self.app.get_state({"configurable": {"thread_id": thread_id}})


def create_resume_evidence_state(
    *, run_id: str, session_id: str, thread_id: str, user_id: str,
    candidate_id: str, input_path: str, allowed_path_roots: list[str],
    force_reparse: bool = False,
) -> ResumeEvidenceGraphState:
    return {
        "run_id": run_id, "session_id": session_id, "thread_id": thread_id,
        "user_id": user_id, "candidate_id": candidate_id,
        "input_path": input_path, "allowed_path_roots": allowed_path_roots,
        "force_reparse": force_reparse,
        "artifact_id": None, "extraction_fragment_ids": [],
        "draft_id": None, "resume_evidence_id": None,
        "candidate_claim_count_at_start": 0,
        "pending_interaction": None, "resume_input": None,
        "status": "initialized", "next_action": None,
        "llm_calls": [], "trace": [], "errors": [],
    }


def build_resume_evidence_graph(
    *, blob_store: BlobStore, repository: EvidenceRepository,
    extractor: ResumeEvidenceExtractor, checkpointer: Any,
):
    nodes = _Nodes(
        blob_store=blob_store, repository=repository, extractor=extractor
    )
    graph = StateGraph(ResumeEvidenceGraphState)
    graph.add_node("validate_context", nodes.validate_context)
    graph.add_node("archive_pdf", nodes.archive_pdf)
    graph.add_node("extract_text", nodes.extract_text)
    graph.add_node("assess_quality", nodes.assess_quality)
    graph.add_node("build_draft", nodes.build_draft)
    graph.add_node("validate_schema", nodes.validate_schema)
    graph.add_node("plan_review", nodes.plan_review)
    graph.add_node("interrupt_for_review", nodes.interrupt_for_review)
    graph.add_node("apply_review", nodes.apply_review)
    graph.add_node("finalize_snapshot", nodes.finalize_snapshot)
    graph.add_node("finalize", nodes.finalize)
    graph.add_edge(START, "validate_context")
    graph.add_edge("validate_context", "archive_pdf")
    graph.add_edge("archive_pdf", "extract_text")
    graph.add_edge("extract_text", "assess_quality")
    graph.add_edge("assess_quality", "build_draft")
    graph.add_edge("build_draft", "validate_schema")
    graph.add_edge("validate_schema", "plan_review")
    graph.add_conditional_edges(
        "plan_review", lambda state: state["next_action"],
        {"review": "interrupt_for_review", "publish": "finalize_snapshot"},
    )
    graph.add_edge("interrupt_for_review", "apply_review")
    graph.add_conditional_edges(
        "apply_review", lambda state: state["next_action"],
        {"review": "plan_review", "retry": "build_draft", "cancel": "finalize"},
    )
    graph.add_edge("finalize_snapshot", "finalize")
    graph.add_edge("finalize", END)
    return graph.compile(checkpointer=checkpointer)


class _Nodes:
    def __init__(
        self, *, blob_store: BlobStore, repository: EvidenceRepository,
        extractor: ResumeEvidenceExtractor,
    ) -> None:
        self.blob_store = blob_store
        self.repository = repository
        self.extractor = extractor
        self.ingestor = ArtifactIngestor(blob_store, repository)
        self.pdf_tool = ExtractPdfTextTool(blob_store, repository)
        self.fragment_tool = CreateFragmentsTool(blob_store, repository)

    def validate_context(
        self, state: ResumeEvidenceGraphState, config: RunnableConfig
    ) -> dict[str, Any]:
        required = ("run_id", "session_id", "thread_id", "user_id", "candidate_id", "input_path")
        missing = [key for key in required if not str(state.get(key, "")).strip()]
        if missing:
            raise ResumeEvidenceWorkflowError(
                "missing required state fields: " + ", ".join(missing)
            )
        if str(config.get("configurable", {}).get("thread_id", "")) != state["thread_id"]:
            raise ResumeEvidenceWorkflowError("configurable.thread_id mismatch")
        path = Path(str(state["input_path"])).expanduser().resolve()
        if path.suffix.lower() != ".pdf" or not path.is_file():
            raise ResumeEvidenceWorkflowError("resume input must be an existing PDF")
        roots = [Path(value).expanduser().resolve() for value in state.get("allowed_path_roots", [])]
        if not any(path == root or root in path.parents for root in roots):
            raise ResumeEvidenceWorkflowError("resume path is outside allowed_path_roots")
        return {
            "status": "running",
            "candidate_claim_count_at_start": len(
                self.repository.list_claims(state["candidate_id"])
            ),
            "trace": [_trace("validate_context", state)],
        }

    def archive_pdf(self, state: ResumeEvidenceGraphState) -> dict[str, Any]:
        result = self.ingestor.ingest_file(
            str(state["input_path"]), owner_id=state["user_id"],
            source_type="resume_upload", extract_text=False,
            parser_version="resume_evidence_v1",
        )
        return {
            "input_path": None, "artifact_id": result.artifact.artifact_id,
            "trace": [_trace("archive_pdf", state, artifact_id=result.artifact.artifact_id)],
        }

    def extract_text(self, state: ResumeEvidenceGraphState) -> dict[str, Any]:
        result = self.pdf_tool.run({
            "artifact_id": state["artifact_id"], "owner_id": state["user_id"],
            "enforce_quality": False, "require_quality_diagnostics": True,
            "preserve_layout": True,
            "force_reparse": bool(state.get("force_reparse")),
        })
        if result.status == "failed":
            raise ResumeEvidenceWorkflowError(
                result.error or "PDF extraction failed",
                error_type=str(result.metadata.get("error_type") or "unsupported_input"),
                retryable=bool(result.metadata.get("retryable")),
            )
        fragments = self.fragment_tool.run({
            "artifact_id": state["artifact_id"], "owner_id": state["user_id"]
        })
        if fragments.status == "failed":
            raise ResumeEvidenceWorkflowError(
                fragments.error or "fragment creation failed",
                error_type=str(fragments.metadata.get("error_type") or "storage_failure"),
                retryable=bool(fragments.metadata.get("retryable")),
            )
        return {
            "extraction_fragment_ids": fragments.evidence_ids,
            "trace": [_trace(
                "extract_text", state, fragment_count=len(fragments.evidence_ids),
                parser=result.metadata.get("parser_name"),
            )],
        }

    def assess_quality(self, state: ResumeEvidenceGraphState) -> dict[str, Any]:
        extraction = self.repository.get_extraction(str(state["artifact_id"]))
        if extraction is None:
            raise ResumeEvidenceWorkflowError(
                "document extraction is missing", error_type="unsupported_input"
            )
        diagnostics = PdfExtractionDiagnostics.model_validate(extraction.diagnostics)
        if not diagnostics.quality_passed:
            raise ResumeEvidenceWorkflowError(
                "PDF text quality failed for pypdf and pdfplumber; OCR is outside v0.7.1",
                error_type="unsupported_input",
            )
        return {
            "trace": [_trace(
                "assess_quality", state,
                parser=diagnostics.selected_parser,
                total_non_whitespace_chars=diagnostics.total_non_whitespace_chars,
            )]
        }

    def build_draft(self, state: ResumeEvidenceGraphState) -> dict[str, Any]:
        artifact_id = str(state["artifact_id"])
        latest = self.repository.find_resume_draft(
            owner_id=state["user_id"], artifact_id=artifact_id
        )
        latest_snapshot = self.repository.get_latest_resume_evidence(
            owner_id=state["user_id"], candidate_id=state["candidate_id"]
        )
        current = (
            self.repository.get_resume_draft(str(state["draft_id"]))
            if state.get("draft_id") else None
        )
        existing = current or latest
        initial_reparse = bool(state.get("force_reparse")) and current is None
        if (
            existing is not None and existing.status == "cancelled"
            and not initial_reparse
        ):
            revived = existing.model_copy(update={
                "revision": existing.revision + 1,
                "status": "awaiting_review",
                "updated_at": utc_now(),
            })
            revived = self.repository.save_resume_draft(
                revived, expected_revision=existing.revision
            )
            return {
                "draft_id": revived.draft_id,
                "trace": [_trace("build_draft", state, revived=True)],
            }
        if (
            existing is not None and existing.status != "extracting"
            and not initial_reparse
        ):
            snapshot = self.repository.get_resume_evidence_for_draft(existing.draft_id)
            return {
                "draft_id": existing.draft_id,
                "resume_evidence_id": snapshot.resume_evidence_id if snapshot else None,
                "trace": [_trace("build_draft", state, deduplicated=True)],
            }
        extraction = self.repository.get_extraction(artifact_id)
        if extraction is None:
            raise ResumeEvidenceWorkflowError("document extraction is missing")
        diagnostics = PdfExtractionDiagnostics.model_validate(extraction.diagnostics)
        fragments = self._fragments(state)
        personal, batch, calls, _ = self.extractor.extract(
            candidate_id=state["candidate_id"], fragments=fragments
        )
        draft = build_resume_draft(
            owner_id=state["user_id"], candidate_id=state["candidate_id"],
            artifact_id=artifact_id, personal=personal, batch=batch,
            fragments=fragments, diagnostics=diagnostics,
            predecessor_draft_id=(
                latest_snapshot.draft_id
                if initial_reparse and latest_snapshot else None
            ),
            candidate_claim_count_at_import=int(
                state.get("candidate_claim_count_at_start", 0)
            ),
        )
        if existing is not None and not initial_reparse:
            draft = draft.model_copy(update={
                "draft_id": existing.draft_id,
                "revision": existing.revision + 1,
                "review_receipt_ids": existing.review_receipt_ids,
                "created_at": existing.created_at,
            })
            draft = self.repository.save_resume_draft(
                draft, expected_revision=existing.revision
            )
        else:
            draft = self.repository.save_resume_draft(draft)
        return {
            "draft_id": draft.draft_id,
            "llm_calls": [item.model_dump(mode="json") for item in calls],
            "trace": [_trace("build_draft", state, draft_id=draft.draft_id)],
        }

    def validate_schema(self, state: ResumeEvidenceGraphState) -> dict[str, Any]:
        draft = self._draft(state)
        if draft.owner_id != state["user_id"] or draft.candidate_id != state["candidate_id"]:
            raise ResumeEvidenceWorkflowError("resume draft identity mismatch")
        missing = missing_source_paths(draft)
        if missing:
            raise ResumeEvidenceWorkflowError(
                "resume fields are missing precise source spans: "
                + ", ".join(missing[:3])
            )
        return {"trace": [_trace("validate_schema", state, revision=draft.revision)]}

    def plan_review(self, state: ResumeEvidenceGraphState) -> dict[str, Any]:
        if state.get("resume_evidence_id"):
            return {"next_action": "publish", "trace": [_trace("plan_review", state)]}
        draft = self._draft(state)
        target = next_review_target(draft)
        if target is None:
            return {
                "pending_interaction": None, "next_action": "publish",
                "trace": [_trace("plan_review", state)],
            }
        section, target_kind, record_id = target
        source_pages = sorted({
            ref.page_number
            for path, refs in draft.field_sources.items()
            if path.startswith(f"/{section}")
            for ref in refs
        })
        allowed = ["confirm", "correct", "retry", "cancel"]
        if target_kind == "record":
            allowed.insert(2, "remove")
        request_material = [
            state["thread_id"], draft.draft_id, draft.revision,
            section, target_kind, record_id,
        ]
        request_id = "request-resume-" + canonical_hash(request_material)[:24]
        request = ResumeReviewRequest(
            request_id=request_id, thread_id=state["thread_id"],
            run_id=state["run_id"], user_id=state["user_id"],
            candidate_id=state["candidate_id"], draft_id=draft.draft_id,
            draft_revision=draft.revision, section=section,
            target_kind=target_kind, record_id=record_id,
            allowed_actions=allowed, source_pages=source_pages,
        )
        return {
            "pending_interaction": request.model_dump(mode="json"),
            "resume_input": None, "status": "interrupted", "next_action": "review",
            "trace": [_trace("plan_review", state, request_id=request_id)],
        }

    def interrupt_for_review(self, state: ResumeEvidenceGraphState) -> dict[str, Any]:
        response = interrupt(state["pending_interaction"])
        return {
            "resume_input": response, "status": "running",
            "trace": [_trace("interrupt_for_review", state)],
        }

    def apply_review(self, state: ResumeEvidenceGraphState) -> dict[str, Any]:
        request = ResumeReviewRequest.model_validate(state["pending_interaction"])
        response = ResumeReviewResponse.model_validate(state["resume_input"])
        payload_hash = resume_response_hash(response)
        existing = self.repository.get_resume_review_receipt(response.response_id)
        if existing is not None:
            if self.repository.get_resume_review_payload_hash(response.response_id) != payload_hash:
                raise ResumeEvidenceWorkflowError(
                    "idempotency_conflict: response_id payload differs"
                )
            return {
                "pending_interaction": None, "resume_input": None,
                "next_action": "cancel" if existing.result_status == "cancelled" else "review",
                "status": "cancelled" if existing.result_status == "cancelled" else "running",
                "trace": [_trace("apply_review", state, response_id=response.response_id, deduplicated=True)],
            }
        draft = self._draft(state)
        updated, receipt = apply_resume_review(
            draft=draft, request=request, response=response,
            fragments=self._fragments(state),
        )
        response_artifact, response_fragment = self._archive_response(response)
        receipt = receipt.model_copy(update={
            "response_artifact_id": response_artifact.artifact_id,
            "response_fragment_id": response_fragment.fragment_id,
        })
        updated, receipt, _ = self.repository.save_resume_review_update(
            updated, expected_revision=draft.revision,
            receipt=receipt, payload_hash=payload_hash,
        )
        next_action = (
            "cancel" if response.action == "cancel"
            else "retry" if response.action == "retry" else "review"
        )
        return {
            "pending_interaction": None, "resume_input": None,
            "status": "cancelled" if next_action == "cancel" else "running",
            "next_action": next_action,
            "trace": [_trace("apply_review", state, response_id=response.response_id)],
        }

    def finalize_snapshot(self, state: ResumeEvidenceGraphState) -> dict[str, Any]:
        if state.get("resume_evidence_id"):
            return {"trace": [_trace("finalize_snapshot", state, deduplicated=True)]}
        draft = self._draft(state)
        latest = self.repository.get_latest_resume_evidence(
            owner_id=draft.owner_id, candidate_id=draft.candidate_id
        )
        snapshot = publish_resume_evidence(
            draft, version=(latest.version + 1) if latest else 1
        )
        snapshot = self.repository.save_resume_evidence_snapshot(snapshot)
        finalized = draft.model_copy(update={
            "revision": draft.revision + 1, "status": "finalized", "updated_at": utc_now()
        })
        self.repository.save_resume_draft(finalized, expected_revision=draft.revision)
        return {
            "resume_evidence_id": snapshot.resume_evidence_id,
            "trace": [_trace("finalize_snapshot", state, snapshot_id=snapshot.resume_evidence_id)],
        }

    def finalize(self, state: ResumeEvidenceGraphState) -> dict[str, Any]:
        status = "cancelled" if state.get("status") == "cancelled" else "completed"
        return {
            "status": status, "pending_interaction": None,
            "resume_input": None,
            "next_action": "candidate.build" if status == "completed" else "resume.import",
            "trace": [_trace("finalize", state, status=status)],
        }

    def _draft(self, state: ResumeEvidenceGraphState) -> ResumeDraft:
        draft = self.repository.get_resume_draft(str(state.get("draft_id") or ""))
        if draft is None:
            raise ResumeEvidenceWorkflowError("resume draft was not persisted")
        return draft

    def _fragments(self, state: ResumeEvidenceGraphState) -> list[EvidenceFragment]:
        fragments = [
            self.repository.get_fragment(value)
            for value in state.get("extraction_fragment_ids", [])
        ]
        result = [item for item in fragments if item is not None]
        if not result:
            result = self.repository.list_fragments(str(state["artifact_id"]))
        if not result:
            raise ResumeEvidenceWorkflowError("resume fragments are missing")
        return result

    def _archive_response(
        self, response: ResumeReviewResponse
    ) -> tuple[EvidenceArtifact, EvidenceFragment]:
        payload = json.dumps(response.model_dump(mode="json"), ensure_ascii=False, sort_keys=True)
        raw = payload.encode("utf-8")
        digest = hashlib.sha256(raw).hexdigest()
        artifact_id = str(uuid5(NAMESPACE_URL, f"resume-review:{response.user_id}:{digest}"))
        uri = self.blob_store.put(
            f"resume-reviews/{hashlib.sha256(response.user_id.encode()).hexdigest()[:24]}/{artifact_id}.json",
            raw,
        )
        artifact = self.repository.save_artifact(EvidenceArtifact(
            artifact_id=artifact_id, owner_id=response.user_id,
            source_type="human_resume_review", content_type="application/json",
            original_name=f"{response.response_id}.json", raw_uri=uri,
            text_uri=uri, content_hash=digest, parser_name="json",
            parser_version="resume_review_v1",
            provenance=Provenance(parser_name="json", parser_version="resume_review_v1"),
        ))
        fragment_id = str(uuid5(NAMESPACE_URL, f"{artifact.artifact_id}:full"))
        fragment = self.repository.save_fragment(EvidenceFragment(
            fragment_id=fragment_id, artifact_id=artifact.artifact_id,
            locator_type="json_object", locator={"path": "/"},
            text=payload, text_hash=digest,
            metadata={"contains_private_resume_review": True},
        ))
        return artifact, fragment


def _trace(node: str, state: ResumeEvidenceGraphState, **extra: Any) -> dict[str, Any]:
    return {
        "node": node, "run_id": state.get("run_id"),
        "thread_id": state.get("thread_id"), "status": extra.pop("status", "success"),
        **extra,
    }


__all__ = [
    "ResumeEvidenceGraphRuntime", "ResumeEvidenceWorkflowError",
    "build_resume_evidence_graph", "create_resume_evidence_state",
]
