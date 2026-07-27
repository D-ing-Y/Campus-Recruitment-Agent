"""SQLite persistence for immutable v0.6 matching and decision records."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel

from campus_job_agent.schemas.matching import canonical_hash


T = TypeVar("T", bound=BaseModel)


class SQLiteMatchingRepository:
    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        migration = Path(__file__).parents[2] / "storage" / "migrations" / "0004_profile_matching.sql"
        with self._connect() as connection:
            connection.executescript(migration.read_text(encoding="utf-8"))

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        return connection

    def save(
        self,
        record_kind: str,
        record: T,
        *,
        owner_id: str,
        idempotency_key: str | None = None,
    ) -> T:
        record_id = _record_id(record)
        payload = record.model_dump(mode="json")
        key = idempotency_key or canonical_hash(record_kind, _without_volatile(payload))
        status = payload.get("status")
        with self._connect() as connection:
            try:
                connection.execute(
                    "INSERT INTO matching_records VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        record_id, record_kind, owner_id, key, status,
                        record.model_dump_json(), datetime.now(UTC).isoformat(),
                    ),
                )
            except sqlite3.IntegrityError:
                row = connection.execute(
                    "SELECT payload_json FROM matching_records WHERE idempotency_key = ?", (key,)
                ).fetchone()
                if row is None:
                    raise
                return type(record).model_validate_json(row[0])
        return record

    def get(self, record_id: str, model: type[T], *, owner_id: str | None = None) -> T | None:
        query = "SELECT payload_json FROM matching_records WHERE record_id = ?"
        params: tuple[Any, ...] = (record_id,)
        if owner_id is not None:
            query += " AND owner_id = ?"
            params += (owner_id,)
        with self._connect() as connection:
            row = connection.execute(query, params).fetchone()
        return None if row is None else model.model_validate_json(row[0])

    def list(self, record_kind: str, model: type[T], *, owner_id: str | None = None) -> list[T]:
        query = "SELECT payload_json FROM matching_records WHERE record_kind = ?"
        params: tuple[Any, ...] = (record_kind,)
        if owner_id is not None:
            query += " AND owner_id = ?"
            params += (owner_id,)
        query += " ORDER BY created_at, record_id"
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [model.model_validate_json(row[0]) for row in rows]

    def replace_lifecycle(self, record_id: str, model: type[T], status: str) -> T:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM matching_records WHERE record_id = ?", (record_id,)
            ).fetchone()
            if row is None:
                raise KeyError(record_id)
            record = model.model_validate_json(row[0])
            updated = record.model_copy(update={"status": status})
            connection.execute(
                "UPDATE matching_records SET lifecycle_status = ?, payload_json = ? WHERE record_id = ?",
                (status, updated.model_dump_json(), record_id),
            )
        return updated

    def save_decision_batch(
        self,
        decisions: list[T],
        *,
        owner_id: str,
        response_id: str,
        payload_hash: str,
    ) -> list[T]:
        """Validate first at the service boundary, then commit all decisions atomically."""

        with self._connect() as connection:
            receipt = connection.execute(
                "SELECT payload_hash, result_json FROM matching_response_receipts WHERE response_id = ?",
                (response_id,),
            ).fetchone()
            if receipt is not None:
                if receipt["payload_hash"] != payload_hash:
                    raise ValueError("idempotency_conflict")
                ids = json.loads(receipt["result_json"])["record_ids"]
                records: list[T] = []
                for record_id in ids:
                    row = connection.execute(
                        "SELECT payload_json FROM matching_records WHERE record_id = ? AND owner_id = ?",
                        (record_id, owner_id),
                    ).fetchone()
                    if row is not None:
                        records.append(type(decisions[0]).model_validate_json(row[0]))
                return records
            for decision in decisions:
                payload = decision.model_dump(mode="json")
                key = canonical_hash("target-decision", _without_volatile(payload))
                connection.execute(
                    "INSERT INTO matching_records VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        _record_id(decision), "target_decision", owner_id, key,
                        payload.get("status"), decision.model_dump_json(), datetime.now(UTC).isoformat(),
                    ),
                )
            connection.execute(
                "INSERT INTO matching_response_receipts VALUES (?, ?, ?, ?)",
                (
                    response_id, payload_hash,
                    json.dumps({"record_ids": [_record_id(item) for item in decisions]}, sort_keys=True),
                    datetime.now(UTC).isoformat(),
                ),
            )
        return decisions

    def get_response_result(self, response_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload_hash, result_json FROM matching_response_receipts WHERE response_id = ?",
                (response_id,),
            ).fetchone()
        return None if row is None else {"payload_hash": row["payload_hash"], **json.loads(row["result_json"])}

    def save_response_result(self, response_id: str, payload_hash: str, result: dict[str, Any]) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload_hash, result_json FROM matching_response_receipts WHERE response_id = ?",
                (response_id,),
            ).fetchone()
            if row is not None:
                if row["payload_hash"] != payload_hash:
                    raise ValueError("idempotency_conflict")
                return json.loads(row["result_json"])
            connection.execute(
                "INSERT INTO matching_response_receipts VALUES (?, ?, ?, ?)",
                (response_id, payload_hash, json.dumps(result, ensure_ascii=False, sort_keys=True), datetime.now(UTC).isoformat()),
            )
        return result


def _record_id(record: BaseModel) -> str:
    for name in type(record).model_fields:
        if name.endswith("_id") and name not in {
            "user_id", "qualification_id", "requirement_id", "career_intent_snapshot_id",
            "candidate_profile_snapshot_id", "job_instance_profile_snapshot_id",
            "previous_intent_snapshot_id", "new_intent_snapshot_id",
        }:
            value = getattr(record, name, None)
            if value:
                return str(value)
    raise ValueError(f"cannot determine record id for {type(record).__name__}")


def _without_volatile(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if key not in {"created_at", "generated_at", "updated_at"}}


__all__ = ["SQLiteMatchingRepository"]
