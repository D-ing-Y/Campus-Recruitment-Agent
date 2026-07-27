"""Application services that project immutable matching records from snapshots."""

from __future__ import annotations

from typing import Any

from campus_job_agent.schemas import (
    CandidateProfile,
    CareerIntent,
    ComparisonSet,
    GapAssessment,
    IntentImpactAssessment,
    JobInstanceRoleProfile,
    MatchingInputSet,
    ProfileSnapshot,
    RebuildDirective,
    RoleFamilyProfile,
    SearchScope,
)
from campus_job_agent.schemas.evidence import utc_now
from campus_job_agent.schemas.matching import canonical_hash
from campus_job_agent.storage.base import EvidenceRepository, ProfileRepository
from campus_job_agent.workflows.profile_matching.policy import (
    CapabilityPolicy,
    PreferencePolicy,
    QualificationPolicy,
    build_fact_index,
    build_gaps,
    comparison_entry,
    compute_coverage,
    stable_sort,
)
from campus_job_agent.workflows.profile_matching.repository import SQLiteMatchingRepository


class MatchingServiceError(RuntimeError):
    pass


class MatchingService:
    def __init__(
        self,
        *,
        profile_repository: ProfileRepository,
        evidence_repository: EvidenceRepository,
        matching_repository: SQLiteMatchingRepository,
        capability_policy: CapabilityPolicy | None = None,
    ) -> None:
        self.profile_repository = profile_repository
        self.evidence_repository = evidence_repository
        self.repository = matching_repository
        self.qualification_policy = QualificationPolicy()
        self.capability_policy = capability_policy or CapabilityPolicy()
        self.preference_policy = PreferencePolicy()

    def load_input_set(
        self,
        *,
        user_id: str,
        candidate_snapshot_id: str,
        intent_snapshot_id: str,
        job_snapshot_ids: list[str],
        family_snapshot_ids: list[str],
    ) -> tuple[MatchingInputSet, CandidateProfile, CareerIntent, dict[str, JobInstanceRoleProfile], dict[str, RoleFamilyProfile]]:
        candidate_snapshot = self._snapshot(candidate_snapshot_id, "candidate")
        intent_snapshot = self._snapshot(intent_snapshot_id, "career_intent")
        candidate = CandidateProfile.model_validate(candidate_snapshot.profile_data)
        intent = CareerIntent.model_validate(intent_snapshot.profile_data)
        if intent.user_id != user_id:
            raise MatchingServiceError("snapshot_owner_mismatch: career intent")
        self._assert_owner(candidate_snapshot, user_id)
        self._assert_owner(intent_snapshot, user_id)
        jobs: dict[str, JobInstanceRoleProfile] = {}
        families: dict[str, RoleFamilyProfile] = {}
        if not job_snapshot_ids:
            raise MatchingServiceError("snapshot_not_found: at least one job instance is required")
        for snapshot_id in job_snapshot_ids:
            snapshot = self._snapshot(snapshot_id, "role")
            self._assert_owner(snapshot, user_id)
            try:
                profile = JobInstanceRoleProfile.model_validate(snapshot.profile_data)
            except Exception as exc:
                raise MatchingServiceError("snapshot_schema_unsupported: job instance") from exc
            jobs[snapshot_id] = profile
        for snapshot_id in family_snapshot_ids:
            snapshot = self._snapshot(snapshot_id, "role")
            self._assert_owner(snapshot, user_id)
            try:
                families[snapshot_id] = RoleFamilyProfile.model_validate(snapshot.profile_data)
            except Exception as exc:
                raise MatchingServiceError("snapshot_schema_unsupported: role family") from exc
        snapshots = [candidate_snapshot, intent_snapshot]
        snapshots.extend(self.profile_repository.get_profile(value) for value in [*job_snapshot_ids, *family_snapshot_ids])
        snapshot_hashes = {
            item.snapshot_id: canonical_hash("profile-snapshot", item.model_dump(mode="json", exclude={"created_at"}))
            for item in snapshots if item is not None
        }
        input_set = MatchingInputSet(
            user_id=user_id,
            candidate_profile_snapshot_id=candidate_snapshot_id,
            career_intent_snapshot_id=intent_snapshot_id,
            job_instance_profile_snapshot_ids=job_snapshot_ids,
            role_family_profile_snapshot_ids=family_snapshot_ids,
            snapshot_hashes=snapshot_hashes,
        )
        input_set = self.repository.save(
            "matching_input", input_set, owner_id=user_id,
            idempotency_key=input_set.canonical_input_hash,
        )
        return input_set, candidate, intent, jobs, families

    def assess_job(
        self,
        *,
        input_set: MatchingInputSet,
        candidate: CandidateProfile,
        intent: CareerIntent,
        job_snapshot_id: str,
        role: JobInstanceRoleProfile,
    ) -> GapAssessment:
        existing = [
            item for item in self.repository.list("gap_assessment", GapAssessment, owner_id=input_set.user_id)
            if item.input_set_id == input_set.input_set_id
            and item.job_instance_profile_snapshot_id == job_snapshot_id
            and item.status == "current"
        ]
        if existing:
            return existing[-1]
        qualifications = [
            self.qualification_policy.evaluate(candidate, item)
            for item in role.qualifications if item.importance == "hard"
        ]
        requirements = [
            self.capability_policy.evaluate(candidate, item)
            for item in [*role.requirements, *role.bonus_items]
            if item.category in {"core_capability", "bonus_capability"}
            or item.importance in {"core", "bonus"}
        ]
        preferences = self.preference_policy.evaluate(intent, role)
        stale_reason = role_refresh_reason(role)
        gaps = build_gaps(requirements, preferences, role_stale_reason=stale_reason)
        core = compute_coverage(requirements, "core_capability")
        bonus = compute_coverage(requirements, "bonus_capability")
        hard = self.qualification_policy.overall(qualifications)
        content = {
            "input_set_id": input_set.input_set_id,
            "job": job_snapshot_id,
            "hard": hard,
            "qualifications": [item.model_dump(mode="json") for item in qualifications],
            "requirements": [item.model_dump(mode="json") for item in requirements],
            "preferences": [item.model_dump(mode="json") for item in preferences],
            "core": core.model_dump(mode="json"),
            "bonus": bonus.model_dump(mode="json"),
            "gaps": [item.model_dump(mode="json") for item in gaps],
        }
        digest = canonical_hash("gap-assessment", content)
        supporting = list(dict.fromkeys(
            claim_id
            for item in [*qualifications, *requirements, *preferences]
            for claim_id in [*getattr(item, "candidate_claim_ids", []), *getattr(item, "role_claim_ids", [])]
        ))
        assessment = GapAssessment(
            assessment_id=f"gap-assessment:{digest[7:31]}",
            schema_version="v0.6",
            input_set_id=input_set.input_set_id,
            candidate_profile_snapshot_id=input_set.candidate_profile_snapshot_id,
            career_intent_snapshot_id=input_set.career_intent_snapshot_id,
            job_instance_profile_snapshot_id=job_snapshot_id,
            role_profile_snapshot_id=job_snapshot_id,
            role_family_profile_snapshot_ids=input_set.role_family_profile_snapshot_ids,
            hard_constraint_status=hard,
            qualification_assessments=[item.model_dump(mode="json") for item in qualifications],
            requirement_assessments=[item.model_dump(mode="json") for item in requirements],
            core_coverage=core.model_dump(mode="json"),
            bonus_coverage=bonus.model_dump(mode="json"),
            preference_assessments=[item.model_dump(mode="json") for item in preferences],
            gaps=gaps,
            supporting_claim_ids=supporting,
            matching_policy_version=input_set.matching_policy_version,
            status="current",
        )
        assessment = assessment.model_copy(update={"fact_index": build_fact_index(assessment)})
        for item in qualifications:
            self.repository.save("qualification_assessment", item, owner_id=input_set.user_id)
        for item in requirements:
            self.repository.save("requirement_assessment", item, owner_id=input_set.user_id)
        for item in preferences:
            self.repository.save("preference_assessment", item, owner_id=input_set.user_id)
        return self.repository.save(
            "gap_assessment", assessment, owner_id=input_set.user_id, idempotency_key=digest
        )

    def build_comparison(self, input_set: MatchingInputSet, assessments: list[GapAssessment]) -> ComparisonSet:
        entries = stable_sort([comparison_entry(item) for item in assessments])
        digest = canonical_hash(
            "comparison", [input_set.input_set_id, [item.model_dump(mode="json") for item in entries], "matching_rank_v1"]
        )
        comparison = ComparisonSet(
            comparison_set_id=f"comparison:{digest[7:31]}",
            input_set_id=input_set.input_set_id,
            entries=entries,
            canonical_hash=digest,
        )
        return self.repository.save(
            "comparison", comparison, owner_id=input_set.user_id, idempotency_key=digest
        )

    def invalidate_input(self, snapshot_id: str, *, owner_id: str) -> tuple[int, int]:
        inputs = [
            item for item in self.repository.list("matching_input", MatchingInputSet, owner_id=owner_id)
            if snapshot_id in {
                item.candidate_profile_snapshot_id, item.career_intent_snapshot_id,
                *item.job_instance_profile_snapshot_ids, *item.role_family_profile_snapshot_ids,
            }
        ]
        input_ids = {item.input_set_id for item in inputs}
        assessment_count = comparison_count = 0
        for item in self.repository.list("gap_assessment", GapAssessment, owner_id=owner_id):
            if item.input_set_id in input_ids and item.status == "current":
                self.repository.replace_lifecycle(item.assessment_id, GapAssessment, "stale")
                assessment_count += 1
        for item in self.repository.list("comparison", ComparisonSet, owner_id=owner_id):
            if item.input_set_id in input_ids and item.status == "current":
                self.repository.replace_lifecycle(item.comparison_set_id, ComparisonSet, "stale")
                comparison_count += 1
        return assessment_count, comparison_count

    def _snapshot(self, snapshot_id: str, expected_type: str) -> ProfileSnapshot:
        snapshot = self.profile_repository.get_profile(snapshot_id)
        if snapshot is None:
            raise MatchingServiceError(f"snapshot_not_found: {snapshot_id}")
        if snapshot.profile_type != expected_type:
            raise MatchingServiceError(f"snapshot_schema_unsupported: expected {expected_type}")
        return snapshot

    def _assert_owner(self, snapshot: ProfileSnapshot, user_id: str) -> None:
        declared = snapshot.profile_data.get("owner_id") or snapshot.profile_data.get("user_id")
        if declared is not None and str(declared) != user_id:
            raise MatchingServiceError("snapshot_owner_mismatch")
        if declared is None and not snapshot.supporting_claim_ids:
            raise MatchingServiceError("snapshot_owner_mismatch: owner cannot be verified")
        for claim_id in snapshot.supporting_claim_ids:
            claim = self.evidence_repository.get_claim(claim_id)
            if claim is None:
                raise MatchingServiceError("snapshot_schema_unsupported: missing supporting claim")
            for fragment_id in claim.evidence_fragment_ids:
                fragment = self.evidence_repository.get_fragment(fragment_id)
                artifact = self.evidence_repository.get_artifact(fragment.artifact_id) if fragment else None
                if artifact is None or artifact.owner_id != user_id:
                    raise MatchingServiceError("snapshot_owner_mismatch")


