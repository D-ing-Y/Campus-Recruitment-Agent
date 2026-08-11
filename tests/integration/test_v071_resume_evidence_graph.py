from __future__ import annotations

from pathlib import Path

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from pypdf import PdfWriter

from campus_job_agent.llm import LLMCache, LLMConfig, MockLLMProvider
from campus_job_agent.schemas import RESUME_SECTION_ORDER, ResumeReviewResponse
from campus_job_agent.storage import LocalBlobStore, SQLiteRepository
from campus_job_agent.workflows.resume_evidence import (
    ResumeEvidenceExtractor,
    ResumeEvidenceGraphRuntime,
    ResumeEvidenceWorkflowError,
    create_resume_evidence_state,
)


def _text_pdf(path: Path, pages: list[str]) -> None:
    objects: list[bytes] = []
    page_ids: list[int] = []
    font_id = 3 + len(pages) * 2
    for index, text in enumerate(pages):
        page_id = 3 + index * 2
        content_id = page_id + 1
        page_ids.append(page_id)
        escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        stream = f"BT /F1 11 Tf 36 740 Td ({escaped}) Tj ET".encode()
        objects.extend([
            (
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                f"/Resources << /Font << /F1 {font_id} 0 R >> >> "
                f"/Contents {content_id} 0 R >>"
            ).encode(),
            f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"\nendstream",
        ])
    all_objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        (
            f"<< /Type /Pages /Kids [{' '.join(f'{value} 0 R' for value in page_ids)}] "
            f"/Count {len(page_ids)} >>"
        ).encode(),
        *objects,
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    output = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for object_id, body in enumerate(all_objects, start=1):
        offsets.append(len(output))
        output.extend(f"{object_id} 0 obj\n".encode())
        output.extend(body)
        output.extend(b"\nendobj\n")
    xref = len(output)
    output.extend(f"xref\n0 {len(all_objects) + 1}\n".encode())
    output.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode())
    output.extend(
        (
            f"trailer\n<< /Size {len(all_objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref}\n%%EOF\n"
        ).encode()
    )
    path.write_bytes(bytes(output))


def _runtime(tmp_path: Path, repository: SQLiteRepository) -> ResumeEvidenceGraphRuntime:
    return ResumeEvidenceGraphRuntime(
        blob_store=LocalBlobStore(tmp_path / "blobs"),
        repository=repository,
        extractor=ResumeEvidenceExtractor(
            LLMConfig(model="mock-resume", cache_enabled=False),
            MockLLMProvider(),
            LLMCache(str(tmp_path / "cache")),
        ),
        checkpointer=InMemorySaver(),
    )


def _start(tmp_path: Path, runtime: ResumeEvidenceGraphRuntime) -> dict:
    resume = tmp_path / "resume.pdf"
    _text_pdf(resume, [
        "Anonymous Candidate email hidden@example.com phone 13900000000 University 2027 "
        "Project Agent platform responsibility implemented Python LangGraph RAG LLM "
        "skills and evaluation evidence. " * 2
    ])
    return runtime.invoke(create_resume_evidence_state(
        run_id="run-resume", session_id="session-resume",
        thread_id="thread-resume", user_id="owner", candidate_id="candidate",
        input_path=str(resume), allowed_path_roots=[str(tmp_path)],
    ))


def _confirm(runtime: ResumeEvidenceGraphRuntime, result: dict, index: int) -> dict:
    request = result["__interrupt__"][0].value
    return runtime.resume(thread_id="thread-resume", response=ResumeReviewResponse(
        response_id=f"response-{index}", request_id=request["request_id"],
        thread_id="thread-resume", user_id="owner", action="confirm",
    ))


