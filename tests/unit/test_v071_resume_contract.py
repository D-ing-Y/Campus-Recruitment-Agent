from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from campus_job_agent.schemas import (
    RESUME_SECTION_ORDER,
    CustomSectionRecord,
    EvidenceFragment,
    PdfExtractionDiagnostics,
    PersonalInformation,
    ResumeData,
    ResumeDraft,
    ResumeExtractionBatch,
    ResumeReviewRequest,
    ResumeReviewResponse,
    ResumeSourceRef,
    ExtractedCustomSection,
)
from campus_job_agent.tools import candidate_profile as candidate_tools
from campus_job_agent.workflows.resume_evidence.extractor import (
    extract_personal_information,
    redact_personal_information,
)
from campus_job_agent.workflows.resume_evidence.policy import (
    apply_resume_review,
    build_resume_draft,
    publish_resume_evidence,
)


def _fragment(text: str) -> EvidenceFragment:
    return EvidenceFragment(
        fragment_id="fragment-resume", artifact_id="artifact-resume",
        locator_type="page_and_char_range",
        locator={"page": 1, "document_start": 0, "document_end": len(text)},
        text=text, text_hash=hashlib.sha256(text.encode()).hexdigest(),
    )


def _diagnostics() -> PdfExtractionDiagnostics:
    return PdfExtractionDiagnostics(
        selected_parser="pypdf_text", attempted_parsers=["pypdf_text"],
        total_non_whitespace_chars=200, nonempty_page_ratio=1.0,
        invalid_character_ratio=0.0, quality_passed=True,
    )


def _text_pdf(text: str) -> bytes:
    escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    stream = f"BT /F1 10 Tf 30 740 Td ({escaped}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    output = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for object_id, body in enumerate(objects, start=1):
        offsets.append(len(output))
        output.extend(f"{object_id} 0 obj\n".encode())
        output.extend(body)
        output.extend(b"\nendobj\n")
    xref = len(output)
    output.extend(f"xref\n0 {len(objects) + 1}\n".encode())
    output.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode())
    output.extend(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    )
    return bytes(output)


def test_boss_section_order_and_model_tool_schema_exclude_personal_information() -> None:
    assert RESUME_SECTION_ORDER == (
        "personal_information", "personal_advantage", "career_expectations",
        "work_experiences", "project_experiences", "education_experiences",
        "professional_skills", "custom_sections",
    )
    schema = ResumeExtractionBatch.model_json_schema()
    assert "personal_information" not in schema["properties"]
    assert schema["additionalProperties"] is False


def test_personal_information_is_local_and_redacted_before_model_input() -> None:
    raw = (
        "姓名：丁乙\n性别：男\n出生年月：2002-02\n当前求职状态：在校-考虑机会\n"
        "牛人身份：学生\n电话：15100000024\n微信号：DingYi_13\n"
        "邮箱：dingyi@example.com\n出生地：山东济南\n"
        "项目经历\nImplemented a LangGraph workflow."
    )
    fragment = _fragment(raw)
    personal = extract_personal_information([fragment])
    assert personal.name == "丁乙"
    assert personal.phone == "15100000024"
    assert personal.email == "dingyi@example.com"
    assert personal.job_search_status == "在校-考虑机会"
    assert personal.identity == "学生"
    assert personal.birthplace == "山东济南"
    redacted = redact_personal_information([fragment], personal)[0].text
    for value in personal.model_dump().values():
        if isinstance(value, str):
            assert value not in redacted
    assert "LangGraph workflow" in redacted


def test_unlabelled_resume_header_extracts_birth_date_and_birthplace() -> None:
    fragment = _fragment(
        "丁乙\n2002-02   男   云南\n15100000024\n\n教育背景\n某大学"
    )
    personal = extract_personal_information([fragment])
    assert personal.birth_date == "2002-02"
    assert personal.birthplace == "云南"


def test_snapshot_requires_every_section_confirmation() -> None:
    draft = ResumeDraft(
        owner_id="owner", candidate_id="candidate", artifact_id="artifact-resume",
        status="awaiting_review", data=ResumeData(),
        extraction_diagnostics=_diagnostics(),
    )
    with pytest.raises(ValueError, match="unconfirmed sections"):
        publish_resume_evidence(draft, version=1)


