from __future__ import annotations

import json
from pathlib import Path

import pytest

from campus_job_agent.llm import MockLLMProvider
from campus_job_agent.runtime import ObjectRef, RuntimeFactory
from campus_job_agent.schemas import CandidateProfile, IntentReviewResponse, ProfileSnapshot


RAW_INTENT = (
    "我想找 Agent 开发岗位，工作地点必须成都，2027 年毕业，"
    "参加校招，优先大型企业以及互联网科技公司"
)


def _runtime_with_candidate(tmp_path: Path, monkeypatch, *, owner: str = "graph-owner"):
    monkeypatch.setenv("CAMPUS_AGENT_LLM_PROVIDER", "mock")
    monkeypatch.setenv("CAMPUS_AGENT_LLM_CACHE_ENABLED", "false")
    runtime = RuntimeFactory(data_root=tmp_path / "data").build(owner_id=owner)
    session = runtime.session_service.start(user_id=owner)
    profile = CandidateProfile(candidate_id=owner, schema_version="v0.7.1")
    snapshot = runtime.profile_repository.save_profile(ProfileSnapshot(
        snapshot_id=f"candidate-snapshot-{owner}", subject_id=owner,
        profile_type="candidate", version=1, schema_version="v0.7.1",
        profile_data=profile.model_dump(mode="json"),
    ))
    runtime.session_repository.register_ref(ObjectRef(
        object_id=snapshot.snapshot_id, object_type="candidate_profile_snapshot",
        owner_id=owner, schema_version="v0.7.1",
    ))
    session = runtime.session_repository.set_current_ref(
        session.session_id, key="candidate_profile_snapshot_id",
        object_id=snapshot.snapshot_id, expected_version=session.session_version,
    )
    session = runtime.session_repository.update_navigation(
        session.session_id, expected_version=session.session_version,
        operation="candidate_completed_for_intent_test", status="active",
        current_stage="intent",
    )
    return runtime, session


def test_provider_failure_keeps_raw_evidence_and_terminal_failed_run(
    tmp_path: Path, monkeypatch,
) -> None:
    runtime, session = _runtime_with_candidate(tmp_path, monkeypatch)
    runtime.llm_provider = MockLLMProvider("provider_error")

    with pytest.raises(Exception, match="Mock provider error"):
        runtime.application_services["intent"].create(
            session_id=session.session_id, raw_text=RAW_INTENT,
        )

    run_dirs = list((tmp_path / "data" / "runs").iterdir())
    assert len(run_dirs) == 1
    manifest = json.loads((run_dirs[0] / "run_manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "failed"
    assert manifest["next_action"] == "inspect.run"
    assert manifest["warnings"] == []
    assert RAW_INTENT not in json.dumps(manifest, ensure_ascii=False)
    errors = [
        json.loads(line) for line in (run_dirs[0] / "errors.jsonl")
        .read_text(encoding="utf-8").splitlines() if line
    ]
    assert errors[0]["error_type"] == "llm_unavailable"
    assert isinstance(errors[0]["retryable"], bool)
    llm_calls = [
        json.loads(line) for line in (run_dirs[0] / "llm_calls.jsonl")
        .read_text(encoding="utf-8").splitlines() if line
    ]
    assert llm_calls
    assert all(item["status"] == "failed" for item in llm_calls)

    with runtime.open_workflow("intent") as workflow:
        state = dict(workflow.get_state(manifest["thread_id"]).values or {})
    assert state["raw_text"] is None
    assert runtime.evidence_repository.get_artifact(state["raw_artifact_id"]) is not None
    assert runtime.evidence_repository.get_fragment(state["raw_fragment_id"]) is not None
    assert state.get("candidate") is None


def test_wrong_request_resume_is_rejected_before_response_evidence_write(
    tmp_path: Path, monkeypatch,
) -> None:
    runtime, session = _runtime_with_candidate(tmp_path, monkeypatch)
    created = runtime.application_services["intent"].create(
        session_id=session.session_id, raw_text=RAW_INTENT,
    )
    request = created["pending_request"]
    response = IntentReviewResponse(
        response_id="response-wrong-request", request_id="request-forged",
        thread_id=created["thread_id"], user_id=session.user_id, action="cancel",
    )
    with pytest.raises(Exception, match="request_id does not match"):
        runtime.application_services["intent"].resume(
            session_id=session.session_id, response=response,
        )
    assert runtime.intent_repository.get_response_result(response.response_id) is None
    assert runtime.session_service.status(session.session_id).pending_request == request["request_id"]


def test_graph_rejects_registered_ref_when_profile_owner_is_inconsistent(
    tmp_path: Path, monkeypatch,
) -> None:
    monkeypatch.setenv("CAMPUS_AGENT_LLM_PROVIDER", "mock")
    runtime = RuntimeFactory(data_root=tmp_path / "data").build(owner_id="session-owner")
    session = runtime.session_service.start(user_id="session-owner")
    snapshot = runtime.profile_repository.save_profile(ProfileSnapshot(
        snapshot_id="candidate-owner-mismatch", subject_id="different-owner",
        profile_type="candidate", version=1, schema_version="v0.7.1", profile_data={},
    ))
    runtime.session_repository.register_ref(ObjectRef(
        object_id=snapshot.snapshot_id, object_type="candidate_profile_snapshot",
        owner_id="session-owner", schema_version="v0.7.1",
    ))
    session = runtime.session_repository.set_current_ref(
        session.session_id, key="candidate_profile_snapshot_id",
        object_id=snapshot.snapshot_id, expected_version=session.session_version,
    )
    session = runtime.session_repository.update_navigation(
        session.session_id, expected_version=session.session_version,
        operation="attach_inconsistent_candidate", status="active", current_stage="intent",
    )
    with pytest.raises(Exception, match="owner-mismatched"):
        runtime.application_services["intent"].create(
            session_id=session.session_id, raw_text=RAW_INTENT,
        )
