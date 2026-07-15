"""Career intent is intentionally separate from candidate capability."""

from datetime import datetime

from pydantic import BaseModel, Field

from campus_job_agent.schemas.evidence import utc_now


class CareerIntent(BaseModel):
    user_id: str
    schema_version: str = "v0.3"
    target_roles: list[str] = Field(default_factory=list)
    locations: list[str] = Field(default_factory=list)
    salary_min: int | None = Field(default=None, ge=0)
    salary_max: int | None = Field(default=None, ge=0)
    salary_unit: str | None = None
    industries: list[str] = Field(default_factory=list)
    company_types: list[str] = Field(default_factory=list)
    hard_constraints: list[str] = Field(default_factory=list)
    negotiable_preferences: list[str] = Field(default_factory=list)
    confirmed: bool = False
    supporting_claim_ids: list[str] = Field(default_factory=list)
    updated_at: datetime = Field(default_factory=utc_now)

