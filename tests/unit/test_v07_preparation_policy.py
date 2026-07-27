from datetime import date, timedelta

import pytest
from pydantic import ValidationError

from campus_job_agent.schemas import (
    JobInstanceRoleProfile, MinimumPreparationPackage, PreparationActivity, PreparationConstraints,
    PreparationInputSet, PreparationObjective, PriorityFactors,
)
from campus_job_agent.workflows.preparation_plan.activity import (
    generate_activities_with_fallback, validate_activity_candidates,
)
from campus_job_agent.workflows.preparation_plan.policy import (
    PreparationPolicyError, build_package, compute_priority, derive_objectives, generate_activities, schedule_activities,
    stable_activity_order, validate_dependency_dag,
)


def _constraints(**updates):
    payload = {"horizon_start": date(2026, 8, 3), "horizon_end": date(2026, 8, 16),
               "weekly_hours": 10, "daily_max_hours": 4, "session_minutes": 60}
    payload.update(updates)
    return PreparationConstraints.model_validate(payload)


def _objective(reason="selected_target_core_evidence_gap", *, addressability="addressable", targets=None,
               objective_type="strengthen_evidence"):
    return PreparationObjective(
        objective_id=f"objective-{reason}", objective_type=objective_type, title="Objective",
        target_job_profile_ids=targets or ["role-1"], gap_ids=["gap-1"],
        addressability=addressability, reason_codes=[reason],
    )


def _activity(activity_id="activity-1", *, hours=2, splittable=True, deadline=None, dependencies=None,
              targets=None, activity_type="strengthen_evidence"):
    return PreparationActivity(
        activity_id=activity_id, activity_type=activity_type, objective_ids=["objective"], title="Activity",
        description="Produce bounded evidence.", expected_outputs=["artifact"], completion_criteria=["archived"],
        verification_method="evidence_ingestion_required", estimated_hours=hours, splittable=splittable,
        minimum_session_minutes=60, deadline=deadline, dependencies=dependencies or [],
        target_job_profile_ids=targets or ["role-1"], gap_ids=["gap-1"], supporting_claim_ids=["claim-1"],
    )


def _factor(activity, band="P1_core", *, sort_key=None):
    return PriorityFactors(
        priority_factor_id=f"priority-{activity.activity_id}", activity_id=activity.activity_id,
        priority_band=band, selected_target_count=len(activity.target_job_profile_ids), role_importance_weight=1.5,
        hiring_signal_strength=0, transfer_target_count=len(activity.target_job_profile_ids), deadline_urgency=0,
        improvability="high", estimated_effort_hours=activity.estimated_hours,
        sort_key=sort_key or (1, -len(activity.target_job_profile_ids), activity.activity_id),
        reason_codes=["fixture"],
    )


def _role(role_id="role-1"):
    return JobInstanceRoleProfile(
        role_profile_id=role_id, job_cluster_id=role_id, role_title="AI", role_family="ai",
        company="Example", source_status="included",
    )


@pytest.mark.parametrize("updates", [
    {"horizon_end": date(2026, 8, 2)},
    {"timezone": "Not/A_Timezone"},
    {"preferred_activity_types": ["strengthen_evidence"], "excluded_activity_types": ["strengthen_evidence"]},
    {"daily_max_hours": 0.5, "session_minutes": 60},
])
def test_constraints_reject_invalid_calendar_and_capacity(updates):
    with pytest.raises(ValidationError):
        _constraints(**updates)


def test_constraints_hash_is_stable_for_set_like_lists():
    first = _constraints(unavailable_dates=[date(2026, 8, 5), date(2026, 8, 4)],
                         preferred_activity_types=["interview_practice", "strengthen_evidence"])
    second = _constraints(unavailable_dates=[date(2026, 8, 4), date(2026, 8, 5)],
                          preferred_activity_types=["strengthen_evidence", "interview_practice"])
    assert first.constraints_id == second.constraints_id


def test_preparation_input_hash_stable_across_reference_order():
    common = dict(user_id="owner", candidate_profile_snapshot_id="c", career_intent_snapshot_id="i",
                  comparison_set_id="cmp", constraints_id="constraints", snapshot_hashes={"c": "hash"})
    first = PreparationInputSet(target_decision_ids=["d2", "d1"], gap_assessment_ids=["g2", "g1"],
                                job_instance_profile_snapshot_ids=["r2", "r1"], **common)
    second = PreparationInputSet(target_decision_ids=["d1", "d2"], gap_assessment_ids=["g1", "g2"],
                                 job_instance_profile_snapshot_ids=["r1", "r2"], **common)
    assert first.canonical_input_hash == second.canonical_input_hash