def project_search_scope(intent: CareerIntent, snapshot_id: str) -> SearchScope:
    scoped_constraints = [
        item.model_dump(mode="json")
        for item in intent.constraints
        if item.affects_search_scope and item.status == "confirmed"
    ]
    return SearchScope(
        career_intent_snapshot_id=snapshot_id,
        target_role_queries=intent.target_roles or ["unknown"],
        target_role_family=(intent.target_role_families or intent.target_roles or ["unknown"])[0],
        locations=intent.locations,
        graduation_year=intent.graduation_year or "unknown",
        recruitment_type=intent.recruitment_type if intent.recruitment_type in {"autumn_campus", "spring_campus", "internship", "unknown"} else "unknown",
        industries=intent.industries,
        companies=intent.companies,
        company_types=intent.company_types,
        hard_constraints=scoped_constraints,
    )


def assess_intent_impact(
    old_intent: CareerIntent,
    new_intent: CareerIntent,
    *,
    old_snapshot_id: str,
    new_snapshot_id: str,
    changed_paths: list[str],
) -> IntentImpactAssessment:
    before = project_search_scope(old_intent, old_snapshot_id).fingerprint()
    after = project_search_scope(new_intent, new_snapshot_id).fingerprint()
    if before != after:
        impact, reasons = "role_research_required", ["search_scope_changed"]
    elif changed_paths:
        impact, reasons = "rematch_only", ["preference_changed_without_scope_change"]
    else:
        impact, reasons = "no_effect", ["intent_unchanged"]
    digest = canonical_hash("intent-impact", [before, after, changed_paths, "intent_impact_v1"])
    return IntentImpactAssessment(
        impact_assessment_id=f"intent-impact:{digest[7:31]}",
        previous_intent_snapshot_id=old_snapshot_id,
        new_intent_snapshot_id=new_snapshot_id,
        changed_paths=changed_paths,
        search_scope_hash_before=before,
        search_scope_hash_after=after,
        impact=impact,
        reason_codes=reasons,
    )


