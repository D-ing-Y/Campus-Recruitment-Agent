"""SQLite persistence for v0.5 derived source and role records."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel

from campus_job_agent.schemas import SourceBatch


T = TypeVar("T", bound=BaseModel)


class SQLiteRoleRepository:
    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._migrate()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _migrate(self) -> None:
        migration = Path(__file__).parents[1] / "storage" / "migrations" / "0003_role_profile_graph.sql"
        with self._connect() as connection:
            connection.executescript(migration.read_text(encoding="utf-8"))

    def save(self, record_kind: str, record: T, *, idempotency_key: str | None = None) -> T:
        record_id = _record_id(record)
        key = idempotency_key or _canonical_key(record_kind, record.model_dump(mode="json"))
        with self._connect() as connection:
            try:
                connection.execute(
                    "INSERT INTO role_records VALUES (?, ?, ?, ?, ?)",
                    (record_id, record_kind, key, record.model_dump_json(), datetime.now(UTC).isoformat()),
                )
            except sqlite3.IntegrityError:
                row = connection.execute(
                    "SELECT payload_json FROM role_records WHERE idempotency_key = ?", (key,)
                ).fetchone()
                if row is None:
                    raise
                return type(record).model_validate_json(row[0])
        return record

    def get(self, record_id: str, model: type[T]) -> T | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM role_records WHERE record_id = ?", (record_id,)
            ).fetchone()
        return None if row is None else model.model_validate_json(row[0])

    def list(self, record_kind: str, model: type[T]) -> list[T]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload_json FROM role_records WHERE record_kind = ? ORDER BY created_at, record_id",
                (record_kind,),
            ).fetchall()
        return [model.model_validate_json(row[0]) for row in rows]

    def save_batch(self, batch: SourceBatch) -> SourceBatch:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM source_batch_receipts WHERE idempotency_key = ?",
                (batch.idempotency_key,),
            ).fetchone()
            if row is not None:
                existing = SourceBatch.model_validate_json(row[0])
                if existing.status not in {"success", "empty"} and batch.status in {"success", "empty"}:
                    connection.execute(
                        "UPDATE source_batch_receipts SET batch_id = ?, payload_json = ?, created_at = ? WHERE idempotency_key = ?",
                        (batch.batch_id, batch.model_dump_json(), datetime.now(UTC).isoformat(), batch.idempotency_key),
                    )
                    return batch
                return existing
            connection.execute(
                "INSERT INTO source_batch_receipts VALUES (?, ?, ?, ?)",
                (batch.idempotency_key, batch.batch_id, batch.model_dump_json(), datetime.now(UTC).isoformat()),
            )
        return batch

    def get_batch(self, idempotency_key: str) -> SourceBatch | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM source_batch_receipts WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
        return None if row is None else SourceBatch.model_validate_json(row[0])


def _record_id(record: BaseModel) -> str:
    for name in type(record).model_fields:
        if name.endswith("_id") and name not in {"query_id", "scope_id"}:
            value = getattr(record, name, None)
            if value:
                return str(value)
    raise ValueError(f"cannot determine record id for {type(record).__name__}")


def _canonical_key(kind: str, value: Any) -> str:
    import hashlib

    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(f"{kind}:{payload}".encode("utf-8")).hexdigest()
