"""Shared mechanics for the isolated v0.7 repository namespaces."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel

from campus_job_agent.schemas.matching import canonical_hash


T = TypeVar("T", bound=BaseModel)


class SQLiteV07Repository:
    table: str
    namespace: str

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        migration = Path(__file__).parents[1] / "storage" / "migrations" / "0005_preparation_feedback.sql"
        with self._connect() as connection:
            connection.executescript(migration.read_text(encoding="utf-8"))

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        return connection

    def save(self, record_kind: str, record: T, *, owner_id: str,
             idempotency_key: str | None = None) -> T:
        with self._connect() as connection:
            return self._save_on(connection, record_kind, record, owner_id=owner_id,
                                 idempotency_key=idempotency_key)

    def save_batch(self, records: list[tuple[str, T, str, str | None]]) -> list[T]:
        """Publish related immutable records in one SQLite transaction."""
        with self._connect() as connection:
            return [
                self._save_on(connection, kind, record, owner_id=owner_id, idempotency_key=key)
                for kind, record, owner_id, key in records
            ]

    def _save_on(self, connection: sqlite3.Connection, record_kind: str, record: T, *,
                 owner_id: str, idempotency_key: str | None = None) -> T:
        record_id = record_identifier(record)
        payload = record.model_dump(mode="json")
        key = idempotency_key or canonical_hash(record_kind, without_volatile(payload))
        try:
            connection.execute(
                f"INSERT INTO {self.table} VALUES (?, ?, ?, ?, ?, ?, ?)",
                (record_id, record_kind, owner_id, key, payload.get("status"),
                 record.model_dump_json(), datetime.now(UTC).isoformat()),
            )
        except sqlite3.IntegrityError:
            row = connection.execute(
                f"SELECT record_id, payload_json FROM {self.table} WHERE idempotency_key = ?", (key,)
            ).fetchone()
            if row is None:
                by_id = connection.execute(
                    f"SELECT idempotency_key FROM {self.table} WHERE record_id = ?", (record_id,)
                ).fetchone()
                if by_id is not None:
                    raise ValueError("idempotency_conflict")
                raise
            return type(record).model_validate_json(row["payload_json"])
        return record

    def get(self, record_id: str, model: type[T], *, owner_id: str | None = None) -> T | None:
        query = f"SELECT payload_json FROM {self.table} WHERE record_id = ?"
        params: tuple[Any, ...] = (record_id,)
        if owner_id is not None:
            query += " AND owner_id = ?"
            params += (owner_id,)
        with self._connect() as connection:
            row = connection.execute(query, params).fetchone()
        return None if row is None else model.model_validate_json(row[0])

    def list(self, record_kind: str, model: type[T], *, owner_id: str | None = None) -> list[T]:
        query = f"SELECT payload_json FROM {self.table} WHERE record_kind = ?"
        params: tuple[Any, ...] = (record_kind,)
        if owner_id is not None:
            query += " AND owner_id = ?"
            params += (owner_id,)
        query += " ORDER BY created_at, record_id"
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [model.model_validate_json(row[0]) for row in rows]

    def count(self, record_kind: str, *, owner_id: str | None = None) -> int:
        query = f"SELECT COUNT(*) FROM {self.table} WHERE record_kind = ?"
        params: tuple[Any, ...] = (record_kind,)
        if owner_id is not None:
            query += " AND owner_id = ?"
            params += (owner_id,)
        with self._connect() as connection:
            return int(connection.execute(query, params).fetchone()[0])

    def replace_lifecycle(self, record_id: str, model: type[T], status: str,
                          *, extra_updates: dict[str, Any] | None = None) -> T:
        with self._connect() as connection:
            row = connection.execute(
                f"SELECT payload_json FROM {self.table} WHERE record_id = ?", (record_id,)
            ).fetchone()
            if row is None:
                raise KeyError(record_id)
            record = model.model_validate_json(row[0])
            updated = record.model_copy(update={"status": status, **(extra_updates or {})})
            connection.execute(
                f"UPDATE {self.table} SET lifecycle_status = ?, payload_json = ? WHERE record_id = ?",
                (status, updated.model_dump_json(), record_id),
            )
        return updated

    def save_response_result(self, response_id: str, payload_hash: str,
                             result: dict[str, Any]) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload_hash, result_json FROM v07_response_receipts WHERE namespace = ? AND response_id = ?",
                (self.namespace, response_id),
            ).fetchone()
            if row is not None:
                if row["payload_hash"] != payload_hash:
                    raise ValueError("idempotency_conflict")
                return json.loads(row["result_json"])
            connection.execute(
                "INSERT INTO v07_response_receipts VALUES (?, ?, ?, ?, ?)",
                (self.namespace, response_id, payload_hash,
                 json.dumps(result, ensure_ascii=False, sort_keys=True), datetime.now(UTC).isoformat()),
            )
        return result

    def get_response_result(self, response_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload_hash, result_json FROM v07_response_receipts WHERE namespace = ? AND response_id = ?",
                (self.namespace, response_id),
            ).fetchone()
        return None if row is None else {"payload_hash": row["payload_hash"], **json.loads(row["result_json"])}


def record_identifier(record: BaseModel) -> str:
    preferred = {
        "PreparationConstraints": "constraints_id", "PreparationInputSet": "input_set_id",
        "PreparationObjective": "objective_id", "PreparationActivity": "activity_id",
        "PriorityFactors": "priority_factor_id", "MinimumPreparationPackage": "package_id",
        "ScheduledSession": "session_id", "LearningPlan": "learning_plan_id",
        "PlanProgressEvent": "progress_event_id", "FeedbackEvent": "feedback_event_id",
        "FeedbackObservation": "observation_id", "FeedbackDiagnosis": "diagnosis_id",
        "FeedbackAttribution": "attribution_id", "FeedbackImpactAssessment": "impact_assessment_id",
        "FeedbackDirective": "directive_id",
    }.get(type(record).__name__)
    if preferred:
        return str(getattr(record, preferred))
    ignored = {
        "user_id", "input_set_id", "constraints_id", "activity_id", "objective_id", "package_id",
        "learning_plan_id", "feedback_event_id", "candidate_profile_snapshot_id",
        "career_intent_snapshot_id", "comparison_set_id", "plan_id",
    }
    for name in type(record).model_fields:
        if name.endswith("_id") and name not in ignored:
            value = getattr(record, name, None)
            if value:
                return str(value)
    # Fallback for future top-level objects whose identifiers are also foreign-key field names.
    for name in ("input_set_id", "constraints_id", "activity_id", "objective_id", "package_id",
                 "learning_plan_id", "feedback_event_id"):
        value = getattr(record, name, None)
        if value:
            return str(value)
    raise ValueError(f"cannot determine record id for {type(record).__name__}")


def without_volatile(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if key not in {"created_at", "generated_at", "occurred_at"}}
