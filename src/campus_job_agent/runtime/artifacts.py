"""Atomic, append-only and redacted diagnostic run artifacts."""

from __future__ import annotations

import fcntl
import hashlib
import json
import math
import os
import re
import tempfile
from pathlib import Path
from time import perf_counter_ns
from typing import Any

from pydantic import BaseModel

from campus_job_agent.runtime.models import (
    ArtifactEntry, ArtifactIndex, ErrorEvent, EventStatus, Handoff, LLMCallReceipt, RunEvent, RunManifest,
    TerminalRunStatus, utc_now,
)


_SECRET_KEY = re.compile(
    r"(?i)(api[_-]?key|authorization|cookie|credential|password|secret|token|resume_(text|input)|feedback_(text|input)|raw_(text|body|content))"
)
_EMAIL = re.compile(r"(?<![\w.+-])[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}")
_PHONE = re.compile(r"(?<!\d)(?:\+?86[- ]?)?1[3-9]\d{9}(?!\d)")


class ArtifactWriteError(RuntimeError):
    pass


def redact(value: Any, *, key: str = "") -> Any:
    if _SECRET_KEY.search(key):
        return "[REDACTED]"
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    if isinstance(value, dict):
        return {str(k): redact(v, key=str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [redact(item) for item in value]
    if isinstance(value, str):
        cleaned = _PHONE.sub("[REDACTED_PHONE]", _EMAIL.sub("[REDACTED_EMAIL]", value))
        if len(cleaned) > 500:
            digest = hashlib.sha256(cleaned.encode("utf-8")).hexdigest()
            return f"[REDACTED_LONG_TEXT sha256:{digest}]"
        return cleaned
    return value


class RunArtifactWriter:
    FILES = (
        "run_manifest.json", "events.jsonl", "state.json", "llm_calls.jsonl",
        "errors.jsonl", "artifact_index.json", "handoffs.jsonl", "report.md",
    )

    def __init__(self, run_root: str | Path, *, software_version: str = "0.7.0") -> None:
        self.run_root = Path(run_root).expanduser().resolve()
        self.software_version = software_version
        self.run_root.mkdir(parents=True, exist_ok=True)

    def initialize_run(
        self, *, session_id: str, thread_id: str, workflow: str, command: str,
        parent_run_id: str | None = None, input_refs: dict[str, Any] | None = None,
        warnings: list[str] | None = None,
    ) -> RunManifest:
        manifest = RunManifest(
            session_id=session_id, thread_id=thread_id, workflow=workflow, command=command,
            parent_run_id=parent_run_id, input_refs=redact(input_refs or {}),
            warnings=list(warnings or []), software_version=self.software_version,
        )
        run_dir = self._run_dir(manifest.run_id)
        run_dir.mkdir(parents=False, exist_ok=False)
        manifest.artifact_paths = {
            name.removesuffix(".json").removesuffix(".jsonl").removesuffix(".md"): str(run_dir / name)
            for name in self.FILES
        }
        # The running manifest is the first durable record.
        self._atomic_json(run_dir / "run_manifest.json", manifest)
        try:
            for name in ("events.jsonl", "llm_calls.jsonl", "errors.jsonl", "handoffs.jsonl"):
                self._atomic_text(run_dir / name, "")
            self._atomic_json(run_dir / "state.json", {})
            self._atomic_json(run_dir / "artifact_index.json", ArtifactIndex(run_id=manifest.run_id))
            self._atomic_text(run_dir / "report.md", self._default_report(manifest))
            self.append_event(RunEvent(
                run_id=manifest.run_id, session_id=session_id, thread_id=thread_id,
                event_type="run_started", workflow=workflow, node=None, status="running",
                input_refs=manifest.input_refs,
            ))
        except Exception as exc:
            failed = manifest.model_copy(update={
                "status": "failed", "ended_at": utc_now(), "next_action": "inspect.run",
                "warnings": [*manifest.warnings, "artifact_initialization_failed"],
            })
            try:
                self._atomic_json(run_dir / "run_manifest.json", failed)
            except Exception:
                pass
            raise ArtifactWriteError("run artifact initialization failed") from exc
        return manifest

    def load_manifest(self, run_id: str) -> RunManifest:
        path = self._existing_run_dir(run_id) / "run_manifest.json"
        try:
            return RunManifest.model_validate_json(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise KeyError(f"run not found: {run_id}") from exc
        except Exception as exc:
            raise ArtifactWriteError(f"run manifest is damaged: {run_id}") from exc

    def finish_run(
        self, run_id: str, *, status: TerminalRunStatus, next_action: str | None = None,
        output_refs: dict[str, Any] | None = None, pending_request_id: str | None = None,
        pending_handoff_ids: list[str] | None = None, warnings: list[str] | None = None,
        reason_codes: list[str] | None = None,
    ) -> RunManifest:
        manifest = self.load_manifest(run_id)
        if manifest.status != "running":
            if manifest.status == status:
                return manifest
            raise ArtifactWriteError("run manifest is already terminal")
        ended = utc_now()
        elapsed_ms = max(1, math.ceil((ended - manifest.started_at).total_seconds() * 1000))
        self.append_event(RunEvent(
            run_id=run_id, session_id=manifest.session_id, thread_id=manifest.thread_id,
            event_type="run_finished", workflow=manifest.workflow, status=status,
            output_refs=redact(output_refs or {}), duration_ms=elapsed_ms,
            reason_codes=list(reason_codes or []),
        ))
        terminal = manifest.model_copy(update={
            "status": status, "next_action": next_action, "output_refs": redact(output_refs or {}),
            "pending_request_id": pending_request_id,
            "pending_handoff_ids": list(pending_handoff_ids or []), "ended_at": ended,
            "warnings": [*manifest.warnings, *(warnings or [])],
        })
        try:
            self._atomic_json(self._run_dir(run_id) / "run_manifest.json", terminal)
        except ArtifactWriteError as exc:
            failed = manifest.model_copy(update={
                "status": "failed", "next_action": "inspect.run", "ended_at": utc_now(),
                "warnings": [*manifest.warnings, "terminal_artifact_write_failed"],
            })
            try:
                self._atomic_json(self._run_dir(run_id) / "run_manifest.json", failed)
            except Exception:
                pass
            raise ArtifactWriteError("terminal run manifest write failed") from exc
        return terminal

    def append_event(self, event: RunEvent) -> RunEvent:
        assigned = self._append_jsonl(self._run_dir(event.run_id) / "events.jsonl", event, sequence=True)
        return RunEvent.model_validate(assigned)

    def append_error(self, error: ErrorEvent) -> ErrorEvent:
        self._append_jsonl(self._run_dir(error.run_id) / "errors.jsonl", error)
        manifest = self.load_manifest(error.run_id)
        self.append_event(RunEvent(
            run_id=error.run_id, session_id=manifest.session_id, thread_id=manifest.thread_id,
            event_type="error_recorded", workflow=error.workflow, node=error.node,
            status="failed", reason_codes=[error.error_type], error_ref=error.error_id,
        ))
        return error

    def append_llm_call(self, receipt: LLMCallReceipt) -> LLMCallReceipt:
        self._append_jsonl(self._run_dir(receipt.run_id) / "llm_calls.jsonl", receipt)
        return receipt

    def append_handoff(self, handoff: Handoff) -> Handoff:
        self._append_jsonl(self._run_dir(handoff.origin_run_id) / "handoffs.jsonl", handoff)
        return handoff

    def add_artifact(self, run_id: str, entry: ArtifactEntry) -> ArtifactIndex:
        path = self._existing_run_dir(run_id) / "artifact_index.json"
        lock_path = path.with_name(".artifact_index.lock")
        lock_path.touch(exist_ok=True)
        with lock_path.open("r+", encoding="utf-8") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                index = ArtifactIndex.model_validate_json(path.read_text(encoding="utf-8"))
                existing = next(
                    (
                        item for item in index.entries
                        if item.logical_type == entry.logical_type and item.object_id == entry.object_id
                    ),
                    None,
                )
                if existing is not None:
                    if existing != entry:
                        raise ArtifactWriteError("artifact index identity conflict")
                    return index
                updated = index.model_copy(update={"entries": [*index.entries, entry]})
                self._atomic_json(path, updated)
                return updated
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    def write_state(self, run_id: str, state: dict[str, Any]) -> Path:
        path = self._run_dir(run_id) / "state.json"
        self._atomic_json(path, redact(state))
        return path

    def write_report(self, run_id: str, text: str) -> Path:
        path = self._run_dir(run_id) / "report.md"
        sanitized = redact(text)
        self._atomic_text(path, str(sanitized))
        return path

    def read_jsonl(self, run_id: str, name: str) -> list[dict[str, Any]]:
        if name not in {"events", "llm_calls", "errors", "handoffs"}:
            raise ValueError("unsupported JSONL artifact")
        path = self._existing_run_dir(run_id) / f"{name}.jsonl"
        try:
            return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
        except Exception as exc:
            raise ArtifactWriteError(f"artifact is damaged: {name}") from exc

    def _append_jsonl(self, path: Path, value: BaseModel | dict[str, Any], *, sequence: bool = False) -> dict[str, Any]:
        payload = value.model_dump(mode="json") if isinstance(value, BaseModel) else dict(value)
        payload = redact(payload)
        lock_path = path.with_name(f".{path.name}.lock")
        lock_path.touch(exist_ok=True)
        with lock_path.open("r+", encoding="utf-8") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                path.touch(exist_ok=True)
                if sequence:
                    last = 0
                    with path.open("r", encoding="utf-8") as current:
                        for line in current:
                            if line.strip():
                                last = int(json.loads(line).get("sequence", last))
                    payload["sequence"] = last + 1
                with path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
                    handle.flush()
                    os.fsync(handle.fileno())
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        return payload

    def _atomic_json(self, path: Path, value: BaseModel | dict[str, Any]) -> None:
        payload = value.model_dump(mode="json") if isinstance(value, BaseModel) else value
        self._atomic_text(path, json.dumps(redact(payload), ensure_ascii=False, indent=2, sort_keys=True))

    def _atomic_text(self, path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, path)
        except Exception as exc:
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass
            raise ArtifactWriteError(f"atomic artifact write failed: {path.name}") from exc

    def _run_dir(self, run_id: str) -> Path:
        if not run_id.startswith("run-") or "/" in run_id or ".." in run_id:
            raise ValueError("invalid run_id")
        return self.run_root / run_id

    def _existing_run_dir(self, run_id: str) -> Path:
        path = self._run_dir(run_id)
        if not path.is_dir():
            raise KeyError(f"run not found: {run_id}")
        return path

    @staticmethod
    def _default_report(manifest: RunManifest) -> str:
        return (
            f"# Run {manifest.run_id}\n\n"
            f"- workflow: `{manifest.workflow}`\n- status: `running`\n"
            f"- inspect: `campus-agent inspect run {manifest.run_id}`\n"
        )


class NodeObserver:
    """Records truthful start/finish/error events around one observable operation."""

    def __init__(self, writer: RunArtifactWriter, manifest: RunManifest, node: str,
                 *, input_refs: dict[str, Any] | None = None) -> None:
        self.writer, self.manifest, self.node = writer, manifest, node
        self.input_refs = input_refs or {}
        self.started_ns = 0

    def __enter__(self) -> "NodeObserver":
        self.started_ns = perf_counter_ns()
        self.writer.append_event(RunEvent(
            run_id=self.manifest.run_id, session_id=self.manifest.session_id,
            thread_id=self.manifest.thread_id, event_type="node_started",
            workflow=self.manifest.workflow, node=self.node, status="running",
            input_refs=redact(self.input_refs),
        ))
        return self

    def finish(self, *, status: EventStatus = "completed", output_refs: dict[str, Any] | None = None,
               counts: dict[str, int | float] | None = None, route: str | None = None,
               reason_codes: list[str] | None = None, fallback: str | None = None) -> None:
        duration = max(1, math.ceil((perf_counter_ns() - self.started_ns) / 1_000_000))
        self.writer.append_event(RunEvent(
            run_id=self.manifest.run_id, session_id=self.manifest.session_id,
            thread_id=self.manifest.thread_id, event_type="node_finished",
            workflow=self.manifest.workflow, node=self.node, status=status,
            input_refs=redact(self.input_refs), output_refs=redact(output_refs or {}),
            counts=counts or {}, route=route, duration_ms=duration,
            reason_codes=reason_codes or [], fallback=fallback,
        ))

    def __exit__(self, exc_type: Any, exc: BaseException | None, traceback: Any) -> bool:
        if exc is None:
            return False
        duration = max(1, math.ceil((perf_counter_ns() - self.started_ns) / 1_000_000))
        error = ErrorEvent(
            run_id=self.manifest.run_id, workflow=self.manifest.workflow, node=self.node,
            error_type="internal_error", message=str(exc), retryable=False,
            recovery_hint=f"inspect node {self.node} and errors for run {self.manifest.run_id}",
        )
        self.writer.append_error(error)
        self.writer.append_event(RunEvent(
            run_id=self.manifest.run_id, session_id=self.manifest.session_id,
            thread_id=self.manifest.thread_id, event_type="node_finished",
            workflow=self.manifest.workflow, node=self.node, status="failed",
            duration_ms=duration, error_ref=error.error_id,
        ))
        return False
