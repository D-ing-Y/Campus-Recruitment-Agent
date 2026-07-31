"""Evidence and profile snapshot contracts for the v0.3 foundation."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from typing import Annotated, Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from campus_job_agent.schemas.candidate_taxonomy import (
    CapabilityId,
    CapabilityClaimValue,
    ExperienceKindValue,
)


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
    source_evidence_ids: list[str] = Field(default_factory=list)
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
            "claim_type": self.claim_type,
            "evidence_fragment_ids": sorted(self.evidence_fragment_ids),
            "source_evidence_ids": sorted(self.source_evidence_ids),
            "schema_version": self.schema_version,
            "supersedes_claim_id": self.supersedes_claim_id,
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
    """Compatibility/runtime Claim before IDs and extractor metadata are assigned."""

    predicate: str
    value: Any
    claim_type: ClaimType
    evidence_fragment_ids: list[str] = Field(min_length=1)
    source_evidence_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)

    @field_validator("confidence", mode="before")
    @classmethod
    def normalize_confidence_label(cls, value: Any) -> Any:
        if isinstance(value, str):
            aliases = {"high": 0.9, "medium": 0.6, "low": 0.3}
            return aliases.get(value.strip().casefold(), value)
        return value


class _TypedClaimCandidate(BaseModel):
    """Fields shared by the model-visible typed Claim variants."""

    model_config = ConfigDict(extra="forbid")

    claim_type: ClaimType
    evidence_fragment_ids: list[str] = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)

    @field_validator("confidence", mode="before")
    @classmethod
    def normalize_confidence_label(cls, value: Any) -> Any:
        if isinstance(value, str):
            aliases = {"high": 0.9, "medium": 0.6, "low": 0.3}
            return aliases.get(value.strip().casefold(), value)
        return value


class CapabilityClaimCandidate(_TypedClaimCandidate):
    claim_kind: Literal["capability"]
    capability_id: CapabilityId = Field(
        description="A versioned capability_id exposed as a closed Tool enum.",
    )
    value: CapabilityClaimValue


class EducationClaimCandidate(_TypedClaimCandidate):
    claim_kind: Literal["education"]
    record_id: str = Field(
        min_length=1,
        description="Batch-local label reused for fields of the same education record.",
    )
    field: Literal["institution", "degree", "major", "graduation_year"]
    value: str = Field(min_length=1)


class ExperienceKindClaimCandidate(_TypedClaimCandidate):
    claim_kind: Literal["experience_kind"]
    record_id: str = Field(
        min_length=1,
        description="Batch-local label reused for fields of the same experience record.",
    )
    value: ExperienceKindValue


class ExperienceTextClaimCandidate(_TypedClaimCandidate):
    claim_kind: Literal["experience_text"]
    record_id: str = Field(
        min_length=1,
        description="Batch-local label reused for fields of the same experience record.",
    )
    field: Literal["title", "description"]
    value: str = Field(min_length=1)


class ExperienceListClaimCandidate(_TypedClaimCandidate):
    claim_kind: Literal["experience_list"]
    record_id: str = Field(
        min_length=1,
        description="Batch-local label reused for fields of the same experience record.",
    )
    field: Literal["responsibilities", "technologies", "outputs", "results"]
    value: list[str] = Field(min_length=1)


class UnsupportedClaimCandidate(_TypedClaimCandidate):
    claim_kind: Literal["unsupported"]
    predicate: str = Field(
        min_length=1,
        description="Source-faithful label for a fact outside the current Candidate contract.",
    )
    value: Any


TypedClaimCandidate = Annotated[
    CapabilityClaimCandidate
    | EducationClaimCandidate
    | ExperienceKindClaimCandidate
    | ExperienceTextClaimCandidate
    | ExperienceListClaimCandidate
    | UnsupportedClaimCandidate,
    Field(discriminator="claim_kind"),
]

class ClaimExtractionBatch(BaseModel):
    """Typed model boundary with deterministic legacy input migration."""

    claims: list[TypedClaimCandidate] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_claims(cls, value: Any) -> Any:
        if not isinstance(value, dict) or not isinstance(value.get("claims"), list):
            return value
        data = dict(value)
        data["claims"] = [
            _upgrade_legacy_claim(item) if isinstance(item, dict) else item
            for item in value["claims"]
        ]
        return data

    def to_extracted_claims(self) -> list[ExtractedClaim]:
        result: list[ExtractedClaim] = []
        for item in self.claims:
            common = {
                "claim_type": item.claim_type,
                "evidence_fragment_ids": item.evidence_fragment_ids,
                "confidence": item.confidence,
            }
            if isinstance(item, CapabilityClaimCandidate):
                predicate = f"capability:{item.capability_id}"
                value = item.value.model_dump(mode="json", exclude_none=True)
            elif isinstance(item, EducationClaimCandidate):
                predicate = f"education:{item.record_id}.{item.field}"
                value = item.value
            elif isinstance(item, ExperienceKindClaimCandidate):
                predicate = f"experience:{item.record_id}.kind"
                value = item.value.model_dump(mode="json", exclude_none=True)
            elif isinstance(item, ExperienceTextClaimCandidate):
                predicate = f"experience:{item.record_id}.{item.field}"
                value = item.value
            elif isinstance(item, ExperienceListClaimCandidate):
                predicate = f"experience:{item.record_id}.{item.field}"
                value = item.value
            else:
                predicate = item.predicate
                value = item.value
            result.append(ExtractedClaim(predicate=predicate, value=value, **common))
        return result


_EDUCATION_CANDIDATE = re.compile(
    r"^education:([a-zA-Z0-9][a-zA-Z0-9_-]*)\."
    r"(institution|degree|major|graduation_year)$"
)
_EXPERIENCE_CANDIDATE = re.compile(
    r"^experience:([a-zA-Z0-9][a-zA-Z0-9_-]*)\."
    r"(kind|title|description|responsibilities|technologies|outputs|results)$"
)


def _upgrade_legacy_claim(item: dict[str, Any]) -> dict[str, Any]:
    """Read old cache/fixtures without publishing their Any schema to the model."""

    if "claim_kind" in item:
        return item
    predicate = str(item.get("predicate") or "")
    raw_value = item.get("value")
    common = {
        key: item.get(key)
        for key in ("claim_type", "evidence_fragment_ids", "confidence")
    }
    if predicate.startswith("capability:") and predicate.count(":") == 1:
        capability_id = predicate.split(":", 1)[1]
        value = dict(raw_value) if isinstance(raw_value, dict) else {"level": raw_value}
        value.setdefault("raw_label", value.get("label") or capability_id)
        return {
            "claim_kind": "capability", "capability_id": capability_id,
            "value": value, **common,
        }
    education = _EDUCATION_CANDIDATE.fullmatch(predicate)
    if education is not None and isinstance(raw_value, str) and raw_value.strip():
        return {
            "claim_kind": "education", "record_id": education.group(1),
            "field": education.group(2), "value": raw_value, **common,
        }
    experience = _EXPERIENCE_CANDIDATE.fullmatch(predicate)
    if experience is not None:
        record_id, field = experience.groups()
        if field == "kind" and isinstance(raw_value, (str, dict)):
            return {
                "claim_kind": "experience_kind", "record_id": record_id,
                "value": raw_value, **common,
            }
        if field in {"title", "description"} and isinstance(raw_value, str) and raw_value.strip():
            return {
                "claim_kind": "experience_text", "record_id": record_id,
                "field": field, "value": raw_value, **common,
            }
        if (
            field in {"responsibilities", "technologies", "outputs", "results"}
            and isinstance(raw_value, list)
            and raw_value
            and all(isinstance(value, str) and value.strip() for value in raw_value)
        ):
            return {
                "claim_kind": "experience_list", "record_id": record_id,
                "field": field, "value": raw_value, **common,
            }
    return {
        "claim_kind": "unsupported", "predicate": predicate or "unsupported",
        "value": raw_value, **common,
    }


class ValidationReceipt(BaseModel):
    """Per-model-item Candidate domain validation result."""

    receipt_id: str = Field(default_factory=lambda: f"validation-{uuid4()}")
    schema_version: str = "v0.7.1"
    run_id: str
    workflow: str
    node: str
    item_index: int
    candidate_hash: str
    subject_ref: str
    fragment_ids: list[str] = Field(default_factory=list)
    predicate: str | None = None
    status: Literal[
        "accepted", "rejected", "duplicate", "retryable_error", "fatal_error"
    ]
    reason_codes: list[str] = Field(default_factory=list)
    persisted_claim_id: str | None = None
    extractor: str | None = None
    prompt_version: str | None = None
    schema_version_used: str | None = None


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
