"""Career intent is intentionally separate from candidate capability.

The original v0.3 fields remain readable. Deterministic v0.6 matching only
consumes confirmed structured constraints; legacy strings are migrated to
explicit, unconfirmed constraints.
"""

from datetime import datetime
import hashlib
import json
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

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


class IntentValueCandidate(BaseModel):
    """One model-proposed value with an exact raw-evidence reference."""

    model_config = ConfigDict(extra="forbid")
    value: str = Field(min_length=1)
    evidence_fragment_ids: list[str] = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)

    @field_validator("value")
    @classmethod
    def normalize_value(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("intent candidate value must be non-empty")
        return normalized


class IntentConstraintCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: Literal[
        "location", "industry", "company", "company_type", "work_mode",
        "recruitment_type", "graduation_year", "other",
    ]
    values: list[str] = Field(min_length=1)
    kind: Literal["hard", "negotiable"]
    evidence_fragment_ids: list[str] = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)

    @field_validator("values")
    @classmethod
    def normalize_values(cls, values: list[str]) -> list[str]:
        result: list[str] = []
        for value in values:
            normalized = " ".join(value.split())
            if not normalized:
                raise ValueError("constraint candidate values must be non-empty")
            if normalized not in result:
                result.append(normalized)
        return result


class CareerIntentCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_roles: list[IntentValueCandidate] = Field(min_length=1)
    constraints: list[IntentConstraintCandidate] = Field(default_factory=list)
    unresolved_fields: list[
        Literal["target_roles", "graduation_year", "recruitment_type"]
    ] = Field(default_factory=list)


class CareerIntentDraft(BaseModel):
    draft_id: str
    schema_version: Literal["v0.7.1"] = "v0.7.1"
    user_id: str
    target_roles: list[str] = Field(min_length=1)
    target_role_families: list[str] = Field(min_length=1)
    constraints: list[IntentConstraint] = Field(default_factory=list)
    raw_artifact_ids: list[str] = Field(min_length=1)
    source_fragment_ids: list[str] = Field(min_length=1)
    unresolved_fields: list[str] = Field(default_factory=list)
    validation_issues: list[str] = Field(default_factory=list)
    revision: int = Field(default=0, ge=0)


class IntentRevisionPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_roles: list[str] | None = None
    locations: list[str] | None = None
    graduation_year: str | None = None
    recruitment_type: Literal[
        "autumn_campus", "spring_campus", "internship", "unknown"
    ] | None = None
    industries: list[str] | None = None
    companies: list[str] | None = None
    company_types: list[str] | None = None


class IntentReviewRequest(BaseModel):
    request_id: str
    schema_version: Literal["v0.7.1"] = "v0.7.1"
    thread_id: str
    run_id: str
    user_id: str
    interaction_type: Literal["review_career_intent"] = "review_career_intent"
    draft_id: str
    summary: dict[str, Any]
    unresolved_fields: list[str] = Field(default_factory=list)
    validation_issues: list[str] = Field(default_factory=list)
    allowed_actions: list[Literal["confirm", "revise", "cancel"]] = Field(min_length=1)


class IntentReviewResponse(BaseModel):
    response_id: str
    schema_version: Literal["v0.7.1"] = "v0.7.1"
    request_id: str
    thread_id: str
    user_id: str
    action: Literal["confirm", "revise", "cancel"]
    patch: IntentRevisionPatch | None = None

    @model_validator(mode="after")
    def validate_action(self) -> "IntentReviewResponse":
        if self.action == "revise" and self.patch is None:
            raise ValueError("revise action requires patch")
        if self.action != "revise" and self.patch is not None:
            raise ValueError("patch is only allowed for revise action")
        return self


class IntentValidationReceipt(BaseModel):
    receipt_id: str
    schema_version: Literal["v0.7.1"] = "v0.7.1"
    run_id: str
    draft_id: str
    status: Literal["accepted", "needs_confirmation", "rejected"]
    reason_codes: list[str] = Field(default_factory=list)
    fragment_ids: list[str] = Field(default_factory=list)
    prompt_version: str = "career_intent_extractor_v1"


class IntentConfirmationRecord(BaseModel):
    confirmation_id: str
    schema_version: Literal["v0.7.1"] = "v0.7.1"
    response_id: str
    request_id: str
    user_id: str
    draft_id: str
    response_artifact_id: str
    response_fragment_id: str
    status: Literal["confirmed", "revised", "cancelled", "needs_confirmation"]
    snapshot_id: str | None = None
    search_scope_id: str | None = None


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
    raw_artifact_ids: list[str] = Field(default_factory=list)
    source_fragment_ids: list[str] = Field(default_factory=list)
    intent_candidate_id: str | None = None
    confirmation_response_id: str | None = None
    updated_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def validate_v071_canonical_projection(self) -> "CareerIntent":
        if self.schema_version != "v0.7.1" or not self.confirmed:
            return self
        if any(item.status != "confirmed" for item in self.constraints):
            raise ValueError("confirmed v0.7.1 intent requires confirmed constraints")
        projected = project_constraint_fields(self.constraints)
        checks = {
            "locations": self.locations,
            "graduation_year": self.graduation_year,
            "recruitment_type": self.recruitment_type,
            "industries": self.industries,
            "companies": self.companies,
            "company_types": self.company_types,
        }
        if checks != projected:
            raise ValueError("v0.7.1 intent flat fields drift from canonical constraints")
        if not self.raw_artifact_ids or not self.source_fragment_ids:
            raise ValueError("confirmed v0.7.1 intent requires raw evidence refs")
        return self


def project_constraint_fields(constraints: list[IntentConstraint]) -> dict[str, Any]:
    """Project only confirmed search-scope constraints into compatibility fields."""

    values: dict[str, list[str]] = {
        "location": [], "graduation_year": [], "recruitment_type": [],
        "industry": [], "company": [], "company_type": [],
    }
    for item in constraints:
        if item.status != "confirmed" or not item.affects_search_scope:
            continue
        if item.key not in values:
            continue
        raw_values = item.value if isinstance(item.value, list) else [item.value]
        for raw in raw_values:
            value = str(raw).strip()
            if value and value not in values[item.key]:
                values[item.key].append(value)
    return {
        "locations": values["location"],
        "graduation_year": values["graduation_year"][0] if values["graduation_year"] else "unknown",
        "recruitment_type": values["recruitment_type"][0] if values["recruitment_type"] else "unknown",
        "industries": values["industry"],
        "companies": values["company"],
        "company_types": values["company_type"],
    }


def role_family_for_query(value: str) -> str:
    normalized = value.casefold()
    if any(marker in normalized for marker in ("agent", "智能体", "langgraph", "llm应用")):
        return "ai_agent_engineering"
    slug = re.sub(r"[^a-z0-9]+", "_", normalized).strip("_")
    if slug:
        return slug
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]
    return f"role_family_{digest}"


def stable_intent_id(prefix: str, payload: Any) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return f"{prefix}:{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:24]}"


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
