"""Deterministic validation, revision and projection policy for CareerIntent."""

from __future__ import annotations

import re
from typing import Any

from campus_job_agent.schemas import (
    CareerIntent,
    CareerIntentCandidate,
    CareerIntentDraft,
    IntentConstraint,
    IntentRevisionPatch,
    project_constraint_fields,
    role_family_for_query,
    role_target_bindings_for_roles,
    stable_intent_id,
)


_SCOPE_KEYS = {"location", "graduation_year", "recruitment_type", "industry", "company", "company_type"}


def validate_candidate(
    *, candidate: CareerIntentCandidate, user_id: str, artifact_id: str,
    fragment_id: str, raw_text: str,
) -> CareerIntentDraft:
    issues: list[str] = []
    unresolved = list(dict.fromkeys(candidate.unresolved_fields))
    allowed_refs = {fragment_id}
    roles: list[str] = []
    for item in candidate.target_roles:
        if not set(item.evidence_fragment_ids).issubset(allowed_refs):
            issues.append("invalid_target_role_fragment_ref")
            continue
        if item.value not in roles:
            roles.append(item.value)
    if not roles:
        issues.append("target_roles_missing")
        roles = ["unknown"]
        if "target_roles" not in unresolved:
            unresolved.append("target_roles")

    constraints: list[IntentConstraint] = []
    for item in candidate.constraints:
        if not set(item.evidence_fragment_ids).issubset(allowed_refs):
            issues.append(f"invalid_fragment_ref:{item.key}")
            continue
        values = list(item.values)
        key = _normalized_key(item.key, values, raw_text)
        if key == "recruitment_type":
            values = ["campus_unspecified" if value in {"校招", "campus", "campus_recruitment"} else value for value in values]
            if "campus_unspecified" in values and "recruitment_type" not in unresolved:
                unresolved.append("recruitment_type")
        if key == "graduation_year":
            invalid_years = [value for value in values if not re.fullmatch(r"(?:19|20)\d{2}", value)]
            if invalid_years:
                issues.append("invalid_graduation_year")
        expected_kind = _expected_kind(key, values, raw_text)
        if expected_kind is not None and item.kind != expected_kind:
            issues.append(f"classification_mismatch:{key}:{expected_kind}")
        kind = expected_kind or item.kind
        affects_scope = kind == "hard" and key in _SCOPE_KEYS
        source_ref = f"{fragment_id}#/intent/{key}"
        constraint_id = stable_intent_id("intent-constraint", [key, values, kind, source_ref])
        constraints.append(IntentConstraint(
            constraint_id=constraint_id,
            key=key,
            operator="in" if len(values) > 1 else "equals",
            value=values if len(values) > 1 else values[0],
            kind=kind,
            affects_search_scope=affects_scope,
            status="unknown",
            source_ref=source_ref,
        ))
    draft_payload = {
        "user_id": user_id,
        "roles": roles,
        "constraints": [item.model_dump(mode="json") for item in constraints],
        "artifact_id": artifact_id,
        "fragment_id": fragment_id,
    }
    bindings = role_target_bindings_for_roles(roles)
    return CareerIntentDraft(
        draft_id=stable_intent_id("intent-draft", draft_payload),
        user_id=user_id,
        target_roles=roles,
        target_role_families=list(dict.fromkeys(item.role_family for item in bindings)),
        role_target_bindings=bindings,
        constraints=constraints,
        raw_artifact_ids=[artifact_id],
        source_fragment_ids=[fragment_id],
        unresolved_fields=unresolved,
        validation_issues=list(dict.fromkeys(issues)),
    )


