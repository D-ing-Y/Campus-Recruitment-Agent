from __future__ import annotations

import json
import os
import sqlite3
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
CLI = Path(os.environ.get(
    "CAMPUS_AGENT_TEST_CLI", str(REPO_ROOT / ".venv" / "bin" / "campus-agent")
))
RAW_INTENT = (
    "我想找 Agent 开发岗位，工作地点必须成都，2027 年毕业，"
    "参加校招，优先大型企业以及互联网科技公司"
)


def _run(tmp_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["CAMPUS_AGENT_DATA_ROOT"] = str(tmp_path / "data")
    env["CAMPUS_AGENT_LLM_PROVIDER"] = "mock"
    env["CAMPUS_AGENT_LLM_CACHE_ENABLED"] = "false"
    return subprocess.run(
        [str(CLI), "--json", *args], cwd=tmp_path, env=env,
        text=True, capture_output=True, check=False,
    )


def _json(result: subprocess.CompletedProcess[str]) -> dict:
    assert result.stdout, result.stderr
    return json.loads(result.stdout)


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


def _seed_candidate(tmp_path: Path, user_id: str) -> tuple[str, str]:
    started = _run(tmp_path, "session", "start", "--user-id", user_id)
    assert started.returncode == 0, started.stderr
    session_id = _json(started)["session_id"]
    resume = tmp_path / f"{user_id}.pdf"
    _text_pdf(resume, (
        "Anonymous University expected graduation 2027. Project Candidate Evidence Workflow. "
        "Responsibilities implemented graph checkpoint recovery and evaluation. "
        "Skills Python LangGraph RAG LLM. " * 2
    ))
    imported = _run(
        tmp_path, "resume", "import", session_id,
        "--candidate-id", user_id, "--input", str(resume),
    )
    assert imported.returncode == 0, imported.stderr
    resume_payload = _json(imported)
    for index in range(30):
        if resume_payload["status"] != "interrupted":
            break
        reviewed = _run(
            tmp_path, "resume", "resume", session_id,
            "--action", "confirm", "--response-id", f"resume-{user_id}-{index}",
        )
        assert reviewed.returncode == 0, reviewed.stderr
        resume_payload = _json(reviewed)
    assert resume_payload["status"] == "completed"
    built = _run(
        tmp_path, "candidate", "build", session_id,
        "--candidate-id", user_id,
        "--resume-evidence", resume_payload["output_refs"]["resume_evidence_id"],
    )
    assert built.returncode == 0, built.stderr
    payload = _json(built)
    assert payload["status"] == "completed"
    return session_id, payload["output_refs"]["candidate_profile_snapshot_id"]


def _record_counts(data_root: Path) -> tuple[int, int, int]:
    with sqlite3.connect(data_root / "db" / "intent.sqlite3") as connection:
        snapshot_related = connection.execute(
            "SELECT COUNT(*) FROM career_intent_records WHERE record_kind IN ('search_scope', 'confirmation')"
        ).fetchone()[0]
        responses = connection.execute(
            "SELECT COUNT(*) FROM career_intent_response_receipts"
        ).fetchone()[0]
    with sqlite3.connect(data_root / "db" / "sessions.sqlite3") as connection:
        handoffs = connection.execute("SELECT COUNT(*) FROM runtime_handoffs").fetchone()[0]
    return snapshot_related, responses, handoffs


def test_installed_cli_intent_interrupt_revision_confirmation_and_idempotency(
    tmp_path: Path,
) -> None:
    session_id, _candidate_snapshot_id = _seed_candidate(tmp_path, "intent-cli-owner")

    created = _run(tmp_path, "intent", "create", session_id, "--text", RAW_INTENT)
    assert created.returncode == 0, created.stderr
    created_payload = _json(created)
    assert created_payload["status"] == "interrupted"
    assert created_payload["next_action"] == "intent.resume"
    assert created_payload["metrics"]["raw_intent_evidence_trace_rate"] == 1.0
    assert created_payload["metrics"]["confirmed_constraint_trace_rate"] is None
    assert created_payload["metrics"]["search_scope_projection_accuracy"] is None
    request = created_payload["pending_request"]
    assert request["unresolved_fields"] == ["recruitment_type"]
    assert RAW_INTENT not in created.stdout

    premature = _run(
        tmp_path, "intent", "resume", session_id,
        "--action", "confirm", "--response-id", "intent-response-premature",
    )
    assert premature.returncode == 0, premature.stderr
    premature_payload = _json(premature)
    assert premature_payload["status"] == "interrupted"
    assert premature_payload["output_refs"]["career_intent_snapshot_id"] is None

    revised = _run(
        tmp_path, "intent", "resume", session_id,
        "--action", "revise", "--response-id", "intent-response-revise",
        "--patch", json.dumps({"recruitment_type": "autumn_campus"}),
    )
    assert revised.returncode == 0, revised.stderr
    revised_payload = _json(revised)
    assert revised_payload["status"] == "interrupted"
    assert revised_payload["pending_request"]["unresolved_fields"] == []
    assert revised_payload["pending_request"]["validation_issues"] == []

    confirmed = _run(
        tmp_path, "intent", "resume", session_id,
        "--action", "confirm", "--response-id", "intent-response-confirmed",
    )
    assert confirmed.returncode == 0, confirmed.stderr
    confirmed_payload = _json(confirmed)
    assert confirmed_payload["status"] == "completed"
    assert confirmed_payload["next_action"] == "role.research"
    assert confirmed_payload["metrics"]["confirmed_constraint_trace_rate"] == 1.0
    assert confirmed_payload["metrics"]["search_scope_projection_accuracy"] == 1.0
    refs = confirmed_payload["output_refs"]
    assert all(refs.values())

    shown = _run(tmp_path, "intent", "show", refs["career_intent_snapshot_id"])
    assert shown.returncode == 0, shown.stderr
    result = _json(shown)["result"]
    profile = result["profile"]
    assert profile["confirmed"] is True
    assert profile["target_roles"] == ["Agent 开发"]
    assert profile["target_role_families"] == ["ai_agent_engineering"]
    assert profile["locations"] == ["成都"]
    assert profile["graduation_year"] == "2027"
    assert profile["recruitment_type"] == "autumn_campus"
    assert profile["company_types"] == []
    company_preferences = [
        item for item in profile["constraints"] if item["key"] == "company_type"
    ]
    assert len(company_preferences) == 1
    assert company_preferences[0]["kind"] == "negotiable"
    assert company_preferences[0]["affects_search_scope"] is False
    assert company_preferences[0]["value"] == ["大型企业", "互联网科技公司"]

    scope = result["search_scopes"][0]
    assert scope["career_intent_snapshot_id"] == refs["career_intent_snapshot_id"]
    assert scope["locations"] == ["成都"]
    assert scope["graduation_year"] == "2027"
    assert scope["recruitment_type"] == "autumn_campus"

    data_root = tmp_path / "data"
    before_duplicate = _record_counts(data_root)
    duplicate = _run(
        tmp_path, "intent", "resume", session_id,
        "--action", "confirm", "--response-id", "intent-response-confirmed",
    )
    assert duplicate.returncode == 0, duplicate.stderr
    duplicate_payload = _json(duplicate)
    assert duplicate_payload["deduplicated"] is True
    assert duplicate_payload["session_version"] == confirmed_payload["session_version"]
    assert _record_counts(data_root) == before_duplicate

    conflict = _run(
        tmp_path, "intent", "resume", session_id,
        "--action", "cancel", "--response-id", "intent-response-confirmed",
    )
    assert conflict.returncode != 0
    assert _json(conflict)["errors"][0]["error_type"] == "contract_violation"

    state_path = Path(confirmed_payload["artifact_paths"]["state"])
    state_text = state_path.read_text(encoding="utf-8")
    assert RAW_INTENT not in state_text
    assert "API key" not in state_text


def test_intent_create_requires_current_candidate_and_cancel_does_not_publish(
    tmp_path: Path,
) -> None:
    started = _run(tmp_path, "session", "start", "--user-id", "no-candidate-owner")
    session_without_candidate = _json(started)["session_id"]
    refused = _run(
        tmp_path, "intent", "create", session_without_candidate, "--text", RAW_INTENT,
    )
    assert refused.returncode != 0
    assert "Candidate snapshot is required" in _json(refused)["errors"][0]["message"]

    session_id, _ = _seed_candidate(tmp_path, "cancel-intent-owner")
    created = _run(tmp_path, "intent", "create", session_id, "--text", RAW_INTENT)
    assert created.returncode == 0
    cancelled = _run(
        tmp_path, "intent", "resume", session_id,
        "--action", "cancel", "--response-id", "intent-response-cancel",
    )
    assert cancelled.returncode == 0, cancelled.stderr
    payload = _json(cancelled)
    assert payload["status"] == "cancelled"
    assert payload["output_refs"]["career_intent_snapshot_id"] is None