def test_objective_requires_evidence_bearing_business_ref():
    with pytest.raises(ValidationError):
        PreparationObjective(objective_id="o", objective_type="target_review", title="x",
                             addressability="unknown", reason_codes=["x"])


def test_selected_role_derives_traceable_required_application_asset():
    role = _role()
    objectives = derive_objectives([], {role.role_profile_id: role})
    objective = next(item for item in objectives if item.objective_type == "prepare_application_asset")
    assert objective.target_job_profile_ids == [role.role_profile_id]
    assert objective.application_asset_refs == [f"{role.role_profile_id}#/application_url"]
    activity = generate_activities([objective], {role.role_profile_id: role})[0]
    factor = compute_priority(activity, {objective.objective_id: objective},
                              {role.role_profile_id: role}, _constraints())
    assert factor.priority_band == "P0_blocker"


@pytest.mark.parametrize("patch,match", [
    ({"description": "Use https://fabricated.example/course"}, "external resource"),
    ({"description": "This guarantees offer probability"}, "prohibited"),
    ({"dependencies": ["activity-1"]}, "depend on itself"),
    ({"estimated_hours": 0}, "greater than 0"),
])
def test_activity_validator_rejects_unsafe_or_invalid_candidates(patch, match):
    payload = _activity().model_dump(mode="json")
    payload.update(patch)
    with pytest.raises(ValidationError, match=match):
        PreparationActivity.model_validate(payload)


@pytest.mark.parametrize("reason,addressability,targets,expected", [
    ("addressable_hard_blocker", "addressable", ["role-1"], "P0_blocker"),
    ("selected_target_core_evidence_gap", "addressable", ["role-1"], "P1_core"),
    ("high_value_unknown", "unknown", ["role-1"], "P2_transferable"),
    ("bonus_gap", "addressable", ["role-1"], "P3_bonus"),
    ("unaddressable_blocker", "unaddressable", ["role-1"], "P4_deferred"),
])
def test_priority_band_truth_table(reason, addressability, targets, expected):
    objective = _objective(reason, addressability=addressability, targets=targets)
    activity = _activity(targets=targets)
    activity = activity.model_copy(update={"objective_ids": [objective.objective_id]})
    result = compute_priority(activity, {objective.objective_id: objective}, {"role-1": _role()}, _constraints())
    assert result.priority_band == expected
    assert result.policy_version == "preparation_priority_v1"
    assert result.sort_key[-1] == activity.activity_id


def test_multi_target_transfer_value_improves_stable_band_order():
    one = _activity("one", targets=["role-1"])
    two = _activity("two", targets=["role-1", "role-2"])
    factors = {"one": _factor(one, sort_key=(1, -1, "one")),
               "two": _factor(two, sort_key=(1, -2, "two"))}
    assert [item.activity_id for item in stable_activity_order([one, two], factors)] == ["two", "one"]


def test_dependency_dag_returns_dependencies_before_dependents():
    first = _activity("first")
    second = _activity("second", dependencies=["first"])
    assert validate_dependency_dag([second, first]) == ["first", "second"]


def test_dependency_cycle_is_rejected():
    first = _activity("first", dependencies=["second"])
    second = _activity("second", dependencies=["first"])
    with pytest.raises(PreparationPolicyError, match="dependency_cycle"):
        validate_dependency_dag([first, second])


def test_unknown_dependency_is_rejected():
    with pytest.raises(PreparationPolicyError, match="invalid_activity_reference"):
        validate_dependency_dag([_activity(dependencies=["missing"])])


def test_scheduler_splits_and_respects_daily_weekly_capacity():
    activity = _activity(hours=6)
    sessions, deferred, _ = schedule_activities([activity], {activity.activity_id: _factor(activity)},
                                                _constraints(weekly_hours=4, daily_max_hours=2,
                                                             horizon_end=date(2026, 8, 9)))
    assert not sessions
    assert deferred[activity.activity_id] == "capacity_shortage"


def test_scheduler_splits_feasible_activity_into_stable_sessions():
    activity = _activity(hours=3)
    constraints = _constraints(daily_max_hours=2)
    first = schedule_activities([activity], {activity.activity_id: _factor(activity)}, constraints)
    second = schedule_activities([activity], {activity.activity_id: _factor(activity)}, constraints)
    assert [item.duration_minutes for item in first[0]] == [60, 60, 60]
    assert first[2] == second[2]


