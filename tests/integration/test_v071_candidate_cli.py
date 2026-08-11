from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
CLI = Path(os.environ.get(
    "CAMPUS_AGENT_TEST_CLI", str(REPO_ROOT / ".venv" / "bin" / "campus-agent")
))


def _run(tmp_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["CAMPUS_AGENT_DATA_ROOT"] = str(tmp_path / "data")
    env["CAMPUS_AGENT_LLM_PROVIDER"] = "mock"
    env["CAMPUS_AGENT_LLM_CACHE_ENABLED"] = "false"
    return subprocess.run(
        [str(CLI), "--json", *args], cwd=tmp_path, env=env,
        text=True, capture_output=True, check=False,
    )


def _run_env(
    tmp_path: Path, overrides: dict[str, str], *args: str
) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["CAMPUS_AGENT_DATA_ROOT"] = str(tmp_path / "data")
    env["CAMPUS_AGENT_LLM_PROVIDER"] = "mock"
    env["CAMPUS_AGENT_LLM_CACHE_ENABLED"] = "false"
    env.update(overrides)
    return subprocess.run(
        [str(CLI), "--json", *args], cwd=tmp_path, env=env,
        text=True, capture_output=True, check=False,
    )


def _start_session(tmp_path: Path, user_id: str) -> str:
    result = _run(tmp_path, "session", "start", "--user-id", user_id)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)["session_id"]


def _text_pdf(path: Path, text: str) -> None:
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
    path.write_bytes(bytes(output))


def _confirmed_resume(
    tmp_path: Path, session_id: str, candidate_id: str, *, sufficient: bool,
    marker: str = "",
) -> str:
    resume = tmp_path / f"{candidate_id}{marker}.pdf"
    responsibility = (
        "Responsibilities implemented graph checkpoint recovery and evaluation."
        if sufficient else "Project Agent workflow requires additional details."
    )
    _text_pdf(
        resume,
        (
            "Anonymous University expected graduation 2027. Project Candidate Evidence Workflow. "
            f"{responsibility} Skills Python LangGraph RAG LLM. {marker} " * 2
        ),
    )
    imported = _run(
        tmp_path, "resume", "import", session_id,
        "--candidate-id", candidate_id, "--input", str(resume),
    )
    assert imported.returncode == 0, imported.stderr
    payload = json.loads(imported.stdout)
    assert payload["metrics"]["pre_confirmation_claim_count"] == 0
    for index in range(30):
        if payload["status"] != "interrupted":
            break
        reviewed = _run(
            tmp_path, "resume", "resume", session_id,
            "--action", "confirm", "--response-id", f"resume-response-{candidate_id}-{marker}-{index}",
        )
        assert reviewed.returncode == 0, reviewed.stderr
        payload = json.loads(reviewed.stdout)
        if index == 0:
            duplicate = _run(
                tmp_path, "resume", "resume", session_id,
                "--action", "confirm",
                "--response-id", f"resume-response-{candidate_id}-{marker}-{index}",
            )
            assert duplicate.returncode == 0, duplicate.stderr
            duplicate_payload = json.loads(duplicate.stdout)
            assert duplicate_payload["deduplicated"] is True
            assert duplicate_payload["metrics"]["duplicate_review_write_count"] == 0
    assert payload["status"] == "completed"
    return payload["output_refs"]["resume_evidence_id"]


def test_candidate_rebuild_selects_only_current_resume_claims(tmp_path: Path) -> None:
    first_session = _start_session(tmp_path, "version-owner")
    resume_v1 = _confirmed_resume(
        tmp_path, first_session, "version-owner", sufficient=True, marker="version-one"
    )
    first_build = _run(
        tmp_path, "candidate", "build", first_session,
        "--candidate-id", "version-owner", "--resume-evidence", resume_v1,
    )
    assert first_build.returncode == 0, first_build.stderr

    second_session = _start_session(tmp_path, "version-owner")
    resume_v2 = _confirmed_resume(
        tmp_path, second_session, "version-owner", sufficient=True, marker="version-two"
    )
    assert resume_v2 != resume_v1
    second_build = _run(
        tmp_path, "candidate", "build", second_session,
        "--candidate-id", "version-owner", "--resume-evidence", resume_v2,
    )
    assert second_build.returncode == 0, second_build.stderr
    payload = json.loads(second_build.stdout)
    assert payload["status"] == "completed"
    assert payload["metrics"]["stale_resume_claim_projection_count"] == 0
    assert payload["metrics"]["legacy_model_claim_projection_count"] == 0
    assert payload["metrics"]["current_resume_claim_trace_rate"] == 1.0

    snapshot_id = payload["output_refs"]["candidate_profile_snapshot_id"]
    profile = json.loads(
        _run(tmp_path, "candidate", "show", snapshot_id).stdout
    )["result"]
    assert profile["profile"]["evidence_basis_ids"] == [resume_v2]
    supporting_ids = set(profile["supporting_claim_ids"])
    claims = json.loads(
        _run(tmp_path, "inspect", "claims", "version-owner").stdout
    )["result"]["claims"]
    supporting = [item for item in claims if item["claim_id"] in supporting_ids]
    assert supporting
    assert all(item["origin_ref"] == resume_v2 for item in supporting)
    assert any(item["origin_ref"] == resume_v1 for item in claims)


