"""Role instance and role-family profile contracts."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from campus_job_agent.schemas.candidate import CapabilityLevel
from campus_job_agent.schemas.evidence import utc_now


RequirementImportance = Literal["hard", "core", "bonus", "context"]


class RoleRequirement(BaseModel):
    requirement_id: str | None = None
    category: Literal[
        "hard_qualification", "core_capability", "bonus_capability",
        "responsibility", "work_context", "other",
    ] = "core_capability"
    capability_id: str | None = None
    raw_label: str
    required_level: CapabilityLevel = "unknown"
    importance: RequirementImportance = "core"
    obligation: Literal["required", "preferred", "mentioned", "unknown"] = "unknown"
    scope: Literal["job_instance", "role_family", "company", "location", "time"] = "job_instance"
    weight: float = Field(default=1.0, gt=0.0)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    authority: Literal["primary", "allowed", "signal_only", "forbidden"] = "allowed"
    supporting_claim_ids: list[str] = Field(default_factory=list)


class HiringSignal(BaseModel):
    signal_id: str | None = None
    signal_type: Literal[
        "written_exam",
        "interview",
        "project_preference",
        "tech_stack",
        "salary",
        "work_context",
        "other",
    ]
    stage: str | None = None
    scope_level: Literal["job_instance", "company_role", "role_family", "company_only", "unknown"] = "unknown"
    summary: str
    occurrence_count: int = Field(default=1, ge=1)
    independent_source_count: int = Field(default=1, ge=1)
    frequency_label: Literal["observed_signal", "frequent_signal", "unknown"] = "observed_signal"
    confidence: float = Field(ge=0.0, le=1.0)
    freshness: Literal["current_window", "historical", "unknown"] = "unknown"
    supporting_claim_ids: list[str] = Field(default_factory=list)


class Qualification(BaseModel):
    qualification_id: str
    qualification_type: Literal["degree", "major", "graduation_year", "recruitment_eligibility", "language", "location", "other"]
    operator: str = "equals"
    value: Any
    importance: RequirementImportance = "hard"
    status: Literal["confirmed", "inferred", "unknown", "conflicted"] = "confirmed"
    confidence: float = Field(ge=0.0, le=1.0)
    supporting_claim_ids: list[str] = Field(default_factory=list)


class FamilyRequirementAggregate(BaseModel):
    aggregate_id: str
    category: str
    capability_id: str | None = None
    raw_labels: list[str] = Field(default_factory=list)
    importance_distribution: dict[str, int] = Field(default_factory=dict)
    supporting_job_instance_count: int = Field(ge=0)
    eligible_job_instance_count: int = Field(ge=0)
    supporting_company_count: int = Field(ge=0)
    eligible_company_count: int = Field(ge=0)
    prevalence: float | None = Field(default=None, ge=0.0, le=1.0)
    company_coverage: float | None = Field(default=None, ge=0.0, le=1.0)
    prevalence_band: Literal["common", "frequent", "observed", "insufficient_sample"]
    scope_notes: list[str] = Field(default_factory=list)
    supporting_claim_ids: list[str] = Field(default_factory=list)


class JobInstanceRoleProfile(BaseModel):
    role_profile_id: str
    schema_version: Literal["v0.5"] = "v0.5"
    profile_scope: Literal["job_instance"] = "job_instance"
    job_cluster_id: str
    role_title: str
    role_family: str
    company: str
    locations: list[str] = Field(default_factory=list)
    recruitment_type: str = "unknown"
    graduation_year: str = "unknown"
    source_status: str = "unknown"
    application_url: str | None = None
    application_deadline: datetime | None = None
    qualifications: list[Qualification] = Field(default_factory=list)
    responsibilities: list[RoleRequirement] = Field(default_factory=list)
    requirements: list[RoleRequirement] = Field(default_factory=list)
    bonus_items: list[RoleRequirement] = Field(default_factory=list)
    hiring_signals: list[HiringSignal] = Field(default_factory=list)
    work_context: list[RoleRequirement] = Field(default_factory=list)
    company_specific_items: list[RoleRequirement] = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)
    conflicts: list[dict[str, Any]] = Field(default_factory=list)
    evidence_coverage: dict[str, Any] = Field(default_factory=dict)
    source_refs: list[dict[str, Any]] = Field(default_factory=list)
    supporting_claim_ids: list[str] = Field(default_factory=list)
    freshness: dict[str, Any] = Field(default_factory=dict)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    previous_snapshot_id: str | None = None
    generated_at: datetime = Field(default_factory=utc_now)


class RoleFamilyProfile(BaseModel):
    role_profile_id: str
    schema_version: Literal["v0.5"] = "v0.5"
    profile_scope: Literal["role_family"] = "role_family"
    role_title: str
    role_family: str
    market_scope: dict[str, Any] = Field(default_factory=dict)
    sample: dict[str, Any]
    hard_qualifications: list[FamilyRequirementAggregate] = Field(default_factory=list)
    common_responsibilities: list[FamilyRequirementAggregate] = Field(default_factory=list)
    core_requirements: list[FamilyRequirementAggregate] = Field(default_factory=list)
    frequent_requirements: list[FamilyRequirementAggregate] = Field(default_factory=list)
    observed_requirements: list[FamilyRequirementAggregate] = Field(default_factory=list)
    bonus_items: list[FamilyRequirementAggregate] = Field(default_factory=list)
    hiring_signals: list[HiringSignal] = Field(default_factory=list)
    company_specific_variations: list[dict[str, Any]] = Field(default_factory=list)
    location_specific_variations: list[dict[str, Any]] = Field(default_factory=list)
    temporal_variations: list[dict[str, Any]] = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)
    conflicts: list[dict[str, Any]] = Field(default_factory=list)
    source_coverage: dict[str, Any] = Field(default_factory=dict)
    supporting_job_instance_profile_ids: list[str] = Field(default_factory=list)
    supporting_claim_ids: list[str] = Field(default_factory=list)
    aggregation_policy_version: str = "role_family_aggregation_v1"
    thresholds: dict[str, float | int] = Field(default_factory=dict)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    previous_snapshot_id: str | None = None
    generated_at: datetime = Field(default_factory=utc_now)


class RoleProfile(BaseModel):
    role_profile_id: str
    schema_version: str = "v0.3"
    profile_scope: Literal["job_instance", "role_family"]
    role_title: str
    role_family: str | None = None
    company: str | None = None
    locations: list[str] = Field(default_factory=list)
    qualifications: list[dict[str, Any]] = Field(default_factory=list)
    responsibilities: list[str] = Field(default_factory=list)
    requirements: list[RoleRequirement] = Field(default_factory=list)
    hiring_signals: list[HiringSignal] = Field(default_factory=list)
    company_specific_items: list[str] = Field(default_factory=list)
    supporting_claim_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    generated_at: datetime = Field(default_factory=utc_now)
