from __future__ import annotations

import pytest
from pydantic import ValidationError

from campus_job_agent.schemas import (
    CandidateProfile,
    CapabilityAssessment,
    CareerIntent,
    ComparisonEntry,
    ComparisonSet,
    CoverageBreakdown,
    EducationRecord,
    GapAssessment,
    IntentConstraint,
    JobInstanceRoleProfile,
    MatchExplanation,
    MatchingInputSet,
    PreferenceAssessment,
    Qualification,
    RoleRequirement,
    migrate_legacy_career_intent,
    migrate_legacy_gap_assessment,
)
from campus_job_agent.workflows.profile_matching.explanation import (
    deterministic_explanation,
    validate_explanation,
)
from campus_job_agent.workflows.profile_matching.policy import (
    CapabilityPolicy,
    CapabilityTransfer,
    PreferencePolicy,
    QualificationPolicy,
    build_gaps,
    compare_values,
    compute_coverage,
    stable_sort,
)
from campus_job_agent.workflows.profile_matching.service import assess_intent_impact


@pytest.mark.parametrize(
    ("operator", "required", "candidate", "expected"),
    [
        ("equals", "2027", "2027", True),
        ("equals", "2027", "2026", False),
        ("in", ["硕士", "博士"], "硕士", True),
        ("in", ["硕士"], "本科", False),
        ("contains_any", ["计算机", "软件"], ["软件工程"], False),
        ("contains_any", ["计算机", "软件工程"], ["软件工程"], True),
        ("contains_all", ["英语", "中文"], ["中文", "英语"], True),
        ("contains_all", ["英语", "中文"], ["中文"], False),
        ("gte", 2027, 2028, True),
        ("gte", 2027, 2026, False),
        ("lte", 2027, 2026, True),
        ("lte", 2027, 2028, False),
        ("range", [2026, 2028], 2027, True),
        ("range", [2026, 2028], 2029, False),
        ("unknown", 1, 1, None),
        ("gte", "not-number", "x", None),
    ],
)
def test_qualification_comparator_truth_table(operator, required, candidate, expected) -> None:
    assert compare_values(operator, required, candidate) is expected


def _candidate(level="intermediate", status="confirmed", claims=None) -> CandidateProfile:
    return CandidateProfile(
        candidate_id="candidate:owner",
        schema_version="v0.4",
        education=[EducationRecord(
            institution="Example University", degree="硕士", major="计算机",
            graduation_year="2027", supporting_claim_ids=["c-edu"],
            field_supporting_claim_ids={"degree": ["c-degree"], "major": ["c-major"], "graduation_year": ["c-grad"]},
        )],
        capabilities=[CapabilityAssessment(
            capability_id="programming.python", raw_label="Python", level=level,
            confidence=1, status=status, supporting_claim_ids=["c-python"] if claims is None else claims,
        )],
    )


def _qualification(status="confirmed", operator="equals", value="2027", claims=None) -> Qualification:
    return Qualification(
        qualification_id="q-grad", qualification_type="graduation_year", operator=operator,
        value=value, importance="hard", status=status, confidence=1,
        supporting_claim_ids=["r-grad"] if claims is None else claims,
    )


@pytest.mark.parametrize(
    ("candidate", "qualification", "outcome"),
    [
        (_candidate(), _qualification(), "passed"),
        (_candidate(), _qualification(value="2026"), "failed"),
        (CandidateProfile(candidate_id="c"), _qualification(), "unknown"),
        (_candidate(), _qualification(status="conflicted"), "conflicted"),
        (_candidate(), _qualification(operator="unsupported"), "unknown"),
        (_candidate(), _qualification(claims=[]), "unknown"),
    ],
)
def test_qualification_outcomes_require_comparable_evidence(candidate, qualification, outcome) -> None:
    assert QualificationPolicy().evaluate(candidate, qualification).outcome == outcome


def test_overall_hard_status_truth_table() -> None:
    policy = QualificationPolicy()
    passed = policy.evaluate(_candidate(), _qualification())
    failed = policy.evaluate(_candidate(), _qualification(value="2026"))
    unknown = policy.evaluate(CandidateProfile(candidate_id="c"), _qualification())
    assert policy.overall([passed]) == "passed"
    assert policy.overall([passed, unknown]) == "unknown"
    assert policy.overall([passed, failed, unknown]) == "failed"
    assert policy.overall([]) == "unknown"


