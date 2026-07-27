from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from campus_job_agent.schemas import (
    CandidateProfile, CareerIntent, ComparisonEntry, ComparisonSet, CoverageBreakdown, GapAssessment,
    GapItem, HiringSignal, JobInstanceRoleProfile, MatchingInputSet, PreparationConstraints,
    ProfileSnapshot, RoleRequirement, TargetDecision,
)
from campus_job_agent.storage import SQLiteRepository
from campus_job_agent.workflows.preparation_plan import SQLitePreparationRepository
from campus_job_agent.workflows.preparation_plan.service import PreparationService
from campus_job_agent.workflows.profile_matching import SQLiteMatchingRepository


def seed_v07(tmp_path, *, user_id="owner", gap_type="evidence_gap", severity="medium",
             hard_status="passed", core=True, two_jobs=False, hiring_signal=False,
             unaddressable=False, weekly_hours=10, daily_max_hours=4):
    database = tmp_path / "domain.sqlite3"
    profiles = SQLiteRepository(database)
    matching = SQLiteMatchingRepository(database)
    preparation = SQLitePreparationRepository(database)
    candidate = CandidateProfile(candidate_id=f"candidate:{user_id}", schema_version="v0.4")
    intent = CareerIntent(user_id=user_id, schema_version="v0.6", target_roles=["AI Engineer"],
                          target_role_families=["ai_engineering"], locations=["成都"],
                          graduation_year="2027", recruitment_type="autumn_campus", confirmed=True)
    candidate_snapshot = ProfileSnapshot(
        snapshot_id="candidate-s1", subject_id=f"candidate:{user_id}", profile_type="candidate", version=1,
        schema_version="v0.4", profile_data={**candidate.model_dump(mode="json"), "owner_id": user_id},
    )
    intent_snapshot = ProfileSnapshot(
        snapshot_id="intent-s1", subject_id=f"intent:{user_id}", profile_type="career_intent", version=1,
        schema_version="v0.6", profile_data={**intent.model_dump(mode="json"), "owner_id": user_id},
    )
    profiles.save_profile(candidate_snapshot)
    profiles.save_profile(intent_snapshot)
    job_ids = ["role-s1", "role-s2"] if two_jobs else ["role-s1"]
    gaps = []
    entries = []
    for index, job_id in enumerate(job_ids, 1):
        requirement = RoleRequirement(
            requirement_id="req-item-python", category="core_capability" if core else "bonus_capability",
            capability_id="programming.python", raw_label="Python", required_level="intermediate",
            importance="core" if core else "bonus", obligation="required" if core else "preferred",
            weight=1, confidence=1, authority="allowed", supporting_claim_ids=[f"role-claim-{index}"],
        )
        signals = [HiringSignal(
            signal_id=f"signal-{index}", signal_type="interview", stage="technical_interview",
            scope_level="job_instance", summary="RAG evaluation", occurrence_count=2,
            independent_source_count=2, frequency_label="frequent_signal", confidence=0.8,
            freshness="current_window", supporting_claim_ids=[f"signal-claim-{index}"],
        )] if hiring_signal else []
        role = JobInstanceRoleProfile(
            role_profile_id=f"role-model-{index}", job_cluster_id=f"cluster-{index}",
            role_title="AI Engineer", role_family="ai_engineering", company=f"Example-{index}",
            locations=["成都"], recruitment_type="autumn_campus", graduation_year="2027",
            source_status="included", application_deadline=datetime.now(UTC) + timedelta(days=20),
            requirements=[requirement] if core else [], bonus_items=[] if core else [requirement],
            hiring_signals=signals, supporting_claim_ids=[],
            freshness={"status": "current"}, confidence=1,
        )
        profiles.save_profile(ProfileSnapshot(
            snapshot_id=job_id, subject_id=f"role_instance:cluster-{index}", profile_type="role", version=1,
            schema_version="v0.5", profile_data={**role.model_dump(mode="json"), "owner_id": user_id},
        ))
        requirement_item = {
            "assessment_item_id": "req-item-python", "requirement_id": "req-item-python",
            "capability_id": "programming.python", "raw_label": "Python", "mapping_type": "exact",
            "required_level": "intermediate", "candidate_level": "beginner",
            "outcome": "insufficient" if gap_type == "capability_gap" else "evidence_insufficient",
            "importance": "core" if core else "bonus", "obligation": "required" if core else "preferred",
            "base_weight": 1.5 if core else 0.5, "effective_weight": 1.5 if core else 0.5,
            "reason_code": "fixture", "candidate_claim_ids": ["candidate-claim"],
            "role_claim_ids": [f"role-claim-{index}"], "policy_version": "matching_weight_v1",
        }
        gap = GapItem(
            gap_id=f"gap-{index}", gap_type=gap_type, capability_id="programming.python",
            summary="Python gap", severity=severity, reason_code="fixture",
            assessment_item_ids=["req-item-python"], candidate_claim_ids=["candidate-claim"],
            role_claim_ids=[f"role-claim-{index}"], confidence=1,
        )
        qualifications = []
        if unaddressable:
            qualifications = [{
                "assessment_item_id": f"qualification-{index}", "qualification_id": f"degree-{index}",
                "qualification_type": "degree", "operator": "equals", "required_value": "master",
                "candidate_value": "bachelor", "outcome": "failed", "reason_code": "degree_mismatch",
                "candidate_claim_ids": ["candidate-degree"], "role_claim_ids": ["role-degree"],
                "comparator_version": "qualification_v1",
            }]
        coverage = CoverageBreakdown(
            dimension="core_capability", total_weight=1.5, eligible_weight=1.5, covered_weight=0,
            uncertain_weight=0, coverage=0, uncovered_item_ids=["req-item-python"],
        ).model_dump(mode="json")
        assessment = GapAssessment(
            assessment_id=f"gap-assessment-{index}", schema_version="v0.6", input_set_id="matching-input",
            candidate_profile_snapshot_id="candidate-s1", career_intent_snapshot_id="intent-s1",
            job_instance_profile_snapshot_id=job_id, role_profile_snapshot_id=job_id,
            hard_constraint_status="failed" if unaddressable else hard_status,
            qualification_assessments=qualifications, requirement_assessments=[requirement_item],
            core_coverage=coverage, bonus_coverage={"coverage": None}, gaps=[gap], status="current",
            supporting_claim_ids=["candidate-claim", f"role-claim-{index}"], matching_policy_version="matching_v1",
        )
        gaps.append(assessment)
        entries.append(ComparisonEntry(
            job_instance_profile_snapshot_id=job_id, gap_assessment_id=assessment.assessment_id,
            recommended_tier="blocked" if unaddressable else "review_first", hard_rank=2 if unaddressable else 0,
            blocking_preference_conflict_count=0, core_coverage=0, uncertainty_weight=0,
            stable_tie_breaker=job_id,
        ))
    matching_input = MatchingInputSet(
        input_set_id="matching-input", user_id=user_id, candidate_profile_snapshot_id="candidate-s1",
        career_intent_snapshot_id="intent-s1", job_instance_profile_snapshot_ids=job_ids,
    )
    matching.save("matching_input", matching_input, owner_id=user_id)
    for assessment in gaps:
        matching.save("gap_assessment", assessment, owner_id=user_id)
    comparison = ComparisonSet(
        comparison_set_id="comparison-1", input_set_id=matching_input.input_set_id,
        entries=entries, canonical_hash="sha256:comparison", status="current",
    )
    matching.save("comparison", comparison, owner_id=user_id)
    decisions = []
    for index, job_id in enumerate(job_ids, 1):
        decision = TargetDecision(
            decision_id=f"decision-{index}", user_id=user_id, comparison_set_id="comparison-1",
            job_instance_profile_snapshot_id=job_id, status="selected", created_from_response_id="selected-response",
        )
        matching.save("target_decision", decision, owner_id=user_id)
        decisions.append(decision)
    constraints = PreparationConstraints(
        horizon_start=date.today(), horizon_end=date.today() + timedelta(days=14),
        weekly_hours=weekly_hours, daily_max_hours=daily_max_hours, session_minutes=60,
    )
    constraints = preparation.save("constraints", constraints, owner_id=user_id)
    return {
        "profiles": profiles, "matching": matching, "preparation": preparation,
        "candidate_snapshot": candidate_snapshot, "intent_snapshot": intent_snapshot,
        "job_ids": job_ids, "gaps": gaps, "comparison": comparison, "decisions": decisions,
        "constraints": constraints,
    }


def build_v07_plan(data):
    service = PreparationService(
        profile_repository=data["profiles"], matching_repository=data["matching"],
        preparation_repository=data["preparation"],
    )
    loaded = service.load_and_validate_input(
        user_id="owner", target_decision_ids=[item.decision_id for item in data["decisions"]],
        candidate_snapshot_id="candidate-s1", intent_snapshot_id="intent-s1", comparison_set_id="comparison-1",
        gap_assessment_ids=[item.assessment_id for item in data["gaps"]], job_snapshot_ids=data["job_ids"],
        family_snapshot_ids=[], constraints_id=data["constraints"].constraints_id,
    )
    plan, objectives, activities, factors = service.build_plan(
        input_set=loaded[0], assessments=loaded[1], roles=loaded[2], constraints=loaded[3],
    )
    return plan, objectives, activities, factors
