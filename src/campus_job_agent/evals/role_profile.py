"""Deterministic v0.5 evaluation helpers."""

from __future__ import annotations

from typing import Any

from campus_job_agent.schemas import EvidenceClaim, SourceDocument
from campus_job_agent.sources.role_pipeline import RoleClaimValidator
from campus_job_agent.storage.base import EvidenceRepository


def raw_before_parse_rate(documents: list[SourceDocument], repository: EvidenceRepository) -> float:
    parsed = [item for item in documents if item.access_status == "success"]
    if not parsed: return 1.0
    return sum(bool(item.raw_artifact_id and repository.get_artifact(item.raw_artifact_id)) for item in parsed) / len(parsed)


def role_claim_trace_rate(claims: list[EvidenceClaim], repository: EvidenceRepository) -> float:
    if not claims: return 1.0
    valid = 0
    for claim in claims:
        if claim.evidence_fragment_ids and all(repository.get_fragment(value) is not None for value in claim.evidence_fragment_ids):
            valid += 1
    return valid / len(claims)


def source_authority_violation_count(claims: list[EvidenceClaim], repository: EvidenceRepository) -> int:
    validator = RoleClaimValidator(repository)
    count = 0
    for claim in claims:
        try:
            if validator.authority_for(claim) == "forbidden": count += 1
        except Exception:
            count += 1
    return count


def credential_secret_leak_count(value: Any, secrets: list[str]) -> int:
    serialized = str(value)
    return sum(bool(secret and secret in serialized) for secret in secrets)


def runtime_generated_code_execution_count(trace: list[dict[str, Any]]) -> int:
    forbidden = {"exec", "eval", "python", "javascript", "shell"}
    return sum(str(item.get("action", "")).casefold() in forbidden and item.get("generated_by") == "llm" for item in trace)
