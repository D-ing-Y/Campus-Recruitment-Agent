from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from campus_job_agent.runtime import (
    ArtifactEntry,
    ArtifactWriteError,
    Handoff,
    NodeObserver,
    ObjectRef,
    RunArtifactWriter,
    RunEvent,
    RuntimeFactory,
    SessionConflictError,
    SessionReferenceError,
    exit_code_for_error,
)
from campus_job_agent.runtime.models import ErrorEvent


def test_runtime_factory_uses_explicit_absolute_data_root(tmp_path: Path) -> None:
    runtime = RuntimeFactory(data_root=tmp_path / "data").build(owner_id="user-1")
    assert runtime.paths.data_root == (tmp_path / "data").resolve()
    assert runtime.paths.run_root == (tmp_path / "data" / "runs").resolve()
    assert runtime.evidence_repository.database_path.is_absolute()
    assert set(runtime.checkpoint_paths) == {
        "candidate", "intent", "role", "matching", "preparation", "feedback"
    }
    for workflow in runtime.checkpoint_paths:
        with runtime.open_workflow(workflow) as graph_runtime:
            assert graph_runtime.app is not None


def test_session_cas_owner_stale_and_idempotent_refs(tmp_path: Path) -> None:
    runtime = RuntimeFactory(data_root=tmp_path / "data").build(owner_id="user-1")
    session = runtime.session_service.start(user_id="user-1", idempotency_key="same")
    assert runtime.session_service.start(
        user_id="user-1", idempotency_key="same"
    ).session_id == session.session_id

    ref = ObjectRef(
        object_id="candidate-snapshot-1",
        object_type="candidate_profile_snapshot",
        owner_id="user-1",
        schema_version="v0.7",
        lifecycle_status="current",
    )
    runtime.session_repository.register_ref(ref)
    updated = runtime.session_repository.set_current_ref(
        session.session_id,
        key="candidate_profile_snapshot_id",
        object_id=ref.object_id,
        expected_version=session.session_version,
    )
    same = runtime.session_repository.set_current_ref(
        session.session_id,
        key="candidate_profile_snapshot_id",
        object_id=ref.object_id,
        expected_version=updated.session_version,
    )
    assert same.session_version == updated.session_version

    with pytest.raises(SessionConflictError):
        runtime.session_repository.set_current_ref(
            session.session_id,
            key="candidate_profile_snapshot_id",
            object_id=ref.object_id,
            expected_version=session.session_version,
        )

    wrong_owner = ref.model_copy(
        update={"object_id": "candidate-snapshot-2", "owner_id": "user-2"}
    )
    runtime.session_repository.register_ref(wrong_owner)
    with pytest.raises(SessionReferenceError, match="owner"):
        runtime.session_repository.set_current_ref(
            session.session_id,
            key="candidate_profile_snapshot_id",
            object_id=wrong_owner.object_id,
            expected_version=updated.session_version,
        )

    stale = ref.model_copy(
        update={"object_id": "candidate-snapshot-3", "lifecycle_status": "stale"}
    )
    runtime.session_repository.register_ref(stale)
    with pytest.raises(SessionReferenceError, match="stale"):
        runtime.session_repository.set_current_ref(
            session.session_id,
            key="candidate_profile_snapshot_id",
            object_id=stale.object_id,
            expected_version=updated.session_version,
        )


def test_run_bundle_manifest_events_locking_and_redaction(tmp_path: Path) -> None:
    writer = RunArtifactWriter(tmp_path / "runs")
    manifest = writer.initialize_run(
        session_id="session-1",
        thread_id="thread-1",
        workflow="runtime",
        command="test.run",
    )
    assert writer.load_manifest(manifest.run_id).status == "running"

    def append(index: int) -> int:
        event = RunEvent(
            run_id=manifest.run_id,
            session_id=manifest.session_id,
            thread_id=manifest.thread_id,
            event_type="node_finished",
            workflow="runtime",
            node=f"node-{index}",
            status="completed",
            duration_ms=index + 1,
            counts={"outputs": index},
        )
        return writer.append_event(event).sequence

    with ThreadPoolExecutor(max_workers=8) as pool:
        sequences = list(pool.map(append, range(24)))
    assert sorted(sequences) == list(range(2, 26))  # run_started is sequence 1

    writer.write_state(
        manifest.run_id,
        {"credential": "secret-value", "resume_text": "private " * 200},
    )
    state_text = (tmp_path / "runs" / manifest.run_id / "state.json").read_text()
    assert "secret-value" not in state_text
    assert "private private" not in state_text
    assert json.loads(state_text)["credential"] == "[REDACTED]"

    terminal = writer.finish_run(manifest.run_id, status="interrupted", next_action="session.resume")
    assert terminal.status == "interrupted"
    assert terminal.ended_at is not None
    expected = {
        "run_manifest.json", "events.jsonl", "state.json", "llm_calls.jsonl",
        "errors.jsonl", "artifact_index.json", "handoffs.jsonl", "report.md",
    }
    assert expected == {path.name for path in (tmp_path / "runs" / manifest.run_id).iterdir() if not path.name.startswith(".")}


