"""Deterministic v0.3 evidence quality metrics."""

from pydantic import BaseModel, Field

from campus_job_agent.schemas import (
    EvidenceArtifact,
    EvidenceClaim,
    EvidenceFragment,
    ProfileSnapshot,
)


class EvidenceEvalReport(BaseModel):
    artifact_count: int
    fragment_count: int
    claim_count: int
    duplicate_artifact_count: int
    dedup_rate: float = Field(ge=0.0, le=1.0)
    evidence_trace_rate: float = Field(ge=0.0, le=1.0)
    unsupported_claim_count: int
    valid_locator_rate: float = Field(ge=0.0, le=1.0)
    profile_claim_reference_rate: float = Field(ge=0.0, le=1.0)


def evaluate_evidence(
    *,
    artifacts: list[EvidenceArtifact],
    fragments: list[EvidenceFragment],
    claims: list[EvidenceClaim],
    profile: ProfileSnapshot | None,
    ingestion_attempts: int | None = None,
) -> EvidenceEvalReport:
    fragment_ids = {item.fragment_id for item in fragments}
    claim_ids = {item.claim_id for item in claims}
    supported = [
        item
        for item in claims
        if item.evidence_fragment_ids
        and all(value in fragment_ids for value in item.evidence_fragment_ids)
    ]
    valid_locators = [item for item in fragments if _valid_locator(item)]
    attempts = ingestion_attempts if ingestion_attempts is not None else len(artifacts)
    duplicates = max(0, attempts - len(artifacts))
    profile_refs = [] if profile is None else profile.supporting_claim_ids
    valid_profile_refs = [value for value in profile_refs if value in claim_ids]
    return EvidenceEvalReport(
        artifact_count=len(artifacts),
        fragment_count=len(fragments),
        claim_count=len(claims),
        duplicate_artifact_count=duplicates,
        dedup_rate=_ratio(duplicates, attempts),
        evidence_trace_rate=_ratio(len(supported), len(claims)),
        unsupported_claim_count=len(claims) - len(supported),
        valid_locator_rate=_ratio(len(valid_locators), len(fragments)),
        profile_claim_reference_rate=_ratio(len(valid_profile_refs), len(profile_refs)),
    )


def _valid_locator(fragment: EvidenceFragment) -> bool:
    if fragment.locator_type != "char_range":
        return bool(fragment.locator)
    start = fragment.locator.get("start")
    end = fragment.locator.get("end")
    return (
        isinstance(start, int)
        and isinstance(end, int)
        and 0 <= start < end
        and end - start == len(fragment.text)
    )


def _ratio(numerator: int, denominator: int) -> float:
    return 1.0 if denominator == 0 else numerator / denominator