def test_resume_review_is_ordered_and_publishes_no_candidate_claims(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "evidence.sqlite3")
    runtime = _runtime(tmp_path, repository)
    result = _start(tmp_path, runtime)
    seen: list[str] = []
    targets: list[tuple[str, str]] = []
    for index in range(30):
        if "__interrupt__" not in result:
            break
        request = result["__interrupt__"][0].value
        seen.append(request["section"])
        targets.append((request["section"], request["target_kind"]))
        assert repository.list_claims("candidate") == []
        result = _confirm(runtime, result, index)

    assert result["status"] == "completed"
    assert list(dict.fromkeys(seen)) == list(RESUME_SECTION_ORDER)
    snapshot = repository.get_resume_evidence_snapshot(result["resume_evidence_id"])
    assert snapshot is not None and snapshot.status == "confirmed"
    draft = repository.get_resume_draft(snapshot.draft_id)
    assert draft is not None and draft.status == "finalized"
    assert len(draft.review_receipt_ids) == len(seen)
    assert repository.list_claims("candidate") == []
    assert all(snapshot.field_sources.values())
    assert all(
        ref.start_offset is not None and ref.end_offset is not None
        for refs in snapshot.field_sources.values()
        for ref in refs
    )
    assert ("project_experiences", "section_complete") not in targets
    assert ("education_experiences", "section_complete") not in targets
    assert ("career_expectations", "section_complete") in targets
    nodes = [item["node"] for item in result["trace"]]
    assert nodes.index("extract_text") < nodes.index("assess_quality") < nodes.index("build_draft")


def test_review_identity_idempotency_and_pdf_correction_boundary(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "evidence.sqlite3")
    runtime = _runtime(tmp_path, repository)
    result = _start(tmp_path, runtime)
    request = result["__interrupt__"][0].value

    with pytest.raises(ResumeEvidenceWorkflowError, match="identity"):
        runtime.resume(thread_id="thread-resume", response=ResumeReviewResponse(
            response_id="response-wrong-owner", request_id=request["request_id"],
            thread_id="thread-resume", user_id="other", action="confirm",
        ))

    with pytest.raises(ResumeEvidenceWorkflowError, match="not supported by PDF"):
        runtime.resume(thread_id="thread-resume", response=ResumeReviewResponse(
            response_id="response-invented", request_id=request["request_id"],
            thread_id="thread-resume", user_id="owner", action="correct",
            patch={"name": "Fact absent from the PDF"}, attests_pdf_source=True,
        ))

    response = ResumeReviewResponse(
        response_id="response-once", request_id=request["request_id"],
        thread_id="thread-resume", user_id="owner", action="confirm",
    )
    first = runtime.resume(thread_id="thread-resume", response=response)
    draft_id = first["draft_id"]
    first_draft = repository.get_resume_draft(draft_id)
    replay = runtime.resume(thread_id="thread-resume", response=response)
    replay_draft = repository.get_resume_draft(draft_id)
    assert replay_draft is not None and first_draft is not None
    assert replay_draft.revision == first_draft.revision
    assert replay_draft.review_receipt_ids == first_draft.review_receipt_ids
    assert replay["draft_id"] == draft_id


def test_scanned_pdf_fails_without_partial_snapshot(tmp_path: Path) -> None:
    scan = tmp_path / "scan.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    with scan.open("wb") as handle:
        writer.write(handle)
    repository = SQLiteRepository(tmp_path / "evidence.sqlite3")
    runtime = _runtime(tmp_path, repository)
    with pytest.raises(ResumeEvidenceWorkflowError, match="OCR"):
        runtime.invoke(create_resume_evidence_state(
            run_id="run-scan", session_id="session-scan", thread_id="thread-scan",
            user_id="owner", candidate_id="candidate", input_path=str(scan),
            allowed_path_roots=[str(tmp_path)],
        ))
    assert repository.get_latest_resume_evidence(
        owner_id="owner", candidate_id="candidate"
    ) is None


