"""SQLite namespace for immutable feedback objects and directive history."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from campus_job_agent.schemas.matching import canonical_hash
from campus_job_agent.workflows._v07_repository import SQLiteV07Repository


class SQLiteFeedbackRepository(SQLiteV07Repository):
    table = "feedback_records"
    namespace = "feedback"

    def save_resolution(self, directive_id: str, response_id: str,
                        payload: dict[str, Any]) -> dict[str, Any]:
        digest = canonical_hash("directive-resolution", payload)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT response_id, payload_hash, result_json FROM feedback_resolution_receipts WHERE directive_id = ?",
                (directive_id,),
            ).fetchone()
            if row is not None:
                if row["response_id"] != response_id or row["payload_hash"] != digest:
                    raise ValueError("idempotency_conflict")
                return json.loads(row["result_json"])
            connection.execute(
                "INSERT INTO feedback_resolution_receipts VALUES (?, ?, ?, ?, ?)",
                (directive_id, response_id, digest, json.dumps(payload, ensure_ascii=False, sort_keys=True),
                 datetime.now(UTC).isoformat()),
            )
        return payload


__all__ = ["SQLiteFeedbackRepository"]
