from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
CLI = REPO_ROOT / ".venv" / "bin" / "campus-agent"
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "v04"


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


def test_candidate_build_show_diff_and_inspect_claim_receipts_from_installed_cli(
    tmp_path: Path,
) -> None:
    session_id = _start_session(tmp_path, "candidate-owner")
    built = _run(
        tmp_path, "candidate", "build", session_id,
        "--candidate-id", "candidate-owner",
        "--input", str(FIXTURES / "candidate_sufficient.md"),
    )
    assert built.returncode == 0, built.stderr
    payload = json.loads(built.stdout)
    assert payload["status"] == "completed"
    assert payload["next_action"] == "intent.create"
    snapshot_id = payload["output_refs"]["candidate_profile_snapshot_id"]
    metrics = payload["metrics"]
    assert metrics["model_item_receipt_rate"] == 1.0
    assert metrics["accepted_candidate_predicate_supported_rate"] == 1.0
    assert metrics["accepted_claim_projection_rate"] == 1.0
    assert metrics["silent_unprojected_active_claim_count"] == 0

    shown = _run(tmp_path, "candidate", "show", snapshot_id)
    assert shown.returncode == 0
    show_payload = json.loads(shown.stdout)
    assert show_payload["result"]["snapshot_id"] == snapshot_id
    assert show_payload["result"]["profile"]["capabilities"]
    assert "raw_text" not in shown.stdout

    diffed = _run(tmp_path, "candidate", "diff", snapshot_id, snapshot_id)
    assert diffed.returncode == 0
    assert json.loads(diffed.stdout)["result"]["changed_paths"] == []

    inspected = _run(tmp_path, "inspect", "claims", "candidate-owner")
    assert inspected.returncode == 0
    inspect_payload = json.loads(inspected.stdout)["result"]
    assert inspect_payload["claims"]
    assert inspect_payload["validation_receipts"]
    assert all(item["status"] in {"accepted", "duplicate"} for item in inspect_payload["validation_receipts"])

    rebuilt = _run(
        tmp_path, "candidate", "build", session_id,
        "--candidate-id", "candidate-owner",
        "--input", str(FIXTURES / "candidate_sufficient.md"),
    )
    assert rebuilt.returncode == 0, rebuilt.stderr
    rebuilt_payload = json.loads(rebuilt.stdout)
    assert rebuilt_payload["output_refs"]["candidate_profile_snapshot_id"] == snapshot_id
    assert set(rebuilt_payload["output_refs"]["claim_ids"]) == set(payload["output_refs"]["claim_ids"])


def test_candidate_interrupt_cross_process_resume_and_duplicate_response_are_idempotent(
    tmp_path: Path,
) -> None:
    session_id = _start_session(tmp_path, "resume-owner")
    built = _run(
        tmp_path, "candidate", "build", session_id,
        "--candidate-id", "resume-candidate",
        "--input", str(FIXTURES / "candidate_missing_responsibility.md"),
    )
    assert built.returncode == 0, built.stderr
    payload = json.loads(built.stdout)
    assert payload["status"] == "interrupted"
    assert payload["next_action"] == "candidate.resume"
    request = payload["pending_request"]
    assert request["request_id"].startswith("request-")
    question_id = request["questions"][0]["question_id"]

    resumed = _run(
        tmp_path, "candidate", "resume", session_id, "--action", "answer",
        "--response-id", "response-cli-idempotent",
        "--answer", f"{question_id}=I implemented graph recovery and evaluation tests.",
    )
    assert resumed.returncode == 0, resumed.stderr
    resumed_payload = json.loads(resumed.stdout)
    assert resumed_payload["status"] == "completed"
    assert resumed_payload["next_action"] == "intent.create"

    duplicate = _run(
        tmp_path, "candidate", "resume", session_id, "--action", "answer",
        "--response-id", "response-cli-idempotent",
        "--answer", f"{question_id}=I implemented graph recovery and evaluation tests.",
    )
    assert duplicate.returncode == 0, duplicate.stderr
    duplicate_payload = json.loads(duplicate.stdout)
    assert duplicate_payload["deduplicated"] is True
    assert duplicate_payload["metrics"]["duplicate_resume_write_count"] == 0
    assert duplicate_payload["session_version"] == resumed_payload["session_version"]


