"""Career intent is intentionally separate from candidate capability.

The original v0.3 fields remain readable. Deterministic v0.6 matching only
consumes confirmed structured constraints; legacy strings are migrated to
explicit, unconfirmed constraints.
"""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from campus_job_agent.schemas.evidence import utc_now


class IntentConstraint(BaseModel):
    constraint_id: str
    key: Literal[
        "location", "salary", "industry", "company", "company_type",
        "work_mode", "recruitment_type", "graduation_year", "other",
    ]
    operator: Literal["equals", "in", "contains_any", "contains_all", "gte", "lte", "range"] = "equals"
    value: Any
    kind: Literal["hard", "negotiable"]
    affects_search_scope: bool = False
    status: Literal["confirmed", "unknown", "conflicted"] = "unknown"
    source_ref: str | None = None


class CareerIntent(BaseModel):
    user_id: str
    schema_version: str = "v0.3"
    target_roles: list[str] = Field(default_factory=list)
    target_role_families: list[str] = Field(default_factory=list)
    locations: list[str] = Field(default_factory=list)
    graduation_year: str = "unknown"
    recruitment_type: str = "unknown"
    salary_min: int | None = Field(default=None, ge=0)
    salary_max: int | None = Field(default=None, ge=0)
    salary_unit: str | None = None
    industries: list[str] = Field(default_factory=list)
    companies: list[str] = Field(default_factory=list)
    company_types: list[str] = Field(default_factory=list)
    hard_constraints: list[str] = Field(default_factory=list)
    negotiable_preferences: list[str] = Field(default_factory=list)
    constraints: list[IntentConstraint] = Field(default_factory=list)
    confirmed: bool = False
    supporting_claim_ids: list[str] = Field(default_factory=list)
    previous_snapshot_id: str | None = None
    search_scope_policy_version: str = "intent_scope_v1"
    updated_at: datetime = Field(default_factory=utc_now)


def migrate_legacy_career_intent(intent: CareerIntent) -> CareerIntent:
    """Return a v0.6-shaped snapshot with legacy text pending confirmation."""

    if intent.schema_version == "v0.6" and not (
        intent.hard_constraints or intent.negotiable_preferences
    ):
        return intent
    constraints = list(intent.constraints)
    for kind, field_name, values in (
        ("hard", "hard_constraints", intent.hard_constraints),
        ("negotiable", "negotiable_preferences", intent.negotiable_preferences),
    ):
        for index, value in enumerate(values):
            constraints.append(
                IntentConstraint(
                    constraint_id=f"legacy:{kind}:{index}",
                    key="other",
                    value=value,
                    kind=kind,
                    affects_search_scope=False,
                    status="unknown",
                    source_ref=f"legacy#/{field_name}/{index}",
                )
            )
    return intent.model_copy(
        update={"schema_version": "v0.6", "constraints": constraints, "confirmed": False}
    )
