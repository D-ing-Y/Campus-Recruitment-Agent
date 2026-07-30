"""Immutable raw intent and confirmation-response evidence ingestion."""

from __future__ import annotations

import hashlib
import json

from campus_job_agent.schemas import EvidenceArtifact, EvidenceFragment, Provenance
from campus_job_agent.storage.base import BlobStore, EvidenceRepository


class IntentEvidenceIngestor:
    def __init__(self, *, blob_store: BlobStore, evidence_repository: EvidenceRepository) -> None:
        self.blobs = blob_store
        self.evidence = evidence_repository

    def archive_text(
        self, *, owner_id: str, text: str, source_type: str = "career_intent"
    ) -> tuple[EvidenceArtifact, EvidenceFragment]:
        normalized = text.strip()
        if not normalized:
            raise ValueError("raw career intent must be non-empty")
        return self._archive(
            owner_id=owner_id,
            raw=normalized.encode("utf-8"),
            text=normalized,
            source_type=source_type,
            original_name="career-intent.txt",
            locator={"start": 0, "end": len(normalized)},
        )

    def archive_response(
        self, *, owner_id: str, response_payload: dict
    ) -> tuple[EvidenceArtifact, EvidenceFragment]:
        raw = json.dumps(
            response_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        return self._archive(
            owner_id=owner_id,
            raw=raw.encode("utf-8"),
            text=raw,
            source_type="career_intent_response",
            original_name="career-intent-response.json",
            locator={"path": "/"},
            locator_type="json_pointer",
        )

    def _archive(
        self, *, owner_id: str, raw: bytes, text: str, source_type: str,
        original_name: str, locator: dict, locator_type: str = "char_range",
    ) -> tuple[EvidenceArtifact, EvidenceFragment]:
        digest = hashlib.sha256(raw).hexdigest()
        existing = self.evidence.find_artifact_by_hash(digest, owner_id)
        if existing is None:
            owner_digest = hashlib.sha256(owner_id.encode("utf-8")).hexdigest()[:24]
            artifact_id = f"intent-artifact:{hashlib.sha256(f'{owner_digest}:{digest}'.encode()).hexdigest()[:24]}"
            raw_uri = self.blobs.put(f"intent/{owner_digest}/{artifact_id}.bin", raw)
            existing = self.evidence.save_artifact(EvidenceArtifact(
                artifact_id=artifact_id,
                owner_id=owner_id,
                source_type=source_type,
                content_type="application/json" if locator_type == "json_pointer" else "text/plain",
                original_name=original_name,
                raw_uri=raw_uri,
                content_hash=digest,
                parser_name="career_intent_text",
                parser_version="intent_ingestor_v1",
                provenance=Provenance(
                    parser_name="career_intent_text",
                    parser_version="intent_ingestor_v1",
                    schema_version="v0.7.1",
                ),
            ))
        text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        fragment_id = f"intent-fragment:{hashlib.sha256(f'{existing.artifact_id}:{text_hash}'.encode()).hexdigest()[:24]}"
        fragment = self.evidence.save_fragment(EvidenceFragment(
            fragment_id=fragment_id,
            artifact_id=existing.artifact_id,
            locator_type=locator_type,
            locator=locator,
            text=text,
            text_hash=text_hash,
            metadata={"parser_version": "intent_ingestor_v1"},
        ))
        return existing, fragment


__all__ = ["IntentEvidenceIngestor"]