def apply_revision(
    draft: CareerIntentDraft, patch: IntentRevisionPatch, *, response_fragment_id: str
) -> CareerIntentDraft:
    values = _all_constraint_values(draft.constraints)
    payload = patch.model_dump(mode="json", exclude_none=True)
    patched_fields = set(payload)
    roles = list(payload.pop("target_roles", draft.target_roles))
    mapping = {
        "locations": "location", "graduation_year": "graduation_year",
        "recruitment_type": "recruitment_type", "industries": "industry",
        "companies": "company", "company_types": "company_type",
    }
    patched_constraint_keys: set[str] = set()
    for field, key in mapping.items():
        if field not in payload:
            continue
        raw = payload[field]
        values[key] = raw if isinstance(raw, list) else [raw]
        patched_constraint_keys.add(key)

    existing_kinds = {item.key: item.kind for item in draft.constraints}
    existing_source_refs = {
        item.key: item.source_ref for item in draft.constraints if item.source_ref
    }
    constraints: list[IntentConstraint] = []
    for key, items in values.items():
        clean = [str(value).strip() for value in items if str(value).strip()]
        if not clean:
            continue
        kind = existing_kinds.get(key) or (
            "hard" if key in {"location", "graduation_year", "recruitment_type"} else "negotiable"
        )
        affects_scope = kind == "hard" and key in _SCOPE_KEYS
        source_ref = (
            f"{response_fragment_id}#/patch/{key}"
            if key in patched_constraint_keys
            else existing_source_refs.get(key, f"{response_fragment_id}#/patch/{key}")
        )
        constraints.append(IntentConstraint(
            constraint_id=stable_intent_id("intent-constraint", [key, clean, kind, source_ref]),
            key=key, operator="in" if len(clean) > 1 else "equals",
            value=clean if len(clean) > 1 else clean[0], kind=kind,
            affects_search_scope=affects_scope, status="unknown", source_ref=source_ref,
        ))
    unresolved = [
        item for item in draft.unresolved_fields
        if item not in patched_fields and item != "target_roles"
    ]
    if "target_roles" not in patched_fields and "target_roles" in draft.unresolved_fields:
        unresolved.append("target_roles")
    issues = [item for item in draft.validation_issues if not item.startswith((
        "classification_mismatch", "invalid_graduation_year", "target_roles_missing",
    ))]
    if not roles:
        issues.append("target_roles_missing")
        unresolved.append("target_roles")
    year_values = values.get("graduation_year", [])
    if any(not re.fullmatch(r"(?:19|20)\d{2}", str(value)) for value in year_values):
        issues.append("invalid_graduation_year")
    draft_payload: dict[str, Any] = {
        "previous": draft.draft_id, "roles": roles,
        "constraints": [item.model_dump(mode="json") for item in constraints],
        "revision": draft.revision + 1,
    }
    bindings = role_target_bindings_for_roles(roles or ["unknown"])
    return CareerIntentDraft(
        draft_id=stable_intent_id("intent-draft", draft_payload),
        user_id=draft.user_id,
        target_roles=roles or ["unknown"],
        target_role_families=list(dict.fromkeys(item.role_family for item in bindings)),
        role_target_bindings=bindings,
        constraints=constraints,
        raw_artifact_ids=draft.raw_artifact_ids,
        source_fragment_ids=list(dict.fromkeys([*draft.source_fragment_ids, response_fragment_id])),
        unresolved_fields=list(dict.fromkeys(unresolved)),
        validation_issues=list(dict.fromkeys(issues)),
        revision=draft.revision + 1,
    )


def publish_intent(
    draft: CareerIntentDraft, *, response_id: str, previous_snapshot_id: str | None = None
) -> CareerIntent:
    if draft.unresolved_fields or draft.validation_issues:
        raise ValueError("career intent draft still requires confirmation or correction")
    confirmed = [item.model_copy(update={"status": "confirmed"}) for item in draft.constraints]
    projected = project_constraint_fields(confirmed)
    return CareerIntent(
        user_id=draft.user_id,
        schema_version="v0.7.1",
        target_roles=draft.target_roles,
        target_role_families=draft.target_role_families,
        role_target_bindings=draft.role_target_bindings or role_target_bindings_for_roles(draft.target_roles),
        constraints=confirmed,
        confirmed=True,
        previous_snapshot_id=previous_snapshot_id,
        raw_artifact_ids=draft.raw_artifact_ids,
        source_fragment_ids=draft.source_fragment_ids,
        intent_candidate_id=draft.draft_id,
        confirmation_response_id=response_id,
        **projected,
    )


def _all_constraint_values(constraints: list[IntentConstraint]) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for item in constraints:
        raw = item.value if isinstance(item.value, list) else [item.value]
        bucket = result.setdefault(item.key, [])
        for value in raw:
            normalized = str(value)
            if normalized not in bucket:
                bucket.append(normalized)
    return result


def _expected_kind(key: str, values: list[str], raw_text: str) -> str | None:
    if key == "location" and "必须" in raw_text:
        return "hard"
    if key in {"company", "company_type", "industry", "work_mode", "other"} and any(
        marker in raw_text for marker in ("优先", "倾向", "最好", "偏好")
    ):
        return "negotiable"
    if key in {"graduation_year", "recruitment_type"}:
        return "hard"
    return None


def _normalized_key(key: str, values: list[str], raw_text: str) -> str:
    """Correct a narrow, contract-owned semantic boundary after model extraction."""

    if key == "industry" and values and all(
        value.endswith("公司") and value in raw_text for value in values
    ):
        return "company_type"
    return key


__all__ = ["validate_candidate", "apply_revision", "publish_intent"]
