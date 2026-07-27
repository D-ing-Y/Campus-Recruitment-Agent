"""Explainable gap assessment contracts."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from campus_job_agent.schemas.evidence import utc_now


GapType = Literal[
    "capability_gap",
    "evidence_gap",
    "preference_conflict",
    "epistemic_uncertainty",
]


class GapItem(BaseModel):
    gap_id: str | None = None
    gap_type: GapType
    capability_id: str | None = None
    summary: str
    severity: Literal["low", "medium", "high", "blocking"]
    reason_code: str | None = None
    assessment_item_ids: list[str] = Field(default_factory=list)
    candidate_claim_ids: list[str] = Field(default_factory=list)
    role_claim_ids: list[str] = Field(default_factory=list)
    allowed_actions: list[str] = Field(default_factory=list)
    supporting_claim_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)


class GapAssessment(BaseModel):
    assessment_id: str
    schema_version: str = "v0.3"
    candidate_profile_snapshot_id: str
    role_profile_snapshot_id: str | None = None
    input_set_id: str | None = None
    career_intent_snapshot_id: str | None = None
    job_instance_profile_snapshot_id: str | None = None
    role_family_profile_snapshot_ids: list[str] = Field(default_factory=list)
    hard_constraint_status: Literal["passed", "failed", "unknown"] | None = None
    qualification_assessments: list[dict[str, Any]] = Field(default_factory=list)
    requirement_assessments: list[dict[str, Any]] = Field(default_factory=list)
    core_coverage: dict[str, Any] | None = None
    bonus_coverage: dict[str, Any] | None = None
    preference_assessments: list[dict[str, Any]] = Field(default_factory=list)
    fact_index: dict[str, Any] = Field(default_factory=dict)
    matching_policy_version: str | None = None
    status: Literal["current", "stale", "superseded"] | None = None
    supersedes_assessment_id: str | None = None
    hard_constraints_passed: bool | None = None
    coverage_score: float | None = Field(default=None, ge=0.0, le=1.0)
    coverage_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    gaps: list[GapItem] = Field(default_factory=list)
    supporting_claim_ids: list[str] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=utc_now)


def migrate_legacy_gap_assessment(legacy: GapAssessment) -> GapAssessment:
    """Keep legacy coverage read-only instead of inventing a v0.6 denominator."""

    if legacy.schema_version != "v0.3":
        return legacy
    return legacy.model_copy(
        update={
            "status": "stale",
            "fact_index": {"legacy_coverage_score": legacy.coverage_score},
        }
    )
