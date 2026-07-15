"""SQLite repository for evidence metadata and profile snapshots."""

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

from campus_job_agent.schemas import (
    EvidenceArtifact,
    EvidenceClaim,
    EvidenceFragment,
    ProfileSnapshot,
)

ModelT = TypeVar("ModelT", bound=BaseModel)


class SQLiteRepository:
    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.migrate()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def migrate(self) -> None:
        migration_dir = Path(__file__).with_name("migrations")
        with self._connect() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations "
                "(version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
            )
            applied = {
                row[0]
                for row in connection.execute("SELECT version FROM schema_migrations")
            }
            for path in sorted(migration_dir.glob("*.sql")):
                if path.stem in applied:
                    continue
                connection.executescript(path.read_text(encoding="utf-8"))
                connection.execute(
                    "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                    (path.stem, datetime.now(UTC).isoformat()),
                )

    def save_artifact(self, artifact: EvidenceArtifact) -> EvidenceArtifact:
        with self._connect() as connection:
            try:
                connection.execute(
                    "INSERT INTO artifacts VALUES (?, ?, ?, ?, ?)",
                    (
                        artifact.artifact_id,
                        artifact.owner_id,
                        artifact.content_hash,
                        artifact.model_dump_json(),
                        artifact.retrieved_at.isoformat(),
                    ),
                )
            except sqlite3.IntegrityError:
                existing = self.find_artifact_by_hash(
                    artifact.content_hash, artifact.owner_id
                )
                if existing is not None:
                    return existing
                raise
        return artifact

    def get_artifact(self, artifact_id: str) -> EvidenceArtifact | None:
        return self._one(
            "SELECT payload_json FROM artifacts WHERE artifact_id = ?",
            (artifact_id,),
            EvidenceArtifact,
        )

    def find_artifact_by_hash(
        self, content_hash: str, owner_id: str | None = None
    ) -> EvidenceArtifact | None:
        if owner_id is None:
            return self._one(
                "SELECT payload_json FROM artifacts WHERE content_hash = ? LIMIT 1",
                (content_hash,),
                EvidenceArtifact,
            )
        return self._one(
            "SELECT payload_json FROM artifacts "
            "WHERE content_hash = ? AND owner_id = ? LIMIT 1",
            (content_hash, owner_id),
            EvidenceArtifact,
        )

    def save_fragment(self, fragment: EvidenceFragment) -> EvidenceFragment:
        with self._connect() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO fragments VALUES (?, ?, ?)",
                (fragment.fragment_id, fragment.artifact_id, fragment.model_dump_json()),
            )
        return self.get_fragment(fragment.fragment_id) or fragment

    def get_fragment(self, fragment_id: str) -> EvidenceFragment | None:
        return self._one(
            "SELECT payload_json FROM fragments WHERE fragment_id = ?",
            (fragment_id,),
            EvidenceFragment,
        )

    def list_fragments(self, artifact_id: str) -> list[EvidenceFragment]:
        return self._many(
            "SELECT payload_json FROM fragments WHERE artifact_id = ? "
            "ORDER BY fragment_id",
            (artifact_id,),
            EvidenceFragment,
        )

    def save_claim(self, claim: EvidenceClaim) -> EvidenceClaim:
        try:
            with self._connect() as connection:
                connection.execute(
                    "INSERT INTO claims VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        claim.claim_id,
                        claim.subject_id,
                        claim.predicate,
                        claim.idempotency_key(),
                        claim.model_dump_json(),
                        claim.created_at.isoformat(),
                    ),
                )
                connection.executemany(
                    "INSERT INTO claim_fragments VALUES (?, ?)",
                    [(claim.claim_id, value) for value in claim.evidence_fragment_ids],
                )
        except sqlite3.IntegrityError:
            # The transaction has rolled back here. Only an already committed
            # idempotent claim may turn an integrity error into a successful read.
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT payload_json FROM claims WHERE idempotency_key = ?",
                    (claim.idempotency_key(),),
                ).fetchone()
            if row is not None:
                return EvidenceClaim.model_validate_json(row[0])
            raise
        return claim

    def get_claim(self, claim_id: str) -> EvidenceClaim | None:
        return self._one(
            "SELECT payload_json FROM claims WHERE claim_id = ?",
            (claim_id,),
            EvidenceClaim,
        )

    def list_claims(self, subject_id: str) -> list[EvidenceClaim]:
        return self._many(
            "SELECT payload_json FROM claims WHERE subject_id = ? ORDER BY created_at",
            (subject_id,),
            EvidenceClaim,
        )

    def save_profile(self, profile: ProfileSnapshot) -> ProfileSnapshot:
        with self._connect() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO profile_snapshots VALUES (?, ?, ?, ?, ?, ?)",
                (
                    profile.snapshot_id,
                    profile.subject_id,
                    profile.profile_type,
                    profile.version,
                    profile.model_dump_json(),
                    profile.created_at.isoformat(),
                ),
            )
        profiles = self.list_profiles(profile.subject_id, profile.profile_type)
        return next((item for item in profiles if item.version == profile.version), profile)

    def get_latest_profile(
        self, subject_id: str, profile_type: str
    ) -> ProfileSnapshot | None:
        return self._one(
            "SELECT payload_json FROM profile_snapshots "
            "WHERE subject_id = ? AND profile_type = ? "
            "ORDER BY version DESC LIMIT 1",
            (subject_id, profile_type),
            ProfileSnapshot,
        )

    def list_profiles(
        self, subject_id: str, profile_type: str | None = None
    ) -> list[ProfileSnapshot]:
        if profile_type is None:
            return self._many(
                "SELECT payload_json FROM profile_snapshots "
                "WHERE subject_id = ? ORDER BY profile_type, version",
                (subject_id,),
                ProfileSnapshot,
            )
        return self._many(
            "SELECT payload_json FROM profile_snapshots "
            "WHERE subject_id = ? AND profile_type = ? ORDER BY version",
            (subject_id, profile_type),
            ProfileSnapshot,
        )

    def _one(
        self, query: str, params: tuple[object, ...], model: type[ModelT]
    ) -> ModelT | None:
        with self._connect() as connection:
            row = connection.execute(query, params).fetchone()
        return None if row is None else model.model_validate_json(row[0])

    def _many(
        self, query: str, params: tuple[object, ...], model: type[ModelT]
    ) -> list[ModelT]:
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [model.model_validate_json(row[0]) for row in rows]
