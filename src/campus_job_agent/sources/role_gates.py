"""Deterministic WP3 role-family, detail-evidence and experience-scope gates."""

from __future__ import annotations

from typing import Iterable

from campus_job_agent.schemas import (
    ExperienceEvidenceRecord,
    ExperienceScopeLink,
    JobIdentityLink,
    JobPostingCluster,
    NormalizedJobPosting,
    RoleDetailEvidenceReceipt,
    RoleFamilyMembership,
    SearchScope,
    SourceDocument,
)
from campus_job_agent.schemas.intent import role_family_for_query
from campus_job_agent.schemas.source import canonical_hash, normalize_text
from campus_job_agent.storage.base import EvidenceRepository


DETAIL_DOCUMENT_KINDS = {"job_detail", "employer_job_detail", "official_job_detail"}


def classify_role_family(
    job: NormalizedJobPosting,
    scope: SearchScope,
) -> RoleFamilyMembership:
    inferred = job.role_family
    if not inferred or inferred == "unknown" or inferred.startswith("role_family_"):
        inferred = role_family_for_query(job.role_title)
    target = scope.target_role_family
    if inferred == target:
        status, confidence, reasons = "accepted", 0.95, ["primary_family_matches_scope"]
    elif inferred.startswith("role_family_"):
        status, confidence, reasons = "ambiguous", 0.4, ["primary_family_unknown"]
    else:
        status, confidence, reasons = "rejected", 0.95, ["role_family_mismatch"]
    secondary = sorted(_secondary_role_tags(job.role_title, primary=inferred))
    payload = [scope.scope_id, job.job_posting_id, target, inferred, status]
    return RoleFamilyMembership(
        membership_id=f"role-membership:{canonical_hash('membership', payload)[7:31]}",
        scope_id=scope.scope_id,
        job_posting_id=job.job_posting_id,
        target_role_family=target,
        primary_role_family=inferred,
        secondary_role_tags=secondary,
        status=status,
        confidence=confidence,
        reason_codes=reasons,
        supporting_fragment_ids=list(job.supporting_fragment_ids),
    )


def assess_role_detail_evidence(
    *,
    scope_id: str,
    cluster: JobPostingCluster,
    jobs: Iterable[NormalizedJobPosting],
    documents: Iterable[SourceDocument],
    repository: EvidenceRepository,
) -> RoleDetailEvidenceReceipt:
    job_artifacts = {
        artifact_id
        for job in jobs
        for artifact_id in job.raw_artifact_ids
    }
    relevant = [
        document
        for document in documents
        if document.raw_artifact_id in job_artifacts
        and document.document_kind in DETAIL_DOCUMENT_KINDS
    ]
    valid = [
        document
        for document in relevant
        if document.access_status == "success"
        and document.raw_artifact_id
        and repository.get_artifact(document.raw_artifact_id) is not None
    ]
    if valid:
        status = "eligible"
        reasons = [
            "official_job_detail_archived"
            if any(item.document_kind == "official_job_detail" for item in valid)
            else "job_detail_archived"
        ]
    elif relevant:
        status, reasons = "invalid", ["detail_artifact_invalid"]
    else:
        status, reasons = "missing", ["detail_evidence_missing"]
    payload = [scope_id, cluster.cluster_id, status, sorted(item.source_document_id for item in valid)]
    return RoleDetailEvidenceReceipt(
        receipt_id=f"role-detail:{canonical_hash('detail-gate', payload)[7:31]}",
        scope_id=scope_id,
        job_cluster_id=cluster.cluster_id,
        status=status,
        detail_document_ids=sorted(item.source_document_id for item in valid),
        detail_artifact_ids=sorted({str(item.raw_artifact_id) for item in valid}),
        reason_codes=reasons,
    )