def test_pdfplumber_is_used_only_after_pypdf_quality_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = _text_pdf(
        "Fallback parser evidence with Python LangGraph project responsibility " * 3
    )

    class BrokenPdfReader:
        def __init__(self, *_args, **_kwargs):
            raise ValueError("synthetic pypdf failure")

    monkeypatch.setattr(candidate_tools, "PdfReader", BrokenPdfReader)
    pages, parser, diagnostics = candidate_tools._extract_pdf_pages(raw)
    assert pages and parser == "pdfplumber_text"
    assert diagnostics["attempted_parsers"] == ["pypdf_text", "pdfplumber_text"]
    assert diagnostics["quality_passed"] is True


def test_resume_pdf_uses_layout_aware_pypdf_mode() -> None:
    raw = _text_pdf(
        "Layout parser evidence with University Project Python responsibility " * 3
    )
    pages, parser, diagnostics = candidate_tools._extract_pdf_pages(
        raw, preserve_layout=True
    )
    assert pages
    assert parser == "pypdf_layout"
    assert diagnostics["attempted_parsers"] == ["pypdf_layout"]


def test_grouped_award_bullets_are_split_into_source_records() -> None:
    text = "荣誉证书\n· 2024.12 奖学金一等奖\n· 2025.12 奖学金三等奖"
    fragment = _fragment(text)
    batch = ResumeExtractionBatch(custom_sections=[ExtractedCustomSection(
        section_title="荣誉证书", section_type="award",
        content="· 2024.12 奖学金一等奖\n· 2025.12 奖学金三等奖",
        evidence_fragment_ids=[fragment.fragment_id],
    )])
    draft = build_resume_draft(
        owner_id="owner", candidate_id="candidate",
        artifact_id=fragment.artifact_id,
        personal=PersonalInformation(),
        batch=batch, fragments=[fragment], diagnostics=_diagnostics(),
    )
    assert [item.start_date for item in draft.data.custom_sections] == [
        "2024.12", "2025.12"
    ]
    assert [item.name for item in draft.data.custom_sections] == [
        "奖学金一等奖", "奖学金三等奖"
    ]


def test_removing_first_record_reindexes_source_pointers_without_drift() -> None:
    first = _fragment("First award")
    second_text = "Second award"
    second = first.model_copy(update={
        "fragment_id": "fragment-second", "text": second_text,
        "text_hash": hashlib.sha256(second_text.encode()).hexdigest(),
    })
    data = ResumeData(custom_sections=[
        CustomSectionRecord(record_id="record-first", content=first.text),
        CustomSectionRecord(record_id="record-second", content=second.text),
    ])
    refs = {
        "/custom_sections/0/content": [ResumeSourceRef(
            artifact_id=first.artifact_id, fragment_id=first.fragment_id,
            page_number=1, text_hash=first.text_hash,
        )],
        "/custom_sections/1/content": [ResumeSourceRef(
            artifact_id=second.artifact_id, fragment_id=second.fragment_id,
            page_number=1, text_hash=second.text_hash,
        )],
    }
    draft = ResumeDraft(
        owner_id="owner", candidate_id="candidate", artifact_id=first.artifact_id,
        status="awaiting_review", data=data, field_sources=refs,
        extraction_diagnostics=_diagnostics(),
    )
    request = ResumeReviewRequest(
        request_id="request-remove", thread_id="thread", run_id="run",
        user_id="owner", candidate_id="candidate", draft_id=draft.draft_id,
        draft_revision=draft.revision, section="custom_sections",
        target_kind="record", record_id="record-first",
        allowed_actions=["confirm", "remove", "retry", "cancel"],
    )
    updated, _ = apply_resume_review(
        draft=draft, request=request,
        response=ResumeReviewResponse(
            response_id="response-remove", request_id=request.request_id,
            thread_id="thread", user_id="owner", action="remove",
        ),
        fragments=[first, second],
    )
    assert updated.data.custom_sections[0].record_id == "record-second"
    assert updated.field_sources["/custom_sections/0/content"][0].fragment_id == second.fragment_id