@pytest.mark.parametrize(
    "terminal_status",
    [
        "completed", "completed_with_unknowns", "partial", "blocked",
        "blocked_by_auth", "interrupted", "reroute_required", "awaiting_rebuild",
        "cancelled", "failed",
    ],
)
def test_every_terminal_status_is_durable(tmp_path: Path, terminal_status: str) -> None:
    writer = RunArtifactWriter(tmp_path / "runs")
    manifest = writer.initialize_run(
        session_id="session-terminal", thread_id="thread-terminal",
        workflow="runtime", command="test.terminal",
    )
    terminal = writer.finish_run(manifest.run_id, status=terminal_status)  # type: ignore[arg-type]
    assert terminal.status == terminal_status
    assert writer.load_manifest(manifest.run_id).ended_at is not None
    assert writer.read_jsonl(manifest.run_id, "events")[-1]["status"] == terminal_status


def test_node_events_pair_real_outputs_and_error_recovery_hint(tmp_path: Path) -> None:
    writer = RunArtifactWriter(tmp_path / "runs")
    manifest = writer.initialize_run(
        session_id="session-node", thread_id="thread-node",
        workflow="runtime", command="test.node",
    )
    with NodeObserver(writer, manifest, "real_node", input_refs={"input_id": "in-1"}) as node:
        node.finish(
            output_refs={"output_id": "out-1"}, counts={"outputs": 1},
            route="next_node", reason_codes=["validated"], fallback="none",
        )
    writer.append_error(ErrorEvent(
        run_id=manifest.run_id, workflow="runtime", node="failed_node",
        error_type="storage_failure", message="safe failure", retryable=True,
        recovery_hint="retry after checking the data root",
    ))
    events = writer.read_jsonl(manifest.run_id, "events")
    started = next(item for item in events if item.get("node") == "real_node" and item["event_type"] == "node_started")
    finished = next(item for item in events if item.get("node") == "real_node" and item["event_type"] == "node_finished")
    assert started["status"] == "running"
    assert finished["status"] == "completed"
    assert finished["duration_ms"] > 0
    assert finished["counts"] == {"outputs": 1}
    assert finished["route"] == "next_node"
    errors = writer.read_jsonl(manifest.run_id, "errors")
    assert errors[0]["recovery_hint"] == "retry after checking the data root"


def test_artifact_initialization_failure_leaves_terminal_manifest(tmp_path: Path) -> None:
    class FailOnceWriter(RunArtifactWriter):
        failed = False

        def _atomic_text(self, path: Path, text: str) -> None:
            if path.name == "events.jsonl" and not self.failed:
                self.failed = True
                raise ArtifactWriteError("injected writer failure")
            super()._atomic_text(path, text)

    writer = FailOnceWriter(tmp_path / "runs")
    with pytest.raises(ArtifactWriteError):
        writer.initialize_run(
            session_id="session-failure", thread_id="thread-failure",
            workflow="runtime", command="test.failure",
        )
    run_dirs = list((tmp_path / "runs").iterdir())
    assert len(run_dirs) == 1
    manifest = json.loads((run_dirs[0] / "run_manifest.json").read_text())
    assert manifest["status"] == "failed"
    assert "artifact_initialization_failed" in manifest["warnings"]