def test_retry_reextracts_into_same_cas_draft(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "evidence.sqlite3")
    runtime = _runtime(tmp_path, repository)
    initial = _start(tmp_path, runtime)
    request = initial["__interrupt__"][0].value
    draft_before = repository.get_resume_draft(initial["draft_id"])
    retried = runtime.resume(
        thread_id="thread-resume",
        response=ResumeReviewResponse(
            response_id="response-retry", request_id=request["request_id"],
            thread_id="thread-resume", user_id="owner", action="retry",
        ),
    )
    draft_after = repository.get_resume_draft(initial["draft_id"])
    assert draft_before is not None and draft_after is not None
    assert retried["draft_id"] == initial["draft_id"]
    assert draft_after.status == "awaiting_review"
    assert draft_after.revision == draft_before.revision + 2
    assert len(draft_after.review_receipt_ids) == 1
    assert retried["__interrupt__"][0].value["section"] == "personal_information"


def test_cancelled_draft_can_be_revived_by_a_new_import(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "evidence.sqlite3")
    runtime = _runtime(tmp_path, repository)
    initial = _start(tmp_path, runtime)
    request = initial["__interrupt__"][0].value
    cancelled = runtime.resume(
        thread_id="thread-resume",
        response=ResumeReviewResponse(
            response_id="response-cancel", request_id=request["request_id"],
            thread_id="thread-resume", user_id="owner", action="cancel",
        ),
    )
    assert cancelled["status"] == "cancelled"
    cancelled_draft = repository.get_resume_draft(initial["draft_id"])
    assert cancelled_draft is not None and cancelled_draft.status == "cancelled"

    resume_path = tmp_path / "resume.pdf"
    revived = runtime.invoke(create_resume_evidence_state(
        run_id="run-reimport", session_id="session-reimport",
        thread_id="thread-reimport", user_id="owner", candidate_id="candidate",
        input_path=str(resume_path), allowed_path_roots=[str(tmp_path)],
    ))
    revived_draft = repository.get_resume_draft(initial["draft_id"])
    assert revived_draft is not None and revived_draft.status == "awaiting_review"
    assert revived_draft.revision == cancelled_draft.revision + 1
    assert revived["draft_id"] == initial["draft_id"]
    assert revived["__interrupt__"][0].value["section"] == "personal_information"


def test_reparse_creates_new_draft_and_version_without_mutating_snapshot(
    tmp_path: Path,
) -> None:
    repository = SQLiteRepository(tmp_path / "evidence.sqlite3")
    runtime = _runtime(tmp_path, repository)
    first = _start(tmp_path, runtime)
    for index in range(30):
        if "__interrupt__" not in first:
            break
        first = _confirm(runtime, first, index)
    old_snapshot = repository.get_resume_evidence_snapshot(
        first["resume_evidence_id"]
    )
    assert old_snapshot is not None and old_snapshot.version == 1

    resume_path = tmp_path / "resume.pdf"
    second = runtime.invoke(create_resume_evidence_state(
        run_id="run-reparse", session_id="session-reparse",
        thread_id="thread-reparse", user_id="owner", candidate_id="candidate",
        input_path=str(resume_path), allowed_path_roots=[str(tmp_path)],
        force_reparse=True,
    ))
    assert second["draft_id"] != old_snapshot.draft_id
    new_draft = repository.get_resume_draft(second["draft_id"])
    assert new_draft is not None
    assert new_draft.predecessor_draft_id == old_snapshot.draft_id
    for index in range(30):
        if "__interrupt__" not in second:
            break
        request = second["__interrupt__"][0].value
        second = runtime.resume(
            thread_id="thread-reparse",
            response=ResumeReviewResponse(
                response_id=f"response-reparse-{index}",
                request_id=request["request_id"], thread_id="thread-reparse",
                user_id="owner", action="confirm",
            ),
        )
    new_snapshot = repository.get_resume_evidence_snapshot(
        second["resume_evidence_id"]
    )
    assert new_snapshot is not None and new_snapshot.version == 2
    assert repository.get_resume_evidence_snapshot(
        old_snapshot.resume_evidence_id
    ) == old_snapshot
