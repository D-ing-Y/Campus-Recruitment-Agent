"""Raw-before-interpret feedback ingestion with path and content dedup guards."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Iterable

from campus_job_agent.schemas import EvidenceArtifact, EvidenceFragment, FeedbackEvent, FeedbackInput, Provenance
from campus_job_agent.schemas.matching import canonical_hash
from campus_job_agent.storage.base import BlobStore, EvidenceRepository
from campus_job_agent.workflows.feedback.repository import SQLiteFeedbackRepository


class FeedbackIngestionError(RuntimeError):
    pass


class FeedbackIngestor:
    def __init__(self, *, blob_store: BlobStore, evidence_repository: EvidenceRepository,
                 feedback_repository: SQLiteFeedbackRepository) -> None:
        self.blobs = blob_store
        self.evidence = evidence_repository
        self.feedback = feedback_repository

    def ingest(self, *, owner_id: str, feedback_input: FeedbackInput, allowed_path_roots: Iterable[str],
               plan_id: str | None, activity_id: str | None,
               target_job_profile_ids: list[str]) -> tuple[FeedbackEvent, EvidenceArtifact, list[EvidenceFragment]]:
        raw, original_name, content_type = self._read(feedback_input, allowed_path_roots)
        digest = hashlib.sha256(raw).hexdigest()
        artifact = self.evidence.find_artifact_by_hash(digest, owner_id)
        if artifact is None:
            owner_digest = hashlib.sha256(owner_id.encode("utf-8")).hexdigest()
            artifact_id = f"feedback-artifact:{hashlib.sha256(f'{owner_digest}:{digest}'.encode()).hexdigest()[:24]}"
            try:
                raw_uri = self.blobs.put(f"feedback/{owner_digest[:24]}/{artifact_id}.bin", raw)
            except Exception as exc:
                raise FeedbackIngestionError("feedback_raw_archive_failed") from exc
            artifact = EvidenceArtifact(
                artifact_id=artifact_id, owner_id=owner_id, source_type="feedback",
                content_type=content_type, original_name=original_name, raw_uri=raw_uri,
                content_hash=digest, parser_name="feedback_text", parser_version="feedback_parser_v1",
                metadata={"feedback_type": feedback_input.feedback_type, "source_kind": feedback_input.source_kind,
                          "stage": feedback_input.stage},
                provenance=Provenance(parser_name="feedback_text", parser_version="feedback_parser_v1", schema_version="v0.7"),
            )
            try:
                artifact = self.evidence.save_artifact(artifact)
            except Exception as exc:
                raise FeedbackIngestionError("feedback_raw_archive_failed") from exc
        text = self._text(raw, feedback_input)
        fragment_digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        fragment = EvidenceFragment(
            fragment_id=f"feedback-fragment:{hashlib.sha256(f'{artifact.artifact_id}:0:{len(text)}'.encode()).hexdigest()[:24]}",
            artifact_id=artifact.artifact_id, locator_type="json_pointer" if feedback_input.structured is not None else "char_range",
            locator={"path": "/"} if feedback_input.structured is not None else {"start": 0, "end": len(text)},
            text=text, text_hash=fragment_digest, metadata={"parser_version": "feedback_parser_v1"},
        )
        fragment = self.evidence.save_fragment(fragment)
        event_hash = canonical_hash("feedback-event", {
            "owner": owner_id, "type": feedback_input.feedback_type, "source": feedback_input.source_kind,
            "occurred_at": feedback_input.occurred_at.isoformat(), "content_hash": digest,
            "plan_id": plan_id, "activity_id": activity_id, "targets": sorted(set(target_job_profile_ids)),
            "stage": feedback_input.stage, "capability_id": feedback_input.capability_id,
            "suggested_scope": feedback_input.suggested_scope,
        })
        event = FeedbackEvent(
            feedback_event_id=f"feedback:{event_hash[7:31]}", user_id=owner_id,
            feedback_type=feedback_input.feedback_type, source_kind=feedback_input.source_kind,
            occurred_at=feedback_input.occurred_at, plan_id=plan_id, activity_id=activity_id,
            target_job_profile_ids=sorted(set(target_job_profile_ids)), stage=feedback_input.stage,
            capability_id=feedback_input.capability_id, suggested_scope=feedback_input.suggested_scope,
            raw_artifact_ids=[artifact.artifact_id], fragment_ids=[fragment.fragment_id],
            canonical_event_hash=event_hash,
        )
        event = self.feedback.save("feedback_event", event, owner_id=owner_id, idempotency_key=event_hash)
        return event, artifact, [fragment]

    def _read(self, value: FeedbackInput, roots: Iterable[str]) -> tuple[bytes, str, str]:
        if value.text is not None:
            return value.text.encode("utf-8"), "feedback.txt", "text/plain"
        if value.structured is not None:
            raw = json.dumps(value.structured, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
            return raw, "feedback.json", "application/json"
        path = Path(str(value.file_path)).resolve()
        allowed = [Path(item).resolve() for item in roots]
        if not any(path == root or root in path.parents for root in allowed):
            raise FeedbackIngestionError("permission_denied: feedback path outside allowed roots")
        if not path.is_file():
            raise FeedbackIngestionError("unsupported_input: feedback file not found")
        return path.read_bytes(), path.name, "text/plain" if path.suffix.lower() in {".txt", ".md", ".json"} else "application/octet-stream"

    @staticmethod
    def _text(raw: bytes, value: FeedbackInput) -> str:
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            return json.dumps({"unsupported_format": True, "byte_length": len(raw)}, sort_keys=True)


__all__ = ["FeedbackIngestor", "FeedbackIngestionError"]
