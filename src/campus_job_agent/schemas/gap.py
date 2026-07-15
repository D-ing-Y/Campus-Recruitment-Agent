"""Explainable gap assessment contracts."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from campus_job_agent.schemas.evidence import utc_now


GapType = Literal[
    "capability_gap",
    "evidence_gap",
    "preference_conflict",
    "epistemic_uncertainty",
]


class GapItem(BaseModel):
    gap_type: GapType
    capability_id: str | None = None
    summary: str
    severity: Literal["low", "medium", "high", "blocking"]
    supporting_claim_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)


class GapAssessment(BaseModel):
    assessment_id: str
    schema_version: str = "v0.3"
    candidate_profile_snapshot_id: str
    role_profile_snapshot_id: str
    hard_constraints_passed: bool | None = None
    coverage_score: float | None = Field(default=None, ge=0.0, le=1.0)
    coverage_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    gaps: list[GapItem] = Field(default_factory=list)
    supporting_claim_ids: list[str] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=utc_now)