def link_experience_scope(
    record: ExperienceEvidenceRecord,
    scope: SearchScope,
    clusters: Iterable[JobPostingCluster],
    jobs_by_id: dict[str, NormalizedJobPosting],
) -> ExperienceScopeLink:
    family = record.role_family or (
        role_family_for_query(record.role_title) if record.role_title else None
    )
    signals: dict[str, str] = {
        "family": "exact" if family == scope.target_role_family else "mismatch",
    }
    cluster_candidates: list[str] = []
    for cluster in clusters:
        job = jobs_by_id.get(cluster.canonical_job_posting_id)
        if job is None or job.role_family != scope.target_role_family:
            continue
        if record.company and normalize_text(record.company) != normalize_text(job.company):
            continue
        if record.role_title and normalize_text(record.role_title) != normalize_text(job.role_title):
            continue
        cluster_candidates.append(cluster.cluster_id)

    if family != scope.target_role_family:
        status, reasons, cluster_id = "rejected", ["experience_scope_mismatch"], None
    elif record.scope_level == "role_family":
        status, reasons, cluster_id = "confirmed", ["role_family_scope_confirmed"], None
    elif record.scope_level == "company_only":
        if record.company:
            status, reasons = "confirmed", ["company_scope_confirmed"]
            signals["company"] = "declared"
        else:
            status, reasons = "ambiguous", ["experience_company_missing"]
        cluster_id = None
    elif record.scope_level == "company_role":
        if record.company and record.role_title:
            status, reasons = "confirmed", ["company_role_scope_confirmed"]
            signals.update({"company": "declared", "role_title": "declared"})
        else:
            status, reasons = "ambiguous", ["experience_company_role_incomplete"]
        cluster_id = None
    elif record.scope_level == "job_instance":
        if len(cluster_candidates) == 1:
            status, reasons, cluster_id = "confirmed", ["unique_job_instance_match"], cluster_candidates[0]
            signals.update({"company": "exact", "role_title": "exact"})
        else:
            status, reasons, cluster_id = "ambiguous", ["experience_scope_ambiguous"], None
            signals["candidate_count"] = str(len(cluster_candidates))
    else:
        status, reasons, cluster_id = "ambiguous", ["experience_scope_unknown"], None

    payload = [scope.scope_id, record.experience_record_id, record.scope_level, cluster_id, status]
    return ExperienceScopeLink(
        experience_scope_link_id=f"experience-link:{canonical_hash('experience-link', payload)[7:31]}",
        scope_id=scope.scope_id,
        experience_record_id=record.experience_record_id,
        scope_level=record.scope_level,
        role_family=family,
        company=record.company,
        job_cluster_id=cluster_id,
        status=status,
        match_signals=signals,
        supporting_fragment_ids=list(record.supporting_fragment_ids),
        reason_codes=reasons,
    )


def experience_link_applies(
    link: ExperienceScopeLink,
    *,
    cluster_id: str,
    job: NormalizedJobPosting,
) -> bool:
    if link.status != "confirmed" or link.role_family != job.role_family:
        return False
    if link.scope_level == "role_family":
        return True
    if link.scope_level == "job_instance":
        return link.job_cluster_id == cluster_id
    if link.scope_level in {"company_role", "company_only"}:
        return bool(link.company) and normalize_text(link.company) == normalize_text(job.company)
    return False


def _secondary_role_tags(title: str, *, primary: str) -> set[str]:
    normalized = title.casefold()
    markers = {
        "frontend_engineering": ("前端", "frontend"),
        "backend_engineering": ("后端", "backend", "服务端"),
        "algorithm_engineering": ("算法", "机器学习", "machine learning", "深度学习", "nlp", "cv"),
        "ai_agent_engineering": ("agent", "智能体", "llm", "大模型", "ai应用", "ai 应用"),
    }
    return {
        family
        for family, aliases in markers.items()
        if family != primary and any(alias in normalized for alias in aliases)
    }


__all__ = [
    "DETAIL_DOCUMENT_KINDS",
    "assess_role_detail_evidence",
    "classify_role_family",
    "experience_link_applies",
    "link_experience_scope",
]
