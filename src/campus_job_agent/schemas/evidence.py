"""Evidence and profile snapshot contracts for the v0.3 foundation."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator


ClaimType = Literal[
    "observed_fact",
    "user_reported",
    "model_inference",
    "feedback_signal",
]
ClaimStatus = Literal["active", "superseded", "rejected"]
ProfileType = Literal["candidate", "career_intent", "role"]


def utc_now() -> datetime:
    return datetime.now(UTC)


def _validate_sha256(value: str) -> str:
    normalized = value.lower()
    if len(normalized) != 64 or any(ch not in "0123456789abcdef" for ch in normalized):
        raise ValueError("value must be a hexadecimal SHA-256 digest")
    return normalized


class Provenance(BaseModel):
    """How an artifact or derived value was obtained."""

    source_url: str | None = None
    published_at: datetime | None = None
    retrieved_at: datetime = Field(default_factory=utc_now)
    parser_name: str | None = None
    parser_version: str | None = None
    provider: str | None = None
    model: str | None = None
    prompt_version: str | None = None
    schema_version: str = "v0.3"


class EvidenceArtifact(BaseModel):
    artifact_id: str = Field(default_factory=lambda: str(uuid4()))
    owner_id: str
    source_type: str
    content_type: str
    source_url: str | None = None
    original_name: str
    raw_uri: str
    text_uri: str | None = None
    content_hash: str
    published_at: datetime | None = None
    retrieved_at: datetime = Field(default_factory=utc_now)
    parser_name: str | None = None
    parser_version: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    provenance: Provenance | None = None

    @field_validator("content_hash")
    @classmethod
    def validate_sha256(cls, value: str) -> str:
        return _validate_sha256(value)


class EvidenceFragment(BaseModel):
    fragment_id: str = Field(default_factory=lambda: str(uuid4()))
    artifact_id: str
    locator_type: str
    locator: dict[str, Any]
    text: str
    text_hash: str
    embedding_ref: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("text_hash")
    @classmethod
    def validate_text_sha256(cls, value: str) -> str:
        return _validate_sha256(value)


class ClaimExtractor(BaseModel):
    provider: str
    model: str


class EvidenceClaim(BaseModel):
    claim_id: str = Field(default_factory=lambda: str(uuid4()))
    subject_id: str
    predicate: str
    value: Any
    claim_type: ClaimType
    evidence_fragment_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    extractor: ClaimExtractor
    prompt_version: str
    schema_version: str = "v0.3"
    status: ClaimStatus = "active"
    created_at: datetime = Field(default_factory=utc_now)
    supersedes_claim_id: str | None = None

    def idempotency_key(self) -> str:
        payload = {
            "subject_id": self.subject_id,
            "predicate": self.predicate,
            "value": self.value,
            "evidence_fragment_ids": sorted(self.evidence_fragment_ids),
            "schema_version": self.schema_version,
        }
        canonical = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class ExtractedClaim(BaseModel):
    """LLM output before the runtime assigns IDs and extractor metadata."""

    predicate: str
    value: Any
    claim_type: ClaimType
    evidence_fragment_ids: list[str] = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)


class ClaimExtractionBatch(BaseModel):
    claims: list[ExtractedClaim] = Field(default_factory=list)


class ProfileSnapshot(BaseModel):
    snapshot_id: str = Field(default_factory=lambda: str(uuid4()))
    subject_id: str
    profile_type: ProfileType
    version: int = Field(ge=1)
    schema_version: str = "v0.3"
    profile_data: dict[str, Any]
    supporting_claim_ids: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)
    provenance: Provenance | None = None
