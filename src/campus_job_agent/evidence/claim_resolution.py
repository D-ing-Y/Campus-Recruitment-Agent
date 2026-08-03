"""Deterministic selection and narrow semantic resolution for Candidate claims."""

from __future__ import annotations

import json
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Literal

from campus_job_agent.schemas import ClaimResolutionSummary, EvidenceClaim
from campus_job_agent.schemas.candidate_taxonomy import (
    CapabilityClaimValue,
    ExperienceKindValue,
)


ValueRelation = Literal["equivalent", "refinement", "conflict"]
_DATE_RE = re.compile(r"^(\d{4})(?:-(\d{1,2}))?$")


@dataclass(frozen=True)
class ClaimResolutionResult:
    selected_claims: list[EvidenceClaim]
    summary: ClaimResolutionSummary


def resolve_candidate_claims(
    claims: list[EvidenceClaim], *, current_resume_evidence_id: str | None
) -> ClaimResolutionResult:
    """Return the claims eligible for one current Candidate projection."""

    eligible: list[EvidenceClaim] = []
    excluded: dict[str, str] = {}
    for claim in claims:
        reason = _base_exclusion_reason(claim, current_resume_evidence_id)
        if reason is None:
            eligible.append(claim)
        else:
            excluded[claim.claim_id] = reason

    superseded_ids = {
        predecessor_id
        for claim in eligible
        for predecessor_id in claim.all_supersedes_claim_ids
    }
    selected = []
    for claim in eligible:
        if claim.claim_id in superseded_ids:
            excluded[claim.claim_id] = "superseded_by_active_claim"
        else:
            selected.append(claim)
    selected.sort(key=lambda item: (item.created_at, item.claim_id))

    refined: set[str] = set()
    conflicted: set[str] = set()
    grouped: dict[str, list[EvidenceClaim]] = defaultdict(list)
    for claim in selected:
        grouped[claim.predicate].append(claim)
    for predicate, values in grouped.items():
        for index, left in enumerate(values):
            for right in values[index + 1 :]:
                relation = relation_for_values(predicate, left.value, right.value)
                if relation == "refinement":
                    refined.update((left.claim_id, right.claim_id))
                elif relation == "conflict":
                    conflicted.update(item.claim_id for item in values)

    return ClaimResolutionResult(
        selected_claims=selected,
        summary=ClaimResolutionSummary(
            selected_claim_ids=[item.claim_id for item in selected],
            exclusion_reasons=dict(sorted(excluded.items())),
            refined_claim_ids=sorted(refined),
            conflicted_claim_ids=sorted(conflicted),
        ),
    )


def relation_for_values(predicate: str, left: Any, right: Any) -> ValueRelation:
    if predicate.startswith("capability:"):
        return (
            "equivalent"
            if _capability_level(left) == _capability_level(right)
            else "conflict"
        )
    if predicate.endswith(".kind") or predicate.endswith(":kind"):
        return (
            "equivalent"
            if _experience_kind(left) == _experience_kind(right)
            else "conflict"
        )
    if predicate.endswith(".graduation_year"):
        return _date_relation(left, right)
    if isinstance(left, str) and isinstance(right, str):
        return "equivalent" if _normalize_text(left) == _normalize_text(right) else "conflict"
    return "equivalent" if _canonical(left) == _canonical(right) else "conflict"


def representative_claim(
    predicate: str, claims: list[EvidenceClaim]
) -> EvidenceClaim:
    if not claims:
        raise ValueError("representative_claim requires at least one claim")
    return max(claims, key=lambda item: _representative_rank(predicate, item))


def claims_have_semantic_conflict(
    predicate: str, claims: list[EvidenceClaim]
) -> bool:
    return any(
        relation_for_values(predicate, left.value, right.value) == "conflict"
        for index, left in enumerate(claims)
        for right in claims[index + 1 :]
    )


def _base_exclusion_reason(
    claim: EvidenceClaim, current_resume_evidence_id: str | None
) -> str | None:
    if claim.status != "active":
        return "claim_not_active"
    if claim.origin_kind == "resume_evidence":
        if current_resume_evidence_id is None:
            return None
        return (
            None
            if claim.origin_ref == current_resume_evidence_id
            else "stale_resume_evidence"
        )
    if claim.origin_kind == "legacy":
        if claim.extractor.provider == "human" or claim.claim_type == "feedback_signal":
            return None
        return "legacy_model_isolated"
    return None


def _representative_rank(predicate: str, claim: EvidenceClaim) -> tuple[Any, ...]:
    origin_rank = {
        "resume_evidence": 4,
        "conversation_response": 3,
        "feedback_event": 2,
        "supplemental_document": 1,
        "legacy": 0,
    }[claim.origin_kind]
    if claim.extractor.model == "profile_correction":
        origin_rank = 5
    elif claim.origin_kind == "legacy" and claim.extractor.provider == "human":
        origin_rank = 3
    elif claim.origin_kind == "legacy" and claim.claim_type == "feedback_signal":
        origin_rank = 2
    precision = 0
    if predicate.endswith(".graduation_year"):
        match = _DATE_RE.fullmatch(str(claim.value).strip())
        precision = 2 if match and match.group(2) else 1 if match else 0
    return (
        precision,
        origin_rank,
        claim.effective_at or claim.created_at,
        claim.created_at,
        claim.claim_id,
    )


def _date_relation(left: Any, right: Any) -> ValueRelation:
    left_match = _DATE_RE.fullmatch(str(left).strip())
    right_match = _DATE_RE.fullmatch(str(right).strip())
    if left_match is None or right_match is None:
        return "equivalent" if _normalize_text(str(left)) == _normalize_text(str(right)) else "conflict"
    if left_match.group(1) != right_match.group(1):
        return "conflict"
    left_month, right_month = left_match.group(2), right_match.group(2)
    if left_month and right_month:
        return "equivalent" if int(left_month) == int(right_month) else "conflict"
    return "equivalent" if left_month == right_month else "refinement"


def _capability_level(value: Any) -> str:
    try:
        return CapabilityClaimValue.model_validate(value).level
    except (TypeError, ValueError):
        return _canonical(value)


def _experience_kind(value: Any) -> tuple[str, str]:
    try:
        normalized = ExperienceKindValue.model_validate(value)
        return normalized.kind, normalized.context
    except (TypeError, ValueError):
        return _canonical(value), "invalid"


def _normalize_text(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).split())


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