def test_non_splittable_activity_larger_than_daily_capacity_is_deferred():
    activity = _activity(hours=3, splittable=False)
    sessions, deferred, _ = schedule_activities([activity], {activity.activity_id: _factor(activity)},
                                                _constraints(daily_max_hours=2))
    assert sessions == []
    assert deferred[activity.activity_id] == "capacity_shortage"


def test_scheduler_respects_unavailable_date_and_timezone():
    activity = _activity(hours=1)
    constraints = _constraints(unavailable_dates=[date(2026, 8, 3)])
    sessions, _, _ = schedule_activities([activity], {activity.activity_id: _factor(activity)}, constraints)
    assert sessions[0].start_at.date() == date(2026, 8, 4)
    assert sessions[0].start_at.tzinfo.key == "Asia/Shanghai"


def test_scheduler_rejects_deadline_before_horizon():
    activity = _activity(hours=1, deadline=date(2026, 8, 2))
    sessions, deferred, _ = schedule_activities([activity], {activity.activity_id: _factor(activity)}, _constraints())
    assert sessions == []
    assert deferred[activity.activity_id] == "deadline_infeasible"


def test_scheduler_completes_dependency_before_dependent():
    first = _activity("first", hours=1)
    second = _activity("second", hours=1, dependencies=["first"])
    factors = {item.activity_id: _factor(item) for item in [first, second]}
    sessions, _, _ = schedule_activities([second, first], factors, _constraints())
    by_activity = {item.activity_id: item for item in sessions}
    assert by_activity["first"].end_at <= by_activity["second"].start_at


@pytest.mark.parametrize("activities,deferred,unaddressable,expected", [
    (1, {}, False, "complete"),
    (1, {"activity-1": "capacity_shortage"}, False, "partial"),
    (1, {}, True, "blocked"),
    (0, {}, False, "unknown"),
])
def test_minimum_package_status_truth_table(activities, deferred, unaddressable, expected):
    objective = _objective("unaddressable_blocker" if unaddressable else "selected_target_core_evidence_gap",
                           addressability="unaddressable" if unaddressable else "addressable")
    items = [_activity(activity_type="target_review" if unaddressable else "strengthen_evidence")] if activities else []
    sessions = []
    if items and not deferred:
        sessions, _, _ = schedule_activities(items, {items[0].activity_id: _factor(items[0])}, _constraints())
    factors = {item.activity_id: _factor(item) for item in items}
    package = build_package([objective] if activities else [], items, sessions, dict(deferred), factors)
    assert package.status == expected


def test_optional_bonus_can_be_visible_and_deferred_without_making_required_package_partial():
    objective = _objective("bonus_gap")
    activity = _activity()
    factor = _factor(activity, band="P3_bonus")
    package = build_package([objective], [activity], [], {activity.activity_id: "bonus_deprioritized"},
                            {activity.activity_id: factor})
    assert package.status == "complete"
    assert package.deferred_activity_ids == [activity.activity_id]


def test_llm_candidate_cannot_mutate_priority():
    objective = _objective()
    candidate = _activity().model_dump(mode="json")
    candidate["objective_ids"] = [objective.objective_id]
    candidate["priority_band"] = "P0_blocker"
    with pytest.raises(ValueError, match="llm_priority_mutation"):
        validate_activity_candidates([candidate], objectives=[objective], allowed_target_ids={"role-1"},
                                     allowed_gap_ids={"gap-1"}, allowed_signal_ids=set(), allowed_claim_ids={"claim-1"})
    candidate.pop("priority_band")
    candidate["dependencies"] = ["fabricated-activity"]
    with pytest.raises(PreparationPolicyError, match="invalid_activity_reference"):
        validate_activity_candidates([candidate], objectives=[objective], allowed_target_ids={"role-1"},
                                     allowed_gap_ids={"gap-1"}, allowed_signal_ids=set(), allowed_claim_ids={"claim-1"})


class _InvalidProvider:
    def __init__(self):
        self.calls = 0

    def generate(self, payload):
        self.calls += 1
        return [{**payload["activity"], "priority_band": "P0_blocker"}], []


def test_invalid_llm_activity_retries_once_then_uses_deterministic_fallback():
    objective = _objective()
    activity = _activity().model_copy(update={"objective_ids": [objective.objective_id]})
    provider = _InvalidProvider()
    result, _, error = generate_activities_with_fallback(
        provider, payload={"activity": activity.model_dump(mode="json")}, deterministic=[activity],
        objectives=[objective], allowed_target_ids={"role-1"}, allowed_gap_ids={"gap-1"},
        allowed_signal_ids=set(), allowed_claim_ids={"claim-1"},
    )
    assert result == [activity]
    assert provider.calls == 2
    assert "llm_priority_mutation" in error