def test_resume_then_candidate_build_show_and_claim_trace_from_installed_cli(
    tmp_path: Path,
) -> None:
    session_id = _start_session(tmp_path, "candidate-owner")
    resume_id = _confirmed_resume(
        tmp_path, session_id, "candidate-owner", sufficient=True
    )
    shown_resume = _run(tmp_path, "resume", "show", resume_id)
    assert shown_resume.returncode == 0
    assert json.loads(shown_resume.stdout)["result"]["status"] == "confirmed"

    built = _run(
        tmp_path, "candidate", "build", session_id,
        "--candidate-id", "candidate-owner", "--resume-evidence", resume_id,
    )
    assert built.returncode == 0, built.stderr
    payload = json.loads(built.stdout)
    assert payload["status"] == "completed"
    assert payload["errors"] == []
    snapshot_id = payload["output_refs"]["candidate_profile_snapshot_id"]
    assert payload["metrics"]["accepted_candidate_predicate_supported_rate"] == 1.0

    shown = _run(tmp_path, "candidate", "show", snapshot_id)
    assert shown.returncode == 0
    shown_profile = json.loads(shown.stdout)["result"]["profile"]
    assert shown_profile["capabilities"]
    assert shown_profile["completion_reason"] == "sufficient"
    assert shown_profile["evidence_basis_ids"] == [resume_id]
    inspected = _run(tmp_path, "inspect", "claims", "candidate-owner")
    claims = json.loads(inspected.stdout)["result"]["claims"]
    assert claims and all(resume_id in item["source_evidence_ids"] for item in claims)


def test_candidate_interrupt_cross_process_resume_and_duplicate_are_idempotent(
    tmp_path: Path,
) -> None:
    session_id = _start_session(tmp_path, "resume-owner")
    resume_id = _confirmed_resume(
        tmp_path, session_id, "resume-owner", sufficient=False
    )
    built = _run(
        tmp_path, "candidate", "build", session_id,
        "--candidate-id", "resume-owner", "--resume-evidence", resume_id,
    )
    payload = json.loads(built.stdout)
    assert payload["status"] == "interrupted"
    question_id = payload["pending_request"]["questions"][0]["question_id"]
    args = (
        "candidate", "resume", session_id, "--action", "answer",
        "--response-id", "response-cli-idempotent",
        "--answer", f"{question_id}=I implemented graph recovery and evaluation tests.",
    )
    resumed = _run(tmp_path, *args)
    assert resumed.returncode == 0, resumed.stderr
    resumed_payload = json.loads(resumed.stdout)
    assert resumed_payload["status"] == "completed"
    duplicate = _run(tmp_path, *args)
    duplicate_payload = json.loads(duplicate.stdout)
    assert duplicate_payload["deduplicated"] is True
    assert duplicate_payload["session_version"] == resumed_payload["session_version"]


def test_candidate_build_rejects_removed_input_and_requires_confirmed_snapshot(
    tmp_path: Path,
) -> None:
    session_id = _start_session(tmp_path, "boundary-owner")
    old = _run(
        tmp_path, "candidate", "build", session_id,
        "--candidate-id", "boundary-owner", "--input", str(tmp_path / "resume.pdf"),
    )
    assert old.returncode != 0
    assert "--resume-evidence" in old.stdout
    help_result = _run(tmp_path, "candidate", "build", "--help")
    assert "--input" not in help_result.stdout
    missing = _run(
        tmp_path, "candidate", "build", session_id,
        "--candidate-id", "boundary-owner",
        "--resume-evidence", "resume-evidence-missing",
    )
    assert missing.returncode == 3
    assert "confirmed ResumeEvidence" in missing.stdout


def test_resume_reparse_starts_new_draft_without_overwriting_snapshot(
    tmp_path: Path,
) -> None:
    session_id = _start_session(tmp_path, "reparse-owner")
    resume_id = _confirmed_resume(
        tmp_path, session_id, "reparse-owner", sufficient=True
    )
    old = json.loads(_run(tmp_path, "resume", "show", resume_id).stdout)["result"]
    resume_path = tmp_path / "reparse-owner.pdf"
    reparsed = _run(
        tmp_path, "resume", "import", session_id,
        "--candidate-id", "reparse-owner", "--input", str(resume_path),
        "--reparse",
    )
    assert reparsed.returncode == 0, reparsed.stderr
    payload = json.loads(reparsed.stdout)
    assert payload["status"] == "interrupted"
    assert payload["output_refs"]["draft_id"] != old["draft_id"]
    assert payload["pending_request"]["section"] == "personal_information"
    still_old = json.loads(
        _run(tmp_path, "resume", "show", resume_id).stdout
    )["result"]
    assert still_old == old


def test_candidate_model_contract_failure_has_terminal_run_and_error_event(
    tmp_path: Path,
) -> None:
    session_id = _start_session(tmp_path, "failure-owner")
    resume_id = _confirmed_resume(
        tmp_path, session_id, "failure-owner", sufficient=True
    )
    failed = _run_env(
        tmp_path, {"CAMPUS_AGENT_MOCK_LLM_MODE": "always_invalid_json"},
        "candidate", "build", session_id, "--candidate-id", "failure-owner",
        "--resume-evidence", resume_id,
    )
    assert failed.returncode == 3
    payload = json.loads(failed.stdout)
    assert payload["status"] == "failed"
    assert payload["next_action"] == "session.resume"
    inspected = _run(tmp_path, "inspect", "run", payload["run_id"])
    result = json.loads(inspected.stdout)["result"]
    assert result["manifest"]["status"] == "failed"
    assert any(item["error_type"] == "llm_invalid_output" for item in result["errors"])
    recovered = _run(tmp_path, "session", "resume", session_id)
    assert recovered.returncode == 0, recovered.stderr
    recovered_payload = json.loads(recovered.stdout)
    assert recovered_payload["status"] == "active"
    assert recovered_payload["next_action"] == "candidate.build"
