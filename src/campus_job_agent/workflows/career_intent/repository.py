"""Dedicated immutable records for CareerIntent intake and SearchScope."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

from campus_job_agent.schemas.matching import canonical_hash


T = TypeVar("T", bound=BaseModel)


class SQLiteIntentRepository:
    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path).expanduser().resolve()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS career_intent_records (
                    record_id TEXT PRIMARY KEY,
                    record_kind TEXT NOT NULL,
                    owner_id TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_career_intent_records
                    ON career_intent_records(record_kind, owner_id, created_at);
                CREATE TABLE IF NOT EXISTS career_intent_response_receipts (
                    response_id TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    payload_hash TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
            """)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    def save(
        self, record_kind: str, record: T, *, owner_id: str,
        idempotency_key: str | None = None,
    ) -> T:
        record_id = _record_id(record)
        key = idempotency_key or canonical_hash(record_kind, record.model_dump(mode="json"))
        with self._connect() as connection:
            try:
                connection.execute(
                    "INSERT INTO career_intent_records VALUES (?, ?, ?, ?, ?, ?)",
                    (record_id, record_kind, owner_id, key, record.model_dump_json(), datetime.now(UTC).isoformat()),
                )
            except sqlite3.IntegrityError:
                row = connection.execute(
                    "SELECT payload_json FROM career_intent_records WHERE idempotency_key = ?", (key,)
                ).fetchone()
                if row is None:
                    raise ValueError("idempotency_conflict")
                return type(record).model_validate_json(row[0])
        return record

    def get(self, record_id: str, model: type[T], *, owner_id: str | None = None) -> T | None:
        query = "SELECT payload_json FROM career_intent_records WHERE record_id = ?"
        params: tuple[str, ...] = (record_id,)
        if owner_id is not None:
            query += " AND owner_id = ?"
            params += (owner_id,)
        with self._connect() as connection:
            row = connection.execute(query, params).fetchone()
        return None if row is None else model.model_validate_json(row[0])

    def list(self, record_kind: str, model: type[T], *, owner_id: str | None = None) -> list[T]:
        query = "SELECT payload_json FROM career_intent_records WHERE record_kind = ?"
        params: tuple[str, ...] = (record_kind,)
        if owner_id is not None:
            query += " AND owner_id = ?"
            params += (owner_id,)
        query += " ORDER BY created_at, record_id"
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [model.model_validate_json(row[0]) for row in rows]

    def save_response_result(
        self, *, response_id: str, owner_id: str, payload_hash: str, result: dict
    ) -> dict:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT owner_id, payload_hash, result_json FROM career_intent_response_receipts WHERE response_id = ?",
                (response_id,),
            ).fetchone()
            if row is not None:
                if row["owner_id"] != owner_id or row["payload_hash"] != payload_hash:
                    raise ValueError("idempotency_conflict")
                return json.loads(row["result_json"])
            connection.execute(
                "INSERT INTO career_intent_response_receipts VALUES (?, ?, ?, ?, ?)",
                (response_id, owner_id, payload_hash, json.dumps(result, ensure_ascii=False, sort_keys=True), datetime.now(UTC).isoformat()),
            )
        return result

    def get_response_result(self, response_id: str) -> dict | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT owner_id, payload_hash, result_json FROM career_intent_response_receipts WHERE response_id = ?",
                (response_id,),
            ).fetchone()
        if row is None:
            return None
        return {"owner_id": row["owner_id"], "payload_hash": row["payload_hash"], **json.loads(row["result_json"])}


def _record_id(record: BaseModel) -> str:
    for name in (
        "receipt_id", "confirmation_id", "scope_id", "request_id", "draft_id",
    ):
        value = getattr(record, name, None)
        if value:
            return str(value)
    raise ValueError(f"cannot determine CareerIntent record ID for {type(record).__name__}")


__all__ = ["SQLiteIntentRepository"]
