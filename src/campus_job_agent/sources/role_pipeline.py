"""Claim authority, field resolution and deterministic role-profile projection."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from campus_job_agent.evidence.claim_validator import ClaimValidationError, ClaimValidator
from campus_job_agent.ontology import CapabilityOntology
from campus_job_agent.schemas import (
    EvidenceClaim,
    ExperienceEvidenceRecord,
    FamilyRequirementAggregate,
    FieldResolution,
    HiringSignal,
    JobIdentityLink,
    JobInstanceRoleProfile,
    JobPostingCluster,
    NormalizedJobPosting,
    ProfileSnapshot,
    Provenance,
    Qualification,
    RoleFamilyProfile,
    RoleRequirement,
    SearchScope,
)
from campus_job_agent.schemas.evidence import ClaimExtractor, utc_now
from campus_job_agent.schemas.source import canonical_hash, normalize_text
from campus_job_agent.storage.base import EvidenceRepository, ProfileRepository


OFFICIAL_PRIMARY_PREFIXES = (
    "role.active", "application.", "qualification.", "responsibility.", "location.", "requirement.",
)
COMMUNITY_FORBIDDEN_PREFIXES = (
    "role.active", "application.", "qualification.", "responsibility.", "requirement.", "location.",
)


class RoleClaimValidator:
    def __init__(self, repository: EvidenceRepository) -> None:
        self.repository = repository
        self.base = ClaimValidator(repository)

    def authority_for(self, claim: EvidenceClaim) -> str:
        channels = {self._channel(fragment_id) for fragment_id in claim.evidence_fragment_ids}
        if len(channels) != 1:
            raise ClaimValidationError("role claim must cite exactly one source channel")
        channel = next(iter(channels))
        if channel == "experience":
            if claim.predicate.startswith(COMMUNITY_FORBIDDEN_PREFIXES):
                return "forbidden"
            return "allowed" if claim.predicate.startswith("hiring_signal.") else "signal_only"
        if channel == "employer_official" and claim.predicate.startswith(OFFICIAL_PRIMARY_PREFIXES):
            return "primary"
        return "allowed"

    def validate_and_save(self, claim: EvidenceClaim, *, owner_id: str) -> EvidenceClaim:
        authority = self.authority_for(claim)
        if authority == "forbidden":
            raise ClaimValidationError(f"authority_violation: experience cannot support {claim.predicate}")
        return self.base.validate_and_save(claim, expected_owner_id=owner_id)

    def _channel(self, fragment_id: str) -> str:
        fragment = self.repository.get_fragment(fragment_id)
        if fragment is None:
            raise ClaimValidationError(f"unknown evidence fragment: {fragment_id}")
        artifact = self.repository.get_artifact(fragment.artifact_id)
        if artifact is None:
            raise ClaimValidationError("fragment has no artifact")
        return str(artifact.metadata.get("channel", "unknown"))


def extract_recruitment_claims(
    job: NormalizedJobPosting,
    *,
    owner_id: str,
    repository: EvidenceRepository,
    subject_id: str,
) -> list[EvidenceClaim]:
    fragment_ids = list(job.supporting_fragment_ids)
    values: list[tuple[str, Any]] = [
        ("role.active", job.status not in {"expired", "closed"}),
        ("location.city", job.city),
        ("application.url", job.application_url),
        ("application.deadline", job.application_deadline.isoformat() if job.application_deadline else None),
        ("qualification.degree", job.degree_requirement),
        ("qualification.major", job.major_requirement),
        ("qualification.graduation_year", job.graduation_year),
    ]
    values.extend((f"responsibility.item:{index}", value) for index, value in enumerate(_split_items(job.job_description)))
    requirements = job.requirements_normalized or _split_items(job.requirements_raw)
    values.extend((f"requirement.item:{index}", value) for index, value in enumerate(requirements))
    if job.salary_min is not None or job.salary_max is not None:
        values.append(("salary.platform_display", {"min": job.salary_min, "max": job.salary_max, "unit": job.salary_unit, "source": job.salary_source}))
    validator = RoleClaimValidator(repository)
    claims: list[EvidenceClaim] = []
    for predicate, value in values:
        if value is None or value == "" or value == "unknown":
            continue
        claim = EvidenceClaim(
            claim_id=str(uuid5(NAMESPACE_URL, f"role-claim:{job.job_posting_id}:{predicate}:{canonical_hash('value', value)}")),
            subject_id=subject_id, predicate=predicate, value=value, claim_type="observed_fact",
            evidence_fragment_ids=fragment_ids, confidence=job.confidence,
            extractor=ClaimExtractor(provider="deterministic", model="role-claim-extractor-v1"),
            prompt_version="role_claim_extractor_v1", schema_version="v0.5",
        )
        claims.append(validator.validate_and_save(claim, owner_id=owner_id))
    return claims


def extract_experience_claims(
    record: ExperienceEvidenceRecord,
    *,
    owner_id: str,
    repository: EvidenceRepository,
    subject_id: str,
) -> list[EvidenceClaim]:
    validator = RoleClaimValidator(repository)
    claims: list[EvidenceClaim] = []
    for signal_type, values in record.signals.model_dump(mode="json").items():
        for index, value in enumerate(values):
            claim = EvidenceClaim(
                claim_id=str(uuid5(NAMESPACE_URL, f"experience-claim:{record.experience_record_id}:{signal_type}:{index}")),
                subject_id=subject_id, predicate=f"hiring_signal.{signal_type}",
                value={"summary": value, "scope_level": record.scope_level, "stage": record.stage,
                       "experience_record_id": record.experience_record_id, "company": record.company,
                       "role_title": record.role_title, "role_family": record.role_family},
                claim_type="feedback_signal", evidence_fragment_ids=record.supporting_fragment_ids,
                confidence=record.confidence,
                extractor=ClaimExtractor(provider="deterministic", model="experience-signal-extractor-v1"),
                prompt_version="experience_signal_extractor_v1", schema_version="v0.5",
            )
            claims.append(validator.validate_and_save(claim, owner_id=owner_id))
    return claims


def resolve_fields(
    identity_link: JobIdentityLink,
    claims: list[EvidenceClaim],
    *,
    repository: EvidenceRepository,
) -> list[FieldResolution]:
    grouped: dict[str, list[EvidenceClaim]] = defaultdict(list)
    for claim in claims:
        grouped[claim.predicate].append(claim)
    validator = RoleClaimValidator(repository)
    results: list[FieldResolution] = []
    for predicate, candidates in sorted(grouped.items()):
        authorities = {claim.claim_id: validator.authority_for(claim) for claim in candidates}
        official = [claim for claim in candidates if authorities[claim.claim_id] == "primary"]
        third_party = [claim for claim in candidates if authorities[claim.claim_id] == "allowed"]
        if identity_link.status not in {"confirmed", "official_not_found", "official_unavailable"}:
            chosen = None
            status = "identity_ambiguous"
            reason = "identity_not_confirmed"
            authority = "allowed"
        elif official:
            chosen = sorted(official, key=lambda item: (item.created_at, item.claim_id))[-1]
            status = "resolved" if third_party else "official_only"
            reason = "official_primary_and_newer" if third_party else "official_only"
            authority = "primary"
        elif third_party:
            chosen = sorted(third_party, key=lambda item: (item.created_at, item.claim_id))[-1]
            status = "third_party_only"
            reason = "official_missing_field" if identity_link.status == "confirmed" else "official_not_found_preserves_third_party"
            authority = "allowed"
        else:
            continue
        conflicting = [claim.claim_id for claim in candidates if chosen is None or claim.claim_id != chosen.claim_id]
        results.append(FieldResolution(
            field_resolution_id=str(uuid5(NAMESPACE_URL, f"field-resolution:{identity_link.job_identity_link_id}:{predicate}:{chosen.claim_id if chosen else 'none'}")),
            job_identity_link_id=identity_link.job_identity_link_id, predicate=predicate,
            chosen_claim_id=chosen.claim_id if chosen else None, conflicting_claim_ids=conflicting,
            resolution_status=status, reason=reason, authority=authority,
            freshness=_claim_freshness(chosen, repository) if chosen else "unknown",
        ))
    return results


class RoleProfileProjector:
    def __init__(self, repository: ProfileRepository, ontology: CapabilityOntology | None = None) -> None:
        self.repository = repository
        self.ontology = ontology or CapabilityOntology.load_default()

    def project_job_instance(
        self,
        cluster: JobPostingCluster,
        jobs: list[NormalizedJobPosting],
        claims: list[EvidenceClaim],
        identity_links: list[JobIdentityLink],
        resolutions: list[FieldResolution],
        experience_claims: list[EvidenceClaim] | None = None,
    ) -> ProfileSnapshot:
        subject_id = f"role_instance:{cluster.cluster_id}"
        members = [job for job in jobs if job.job_posting_id in cluster.member_job_posting_ids]
        canonical = next(job for job in members if job.job_posting_id == cluster.canonical_job_posting_id)
        resolved = {item.predicate: item for item in resolutions if item.chosen_claim_id}
        claims_by_id = {claim.claim_id: claim for claim in [*claims, *(experience_claims or [])]}
        requirements: list[RoleRequirement] = []
        bonus_items: list[RoleRequirement] = []
        responsibilities: list[RoleRequirement] = []
        qualifications: list[Qualification] = []
        signals: list[HiringSignal] = []
        used: list[str] = []
        for claim in claims:
            if claim.predicate in resolved and resolved[claim.predicate].chosen_claim_id != claim.claim_id:
                continue
            if claim.predicate.startswith("requirement."):
                requirement = self._requirement(claim, "core_capability")
                (bonus_items if requirement.category == "bonus_capability" else requirements).append(requirement)
            elif claim.predicate.startswith("responsibility."):
                responsibilities.append(self._requirement(claim, "responsibility"))
            elif claim.predicate.startswith("qualification."):
                qtype = claim.predicate.split(".", 1)[1]
                if qtype not in {"degree", "major", "graduation_year", "recruitment_eligibility", "language", "location", "other"}:
                    qtype = "other"
                qualifications.append(Qualification(
                    qualification_id=f"qualification:{claim.claim_id}", qualification_type=qtype,
                    value=claim.value, confidence=claim.confidence, supporting_claim_ids=[claim.claim_id],
                ))
            used.append(claim.claim_id)
        for claim in experience_claims or []:
            if not claim.predicate.startswith("hiring_signal."):
                continue
            value = claim.value if isinstance(claim.value, dict) else {"summary": str(claim.value)}
            signal_type = claim.predicate.split(".", 1)[1]
            if signal_type not in {"written_exam", "interview", "project_preference", "tech_stack", "salary", "work_context", "other"}:
                signal_type = "other"
            signals.append(HiringSignal(
                signal_id=f"signal:{claim.claim_id}", signal_type=signal_type,
                stage=value.get("stage"), scope_level=value.get("scope_level", "unknown"),
                summary=str(value.get("summary", "")), confidence=claim.confidence,
                supporting_claim_ids=[claim.claim_id], freshness="current_window",
            ))
            used.append(claim.claim_id)
        status = canonical.status
        confirmed_link = next((item for item in identity_links if item.status == "confirmed"), None)
        profile = JobInstanceRoleProfile(
            role_profile_id=subject_id, job_cluster_id=cluster.cluster_id,
            role_title=canonical.role_title, role_family=canonical.role_family, company=canonical.company,
            locations=sorted({job.city for job in members if job.city != "unknown"}),
            recruitment_type=canonical.recruitment_type, graduation_year=canonical.graduation_year,
            source_status=status, application_url=_resolved_value("application.url", resolved, claims_by_id, canonical.application_url),
            application_deadline=_resolved_datetime("application.deadline", resolved, claims_by_id, canonical.application_deadline),
            qualifications=qualifications, responsibilities=responsibilities, requirements=requirements, bonus_items=bonus_items,
            hiring_signals=signals, unknowns=_job_unknowns(canonical),
            conflicts=[item.model_dump(mode="json") for item in resolutions if item.conflicting_claim_ids],
            evidence_coverage={"claim_count": len(set(used)), "official_confirmed": confirmed_link is not None},
            source_refs=[{"source_id": job.source_id, "source_type": job.source_type, "source_url": job.source_url,
                          "raw_artifact_ids": job.raw_artifact_ids, "retrieved_at": job.retrieved_at.isoformat()} for job in members],
            supporting_claim_ids=sorted(set(used)), freshness={"status": _freshness(canonical), "valid_as_of": utc_now().isoformat(),
                                                                "published_at": canonical.source_date.isoformat() if canonical.source_date else None,
                                                                "retrieved_at": canonical.retrieved_at.isoformat()},
            confidence=round(sum(claim.confidence for claim in claims) / max(1, len(claims)), 4),
        )
        return self._save(subject_id, profile, profile.supporting_claim_ids, "job-instance-role-projector-v1")

    def aggregate_role_family(
        self,
        scope: SearchScope,
        job_snapshots: list[ProfileSnapshot],
        *,
        valid_as_of: datetime | None = None,
        thresholds: dict[str, float | int] | None = None,
    ) -> ProfileSnapshot:
        valid_as_of = valid_as_of or utc_now()
        policy = {
            "common_min_prevalence": 0.6, "frequent_min_prevalence": 0.3,
            "min_job_instances": 3, "min_distinct_companies": 2,
            "common_min_supporting_job_instances": 2, "common_min_supporting_companies": 2,
            **(thresholds or {}),
        }
        profiles = [JobInstanceRoleProfile.model_validate(item.profile_data) for item in job_snapshots]
        eligible = [item for item in profiles if item.source_status != "excluded_hard_scope"]
        companies = sorted({item.company for item in eligible})
        sample_status = "sufficient"
        if len(eligible) < int(policy["min_job_instances"]):
            sample_status = "insufficient_jobs"
        elif len(companies) < int(policy["min_distinct_companies"]):
            sample_status = "insufficient_companies"
        def aggregate_requirements(items_by_profile: list[tuple[JobInstanceRoleProfile, list[RoleRequirement]]]):
            grouped: dict[str, list[tuple[JobInstanceRoleProfile, RoleRequirement]]] = defaultdict(list)
            for item_profile, items in items_by_profile:
                seen: set[str] = set()
                for requirement in items:
                    key = requirement.capability_id or normalize_text(requirement.raw_label)
                    if key in seen: continue
                    seen.add(key); grouped[key].append((item_profile, requirement))
            aggregates: list[FamilyRequirementAggregate] = []
            for key, values in sorted(grouped.items()):
                aggregates.append(build_aggregate(key, values))
            return aggregates

        def build_aggregate(key: str, values: list[tuple[JobInstanceRoleProfile, RoleRequirement]]):
            supporting_profiles = {profile.role_profile_id for profile, _ in values}
            supporting_companies = {profile.company for profile, _ in values}
            prevalence = len(supporting_profiles) / len(eligible) if eligible else None
            company_coverage = len(supporting_companies) / len(companies) if companies else None
            if sample_status != "sufficient":
                band = "insufficient_sample"
            elif prevalence is not None and prevalence >= float(policy["common_min_prevalence"]) and len(supporting_profiles) >= int(policy["common_min_supporting_job_instances"]) and len(supporting_companies) >= int(policy["common_min_supporting_companies"]):
                band = "common"
            elif prevalence is not None and prevalence >= float(policy["frequent_min_prevalence"]):
                band = "frequent"
            else:
                band = "observed"
            return FamilyRequirementAggregate(
                aggregate_id=f"aggregate:{canonical_hash('family-requirement', [scope.fingerprint(), key])}",
                category=values[0][1].category, capability_id=values[0][1].capability_id,
                raw_labels=sorted({item.raw_label for _, item in values}),
                importance_distribution={name: sum(item.importance == name for _, item in values) for name in ["hard", "core", "bonus", "context"]},
                supporting_job_instance_count=len(supporting_profiles), eligible_job_instance_count=len(eligible),
                supporting_company_count=len(supporting_companies), eligible_company_count=len(companies),
                prevalence=prevalence, company_coverage=company_coverage, prevalence_band=band,
                supporting_claim_ids=sorted({claim_id for _, item in values for claim_id in item.supporting_claim_ids}),
            )

        requirement_aggregates = aggregate_requirements([(item, item.requirements) for item in eligible])
        common = [item for item in requirement_aggregates if item.prevalence_band == "common"]
        frequent = [item for item in requirement_aggregates if item.prevalence_band == "frequent"]
        observed = [item for item in requirement_aggregates if item.prevalence_band not in {"common", "frequent"}]
        responsibility_aggregates = aggregate_requirements([(item, item.responsibilities) for item in eligible])
        bonus_aggregates = aggregate_requirements([(item, item.bonus_items) for item in eligible])
        qualification_pairs: list[tuple[JobInstanceRoleProfile, RoleRequirement]] = []
        for item in eligible:
            for qualification in item.qualifications:
                qualification_pairs.append((item, RoleRequirement(
                    requirement_id=qualification.qualification_id, category="hard_qualification",
                    raw_label=f"{qualification.qualification_type}:{qualification.value}", importance="hard",
                    obligation="required", confidence=qualification.confidence,
                    supporting_claim_ids=qualification.supporting_claim_ids,
                )))
        qualification_groups: dict[str, list[tuple[JobInstanceRoleProfile, RoleRequirement]]] = defaultdict(list)
        for item, requirement in qualification_pairs: qualification_groups[normalize_text(requirement.raw_label)].append((item, requirement))
        hard_qualifications = [build_aggregate(key, values) for key, values in sorted(qualification_groups.items())]
        variations = []
        for aggregate in [*requirement_aggregates, *responsibility_aggregates, *bonus_aggregates]:
            if aggregate.supporting_company_count == 1 and len(companies) >= 2:
                supporting_companies = sorted({profile.company for profile, items in [(p, p.requirements + p.responsibilities + p.bonus_items) for p in eligible]
                                               if any((req.capability_id or normalize_text(req.raw_label)) == (aggregate.capability_id or normalize_text(aggregate.raw_labels[0])) for req in items)})
                variations.append({"aggregate_id": aggregate.aggregate_id, "companies": supporting_companies,
                                   "reason": "single_company_observation"})
        signals = _aggregate_signals(eligible)
        times = [datetime.fromisoformat(item.freshness["retrieved_at"]) for item in eligible if item.freshness.get("retrieved_at")]
        subject_id = f"role_family:{scope.fingerprint()}"
        profile = RoleFamilyProfile(
            role_profile_id=subject_id, role_title=" / ".join(scope.target_role_queries), role_family=scope.target_role_family,
            market_scope={"locations": scope.locations, "recruitment_type": scope.recruitment_type,
                          "graduation_year": scope.graduation_year, "industries": scope.industries, "companies": scope.companies},
            sample={"job_instance_count": len(eligible), "distinct_company_count": len(companies),
                    "distinct_location_count": len({location for item in eligible for location in item.locations}),
                    "experience_post_count": len({claim_id for item in eligible for signal in item.hiring_signals for claim_id in signal.supporting_claim_ids}),
                    "collection_window_start": min(times).isoformat() if times else None,
                    "collection_window_end": max(times).isoformat() if times else None,
                    "valid_as_of": valid_as_of.isoformat(), "sample_status": sample_status},
            hard_qualifications=hard_qualifications,
            common_responsibilities=[item for item in responsibility_aggregates if item.prevalence_band == "common"],
            core_requirements=common, frequent_requirements=frequent, observed_requirements=observed,
            bonus_items=bonus_aggregates, hiring_signals=signals, company_specific_variations=variations,
            unknowns=[] if sample_status == "sufficient" else ["insufficient_sample"],
            source_coverage={"job_instances": len(eligible), "companies": len(companies),
                             "official_confirmed": sum(bool(item.evidence_coverage.get("official_confirmed")) for item in eligible)},
            supporting_job_instance_profile_ids=[item.role_profile_id for item in eligible],
            supporting_claim_ids=sorted({claim_id for item in eligible for claim_id in item.supporting_claim_ids}),
            thresholds=policy, confidence=round(sum(item.confidence for item in eligible) / max(1, len(eligible)), 4),
        )
        return self._save(subject_id, profile, profile.supporting_claim_ids, "role-family-aggregator-v1")

    def _requirement(self, claim: EvidenceClaim, category: str) -> RoleRequirement:
        raw = str(claim.value)
        resolved = self.ontology.resolve(raw)
        lower = raw.casefold()
        preferred = any(marker in lower for marker in ["优先", "加分", "preferred", "plus"])
        obligation = "preferred" if preferred else "required" if any(marker in lower for marker in ["必须", "要求", "required", "must"]) else "mentioned"
        return RoleRequirement(
            requirement_id=f"requirement:{claim.claim_id}", category="bonus_capability" if preferred else category,
            capability_id=resolved.capability_id, raw_label=resolved.canonical_name or raw,
            importance="bonus" if preferred else "core" if category != "responsibility" else "context", obligation=obligation,
            confidence=claim.confidence, supporting_claim_ids=[claim.claim_id],
        )

    def _save(self, subject_id: str, profile: Any, claim_ids: list[str], model: str) -> ProfileSnapshot:
        latest = self.repository.get_latest_profile(subject_id, "role")
        payload = profile.model_dump(mode="json")
        canonical = _canonical_profile(payload)
        if latest is not None and _canonical_profile(latest.profile_data) == canonical:
            return latest
        if "previous_snapshot_id" in payload:
            payload["previous_snapshot_id"] = latest.snapshot_id if latest else None
        snapshot = ProfileSnapshot(
            subject_id=subject_id, profile_type="role", version=1 if latest is None else latest.version + 1,
            schema_version="v0.5", profile_data=payload, supporting_claim_ids=sorted(set(claim_ids)),
            provenance=Provenance(provider="deterministic", model=model, schema_version="v0.5"),
        )
        return self.repository.save_profile(snapshot)


def _aggregate_signals(profiles: list[JobInstanceRoleProfile]) -> list[HiringSignal]:
    grouped: dict[tuple[str, str, str], list[HiringSignal]] = defaultdict(list)
    for profile in profiles:
        for signal in profile.hiring_signals:
            grouped[(signal.signal_type, signal.summary, signal.scope_level)].append(signal)
    result: list[HiringSignal] = []
    for (signal_type, summary, scope), values in sorted(grouped.items()):
        claim_ids = sorted({claim_id for item in values for claim_id in item.supporting_claim_ids})
        count = len(claim_ids)
        result.append(HiringSignal(
            signal_id=f"family-signal:{canonical_hash('signal', [signal_type, summary, scope])}",
            signal_type=signal_type, scope_level=scope, summary=summary,
            occurrence_count=count, independent_source_count=count,
            frequency_label="frequent_signal" if count >= 2 else "observed_signal",
            confidence=round(sum(item.confidence for item in values) / len(values), 4),
            freshness="current_window", supporting_claim_ids=claim_ids,
        ))
    return result


def _resolved_value(predicate: str, resolutions: dict[str, FieldResolution], claims: dict[str, EvidenceClaim], fallback: Any) -> Any:
    resolution = resolutions.get(predicate)
    return claims[resolution.chosen_claim_id].value if resolution and resolution.chosen_claim_id in claims else fallback


def _resolved_datetime(predicate: str, resolutions: dict[str, FieldResolution], claims: dict[str, EvidenceClaim], fallback: datetime | None) -> datetime | None:
    value = _resolved_value(predicate, resolutions, claims, fallback)
    if value is None or isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return fallback


def _freshness(job: NormalizedJobPosting) -> str:
    now = utc_now()
    if job.application_deadline and job.application_deadline < now:
        return "expired"
    age = now - job.retrieved_at
    return "current" if age <= timedelta(days=30) else "recent" if age <= timedelta(days=180) else "historical"


def _claim_freshness(claim: EvidenceClaim, repository: EvidenceRepository) -> str:
    retrieved = []
    for fragment_id in claim.evidence_fragment_ids:
        fragment = repository.get_fragment(fragment_id)
        artifact = repository.get_artifact(fragment.artifact_id) if fragment else None
        if artifact is not None: retrieved.append(artifact.retrieved_at)
    if not retrieved: return "unknown"
    age = utc_now() - max(retrieved)
    return "current" if age <= timedelta(days=30) else "recent" if age <= timedelta(days=180) else "historical"


def _job_unknowns(job: NormalizedJobPosting) -> list[str]:
    return [name for name, value in {
        "application_deadline": job.application_deadline, "degree_requirement": job.degree_requirement,
        "major_requirement": job.major_requirement, "salary": job.salary_min,
    }.items() if value is None]


def _split_items(text: str) -> list[str]:
    return [value.strip(" -•\t") for value in re_split(text) if value.strip(" -•\t")][:30]


def re_split(text: str) -> list[str]:
    import re
    return re.split(r"[\n；;。]+", text or "")


def _canonical_profile(payload: dict[str, Any]) -> str:
    cleaned = {key: value for key, value in payload.items() if key not in {"role_profile_id", "generated_at", "previous_snapshot_id"}}
    return json.dumps(cleaned, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
