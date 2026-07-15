"""Candidate profile domain contracts."""

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


class CapabilityAssessment(BaseModel):
    capability_id: str | None = None
    raw_label: str
    level: CapabilityLevel = "unknown"
    confidence: float = Field(ge=0.0, le=1.0)
    status: ProfileFieldStatus = "inferred"
    supporting_claim_ids: list[str] = Field(default_factory=list)


class EducationRecord(BaseModel):
    institution: str
    degree: str | None = None
    major: str | None = None
    graduation_year: str | None = None
    supporting_claim_ids: list[str] = Field(default_factory=list)


class ExperienceRecord(BaseModel):
    experience_id: str
    kind: Literal["research", "project", "internship", "competition", "other"]
    title: str
    description: str | None = None
    responsibilities: list[str] = Field(default_factory=list)
    technologies: list[str] = Field(default_factory=list)
    supporting_claim_ids: list[str] = Field(default_factory=list)


class CandidateProfile(BaseModel):
    candidate_id: str
    schema_version: str = "v0.3"
    education: list[EducationRecord] = Field(default_factory=list)
    capabilities: list[CapabilityAssessment] = Field(default_factory=list)
    experiences: list[ExperienceRecord] = Field(default_factory=list)
    transferable_skills: list[CapabilityAssessment] = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)
    conflicts: list[dict[str, Any]] = Field(default_factory=list)
    supporting_claim_ids: list[str] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=utc_now)