def _requirement(capability_id="programming.python", level="intermediate", category="core_capability", authority="allowed") -> RoleRequirement:
    return RoleRequirement(
        requirement_id=f"req:{capability_id}", category=category, capability_id=capability_id,
        raw_label="Python", required_level=level, importance="bonus" if category == "bonus_capability" else "core",
        obligation="required" if category == "core_capability" else "preferred", authority=authority,
        confidence=1, supporting_claim_ids=["r-python"],
    )


@pytest.mark.parametrize(
    ("candidate", "requirement", "outcome"),
    [
        (_candidate("advanced"), _requirement(level="intermediate"), "satisfied"),
        (_candidate("beginner"), _requirement(level="advanced"), "insufficient"),
        (_candidate("unknown"), _requirement(), "evidence_insufficient"),
        (_candidate("advanced", status="inferred"), _requirement(), "evidence_insufficient"),
        (_candidate("advanced", status="conflicted"), _requirement(), "unknown"),
        (CandidateProfile(candidate_id="c"), _requirement(), "unknown"),
        (_candidate(), _requirement(capability_id=None), "unmapped"),
        (_candidate(), _requirement(authority="forbidden"), "not_applicable"),
    ],
)
def test_capability_outcome_boundaries(candidate, requirement, outcome) -> None:
    assert CapabilityPolicy().evaluate(candidate, requirement).outcome == outcome


def test_transfer_requires_explicit_versioned_relation_and_discount() -> None:
    policy = CapabilityPolicy([CapabilityTransfer("transfer:python-backend:v1", "programming.python", "engineering.backend", 0.75)])
    item = policy.evaluate(_candidate("advanced"), _requirement("engineering.backend", "intermediate"))
    assert item.mapping_type == "transfer"
    assert item.ontology_relation_id == "transfer:python-backend:v1"
    assert item.effective_weight == 1.125
    assert item.outcome == "satisfied"


def test_raw_label_is_unmapped_even_when_human_readable() -> None:
    item = CapabilityPolicy().evaluate(_candidate(), _requirement(None))
    assert item.mapping_type == "unmapped"
    assert item.outcome == "unmapped"


def test_coverage_excludes_unknown_from_denominator_but_reports_uncertainty() -> None:
    policy = CapabilityPolicy()
    items = [
        policy.evaluate(_candidate("advanced"), _requirement(level="intermediate")),
        policy.evaluate(_candidate("beginner"), _requirement("programming.python", "advanced")),
        policy.evaluate(CandidateProfile(candidate_id="c"), _requirement("database.sql")),
    ]
    coverage = compute_coverage(items, "core_capability")
    assert coverage.total_weight == 4.5
    assert coverage.eligible_weight == 3
    assert coverage.covered_weight == 1.5
    assert coverage.uncertain_weight == 1.5
    assert coverage.coverage == 0.5


def test_coverage_is_null_when_nothing_is_eligible() -> None:
    item = CapabilityPolicy().evaluate(CandidateProfile(candidate_id="c"), _requirement("database.sql"))
    coverage = compute_coverage([item], "core_capability")
    assert coverage.eligible_weight == 0
    assert coverage.coverage is None


def test_coverage_schema_rejects_mutated_arithmetic() -> None:
    with pytest.raises(ValidationError):
        CoverageBreakdown(
            dimension="core_capability", total_weight=1, eligible_weight=1,
            covered_weight=1, uncertain_weight=0, coverage=0.5,
        )


def _role(**updates) -> JobInstanceRoleProfile:
    data = dict(
        role_profile_id="role-1", job_cluster_id="cluster-1", role_title="AI Engineer",
        role_family="ai", company="Example", locations=["成都"], recruitment_type="autumn_campus",
        graduation_year="2027", source_status="included", industry="软件", company_type="民营",
        work_mode="hybrid", salary_min=15000, salary_max=20000, salary_unit="CNY/month",
        supporting_claim_ids=["r-profile"], freshness={"status": "current"}, confidence=1,
    )
    data.update(updates)
    return JobInstanceRoleProfile(**data)


