"""SQLite RunSession and typed Handoff persistence with optimistic concurrency."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from campus_job_agent.runtime.models import Handoff, ObjectRef, RunSession, utc_now


class SessionError(RuntimeError):
    error_type = "contract_violation"


class SessionNotFoundError(SessionError):
    error_type = "not_found"


class SessionConflictError(SessionError):
    error_type = "stale_input"


class SessionReferenceError(SessionError):
    error_type = "contract_violation"


_REF_TYPES = {
    "candidate_profile_snapshot_id": "candidate_profile_snapshot",
    "career_intent_snapshot_id": "career_intent_snapshot",
    "role_profile_snapshot_ids": "role_profile_snapshot",
    "comparison_set_id": "comparison_set",
    "target_decision_ids": "target_decision",
    "learning_plan_id": "learning_plan",
}


class SQLiteSessionRepository:
    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path).expanduser().resolve()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._migrate()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def _migrate(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS run_sessions (
                    session_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    session_version INTEGER NOT NULL,
                    idempotency_key TEXT,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(user_id, idempotency_key)
                );
                CREATE TABLE IF NOT EXISTS session_history (
                    history_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    session_version INTEGER NOT NULL,
                    operation TEXT NOT NULL,
                    run_id TEXT,
                    payload_json TEXT NOT NULL,
                    occurred_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_session_history
                    ON session_history(session_id, history_id);
                CREATE TABLE IF NOT EXISTS runtime_object_refs (
                    object_id TEXT PRIMARY KEY,
                    object_type TEXT NOT NULL,
                    owner_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS runtime_handoffs (
                    handoff_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    UNIQUE(session_id, handoff_id)
                );
                """
            )

    def create(self, *, user_id: str, idempotency_key: str | None = None) -> RunSession:
        key = idempotency_key or None
        with self._connect() as connection:
            if key is not None:
                row = connection.execute(
                    "SELECT payload_json FROM run_sessions WHERE user_id = ? AND idempotency_key = ?",
                    (user_id, key),
                ).fetchone()
                if row is not None:
                    return RunSession.model_validate_json(row[0])
            session = RunSession(user_id=user_id)
            now = session.created_at.isoformat()
            connection.execute(
                "INSERT INTO run_sessions VALUES (?, ?, ?, ?, ?, ?, ?)",
                (session.session_id, user_id, session.session_version, key,
                 session.model_dump_json(), now, now),
            )
            self._history(connection, session, "created")
        return session

    def get(self, session_id: str, *, user_id: str | None = None) -> RunSession:
        query = "SELECT payload_json FROM run_sessions WHERE session_id = ?"
        params: tuple[Any, ...] = (session_id,)
        if user_id is not None:
            query += " AND user_id = ?"
            params += (user_id,)
        with self._connect() as connection:
            row = connection.execute(query, params).fetchone()
        if row is None:
            raise SessionNotFoundError(f"session not found: {session_id}")
        return RunSession.model_validate_json(row[0])

    def list(self, *, user_id: str | None = None) -> list[RunSession]:
        query = "SELECT payload_json FROM run_sessions"
        params: tuple[Any, ...] = ()
        if user_id is not None:
            query += " WHERE user_id = ?"
            params = (user_id,)
        query += " ORDER BY created_at, session_id"
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [RunSession.model_validate_json(row[0]) for row in rows]

    def history(self, session_id: str) -> list[dict[str, Any]]:
        self.get(session_id)
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT session_version, operation, run_id, payload_json, occurred_at "
                "FROM session_history WHERE session_id = ? ORDER BY history_id",
                (session_id,),
            ).fetchall()
        return [
            {
                "session_version": row["session_version"], "operation": row["operation"],
                "run_id": row["run_id"], "session": json.loads(row["payload_json"]),
                "occurred_at": row["occurred_at"],
            }
            for row in rows
        ]

    def register_ref(self, ref: ObjectRef) -> ObjectRef:
        if not ref.schema_version.startswith(("v0.3", "v0.4", "v0.5", "v0.6", "v0.7")):
            raise SessionReferenceError("object ref schema version is unsupported")
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM runtime_object_refs WHERE object_id = ?", (ref.object_id,)
            ).fetchone()
            if row is not None:
                existing = ObjectRef.model_validate_json(row[0])
                if existing != ref:
                    raise SessionReferenceError("object ref identity conflict")
                return existing
            connection.execute(
                "INSERT INTO runtime_object_refs VALUES (?, ?, ?, ?)",
                (ref.object_id, ref.object_type, ref.owner_id, ref.model_dump_json()),
            )
        return ref

    def get_ref(self, object_id: str) -> ObjectRef:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM runtime_object_refs WHERE object_id = ?", (object_id,)
            ).fetchone()
        if row is None:
            raise SessionReferenceError(f"object ref not registered: {object_id}")
        return ObjectRef.model_validate_json(row[0])

    def set_current_ref(
        self, session_id: str, *, key: str, object_id: str,
        expected_version: int,
    ) -> RunSession:
        if key not in _REF_TYPES:
            raise SessionReferenceError(f"unsupported current ref key: {key}")
        ref = self.get_ref(object_id)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            session = self._get_on(connection, session_id)
            self._check_version(session, expected_version)
            if ref.owner_id != session.user_id:
                raise SessionReferenceError("object ref owner does not match session owner")
            if ref.object_type != _REF_TYPES[key]:
                raise SessionReferenceError("object ref type does not match session slot")
            if ref.lifecycle_status in {"stale", "superseded"}:
                raise SessionReferenceError("stale or superseded object cannot become current")
            previous_value = session.current_refs.get(key)
            is_list = key.endswith("_ids")
            previous_ids = list(previous_value or []) if is_list else ([str(previous_value)] if previous_value else [])
            if object_id in previous_ids:
                return session
            if previous_ids and not any(
                previous in ref.predecessor_ids or ref.successor_of == previous
                for previous in previous_ids
            ):
                raise SessionReferenceError("successor ref does not declare the current predecessor")
            refs = dict(session.current_refs)
            refs[key] = [*previous_ids, object_id] if is_list else object_id
            updated = session.model_copy(update={
                "current_refs": refs,
                "session_version": session.session_version + 1,
                "updated_at": utc_now(),
            })
            self._save_on(connection, updated, "set_current_ref")
        return updated

    def update_navigation(
        self, session_id: str, *, expected_version: int, operation: str,
        status: str | None = None, current_stage: str | None = None,
        pending_request: str | None | object = ..., pending_handoff_ids: list[str] | None = None,
        latest_run_id: str | None = None,
    ) -> RunSession:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            session = self._get_on(connection, session_id)
            self._check_version(session, expected_version)
            changes: dict[str, Any] = {"session_version": session.session_version + 1, "updated_at": utc_now()}
            if status is not None:
                changes["status"] = status
            if current_stage is not None:
                changes["current_stage"] = current_stage
            if pending_request is not ...:
                value = pending_request
                if value is not None and not str(value).startswith("request-"):
                    raise SessionReferenceError("pending request must be a request reference")
                changes["pending_request"] = value
            if pending_handoff_ids is not None:
                changes["pending_handoff_ids"] = list(dict.fromkeys(pending_handoff_ids))
            if latest_run_id is not None:
                if not latest_run_id.startswith("run-"):
                    raise SessionReferenceError("latest run must be a run reference")
                changes["latest_run_id"] = latest_run_id
            updated = session.model_copy(update=changes)
            self._save_on(connection, updated, operation)
        return updated

    def save_handoff(self, handoff: Handoff) -> Handoff:
        session = self.get(handoff.session_id)
        if session.user_id != handoff.user_id:
            raise SessionReferenceError("handoff owner does not match session owner")
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM runtime_handoffs WHERE handoff_id = ?", (handoff.handoff_id,)
            ).fetchone()
            if row is not None:
                existing = Handoff.model_validate_json(row[0])
                if existing != handoff:
                    raise SessionConflictError("handoff identity conflict")
                return existing
            connection.execute(
                "INSERT INTO runtime_handoffs VALUES (?, ?, ?, ?, ?)",
                (handoff.handoff_id, handoff.session_id, handoff.user_id,
                 handoff.status, handoff.model_dump_json()),
            )
        return handoff

    def get_handoff(self, handoff_id: str, *, user_id: str | None = None) -> Handoff:
        query = "SELECT payload_json FROM runtime_handoffs WHERE handoff_id = ?"
        params: tuple[Any, ...] = (handoff_id,)
        if user_id is not None:
            query += " AND user_id = ?"
            params += (user_id,)
        with self._connect() as connection:
            row = connection.execute(query, params).fetchone()
        if row is None:
            raise SessionNotFoundError(f"handoff not found: {handoff_id}")
        return Handoff.model_validate_json(row[0])

    def list_handoffs(self, *, session_id: str | None = None, status: str | None = None) -> list[Handoff]:
        clauses, params = [], []
        if session_id:
            clauses.append("session_id = ?")
            params.append(session_id)
        if status:
            clauses.append("status = ?")
            params.append(status)
        query = "SELECT payload_json FROM runtime_handoffs"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY handoff_id"
        with self._connect() as connection:
            rows = connection.execute(query, tuple(params)).fetchall()
        return [Handoff.model_validate_json(row[0]) for row in rows]

    def resolve_handoff(self, handoff_id: str, *, resolved_refs: dict[str, str], user_id: str) -> Handoff:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT payload_json FROM runtime_handoffs WHERE handoff_id = ?", (handoff_id,)
            ).fetchone()
            if row is None:
                raise SessionNotFoundError(f"handoff not found: {handoff_id}")
            handoff = Handoff.model_validate_json(row[0])
            if handoff.user_id != user_id:
                raise SessionReferenceError("handoff owner mismatch")
            if handoff.status == "resolved":
                if handoff.resolved_refs == resolved_refs:
                    return handoff
                raise SessionConflictError("handoff already has a different resolution")
            if handoff.status not in {"pending", "processing", "failed_retryable"}:
                raise SessionConflictError("handoff cannot be resolved from its current status")
            for object_id in resolved_refs.values():
                ref = self.get_ref(object_id)
                if ref.owner_id != user_id or ref.lifecycle_status != "current":
                    raise SessionReferenceError("resolved successor ref is invalid")
                origins = {str(item) for item in handoff.origin_object_refs.values() if isinstance(item, str)}
                if origins and not origins.intersection(set(ref.predecessor_ids) | {ref.successor_of or ""}):
                    raise SessionReferenceError("resolved ref does not reference a handoff predecessor")
            updated = handoff.model_copy(update={
                "status": "resolved", "resolved_refs": resolved_refs,
                "attempt_count": handoff.attempt_count + 1, "resolved_at": utc_now(),
            })
            connection.execute(
                "UPDATE runtime_handoffs SET status = ?, payload_json = ? WHERE handoff_id = ?",
                (updated.status, updated.model_dump_json(), handoff_id),
            )
        return updated

    def _get_on(self, connection: sqlite3.Connection, session_id: str) -> RunSession:
        row = connection.execute(
            "SELECT payload_json FROM run_sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
        if row is None:
            raise SessionNotFoundError(f"session not found: {session_id}")
        return RunSession.model_validate_json(row[0])

    @staticmethod
    def _check_version(session: RunSession, expected_version: int) -> None:
        if session.session_version != expected_version:
            raise SessionConflictError(
                f"session version conflict: expected {expected_version}, current {session.session_version}"
            )

    def _save_on(self, connection: sqlite3.Connection, session: RunSession, operation: str) -> None:
        changed = connection.execute(
            "UPDATE run_sessions SET session_version = ?, payload_json = ?, updated_at = ? "
            "WHERE session_id = ? AND session_version = ?",
            (session.session_version, session.model_dump_json(), session.updated_at.isoformat(),
             session.session_id, session.session_version - 1),
        ).rowcount
        if changed != 1:
            raise SessionConflictError("session compare-and-set failed")
        self._history(connection, session, operation)

    @staticmethod
    def _history(connection: sqlite3.Connection, session: RunSession, operation: str) -> None:
        connection.execute(
            "INSERT INTO session_history(session_id, session_version, operation, run_id, payload_json, occurred_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (session.session_id, session.session_version, operation, session.latest_run_id,
             session.model_dump_json(), datetime.now(UTC).isoformat()),
        )


class SessionService:
    def __init__(self, repository: SQLiteSessionRepository) -> None:
        self.repository = repository

    def start(self, *, user_id: str, idempotency_key: str | None = None) -> RunSession:
        if not user_id.strip():
            raise SessionReferenceError("user_id is required")
        return self.repository.create(user_id=user_id, idempotency_key=idempotency_key)

    def status(self, session_id: str) -> RunSession:
        return self.repository.get(session_id)

    def resume(self, session_id: str, *, expected_version: int | None = None,
               latest_run_id: str | None = None) -> RunSession:
        session = self.repository.get(session_id)
        # A replay after a successful resume is a navigation no-op. The new
        # diagnostic Run may still exist, but Session/domain state is unchanged.
        if session.status == "active" and session.pending_request is None:
            return session
        return self.repository.update_navigation(
            session_id, expected_version=expected_version or session.session_version,
            operation="resumed", status="active", pending_request=None,
            latest_run_id=latest_run_id,
        )

    def history(self, session_id: str) -> list[dict[str, Any]]:
        return self.repository.history(session_id)