def test_terminal_artifact_failure_is_not_reported_as_success(tmp_path: Path) -> None:
    class FailTerminalOnceWriter(RunArtifactWriter):
        fail_terminal = False

        def _atomic_json(self, path: Path, value: object) -> None:
            status = getattr(value, "status", None)
            if path.name == "run_manifest.json" and status == "completed" and not self.fail_terminal:
                self.fail_terminal = True
                raise ArtifactWriteError("injected terminal failure")
            super()._atomic_json(path, value)  # type: ignore[arg-type]

    writer = FailTerminalOnceWriter(tmp_path / "runs")
    manifest = writer.initialize_run(
        session_id="session-mid-failure", thread_id="thread-mid-failure",
        workflow="runtime", command="test.mid-failure",
    )
    with pytest.raises(ArtifactWriteError, match="terminal run manifest"):
        writer.finish_run(manifest.run_id, status="completed")
    failed = writer.load_manifest(manifest.run_id)
    assert failed.status == "failed"
    assert "terminal_artifact_write_failed" in failed.warnings


def test_session_survives_factory_restart_and_duplicate_resume_is_noop(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    first = RuntimeFactory(data_root=data_root).build(owner_id="user-restart")
    session = first.session_service.start(user_id="user-restart", idempotency_key="restart")
    interrupted = first.session_repository.update_navigation(
        session.session_id, expected_version=session.session_version,
        operation="interrupted", status="interrupted", pending_request="request-1",
    )
    second = RuntimeFactory(data_root=data_root).build(owner_id="user-restart")
    resumed = second.session_service.resume(interrupted.session_id)
    assert resumed.status == "active"
    duplicate = second.session_service.resume(interrupted.session_id)
    assert duplicate.session_version == resumed.session_version


def test_handoff_resolution_validates_successor_and_is_idempotent(tmp_path: Path) -> None:
    runtime = RuntimeFactory(data_root=tmp_path / "data").build(owner_id="user-handoff")
    session = runtime.session_service.start(user_id="user-handoff")
    origin = ObjectRef(
        object_id="candidate-old", object_type="candidate_profile_snapshot",
        owner_id="user-handoff", schema_version="v0.7", lifecycle_status="historical",
    )
    successor = ObjectRef(
        object_id="candidate-new", object_type="candidate_profile_snapshot",
        owner_id="user-handoff", schema_version="v0.7", lifecycle_status="current",
        predecessor_ids=[origin.object_id], successor_of=origin.object_id,
    )
    runtime.session_repository.register_ref(origin)
    runtime.session_repository.register_ref(successor)
    handoff = runtime.session_repository.save_handoff(Handoff(
        session_id=session.session_id, user_id=session.user_id,
        handoff_type="candidate_profile_rebuild_required", origin_run_id="run-origin",
        origin_object_refs={"candidate_profile_snapshot_id": origin.object_id},
        handler_version="candidate-rebuild-v1",
    ))
    resolved = runtime.session_repository.resolve_handoff(
        handoff.handoff_id,
        resolved_refs={"candidate_profile_snapshot_id": successor.object_id},
        user_id=session.user_id,
    )
    duplicate = runtime.session_repository.resolve_handoff(
        handoff.handoff_id,
        resolved_refs={"candidate_profile_snapshot_id": successor.object_id},
        user_id=session.user_id,
    )
    assert resolved.status == "resolved"
    assert duplicate.attempt_count == 1


def test_artifact_index_is_navigation_only_and_idempotent(tmp_path: Path) -> None:
    writer = RunArtifactWriter(tmp_path / "runs")
    manifest = writer.initialize_run(
        session_id="session-index", thread_id="thread-index",
        workflow="runtime", command="test.index",
    )
    entry = ArtifactEntry(
        logical_type="candidate_profile", object_id="snapshot-1",
        locator="repository://profiles/snapshot-1", owner="user-1",
        sensitivity="private", canonical_hash="sha256:abc",
    )
    first = writer.add_artifact(manifest.run_id, entry)
    second = writer.add_artifact(manifest.run_id, entry)
    assert len(first.entries) == len(second.entries) == 1
    text = (tmp_path / "runs" / manifest.run_id / "artifact_index.json").read_text()
    assert "profile_data" not in text


@pytest.mark.parametrize(
    ("error_type", "expected"),
    [
        ("invalid_input", 2), ("contract_violation", 3),
        ("llm_unavailable", 4), ("storage_failure", 5),
        ("internal_error", 6),
    ],
)
def test_stable_exit_code_contract(error_type: str, expected: int) -> None:
    assert exit_code_for_error(error_type) == expected
