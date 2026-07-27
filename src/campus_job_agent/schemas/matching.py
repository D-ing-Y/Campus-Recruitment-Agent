"""Immutable v0.6 deterministic profile-matching contracts."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from campus_job_agent.schemas.evidence import utc_now


def canonical_hash(prefix: str, value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return "sha256:" + hashlib.sha256(f"{prefix}:{payload}".encode()).hexdigest()


class MatchingInputSet(BaseModel):
    model_config = ConfigDict(frozen=True)
    input_set_id: str = ""
    schema_version: Literal["v0.6"] = "v0.6"
    user_id: str
    candidate_profile_snapshot_id: str
    career_intent_snapshot_id: str
    job_instance_profile_snapshot_ids: list[str] = Field(min_length=1)
    role_family_profile_snapshot_ids: list[str] = Field(default_factory=list)
    candidate_policy_version: str = "candidate_v0.4"
    role_policy_version: str = "role_v0.5"
    matching_policy_version: str = "matching_v1"
    snapshot_hashes: dict[str, str] = Field(default_factory=dict)
    canonical_input_hash: str = ""
    created_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def canonicalize(self) -> "MatchingInputSet":
        jobs = sorted(dict.fromkeys(self.job_instance_profile_snapshot_ids))
        families = sorted(dict.fromkeys(self.role_family_profile_snapshot_ids))
        value = {
            "user_id": self.user_id, "candidate": self.candidate_profile_snapshot_id,
            "intent": self.career_intent_snapshot_id, "jobs": jobs, "families": families,
            "policies": [self.candidate_policy_version, self.role_policy_version, self.matching_policy_version],
            "snapshot_hashes": self.snapshot_hashes,
        }
        digest = canonical_hash("matching-input", value)
        object.__setattr__(self, "job_instance_profile_snapshot_ids", jobs)
        object.__setattr__(self, "role_family_profile_snapshot_ids", families)
        object.__setattr__(self, "canonical_input_hash", digest)
        object.__setattr__(self, "input_set_id", self.input_set_id or f"matching-input:{digest[7:31]}")
        return self


class QualificationAssessment(BaseModel):
    assessment_item_id: str
    qualification_id: str
    qualification_type: str
    operator: str
    required_value: Any = None
    candidate_value: Any = None
    outcome: Literal["passed", "failed", "unknown", "conflicted", "not_applicable"]
    reason_code: str
    candidate_claim_ids: list[str] = Field(default_factory=list)
    role_claim_ids: list[str] = Field(default_factory=list)
    comparator_version: str = "qualification_v1"


class RequirementAssessment(BaseModel):
    assessment_item_id: str
    requirement_id: str
    capability_id: str | None = None
    raw_label: str
    mapping_type: Literal["exact", "transfer", "unmapped"]
    ontology_relation_id: str | None = None
    required_level: str = "unknown"
    candidate_level: str = "unknown"
    outcome: Literal["satisfied", "insufficient", "evidence_insufficient", "unknown", "unmapped", "not_applicable"]
    importance: Literal["core", "bonus"]
    obligation: str
    base_weight: float = Field(gt=0)
    effective_weight: float = Field(gt=0)
    reason_code: str
    candidate_claim_ids: list[str] = Field(default_factory=list)
    role_claim_ids: list[str] = Field(default_factory=list)
    policy_version: str = "matching_weight_v1"

    @model_validator(mode="after")
    def validate_transfer(self) -> "RequirementAssessment":
        if self.mapping_type == "transfer" and not self.ontology_relation_id:
            raise ValueError("transfer mapping requires ontology_relation_id")
        return self


class CoverageContribution(BaseModel):
    assessment_item_id: str
    outcome: str
    effective_weight: float = Field(gt=0)
    eligible: bool
    covered: bool
    uncertain: bool


class CoverageBreakdown(BaseModel):
    dimension: Literal["core_capability", "bonus_capability"]
    total_weight: float = Field(ge=0)
    eligible_weight: float = Field(ge=0)
    covered_weight: float = Field(ge=0)
    uncertain_weight: float = Field(ge=0)
    coverage: float | None = Field(default=None, ge=0, le=1)
    covered_item_ids: list[str] = Field(default_factory=list)
    uncovered_item_ids: list[str] = Field(default_factory=list)
    uncertain_item_ids: list[str] = Field(default_factory=list)
    excluded_item_ids: list[str] = Field(default_factory=list)
    contributions: list[CoverageContribution] = Field(default_factory=list)
    policy_version: str = "matching_weight_v1"

    @model_validator(mode="after")
    def validate_arithmetic(self) -> "CoverageBreakdown":
        if self.covered_weight > self.eligible_weight + 1e-9:
            raise ValueError("covered_weight cannot exceed eligible_weight")
        expected = None if self.eligible_weight == 0 else round(self.covered_weight / self.eligible_weight, 6)
        if self.coverage != expected:
            raise ValueError("coverage must equal covered_weight / eligible_weight, or null")
        return self


class PreferenceAssessment(BaseModel):
    assessment_item_id: str
    preference_key: str
    constraint_kind: Literal["hard", "negotiable"]
    intent_value: Any = None
    role_value: Any = None
    outcome: Literal["aligned", "conflict", "unknown", "not_applicable"]
    reason_code: str
    intent_source_ref: str | None = None
    role_claim_ids: list[str] = Field(default_factory=list)


class ComparisonEntry(BaseModel):
    job_instance_profile_snapshot_id: str
    gap_assessment_id: str
    recommended_tier: Literal["review_first", "needs_clarification", "blocked"]
    hard_rank: int = Field(ge=0, le=2)
    blocking_preference_conflict_count: int = Field(ge=0)
    core_coverage: float | None = Field(default=None, ge=0, le=1)
    uncertainty_weight: float = Field(ge=0)
    stable_tie_breaker: str


class ComparisonSet(BaseModel):
    comparison_set_id: str
    schema_version: Literal["v0.6"] = "v0.6"
    input_set_id: str
    entries: list[ComparisonEntry] = Field(min_length=1)
    ranking_policy_version: str = "matching_rank_v1"
    status: Literal["current", "stale", "superseded"] = "current"
    supersedes_comparison_set_id: str | None = None
    canonical_hash: str
    generated_at: datetime = Field(default_factory=utc_now)


class JobMatchExplanation(BaseModel):
    job_profile_id: str
    summary: str
    fact_ids: list[str] = Field(min_length=1)
    claim_ids: list[str] = Field(default_factory=list)
    suggested_actions: list[str] = Field(default_factory=list)


class MatchExplanation(BaseModel):
    explanation_id: str
    schema_version: Literal["v0.6"] = "v0.6"
    comparison_set_id: str
    job_explanations: list[JobMatchExplanation]
    warnings: list[str] = Field(default_factory=lambda: ["coverage_is_not_offer_probability"])
    prompt_version: str = "match_explanation_v1"
