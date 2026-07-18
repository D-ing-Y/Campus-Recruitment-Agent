"""Candidate profile domain contracts.

``CandidateProfile`` keeps accepting v0.3 payloads while exposing the additive
v0.4 fields.  The v0.4 projector always writes ``schema_version="v0.4"``;
leaving the constructor default at v0.3 preserves compatibility with callers
that used the original v0.3 model as an empty profile factory.
"""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from campus_job_agent.schemas.evidence import utc_now


CapabilityLevel = Literal[
    "unknown",
    "beginner",
    "intermediate",
    "advanced",
    "expert",
]
ProfileFieldStatus = Literal["confirmed", "inferred", "unknown", "conflicted"]
CompletionReason = Literal[
    "sufficient",
    "low_information_value",
    "user_skipped",
    "budget_exhausted",
    "cancelled",
    "failed",
]


class CapabilityAssessment(BaseModel):
    capability_id: str | None = None
    raw_label: str
    level: CapabilityLevel = "unknown"
    confidence: float = Field(ge=0.0, le=1.0)
    status: ProfileFieldStatus = "inferred"
    evidence_summary: str | None = None
    supporting_claim_ids: list[str] = Field(default_factory=list)


class EducationRecord(BaseModel):
    institution: str
    degree: str | None = None
    major: str | None = None
    graduation_year: str | None = None
    supporting_claim_ids: list[str] = Field(default_factory=list)
    field_supporting_claim_ids: dict[str, list[str]] = Field(default_factory=dict)


class ExperienceRecord(BaseModel):
    experience_id: str
    kind: Literal["research", "project", "internship", "competition", "other"]
    title: str
    description: str | None = None
    responsibilities: list[str] = Field(default_factory=list)
    technologies: list[str] = Field(default_factory=list)
    outputs: list[str] = Field(default_factory=list)
    results: list[str] = Field(default_factory=list)
    supporting_claim_ids: list[str] = Field(default_factory=list)
    field_supporting_claim_ids: dict[str, list[str]] = Field(default_factory=dict)


class ResponsibilityBoundary(BaseModel):
    experience_id: str
    scope: str
    status: ProfileFieldStatus = "confirmed"
    confidence: float = Field(ge=0.0, le=1.0)
    supporting_claim_ids: list[str] = Field(min_length=1)


class EvidenceCoverage(BaseModel):
    supported_field_count: int = Field(default=0, ge=0)
    inferred_field_count: int = Field(default=0, ge=0)
    unknown_field_count: int = Field(default=0, ge=0)
    conflicted_field_count: int = Field(default=0, ge=0)


class CandidateProfile(BaseModel):
    candidate_id: str
    schema_version: str = "v0.3"
    education: list[EducationRecord] = Field(default_factory=list)
    capabilities: list[CapabilityAssessment] = Field(default_factory=list)
    experiences: list[ExperienceRecord] = Field(default_factory=list)
    transferable_skills: list[CapabilityAssessment] = Field(default_factory=list)
    responsibility_boundaries: list[ResponsibilityBoundary] = Field(
        default_factory=list
    )
    unknowns: list[str] = Field(default_factory=list)
    conflicts: list[dict[str, Any]] = Field(default_factory=list)
    evidence_coverage: EvidenceCoverage = Field(default_factory=EvidenceCoverage)
    supporting_claim_ids: list[str] = Field(default_factory=list)
    previous_snapshot_id: str | None = None
    completion_reason: CompletionReason | None = None
    generated_at: datetime = Field(default_factory=utc_now)
