from __future__ import annotations

import io
from pathlib import Path

from pypdf import PdfWriter

from campus_job_agent.evidence import ClaimExtractorService
from campus_job_agent.llm import LLMCache, LLMConfig, MockLLMProvider
from campus_job_agent.storage import LocalBlobStore, SQLiteRepository
from campus_job_agent.tools import build_candidate_profile_registry


FIXTURES = Path(__file__).parents[1] / "fixtures" / "v04"


def _registry(tmp_path):
    repository = SQLiteRepository(tmp_path / "evidence.sqlite3")
    registry = build_candidate_profile_registry(
        blob_store=LocalBlobStore(tmp_path / "blobs"),
        repository=repository,
        profile_repository=repository,
        claim_extractor=ClaimExtractorService(
            LLMConfig(model="mock-claims", cache_enabled=False),
            MockLLMProvider(),
            LLMCache(str(tmp_path / "cache")),
        ),
    )
    return registry, repository


def _text_pdf(pages: list[str]) -> bytes:
    objects: list[bytes] = []
    page_ids: list[int] = []
    font_id = 3 + len(pages) * 2
    for index, text in enumerate(pages):
        page_id = 3 + index * 2
        content_id = page_id + 1
        page_ids.append(page_id)
        escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        stream = f"BT /F1 12 Tf 72 720 Td ({escaped}) Tj ET".encode()
        objects.extend(
            [
                (
                    f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                    f"/Resources << /Font << /F1 {font_id} 0 R >> >> "
                    f"/Contents {content_id} 0 R >>"
                ).encode(),
                (
                    f"<< /Length {len(stream)} >>\nstream\n".encode()
                    + stream
                    + b"\nendstream"
                ),
            ]
        )
    header_objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        (
            f"<< /Type /Pages /Kids [{' '.join(f'{value} 0 R' for value in page_ids)}] "
            f"/Count {len(page_ids)} >>"
        ).encode(),
    ]
    all_objects = [
        *header_objects,
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
    return bytes(output)


def test_plain_text_and_readme_have_line_locators(tmp_path) -> None:
    registry, repository = _registry(tmp_path)
    for source in [FIXTURES / "candidate_sufficient.md", FIXTURES / "README"]:
        ingested = registry.run(
            "candidate.ingest_material", {"path": str(source), "owner_id": "owner"}
        )
        assert ingested.status == "success"
        artifact_id = ingested.evidence_ids[0]
        extracted = registry.run(
            "evidence.extract_plain_text",
            {"artifact_id": artifact_id, "owner_id": "owner"},
        )
        assert extracted.status == "success"
        fragmented = registry.run(
            "evidence.create_fragments",
            {"artifact_id": artifact_id, "owner_id": "owner"},
        )
        assert fragmented.status == "success"
        fragments = repository.list_fragments(artifact_id)
        assert fragments
        assert all(item.locator_type == "line_and_char_range" for item in fragments)
        assert all("start_line" in item.locator for item in fragments)


def test_text_pdf_preserves_page_locators(tmp_path) -> None:
    registry, repository = _registry(tmp_path)
    path = tmp_path / "resume.pdf"
    path.write_bytes(_text_pdf(["Page one Python", "Page two LangGraph"]))
    ingested = registry.run(
        "candidate.ingest_material", {"path": str(path), "owner_id": "owner"}
    )
    artifact_id = ingested.evidence_ids[0]
    extracted = registry.run(
        "evidence.extract_pdf_text",
        {"artifact_id": artifact_id, "owner_id": "owner"},
    )
    assert extracted.status == "success"
    assert extracted.records[0]["unit_count"] == 2
    fragmented = registry.run(
        "evidence.create_fragments",
        {"artifact_id": artifact_id, "owner_id": "owner"},
    )
    assert fragmented.status == "success"
    fragments = repository.list_fragments(artifact_id)
    assert {item.locator["page"] for item in fragments} == {1, 2}
    assert all(item.locator_type == "page_and_char_range" for item in fragments)


def test_scanned_pdf_is_structured_unsupported_input(tmp_path) -> None:
    registry, _ = _registry(tmp_path)
    path = tmp_path / "scan.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    with path.open("wb") as handle:
        writer.write(handle)
    ingested = registry.run(
        "candidate.ingest_material", {"path": str(path), "owner_id": "owner"}
    )
    assert ingested.status == "success"
    extracted = registry.run(
        "evidence.extract_pdf_text",
        {"artifact_id": ingested.evidence_ids[0], "owner_id": "owner"},
    )
    assert extracted.status == "failed"
    assert extracted.metadata["error_type"] == "unsupported_input"
    assert extracted.metadata["needs_user_action"] is True


def test_claim_extraction_failure_is_structured_and_profile_projection_is_idempotent(
    tmp_path,
) -> None:
    repository = SQLiteRepository(tmp_path / "evidence.sqlite3")
    registry = build_candidate_profile_registry(
        blob_store=LocalBlobStore(tmp_path / "blobs"),
        repository=repository,
        profile_repository=repository,
        claim_extractor=ClaimExtractorService(
            LLMConfig(
                model="broken-claims",
                cache_enabled=False,
                max_retries=1,
            ),
            MockLLMProvider("always_invalid_json"),
            LLMCache(str(tmp_path / "cache")),
        ),
    )
    ingested = registry.run(
        "candidate.ingest_material",
        {
            "path": str(FIXTURES / "candidate_sufficient.md"),
            "owner_id": "owner",
        },
    )
    artifact_id = ingested.evidence_ids[0]
    assert (
        registry.run(
            "evidence.extract_plain_text",
            {"artifact_id": artifact_id, "owner_id": "owner"},
        ).status
        == "success"
    )
    fragments = registry.run(
        "evidence.create_fragments",
        {"artifact_id": artifact_id, "owner_id": "owner"},
    )
    failed = registry.run(
        "evidence.extract_candidate_claims",
        {
            "subject_id": "candidate",
            "owner_id": "owner",
            "fragment_ids": fragments.evidence_ids,
        },
    )
    assert failed.status == "failed"
    assert failed.metadata["error_type"] == "llm_output_error"
    assert failed.records[0]["llm_calls"][0]["status"] == "failed"
    assert repository.list_claims("candidate") == []

    first = registry.run(
        "profile.project_candidate", {"candidate_id": "candidate"}
    )
    second = registry.run(
        "profile.project_candidate", {"candidate_id": "candidate"}
    )
    assert first.records[0]["snapshot_id"] == second.records[0]["snapshot_id"]
    assert len(repository.list_profiles("candidate", "candidate")) == 1


def test_malicious_looking_filename_cannot_escape_blob_root(tmp_path) -> None:
    registry, repository = _registry(tmp_path)
    source = tmp_path / "..resume.md"
    source.write_text("Skills: Python", encoding="utf-8")
    ingested = registry.run(
        "candidate.ingest_material",
        {"path": str(source), "owner_id": "owner/../../attempt"},
    )
    assert ingested.status == "success"
    artifact = repository.get_artifact(ingested.evidence_ids[0])
    assert artifact is not None
    blob_root = (tmp_path / "blobs").resolve()
    raw_path = Path(artifact.raw_uri.removeprefix("file://")).resolve()
    assert blob_root in raw_path.parents
