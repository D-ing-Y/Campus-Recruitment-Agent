from __future__ import annotations

import json
import hashlib
import os
import sqlite3
import subprocess
from pathlib import Path

from campus_job_agent.runtime import Handoff, RuntimeFactory
from campus_job_agent.schemas import (
    ClaimExtractor, EvidenceArtifact, EvidenceClaim, EvidenceFragment, ProfileSnapshot,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
CLI = REPO_ROOT / ".venv" / "bin" / "campus-agent"


def _run(tmp_path: Path, *args: str, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["CAMPUS_AGENT_DATA_ROOT"] = str(tmp_path / "shared-data")
    return subprocess.run(
        [str(CLI), *args], cwd=tmp_path, env=env, input=input_text,
        text=True, capture_output=True, check=False,
    )


def test_default_guide_doctor_and_json_are_cwd_independent(tmp_path: Path) -> None:
    guide = _run(tmp_path)
    assert guide.returncode == 0
    assert "session start" in guide.stdout

    doctor = _run(tmp_path, "--json", "doctor")
    assert doctor.returncode == 0, doctor.stderr
    payload = json.loads(doctor.stdout)
    assert payload["status"] in {"completed", "partial"}
    assert Path(payload["checks"]["paths"]["data_root"]) == (tmp_path / "shared-data").resolve()
    assert not (tmp_path / "data").exists()


def test_doctor_reports_incomplete_llm_without_exposing_or_crashing(tmp_path: Path) -> None:
    env = dict(os.environ)
    env["CAMPUS_AGENT_DATA_ROOT"] = str(tmp_path / "shared-data")
    env["CAMPUS_AGENT_LLM_PROVIDER"] = "openai_compatible"
    env.pop("OPENAI_API_KEY", None)
    env.pop("OPENAI_BASE_URL", None)
    env.pop("OPENAI_MODEL", None)
    result = subprocess.run(
        [str(CLI), "--json", "doctor"], cwd=tmp_path, env=env,
        text=True, capture_output=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] == "partial"
    assert payload["checks"]["llm"]["configuration_complete"] is False
    assert payload["checks"]["llm"]["api_key_present"] is False


def test_session_start_status_resume_history_and_inspect(tmp_path: Path) -> None:
    started = _run(tmp_path, "--json", "session", "start", "--user-id", "user-1", "--idempotency-key", "case-1")
    assert started.returncode == 0, started.stderr
    start_payload = json.loads(started.stdout)
    session_id = start_payload["session_id"]
    run_id = start_payload["run_id"]

    duplicate = _run(tmp_path, "--json", "session", "start", "--user-id", "user-1", "--idempotency-key", "case-1")
    assert json.loads(duplicate.stdout)["session_id"] == session_id

    for args in (
        ("session", "status", session_id),
        ("session", "resume", session_id),
        ("session", "history", session_id),
        ("inspect", "run", run_id),
        ("inspect", "node", run_id),
        ("inspect", "llm", run_id),
        ("inspect", "handoff", "--run-id", run_id),
    ):
        result = _run(tmp_path, "--json", *args)
        assert result.returncode == 0, (args, result.stderr)
        json.loads(result.stdout)


def test_cli_errors_and_legacy_label_are_stable(tmp_path: Path) -> None:
    missing = _run(tmp_path, "--json", "session", "status", "session-missing")
    assert missing.returncode == 3
    assert json.loads(missing.stdout)["errors"][0]["error_type"] == "not_found"

    legacy_help = _run(tmp_path, "run", "--help")
    assert "legacy-mini-runtime" in legacy_help.stdout
    legacy = _run(tmp_path, "--json", "run", "成都 AI Agent 2027 秋招")
    assert legacy.returncode == 0
    payload = json.loads(legacy.stdout)
    assert payload["workflow"] == "legacy-mini-runtime"
    assert "not_formal_business_workflow" in payload["warnings"]

    invalid = _run(tmp_path, "session", "status", "--json")
    assert invalid.returncode == 2
    assert json.loads(invalid.stdout)["errors"][0]["error_type"] == "invalid_input"

    storage = _run(tmp_path, "--json", "--data-root", "/dev/null/not-a-directory", "doctor")
    assert storage.returncode == 5
    assert json.loads(storage.stdout)["errors"][0]["error_type"] == "storage_failure"

    runtime = RuntimeFactory(data_root=tmp_path / "shared-data").build(owner_id="contract-user")
    session = runtime.session_service.start(user_id="contract-user")
    interrupted = runtime.session_repository.update_navigation(
        session.session_id, expected_version=session.session_version,
        operation="test_interrupted", status="interrupted", pending_request="request-contract",
    )
    contract = _run(
        tmp_path, "--json", "session", "resume", interrupted.session_id,
        "--expected-version", str(interrupted.session_version - 1),
    )
    assert contract.returncode == 3
    assert json.loads(contract.stdout)["errors"][0]["error_type"] == "stale_input"

    external_env = dict(os.environ)
    external_env["CAMPUS_AGENT_DATA_ROOT"] = str(tmp_path / "external-data")
    external_env["CAMPUS_AGENT_MOCK_LLM_MODE"] = "provider_error"
    external_env["CAMPUS_AGENT_LLM_CACHE_ENABLED"] = "false"
    external = subprocess.run(
        [str(CLI), "--json", "run", "成都 AI Agent 2027 秋招"],
        cwd=tmp_path, env=external_env, text=True, capture_output=True, check=False,
    )
    assert external.returncode == 4
    assert json.loads(external.stdout)["errors"][0]["error_type"] == "external_dependency"

    corrupt_run = tmp_path / "shared-data" / "runs" / "run-corrupt"
    corrupt_run.mkdir(parents=True)
    (corrupt_run / "run_manifest.json").write_text("{broken", encoding="utf-8")
    damaged = _run(tmp_path, "--json", "inspect", "run", "run-corrupt")
    assert damaged.returncode == 5
    assert json.loads(damaged.stdout)["errors"][0]["error_type"] == "storage_failure"

    internal_session = runtime.session_service.start(user_id="internal-user")
    with sqlite3.connect(runtime.session_repository.database_path) as connection:
        connection.execute(
            "UPDATE run_sessions SET payload_json = ? WHERE session_id = ?",
            ("{}", internal_session.session_id),
        )
    internal = _run(tmp_path, "--json", "session", "status", internal_session.session_id)
    assert internal.returncode == 6
    assert json.loads(internal.stdout)["errors"][0]["error_type"] == "internal_error"


def test_inspect_resolves_evidence_claim_profile_and_handoff_without_checkpoint_parsing(tmp_path: Path) -> None:
    data_root = tmp_path / "shared-data"
    runtime = RuntimeFactory(data_root=data_root).build(owner_id="user-inspect")
    raw = b"safe synthetic material"
    digest = hashlib.sha256(raw).hexdigest()
    uri = runtime.blob_store.put("inspect/material.txt", raw)
    artifact = runtime.evidence_repository.save_artifact(EvidenceArtifact(
        artifact_id="artifact-inspect", owner_id="user-inspect", source_type="fixture",
        content_type="text/plain", original_name="material.txt", raw_uri=uri,
        content_hash=digest,
    ))
    fragment = runtime.evidence_repository.save_fragment(EvidenceFragment(
        fragment_id="fragment-inspect", artifact_id=artifact.artifact_id,
        locator_type="line_range", locator={"start": 1, "end": 1},
        text="safe synthetic material", text_hash=digest,
    ))
    claim = runtime.evidence_repository.save_claim(EvidenceClaim(
        claim_id="claim-inspect", subject_id="candidate-inspect",
        predicate="capability:cap:python", value="intermediate",
        claim_type="observed_fact", evidence_fragment_ids=[fragment.fragment_id],
        confidence=1.0, extractor=ClaimExtractor(provider="fixture", model="fixture"),
        prompt_version="fixture-v1", schema_version="v0.7",
    ))
    profile = runtime.profile_repository.save_profile(ProfileSnapshot(
        snapshot_id="profile-inspect", subject_id="candidate-inspect",
        profile_type="candidate", version=1, schema_version="v0.7",
        profile_data={"private_resume": "must not appear", "capabilities": []},
        supporting_claim_ids=[claim.claim_id],
    ))
    session = runtime.session_service.start(user_id="user-inspect")
    handoff = runtime.session_repository.save_handoff(Handoff(
        handoff_id="handoff-inspect", session_id=session.session_id,
        user_id=session.user_id, handoff_type="rematch_required",
        origin_run_id="run-inspect", handler_version="rematch-v1",
    ))

    cases = (
        ("evidence", artifact.artifact_id),
        ("claims", claim.subject_id),
        ("profile", profile.snapshot_id),
        ("handoff", handoff.handoff_id),
    )
    for kind, object_id in cases:
        result = _run(tmp_path, "--json", "inspect", kind, object_id)
        assert result.returncode == 0, (kind, result.stderr)
        payload = json.loads(result.stdout)
        assert payload["status"] == "completed"
        assert "must not appear" not in result.stdout