@pytest.mark.parametrize(
    ("constraint", "outcome"),
    [
        (IntentConstraint(constraint_id="p1", key="location", operator="in", value=["成都"], kind="hard", status="confirmed"), "aligned"),
        (IntentConstraint(constraint_id="p2", key="location", operator="in", value=["北京"], kind="hard", status="confirmed"), "conflict"),
        (IntentConstraint(constraint_id="p3", key="work_mode", value="remote", kind="negotiable", status="confirmed"), "conflict"),
        (IntentConstraint(constraint_id="p4", key="other", value="成长", kind="negotiable", status="confirmed"), "unknown"),
        (IntentConstraint(constraint_id="p5", key="location", value="成都", kind="hard", status="unknown"), "unknown"),
    ],
)
def test_preference_hard_negotiable_and_unknown(constraint, outcome) -> None:
    intent = CareerIntent(user_id="owner", schema_version="v0.6", constraints=[constraint], confirmed=True)
    assert PreferencePolicy().evaluate(intent, _role())[0].outcome == outcome


def test_salary_unit_mismatch_is_unknown() -> None:
    constraint = IntentConstraint(
        constraint_id="salary", key="salary", operator="gte",
        value={"min": 18000, "unit": "CNY/year"}, kind="negotiable", status="confirmed",
    )
    result = PreferencePolicy().evaluate(CareerIntent(user_id="owner", constraints=[constraint]), _role())[0]
    assert result.outcome == "unknown"


def test_four_gap_classes_and_unknown_not_capability_gap() -> None:
    capability = CapabilityPolicy().evaluate(_candidate("beginner"), _requirement(level="advanced"))
    evidence = CapabilityPolicy().evaluate(_candidate("unknown"), _requirement())
    unknown = CapabilityPolicy().evaluate(CandidateProfile(candidate_id="c"), _requirement("database.sql"))
    preference = PreferenceAssessment(
        assessment_item_id="p", preference_key="location", constraint_kind="hard",
        intent_value="北京", role_value="成都", outcome="conflict", reason_code="preference_conflict",
    )
    gaps = build_gaps([capability, evidence, unknown], [preference])
    assert {item.gap_type for item in gaps} == {
        "capability_gap", "evidence_gap", "preference_conflict", "epistemic_uncertainty"
    }
    unknown_gap = next(item for item in gaps if item.assessment_item_ids == [unknown.assessment_item_id])
    assert unknown_gap.gap_type == "epistemic_uncertainty"
    assert next(item for item in gaps if item.gap_type == "preference_conflict").severity == "blocking"


def test_stable_order_uses_documented_lexicographic_key_and_keeps_failed() -> None:
    entries = [
        ComparisonEntry(job_instance_profile_snapshot_id="failed", gap_assessment_id="g3", recommended_tier="blocked", hard_rank=2, blocking_preference_conflict_count=0, core_coverage=1, uncertainty_weight=0, stable_tie_breaker="failed"),
        ComparisonEntry(job_instance_profile_snapshot_id="b", gap_assessment_id="g2", recommended_tier="review_first", hard_rank=0, blocking_preference_conflict_count=0, core_coverage=.5, uncertainty_weight=0, stable_tie_breaker="b"),
        ComparisonEntry(job_instance_profile_snapshot_id="a", gap_assessment_id="g1", recommended_tier="review_first", hard_rank=0, blocking_preference_conflict_count=0, core_coverage=.5, uncertainty_weight=0, stable_tie_breaker="a"),
    ]
    assert [item.job_instance_profile_snapshot_id for item in stable_sort(entries)] == ["a", "b", "failed"]


def test_matching_input_hash_is_stable_across_input_order() -> None:
    kwargs = dict(user_id="owner", candidate_profile_snapshot_id="c", career_intent_snapshot_id="i", snapshot_hashes={"c": "x"})
    left = MatchingInputSet(**kwargs, job_instance_profile_snapshot_ids=["b", "a", "a"])
    right = MatchingInputSet(**kwargs, job_instance_profile_snapshot_ids=["a", "b"])
    assert left.canonical_input_hash == right.canonical_input_hash
    assert left.job_instance_profile_snapshot_ids == ["a", "b"]


