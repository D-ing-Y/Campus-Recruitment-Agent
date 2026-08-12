"""Deterministic v0.5 evaluation helpers."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Any

from campus_job_agent.schemas import (
    CommunityEvidenceSegment, EvidenceClaim, SourceDocument,
)
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


def community_detail_raw_trace_rate(
    documents: list[SourceDocument], repository: EvidenceRepository,
) -> float:
    """Measure accepted detail documents with an archived Raw Artifact."""

    accepted = [
        item for item in documents
        if item.document_kind == "experience_post" and item.access_status == "success"
    ]
    if not accepted:
        return 1.0
    return sum(
        bool(
            item.raw_artifact_id
            and repository.get_artifact(str(item.raw_artifact_id)) is not None
        )
        for item in accepted
    ) / len(accepted)


def community_segment_quote_trace_rate(
    segments: list[CommunityEvidenceSegment], repository: EvidenceRepository,
) -> float:
    """Revalidate every accepted quote locator and hash against its Fragment."""

    accepted = [item for item in segments if item.validation_status == "accepted"]
    if not accepted:
        return 1.0
    valid = 0
    for segment in accepted:
        fragment = repository.get_fragment(segment.fragment_id)
        if fragment is None or segment.quote_end > len(fragment.text):
            continue
        quote = fragment.text[segment.quote_start:segment.quote_end]
        if hashlib.sha256(quote.encode("utf-8")).hexdigest() == segment.quote_hash:
            valid += 1
    return valid / len(accepted)


@dataclass(frozen=True)
class CommunityStrategyMetrics:
    strategy: str
    detail_count: int
    unique_cluster_count: int
    accepted_segment_count: int
    duplicate_detail_count: int
    scope_hit_count: int
    search_calls: int
    detail_calls: int
    llm_calls: int
    latency_ms: int
    search_cost_units: float

    @property
    def unique_cluster_per_detail(self) -> float:
        return self.unique_cluster_count / self.detail_count if self.detail_count else 0.0

    @property
    def accepted_segment_per_detail(self) -> float:
        return self.accepted_segment_count / self.detail_count if self.detail_count else 0.0

    @property
    def duplicate_detail_rate(self) -> float:
        return self.duplicate_detail_count / self.detail_count if self.detail_count else 0.0

    @property
    def scope_accept_rate(self) -> float:
        return self.scope_hit_count / self.detail_count if self.detail_count else 0.0


def adaptive_reduces_duplicate_work(
    baseline: CommunityStrategyMetrics,
    adaptive: CommunityStrategyMetrics,
) -> bool:
    """Gate claimed efficiency gains without relaxing independent coverage."""

    return (
        adaptive.unique_cluster_count >= baseline.unique_cluster_count
        and adaptive.accepted_segment_count >= baseline.accepted_segment_count
        and adaptive.detail_count < baseline.detail_count
        and adaptive.duplicate_detail_count < baseline.duplicate_detail_count
    )
