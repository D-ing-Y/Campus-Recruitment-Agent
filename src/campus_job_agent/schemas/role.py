"""Role instance and role-family profile contracts."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from campus_job_agent.schemas.candidate import CapabilityLevel
from campus_job_agent.schemas.evidence import utc_now


RequirementImportance = Literal["hard", "core", "bonus"]


class RoleRequirement(BaseModel):
    capability_id: str | None = None
    raw_label: str
    required_level: CapabilityLevel = "unknown"
    importance: RequirementImportance = "core"
    weight: float = Field(default=1.0, gt=0.0)
    supporting_claim_ids: list[str] = Field(default_factory=list)


class HiringSignal(BaseModel):
    signal_type: Literal[
        "written_exam",
        "interview",
        "project_preference",
        "salary",
        "work_context",
        "other",
    ]
    summary: str
    confidence: float = Field(ge=0.0, le=1.0)
    supporting_claim_ids: list[str] = Field(default_factory=list)


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

