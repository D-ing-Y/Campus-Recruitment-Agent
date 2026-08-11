"""Deterministic WP3.1 Demand/Reputation projection and consumer isolation."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Iterable

from campus_job_agent.ontology import CapabilityOntology
from campus_job_agent.schemas import (
    AssessmentSignal,
    CommunityEvidenceDocument,
    CommunityEvidenceSegment,
    CompanyReputationProfile,
    DemandDenominator,
    EvidenceClaim,
    FamilyRequirementAggregate,
    JobDemandProfile,
    JobDemandRequirements,
    JobPostingCluster,
    JobReputationProfile,
    NormalizedJobPosting,
    OfficialEscalationReceipt,
    Qualification,
    ReputationDimension,
    RoleDetailEvidenceReceipt,
    RoleFamilyDemandProfile,
    RoleIntelligenceBundle,
    RoleRequirement,
    SearchScope,
)
from campus_job_agent.schemas.evidence import utc_now
from campus_job_agent.schemas.role_intelligence import INTERVIEW_SEGMENT_TYPES
from campus_job_agent.schemas.source import normalize_text
from campus_job_agent.sources.repository import SQLiteRoleRepository
from campus_job_agent.storage.base import EvidenceRepository


class EvidenceUsageViolation(ValueError):
    pass


class DemandReputationProjector:
    def __init__(
        self, role_repository: SQLiteRoleRepository,
        evidence_repository: EvidenceRepository,
        ontology: CapabilityOntology | None = None,
    ) -> None:
        self.role_repository = role_repository
        self.evidence_repository = evidence_repository
        self.ontology = ontology or CapabilityOntology.load_default()

    def project_job_demand(
        self, *, scope: SearchScope, cluster: JobPostingCluster,
        jobs_by_id: dict[str, NormalizedJobPosting], claims: list[EvidenceClaim],
        detail_receipt: RoleDetailEvidenceReceipt,
        segments: list[CommunityEvidenceSegment],
        documents_by_id: dict[str, CommunityEvidenceDocument],
        escalation_receipt: OfficialEscalationReceipt | None = None,
    ) -> JobDemandProfile:
        if detail_receipt.status != "eligible" or not detail_receipt.detail_document_ids:
            raise EvidenceUsageViolation("detail_evidence_missing")
        job = jobs_by_id.get(cluster.canonical_job_posting_id)
        if job is None:
            raise ValueError("canonical job is missing")
        subjects = {f"job:{item}" for item in cluster.member_job_posting_ids}
        relevant = [item for item in claims if item.subject_id in subjects]
        responsibilities: list[RoleRequirement] = []
        capabilities: list[RoleRequirement] = []
        preferred: list[RoleRequirement] = []
        qualifications: list[Qualification] = []
        work_context: list[RoleRequirement] = []
        for claim in relevant:
            if claim.predicate.startswith("responsibility."):
                responsibilities.append(self._requirement(claim, "responsibility"))
            elif claim.predicate.startswith("requirement."):
                item = self._requirement(claim, "core_capability")
                (preferred if item.importance == "bonus" else capabilities).append(item)
            elif claim.predicate.startswith("qualification."):
                qtype = claim.predicate.split(".", 1)[1]
                allowed = {
                    "degree", "major", "graduation_year", "recruitment_eligibility",
                    "language", "location", "other",
                }
                qualifications.append(Qualification(
                    qualification_id=f"qualification:{claim.claim_id}",
                    qualification_type=qtype if qtype in allowed else "other",
                    value=claim.value, confidence=claim.confidence,
                    supporting_claim_ids=[claim.claim_id],
                ))
        company_key = normalize_text(job.company)
        job_segments = [
            item for item in segments
            if item.validation_status == "accepted" and item.usage == "demand_assessment"
            and item.role_family_id == scope.target_role_family
            and (item.company_key in {None, company_key})
            and (item.job_instance_id in {None, cluster.cluster_id})
        ]
        profile = JobDemandProfile(
            profile_id=_stable("job-demand", [scope.scope_id, cluster.cluster_id, detail_receipt.receipt_id, sorted(item.segment_id for item in job_segments)]),
            job_instance_id=cluster.cluster_id, company_key=company_key,
            role_family_id=scope.target_role_family, search_scope_id=scope.scope_id,
            jd_requirements=JobDemandRequirements(
                responsibilities=responsibilities, qualifications=qualifications,
                capabilities=capabilities, preferred_qualifications=preferred,
                work_context=work_context,
            ),
            assessment_signals=self._assessment_signals(job_segments, documents_by_id),
            source_document_ids=detail_receipt.detail_document_ids,
            official_escalation_receipt_id=escalation_receipt.receipt_id if escalation_receipt else None,
        )
        return self.role_repository.save(
            "job_demand_profile", profile,
            idempotency_key=f"job-demand-profile:{profile.profile_id}",
        )

    def project_family_demand(
        self, *, scope: SearchScope, jobs: list[JobDemandProfile],
        all_segments: list[CommunityEvidenceSegment],
        documents_by_id: dict[str, CommunityEvidenceDocument],
    ) -> RoleFamilyDemandProfile:
        aggregates = self._aggregate_requirements(jobs)
        family_segments = [
            item for item in all_segments
            if item.validation_status == "accepted" and item.usage == "demand_assessment"
            and item.role_family_id == scope.target_role_family
        ]
        interview_docs = {
            item.document_id for item in family_segments
            if documents_by_id.get(item.document_id) is not None
        }
        common = [item for item in aggregates if item.prevalence_band == "common"]
        differentiating = [item for item in aggregates if item.prevalence_band != "common"]
        profile = RoleFamilyDemandProfile(
            profile_id=_stable("family-demand", [scope.scope_id, sorted(item.profile_id for item in jobs), sorted(item.segment_id for item in family_segments)]),
            role_family_id=scope.target_role_family, search_scope_id=scope.scope_id,
            member_job_profile_ids=sorted(item.profile_id for item in jobs),
            common_requirements=common,
            differentiating_requirements=differentiating,
            assessment_signals=self._assessment_signals(family_segments, documents_by_id),
            denominator=DemandDenominator(
                accepted_job_count=len(jobs),
                accepted_interview_document_count=len(interview_docs),
            ),
        )
        return self.role_repository.save(
            "role_family_demand_profile", profile,
            idempotency_key=f"role-family-demand-profile:{profile.profile_id}",
        )

    def project_reputation(
        self, *, segments: list[CommunityEvidenceSegment],
        documents_by_id: dict[str, CommunityEvidenceDocument],
        jobs_by_company_family: dict[tuple[str, str], list[str]],
    ) -> tuple[list[JobReputationProfile], list[CompanyReputationProfile]]:
        accepted = [
            item for item in segments
            if item.validation_status == "accepted"
            and item.usage in {"reputation_job", "reputation_company"}
        ]
        job_groups: dict[tuple[str, str], list[CommunityEvidenceSegment]] = defaultdict(list)
        company_groups: dict[str, list[CommunityEvidenceSegment]] = defaultdict(list)
        for item in accepted:
            if item.company_key:
                company_groups[item.company_key].append(item)
            if item.usage == "reputation_job" and item.company_key and item.role_family_id:
                job_groups[(item.company_key, item.role_family_id)].append(item)
        job_profiles: list[JobReputationProfile] = []
        for key, values in sorted(job_groups.items()):
            profile = JobReputationProfile(
                profile_id=_stable("job-reputation", [key, sorted(item.segment_id for item in values)]),
                company_key=key[0], role_family_id=key[1],
                job_instance_ids=sorted(jobs_by_company_family.get(key, [])),
                dimensions=_dimensions(values, documents_by_id),
                source_document_ids=sorted({item.document_id for item in values}),
            )
            job_profiles.append(self.role_repository.save(
                "job_reputation_profile", profile,
                idempotency_key=f"job-reputation-profile:{profile.profile_id}",
            ))
        company_profiles: list[CompanyReputationProfile] = []
        for company, values in sorted(company_groups.items()):
            profile = CompanyReputationProfile(
                profile_id=_stable("company-reputation", [company, sorted(item.segment_id for item in values)]),
                company_key=company,
                covered_role_families=sorted({item.role_family_id for item in values if item.role_family_id}),
                dimensions=_dimensions(values, documents_by_id),
                source_document_ids=sorted({item.document_id for item in values}),
            )
            company_profiles.append(self.role_repository.save(
                "company_reputation_profile", profile,
                idempotency_key=f"company-reputation-profile:{profile.profile_id}",
            ))
        return job_profiles, company_profiles

    def build_bundle(
        self, *, scope: SearchScope, family: RoleFamilyDemandProfile,
        jobs: list[JobDemandProfile], job_reputation: list[JobReputationProfile],
        company_reputation: list[CompanyReputationProfile], raw_evidence_refs: list[str],
        source_receipt_ids: list[str], segments: list[CommunityEvidenceSegment],
    ) -> RoleIntelligenceBundle:
        missing: list[str] = []
        if not jobs:
            missing.append("job_demand")
        if not any(item.usage == "demand_assessment" and item.validation_status == "accepted" for item in segments):
            missing.append("interview_assessment")
        if not job_reputation:
            missing.append("job_reputation")
        if not company_reputation:
            missing.append("company_reputation")
        bundle = RoleIntelligenceBundle(
            bundle_id=_stable("role-intelligence-bundle", [
                scope.scope_id, family.profile_id, sorted(item.profile_id for item in jobs),
                sorted(item.profile_id for item in job_reputation),
                sorted(item.profile_id for item in company_reputation),
            ]),
            search_scope_id=scope.scope_id,
            role_family_demand_profile_id=family.profile_id,
            job_demand_profile_ids=sorted(item.profile_id for item in jobs),
            job_reputation_profile_ids=sorted(item.profile_id for item in job_reputation),
            company_reputation_profile_ids=sorted(item.profile_id for item in company_reputation),
            raw_evidence_refs=sorted(set(raw_evidence_refs)),
            source_receipt_ids=sorted(set(source_receipt_ids)), missing_sections=missing,
        )
        return self.role_repository.save(
            "role_intelligence_bundle", bundle,
            idempotency_key=f"role-intelligence-bundle:{bundle.bundle_id}",
        )

    def _requirement(self, claim: EvidenceClaim, category: str) -> RoleRequirement:
        raw = str(claim.value)
        resolved = self.ontology.resolve(raw)
        preferred = any(marker in raw.casefold() for marker in ("优先", "加分", "preferred", "plus"))
        return RoleRequirement(
            requirement_id=f"demand-requirement:{claim.claim_id}",
            category="bonus_capability" if preferred else category,
            capability_id=resolved.capability_id, raw_label=resolved.canonical_name or raw,
            importance="bonus" if preferred else "context" if category == "responsibility" else "core",
            obligation="preferred" if preferred else "mentioned",
            confidence=claim.confidence, supporting_claim_ids=[claim.claim_id],
        )

    def _assessment_signals(
        self, segments: list[CommunityEvidenceSegment],
        documents_by_id: dict[str, CommunityEvidenceDocument],
    ) -> list[AssessmentSignal]:
        grouped: dict[tuple[str, str], list[CommunityEvidenceSegment]] = defaultdict(list)
        for item in segments:
            fragment = self.evidence_repository.get_fragment(item.fragment_id)
            topic = item.limited_summary or (fragment.text if fragment else item.segment_type)
            grouped[(item.segment_type, topic)].append(item)
        result: list[AssessmentSignal] = []
        for (kind, topic), values in sorted(grouped.items()):
            docs = {item.document_id for item in values}
            count = len(values)
            observation = "frequent" if len(docs) >= 2 else "insufficient_sample"
            result.append(AssessmentSignal(
                signal_id=_stable("assessment-signal", [kind, topic, sorted(item.segment_id for item in values)]),
                topic=topic, observation=observation, sample_count=count,
                independent_source_count=max(1, len(docs)),
                segment_ids=sorted(item.segment_id for item in values),
            ))
        return result

    @staticmethod
    def _aggregate_requirements(jobs: list[JobDemandProfile]) -> list[FamilyRequirementAggregate]:
        grouped: dict[str, list[tuple[JobDemandProfile, RoleRequirement]]] = defaultdict(list)
        for job in jobs:
            values = [
                *job.jd_requirements.capabilities,
                *job.jd_requirements.preferred_qualifications,
                *job.jd_requirements.responsibilities,
                *job.jd_requirements.work_context,
            ]
            seen: set[str] = set()
            for item in values:
                key = item.capability_id or normalize_text(item.raw_label)
                if key in seen:
                    continue
                seen.add(key)
                grouped[key].append((job, item))
        companies = {item.company_key for item in jobs}
        result: list[FamilyRequirementAggregate] = []
        for key, values in sorted(grouped.items()):
            supporting_jobs = {item.profile_id for item, _ in values}
            supporting_companies = {item.company_key for item, _ in values}
            prevalence = len(supporting_jobs) / len(jobs) if jobs else None
            if len(jobs) < 3 or len(companies) < 2:
                band = "insufficient_sample"
            elif prevalence is not None and prevalence >= 0.6 and len(supporting_companies) >= 2:
                band = "common"
            elif prevalence is not None and prevalence >= 0.3:
                band = "frequent"
            else:
                band = "observed"
            result.append(FamilyRequirementAggregate(
                aggregate_id=_stable("family-demand-requirement", key),
                category=values[0][1].category, capability_id=values[0][1].capability_id,
                raw_labels=sorted({value.raw_label for _, value in values}),
                importance_distribution={name: sum(value.importance == name for _, value in values) for name in ("hard", "core", "bonus", "context")},
                supporting_job_instance_count=len(supporting_jobs),
                eligible_job_instance_count=len(jobs),
                supporting_company_count=len(supporting_companies),
                eligible_company_count=len(companies), prevalence=prevalence,
                company_coverage=len(supporting_companies) / len(companies) if companies else None,
                prevalence_band=band,
                supporting_claim_ids=sorted({claim_id for _, value in values for claim_id in value.supporting_claim_ids}),
            ))
        return result


def official_escalation_for_job(
    cluster: JobPostingCluster, job: NormalizedJobPosting,
    *, user_requested: bool = False,
) -> OfficialEscalationReceipt:
    if user_requested:
        trigger, status, reasons = "user_priority_request", "adapter_required", ["user_requested_official_verification"]
    elif cluster.conflicts:
        trigger, status, reasons = "cross_platform_conflict", "adapter_required", ["cross_platform_conflict"]
    elif job.status in {"expired", "closed"}:
        trigger, status, reasons = "suspected_stale_or_closed", "adapter_required", ["job_status_requires_verification"]
    elif not job.job_description.strip() or not (job.requirements_raw.strip() or job.requirements_normalized):
        trigger, status, reasons = "missing_critical_fields", "adapter_required", ["missing_job_description_or_requirements"]
    else:
        trigger, status, reasons = "not_required", "not_requested", ["platform_detail_sufficient"]
    return OfficialEscalationReceipt(
        receipt_id=_stable("official-escalation", [cluster.cluster_id, trigger, status]),
        job_instance_id=cluster.cluster_id, trigger=trigger, status=status,
        reason_codes=reasons,
    )


def select_consumer_inputs(
    bundle: RoleIntelligenceBundle, *, consumer: str,
) -> dict[str, list[str] | str]:
    if consumer == "matching":
        return {
            "role_family_demand_profile_id": bundle.role_family_demand_profile_id,
            "job_demand_profile_ids": list(bundle.job_demand_profile_ids),
        }
    if consumer == "preparation":
        return {
            "role_family_demand_profile_id": bundle.role_family_demand_profile_id,
            "job_demand_profile_ids": list(bundle.job_demand_profile_ids),
        }
    if consumer in {"target_decision", "role_qa"}:
        return {
            "role_family_demand_profile_id": bundle.role_family_demand_profile_id,
            "job_demand_profile_ids": list(bundle.job_demand_profile_ids),
            "job_reputation_profile_ids": list(bundle.job_reputation_profile_ids),
            "company_reputation_profile_ids": list(bundle.company_reputation_profile_ids),
        }
    raise EvidenceUsageViolation("evidence_usage_violation")


def _dimensions(
    segments: list[CommunityEvidenceSegment],
    documents_by_id: dict[str, CommunityEvidenceDocument],
) -> list[ReputationDimension]:
    grouped: dict[str, list[CommunityEvidenceSegment]] = defaultdict(list)
    for item in segments:
        grouped[item.segment_type].append(item)
    result: list[ReputationDimension] = []
    for dimension, values in sorted(grouped.items()):
        polarities = {item.polarity for item in values if item.polarity != "unknown"}
        disputed = "favorable" in polarities and "unfavorable" in polarities
        polarity = "mixed" if disputed or len(polarities) > 1 else next(iter(polarities), "unknown")
        documents = {item.document_id for item in values}
        sample_status = "disputed" if disputed else "sufficient" if len(documents) >= 3 else "observed" if len(documents) >= 2 else "insufficient_sample"
        roles: dict[str, int] = defaultdict(int)
        dates: list[datetime] = []
        for item in values:
            roles[item.role_family_id or "company_only"] += 1
            document = documents_by_id.get(item.document_id)
            if document and document.published_at:
                dates.append(document.published_at)
        summaries = [item.limited_summary for item in values if item.limited_summary]
        result.append(ReputationDimension(
            dimension=dimension, polarity=polarity, sample_status=sample_status,
            sample_count=len(values), independent_source_count=max(1, len(documents)),
            role_distribution=[{"role_family_id": key, "sample_count": count} for key, count in sorted(roles.items())],
            earliest_published_at=min(dates) if dates else None,
            latest_published_at=max(dates) if dates else None,
            supporting_segment_ids=sorted(item.segment_id for item in values if item.polarity != "unfavorable" or not disputed),
            contradicting_segment_ids=sorted(item.segment_id for item in values if disputed and item.polarity == "unfavorable"),
            limited_summary="；".join(dict.fromkeys(summaries))[:500],
        ))
    return result


def _stable(prefix: str, payload: object) -> str:
    from campus_job_agent.schemas.role_intelligence import stable_role_id
    return stable_role_id(prefix, payload)


__all__ = [
    "DemandReputationProjector", "EvidenceUsageViolation", "official_escalation_for_job",
    "select_consumer_inputs",
]