def role_refresh_reason(role: JobInstanceRoleProfile) -> str | None:
    freshness = str(role.freshness.get("status", "unknown"))
    if freshness in {"expired", "historical"} or role.source_status in {"expired", "closed"}:
        return "role_expired"
    if any(
        item.get("category") in {"identity_ambiguity", "identity"}
        or item.get("status") in {"ambiguous", "conflicted"}
        for item in role.conflicts
    ):
        return "role_identity_ambiguous"
    return None


def build_directive(
    *,
    directive_type: str,
    run_id: str,
    comparison_set_id: str,
    reason_codes: list[str],
    required_input_refs: list[str],
    affected_job_profile_ids: list[str],
    requested_scope: dict[str, Any] | None = None,
) -> RebuildDirective:
    digest = canonical_hash(
        "rebuild-directive",
        [comparison_set_id, directive_type, reason_codes, required_input_refs, affected_job_profile_ids, requested_scope],
    )
    return RebuildDirective(
        directive_id=f"directive:{digest[7:31]}",
        directive_type=directive_type,
        originating_run_id=run_id,
        originating_comparison_set_id=comparison_set_id,
        reason_codes=reason_codes,
        required_input_refs=required_input_refs,
        affected_job_profile_ids=affected_job_profile_ids,
        requested_scope=requested_scope,
        created_at=utc_now(),
    )


__all__ = [
    "MatchingService", "MatchingServiceError", "assess_intent_impact", "build_directive",
    "project_search_scope", "role_refresh_reason",
]