def test_legacy_intent_becomes_unconfirmed_constraints() -> None:
    migrated = migrate_legacy_career_intent(CareerIntent(user_id="owner", hard_constraints=["只看成都"], negotiable_preferences=["远程优先"]))
    assert migrated.schema_version == "v0.6"
    assert migrated.confirmed is False
    assert [item.status for item in migrated.constraints] == ["unknown", "unknown"]


def test_legacy_gap_is_read_only_and_not_reinterpreted() -> None:
    legacy = GapAssessment(assessment_id="g", candidate_profile_snapshot_id="c", role_profile_snapshot_id="r", coverage_score=.8)
    migrated = migrate_legacy_gap_assessment(legacy)
    assert migrated.status == "stale"
    assert migrated.core_coverage is None
    assert migrated.fact_index == {"legacy_coverage_score": .8}


@pytest.mark.parametrize(
    ("patch", "expected"),
    [
        ({"salary_min": 20000}, "rematch_only"),
        ({"locations": ["上海"]}, "role_research_required"),
        ({}, "no_effect"),
    ],
)
def test_intent_impact_reuses_search_scope_projector(patch, expected) -> None:
    old = CareerIntent(user_id="owner", schema_version="v0.6", target_roles=["AI"], target_role_families=["ai"], locations=["成都"], graduation_year="2027", recruitment_type="autumn_campus", confirmed=True)
    new = old.model_copy(update=patch)
    impact = assess_intent_impact(old, new, old_snapshot_id="i1", new_snapshot_id="i2", changed_paths=[f"/{key}" for key in patch])
    assert impact.impact == expected


def _explanation_context():
    assessment = GapAssessment(
        assessment_id="g1", schema_version="v0.6", input_set_id="input", candidate_profile_snapshot_id="c",
        career_intent_snapshot_id="i", role_profile_snapshot_id="r", job_instance_profile_snapshot_id="r",
        hard_constraint_status="passed", core_coverage={"total_weight": 1.5, "eligible_weight": 1.5, "covered_weight": 1.5, "uncertain_weight": 0, "coverage": 1},
        bonus_coverage={"total_weight": 0, "eligible_weight": 0, "covered_weight": 0, "uncertain_weight": 0, "coverage": None},
        fact_index={
            "fact:g1:hard": {"kind": "hard_status", "value": "passed"},
            "fact:g1:core": {"kind": "coverage", "value": {"eligible_weight": 1.5, "covered_weight": 1.5, "uncertain_weight": 0}},
        },
        supporting_claim_ids=["c1", "r1"], status="current",
    )
    comparison = ComparisonSet(
        comparison_set_id="cmp", input_set_id="input", canonical_hash="h",
        entries=[ComparisonEntry(job_instance_profile_snapshot_id="r", gap_assessment_id="g1", recommended_tier="review_first", hard_rank=0, blocking_preference_conflict_count=0, core_coverage=1, uncertainty_weight=0, stable_tie_breaker="r")],
    )
    return comparison, {"g1": assessment}


def test_deterministic_explanation_is_valid_and_warns_about_probability() -> None:
    comparison, assessments = _explanation_context()
    explanation = deterministic_explanation(comparison, assessments)
    validate_explanation(explanation, comparison=comparison, assessments=assessments)
    assert explanation.warnings == ["coverage_is_not_offer_probability"]


@pytest.mark.parametrize(
    ("mutation", "match"),
    [
        ({"summary": "Offer概率为80%"}, "probability"),
        ({"summary": "覆盖权重为9/10"}, "unsupported number"),
        ({"fact_ids": ["fact:not-found"]}, "fact"),
        ({"suggested_actions": ["auto_apply"]}, "action"),
    ],
)
def test_explanation_rejects_fact_mutation(mutation, match) -> None:
    comparison, assessments = _explanation_context()
    explanation = deterministic_explanation(comparison, assessments)
    data = explanation.model_dump(mode="json")
    data["job_explanations"][0].update(mutation)
    with pytest.raises(ValueError, match=match):
        validate_explanation(MatchExplanation.model_validate(data), comparison=comparison, assessments=assessments)