def test_candidate_cli_supports_upload_skip_cancel_and_correct(tmp_path: Path) -> None:
    upload_session = _start_session(tmp_path, "upload-owner")
    interrupted = _run(
        tmp_path, "candidate", "build", upload_session,
        "--candidate-id", "upload-owner",
    )
    upload_request = json.loads(interrupted.stdout)["pending_request"]
    uploaded = _run(
        tmp_path, "candidate", "resume", upload_session, "--action", "upload",
        "--response-id", "response-cli-upload",
        "--upload", str(FIXTURES / "candidate_sufficient.md"),
    )
    assert uploaded.returncode == 0, uploaded.stderr
    assert json.loads(uploaded.stdout)["status"] == "completed"

    skip_session = _start_session(tmp_path, "skip-owner")
    interrupted = _run(
        tmp_path, "candidate", "build", skip_session,
        "--candidate-id", "skip-owner",
        "--input", str(FIXTURES / "candidate_missing_responsibility.md"),
    )
    skip_request = json.loads(interrupted.stdout)["pending_request"]
    skipped = _run(
        tmp_path, "candidate", "resume", skip_session, "--action", "skip",
        "--response-id", "response-cli-skip",
        "--skip-id", skip_request["questions"][0]["question_id"],
    )
    assert skipped.returncode == 0, skipped.stderr
    assert json.loads(skipped.stdout)["status"] == "completed_with_unknowns"

    cancel_session = _start_session(tmp_path, "cancel-owner")
    interrupted = _run(
        tmp_path, "candidate", "build", cancel_session,
        "--candidate-id", "cancel-owner",
    )
    assert json.loads(interrupted.stdout)["pending_request"]
    cancelled = _run(
        tmp_path, "candidate", "resume", cancel_session, "--action", "cancel",
        "--response-id", "response-cli-cancel",
    )
    assert cancelled.returncode == 0, cancelled.stderr
    assert json.loads(cancelled.stdout)["status"] == "cancelled"

    correction_session = _start_session(tmp_path, "correction-owner")
    conflicted = _run(
        tmp_path, "candidate", "build", correction_session,
        "--candidate-id", "correction-owner",
        "--input", str(FIXTURES / "candidate_conflict_a.md"),
        "--input", str(FIXTURES / "candidate_conflict_b.md"),
    )
    conflicted_payload = json.loads(conflicted.stdout)
    before_id = conflicted_payload["output_refs"]["candidate_profile_snapshot_id"]
    before = json.loads(_run(tmp_path, "candidate", "show", before_id).stdout)
    conflict = before["result"]["profile"]["conflicts"][0]
    correction = json.dumps({
        "correction_id": "correction-cli-1",
        "candidate_id": "correction-owner",
        "target_path": conflict["predicate"],
        "operation": "replace",
        "new_value": "Implemented the evaluation tests only.",
        "reason": "The source overstated responsibility.",
        "supersedes_claim_ids": conflict["claim_ids"],
    })
    corrected = _run(
        tmp_path, "candidate", "resume", correction_session,
        "--action", "correct", "--response-id", "response-cli-correct",
        "--correction", correction,
    )
    assert corrected.returncode == 0, corrected.stderr
    corrected_payload = json.loads(corrected.stdout)
    assert corrected_payload["status"] == "completed"
    assert corrected_payload["output_refs"]["candidate_profile_snapshot_id"] != before_id


def test_candidate_model_contract_failure_has_terminal_run_and_error_event(
    tmp_path: Path,
) -> None:
    session_id = _start_session(tmp_path, "failure-owner")
    failed = _run_env(
        tmp_path, {"CAMPUS_AGENT_MOCK_LLM_MODE": "always_invalid_json"},
        "candidate", "build", session_id,
        "--candidate-id", "failure-owner",
        "--input", str(FIXTURES / "candidate_sufficient.md"),
    )
    assert failed.returncode == 3
    payload = json.loads(failed.stdout)
    assert payload["status"] == "failed"
    assert payload["next_action"] == "inspect.run"
    inspected = _run(tmp_path, "inspect", "run", payload["run_id"])
    assert inspected.returncode == 0
    result = json.loads(inspected.stdout)["result"]
    assert result["manifest"]["status"] == "failed"
    assert any(item["error_type"] == "llm_invalid_output" for item in result["errors"])
