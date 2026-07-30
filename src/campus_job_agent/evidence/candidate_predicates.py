"""Versioned Candidate Claim predicate and value contract."""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel


CAPABILITY_LEVELS = {"unknown", "beginner", "intermediate", "advanced", "expert"}
EXPERIENCE_KINDS = {"research", "project", "internship", "competition", "other"}
EDUCATION_FIELDS = {"institution", "degree", "major", "graduation_year"}
EXPERIENCE_LIST_FIELDS = {"responsibilities", "technologies", "outputs", "results"}

_CAPABILITY = re.compile(r"^capability:([a-z0-9][a-z0-9._-]*)$")
_EDUCATION = re.compile(
    r"^education:([a-z0-9][a-z0-9_-]*)\.(institution|degree|major|graduation_year)$"
)
_EXPERIENCE = re.compile(
    r"^experience:([a-z0-9][a-z0-9_-]*)\."
    r"(kind|title|description|responsibilities|technologies|outputs|results)$"
)
_LEGACY_EXPERIENCE = re.compile(
    r"^experiences\[([^\]]+)\]\."
    r"(kind|title|description|responsibilities|technologies|outputs|results)$"
)


class CandidatePredicateError(ValueError):
    def __init__(self, message: str, *, reason_code: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


class CandidatePredicate(BaseModel):
    kind: Literal["capability", "education", "experience"]
    capability_id: str | None = None
    record_id: str | None = None
    field: str | None = None
    legacy: bool = False


def parse_candidate_predicate(
    predicate: str, *, allow_legacy: bool = False
) -> CandidatePredicate:
    value = predicate.strip()
    match = _CAPABILITY.fullmatch(value)
    if match:
        return CandidatePredicate(kind="capability", capability_id=match.group(1))
    match = _EDUCATION.fullmatch(value)
    if match:
        return CandidatePredicate(
            kind="education", record_id=match.group(1), field=match.group(2)
        )
    match = _EXPERIENCE.fullmatch(value)
    if match:
        return CandidatePredicate(
            kind="experience", record_id=match.group(1), field=match.group(2)
        )
    legacy = _parse_legacy(value) if allow_legacy else None
    if legacy is not None:
        return legacy
    if _looks_legacy(value):
        raise CandidatePredicateError(
            "legacy Candidate predicate is forbidden for candidate_claim_v0.7.1",
            reason_code="legacy_predicate_forbidden",
        )
    if value.startswith(("capability:", "education:", "experience:")):
        raise CandidatePredicateError(
            "Candidate predicate does not match the versioned field grammar",
            reason_code="invalid_predicate_shape",
        )
    raise CandidatePredicateError(
        "predicate has no CandidateProfile projection semantics",
        reason_code="unsupported_predicate",
    )


def validate_candidate_value(parsed: CandidatePredicate, value: Any) -> None:
    if parsed.kind == "capability":
        valid = (
            isinstance(value, dict)
            and isinstance(value.get("level"), str)
            and value.get("level") in CAPABILITY_LEVELS
        )
    elif parsed.kind == "education":
        valid = isinstance(value, str) and bool(value.strip())
    elif parsed.field == "kind":
        valid = isinstance(value, str) and value in EXPERIENCE_KINDS
    elif parsed.field in EXPERIENCE_LIST_FIELDS:
        valid = (
            isinstance(value, list)
            and bool(value)
            and all(isinstance(item, str) and item.strip() for item in value)
        )
    else:
        valid = isinstance(value, str) and bool(value.strip())
    if not valid:
        raise CandidatePredicateError(
            f"invalid value shape for Candidate predicate kind={parsed.kind} field={parsed.field}",
            reason_code="invalid_value_shape",
        )


def profile_path_to_candidate_predicate(path: str) -> str:
    value = path.strip()
    if _CAPABILITY.fullmatch(value) or _EDUCATION.fullmatch(value) or _EXPERIENCE.fullmatch(value):
        return value
    match = _LEGACY_EXPERIENCE.fullmatch(value)
    if match:
        return f"experience:{_record_id(match.group(1))}.{match.group(2)}"
    if value == "education":
        return "education:primary.institution"
    if value.startswith("education."):
        field = value.split(".", 1)[1]
        if field in EDUCATION_FIELDS:
            return f"education:primary.{field}"
    raise CandidatePredicateError(
        "profile target path has no Candidate Claim projection",
        reason_code="unsupported_target_path",
    )


def normalize_human_candidate_value(predicate: str, value: Any) -> Any:
    parsed = parse_candidate_predicate(predicate, allow_legacy=True)
    if parsed.kind == "experience" and parsed.field in EXPERIENCE_LIST_FIELDS:
        if isinstance(value, str):
            return [value]
    return value


def _parse_legacy(value: str) -> CandidatePredicate | None:
    if value.startswith("capability:") and value.split(":", 1)[1].strip():
        return CandidatePredicate(
            kind="capability", capability_id=value.split(":", 1)[1], legacy=True
        )
    aliases = {
        "education.institution": "institution", "education:institution": "institution",
        "education.degree": "degree", "education:degree": "degree",
        "education.major": "major", "education:major": "major",
        "education.graduation_year": "graduation_year",
        "education:graduation_year": "graduation_year",
    }
    if value in aliases:
        return CandidatePredicate(
            kind="education", record_id="primary", field=aliases[value], legacy=True
        )
    match = _LEGACY_EXPERIENCE.fullmatch(value)
    if match:
        return CandidatePredicate(
            kind="experience", record_id=match.group(1), field=match.group(2), legacy=True
        )
    return None


def _looks_legacy(value: str) -> bool:
    return (
        value.startswith("education.")
        or value in {
            "education:institution", "education:degree", "education:major",
            "education:graduation_year",
        }
        or _LEGACY_EXPERIENCE.fullmatch(value) is not None
        or (value.startswith("capability:") and _CAPABILITY.fullmatch(value) is None)
    )


def _record_id(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9_-]+", "-", value.casefold()).strip("-")
    return normalized or "record"
