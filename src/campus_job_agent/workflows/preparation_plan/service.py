"""Application service for immutable preparation-plan projection."""

from __future__ import annotations

from typing import Any

from campus_job_agent.schemas import (
    ComparisonSet, GapAssessment, JobInstanceRoleProfile, LearningPlan, MatchingInputSet,
    PreparationActivity, PreparationConstraints, PreparationInputSet, PreparationObjective,
    PriorityFactors, ProfileSnapshot, TargetDecision,
)
from campus_job_agent.schemas.matching import canonical_hash
from campus_job_agent.storage.base import ProfileRepository
from campus_job_agent.workflows.preparation_plan.policy import (
    build_package, compute_priority, derive_objectives, generate_activities, schedule_activities,
    stable_activity_order, validate_dependency_dag,
)
from campus_job_agent.workflows.preparation_plan.repository import SQLitePreparationRepository
from campus_job_agent.workflows.profile_matching.repository import SQLiteMatchingRepository


class PreparationServiceError(RuntimeError):
    pass


class PreparationService:
    def __init__(self, *, profile_repository: ProfileRepository,
                 matching_repository: SQLiteMatchingRepository,
                 preparation_repository: SQLitePreparationRepository) -> None:
        self.profiles = profile_repository
        self.matching = matching_repository
        self.repository = preparation_repository

    def save_constraints(self, constraints: PreparationConstraints, *, owner_id: str) -> PreparationConstraints:
        return self.repository.save("constraints", constraints, owner_id=owner_id)

    def load_and_validate_input(
        self, *, user_id: str, target_decision_ids: list[str], candidate_snapshot_id: str,
        intent_snapshot_id: str, comparison_set_id: str, gap_assessment_ids: list[str],
        job_snapshot_ids: list[str], family_snapshot_ids: list[str], constraints_id: str,
    ) -> tuple[PreparationInputSet, list[GapAssessment], dict[str, JobInstanceRoleProfile], PreparationConstraints]:
        decisions = [self.matching.get(item, TargetDecision, owner_id=user_id) for item in target_decision_ids]
        if not decisions or any(item is None or item.status != "selected" for item in decisions):
            raise PreparationServiceError("target_selection_required")
        comparison = self.matching.get(comparison_set_id, ComparisonSet, owner_id=user_id)
        if comparison is None or comparison.status != "current":
            raise PreparationServiceError("preparation_input_stale: comparison")
        if any(item.comparison_set_id != comparison_set_id for item in decisions if item is not None):
            raise PreparationServiceError("preparation_input_stale: decision comparison mismatch")
        selected_jobs = sorted(item.job_instance_profile_snapshot_id for item in decisions if item is not None)
        if selected_jobs != sorted(set(job_snapshot_ids)):
            raise PreparationServiceError("preparation_input_stale: target snapshot mismatch")
        matching_input = self.matching.get(comparison.input_set_id, MatchingInputSet, owner_id=user_id)
        if matching_input is None or matching_input.candidate_profile_snapshot_id != candidate_snapshot_id \
                or matching_input.career_intent_snapshot_id != intent_snapshot_id:
            raise PreparationServiceError("preparation_input_stale: matching input mismatch")
        if sorted(matching_input.job_instance_profile_snapshot_ids) != sorted(set(job_snapshot_ids)) \
                or sorted(matching_input.role_family_profile_snapshot_ids) != sorted(set(family_snapshot_ids)):
            raise PreparationServiceError("preparation_input_stale: role snapshot mismatch")
        assessments = [self.matching.get(item, GapAssessment, owner_id=user_id) for item in gap_assessment_ids]
        entry_map = {item.job_instance_profile_snapshot_id: item.gap_assessment_id for item in comparison.entries}
        if any(item is None or item.status != "current" or entry_map.get(item.job_instance_profile_snapshot_id) != item.assessment_id
               for item in assessments):
            raise PreparationServiceError("preparation_input_stale: gap assessment")
        constraints = self.repository.get(constraints_id, PreparationConstraints, owner_id=user_id)
        if constraints is None:
            raise PreparationServiceError("constraints_not_found")
        snapshots = [self._current_snapshot(candidate_snapshot_id, "candidate", user_id),
                     self._current_snapshot(intent_snapshot_id, "career_intent", user_id)]
        roles: dict[str, JobInstanceRoleProfile] = {}
        for snapshot_id in job_snapshot_ids:
            snapshot = self._current_snapshot(snapshot_id, "role", user_id)
            roles[snapshot_id] = JobInstanceRoleProfile.model_validate(snapshot.profile_data)
            snapshots.append(snapshot)
        for snapshot_id in family_snapshot_ids:
            snapshots.append(self._current_snapshot(snapshot_id, "role", user_id))
        snapshot_hashes = {
            item.snapshot_id: canonical_hash("profile-snapshot", item.model_dump(mode="json", exclude={"created_at"}))
            for item in snapshots
        }
        snapshot_hashes[comparison_set_id] = comparison.canonical_hash
        snapshot_hashes[constraints_id] = canonical_hash("constraints", constraints.model_dump(mode="json"))
        input_set = PreparationInputSet(
            user_id=user_id, target_decision_ids=target_decision_ids,
            candidate_profile_snapshot_id=candidate_snapshot_id, career_intent_snapshot_id=intent_snapshot_id,
            comparison_set_id=comparison_set_id, gap_assessment_ids=gap_assessment_ids,
            job_instance_profile_snapshot_ids=job_snapshot_ids,
            role_family_profile_snapshot_ids=family_snapshot_ids, constraints_id=constraints_id,
            snapshot_hashes=snapshot_hashes,
        )
        input_set = self.repository.save("preparation_input", input_set, owner_id=user_id,
                                         idempotency_key=input_set.canonical_input_hash)
        return input_set, [item for item in assessments if item is not None], roles, constraints

    def build_plan(self, *, input_set: PreparationInputSet, assessments: list[GapAssessment],
                   roles: dict[str, JobInstanceRoleProfile], constraints: PreparationConstraints,
                   excluded_activity_ids: set[str] | None = None, previous_plan_id: str | None = None,
                   change_reason_codes: list[str] | None = None,
                   activity_revisions: dict[str, dict[str, Any]] | None = None,
                   max_activities: int = 30) -> tuple[LearningPlan, list[PreparationObjective], list[PreparationActivity], list[PriorityFactors]]:
        objectives = derive_objectives(assessments, roles)
        activities = generate_activities(objectives, roles)
        if activity_revisions:
            allowed = {"title", "description", "expected_outputs", "completion_criteria"}
            activities = [
                item.model_copy(update={key: value for key, value in activity_revisions.get(item.activity_id, {}).items() if key in allowed})
                for item in activities
            ]
        if excluded_activity_ids:
            activities = [item for item in activities if item.activity_id not in excluded_activity_ids]
        objective_map = {item.objective_id: item for item in objectives}
        factors = [compute_priority(item, objective_map, roles, constraints) for item in activities]
        factor_map = {item.activity_id: item for item in factors}
        activities = stable_activity_order(activities, factor_map)
        validate_dependency_dag(activities)
        # The minimum package contains policy-required work. Optional P3 work stays visible
        # but does not consume capacity unless the user explicitly prefers that activity type.
        budget_deferred = {item.activity_id for item in activities[max_activities:]}
        active = activities[:max_activities]
        optional = {
            item.activity_id for item in active
            if factor_map[item.activity_id].priority_band == "P3_bonus"
            and item.activity_type not in constraints.preferred_activity_types
        }
        schedulable = [item for item in active if item.activity_id not in optional]
        sessions, deferred, schedule_hash = schedule_activities(schedulable, factor_map, constraints)
        deferred.update({item: "bonus_deprioritized" for item in sorted(optional)})
        deferred.update({item: "max_activity_budget_reached" for item in sorted(budget_deferred)})
        package = build_package(objectives, activities, sessions, deferred, factor_map)
        if len(activities) == 0 and objectives:
            package = package.model_copy(update={"status": "partial", "warnings": [*package.warnings, "all_activities_excluded"]})
        persisted_activities: list[PreparationActivity] = []
        for item in activities:
            status = "scheduled" if any(session.activity_id == item.activity_id for session in sessions) else "deferred"
            reason = deferred.get(item.activity_id)
            persisted_activities.append(item.model_copy(update={"status": status, "deferred_reason": reason}))
        ordered_ids = [item.activity_id for item in activities]
        digest = canonical_hash("learning-plan", [input_set.input_set_id, ordered_ids,
                                                   [item.model_dump(mode="json") for item in sessions], package.package_id])
        plan = LearningPlan(
            learning_plan_id=f"learning-plan:{digest[7:31]}", user_id=input_set.user_id,
            input_set_id=input_set.input_set_id, constraints_id=constraints.constraints_id,
            package_id=package.package_id, objective_ids=[item.objective_id for item in objectives],
            activity_ids=ordered_ids, priority_factor_ids=[item.priority_factor_id for item in factors],
            schedule=sessions, schedule_hash=schedule_hash,
            status="blocked" if package.status == "blocked" else "partial" if package.status == "partial" else "proposed",
            previous_plan_id=previous_plan_id, supersedes_plan_id=previous_plan_id,
            change_reason_codes=change_reason_codes or [], canonical_hash=digest,
        )
        self.repository.save_batch([
            *[("objective", item, input_set.user_id, None) for item in objectives],
            *[("activity", item, input_set.user_id, None) for item in persisted_activities],
            *[("priority_factor", item, input_set.user_id, None) for item in factors],
            ("package", package, input_set.user_id, None),
            ("learning_plan", plan, input_set.user_id, digest),
        ])
        if previous_plan_id and previous_plan_id != plan.learning_plan_id:
            old = self.repository.get(previous_plan_id, LearningPlan, owner_id=input_set.user_id)
            if old is not None and old.status not in {"superseded", "cancelled"}:
                self.repository.replace_lifecycle(previous_plan_id, LearningPlan, "superseded")
        return plan, objectives, activities, factors

    def mark_plan_stale(self, plan_id: str, *, owner_id: str) -> LearningPlan:
        return self.repository.replace_lifecycle(plan_id, LearningPlan, "stale")

    def _current_snapshot(self, snapshot_id: str, expected_type: str, owner_id: str) -> ProfileSnapshot:
        snapshot = self.profiles.get_profile(snapshot_id)
        if snapshot is None or snapshot.profile_type != expected_type:
            raise PreparationServiceError(f"snapshot_not_found: {snapshot_id}")
        latest = self.profiles.get_latest_profile(snapshot.subject_id, expected_type)
        if latest is None or latest.snapshot_id != snapshot_id:
            raise PreparationServiceError(f"preparation_input_stale: {snapshot_id}")
        declared = snapshot.profile_data.get("owner_id") or snapshot.profile_data.get("user_id")
        if declared is not None and str(declared) != owner_id:
            raise PreparationServiceError("snapshot_owner_mismatch")
        if declared is None and not snapshot.supporting_claim_ids:
            raise PreparationServiceError("snapshot_owner_mismatch: owner cannot be verified")
        return snapshot
