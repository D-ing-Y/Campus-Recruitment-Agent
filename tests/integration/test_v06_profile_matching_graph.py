from __future__ import annotations

from langgraph.checkpoint.memory import InMemorySaver
import pytest

from campus_job_agent.schemas import (
    CandidateProfile,
    CapabilityAssessment,
    CareerIntent,
    ComparisonSet,
    ComparisonReviewResponse,
    EducationRecord,
    GapAssessment,
    IntentConstraint,
    JobInstanceRoleProfile,
    MatchExplanation,
    ProfileSnapshot,
    Qualification,
    RebuildDirective,
    RoleRequirement,
    TargetDecision,
)
from campus_job_agent.storage import SQLiteRepository
from campus_job_agent.workflows.profile_matching import (
    ProfileMatchingGraphRuntime,
    ProfileMatchingWorkflowError,
    SQLiteMatchingRepository,
    create_profile_matching_state,
    open_sqlite_checkpointer,
)


def _profiles(repository: SQLiteRepository, *, stale=False, two_jobs=False):
    candidate = CandidateProfile(
        candidate_id="candidate:owner", schema_version="v0.4",
        education=[EducationRecord(
            institution="Example University", degree="硕士", major="计算机", graduation_year="2027",
            supporting_claim_ids=["c-education"], field_supporting_claim_ids={"graduation_year": ["c-grad"]},
        )],
        capabilities=[CapabilityAssessment(
            capability_id="programming.python", raw_label="Python", level="advanced",
            confidence=1, status="confirmed", supporting_claim_ids=["c-python"],
        )],
        supporting_claim_ids=[],
    )
    intent = CareerIntent(
        user_id="owner", schema_version="v0.6", target_roles=["AI Engineer"],
        target_role_families=["ai_engineering"], locations=["成都"], graduation_year="2027",
        recruitment_type="autumn_campus", constraints=[IntentConstraint(
            constraint_id="location", key="location", operator="in", value=["成都"],
            kind="hard", affects_search_scope=True, status="confirmed", source_ref="intent#/locations",
        )], confirmed=True,
    )
    role = JobInstanceRoleProfile(
        role_profile_id="role-model-1", job_cluster_id="cluster-1", role_title="AI Engineer",
        role_family="ai_engineering", company="Example", locations=["成都"],
        recruitment_type="autumn_campus", graduation_year="2027", source_status="included",
        qualifications=[Qualification(
            qualification_id="q-grad", qualification_type="graduation_year", operator="equals",
            value="2027", importance="hard", status="confirmed", confidence=1,
            supporting_claim_ids=["r-grad"],
        )],
        requirements=[RoleRequirement(
            requirement_id="req-python", category="core_capability", capability_id="programming.python",
            raw_label="Python", required_level="intermediate", importance="core", obligation="required",
            confidence=1, authority="primary", supporting_claim_ids=["r-python"],
        ), RoleRequirement(
            requirement_id="req-sql", category="core_capability", capability_id="database.sql",
            raw_label="SQL", required_level="intermediate", importance="core", obligation="required",
            confidence=1, authority="allowed", supporting_claim_ids=["r-sql"],
        )],
        bonus_items=[RoleRequirement(
            requirement_id="req-rag", category="bonus_capability", capability_id="ai.rag",
            raw_label="RAG", required_level="unknown", importance="bonus", obligation="preferred",
            confidence=1, authority="allowed", supporting_claim_ids=["r-rag"],
        )],
        supporting_claim_ids=["r-profile"], freshness={"status": "expired" if stale else "current"}, confidence=1,
    )
    snapshots = [
        ProfileSnapshot(snapshot_id="candidate-s1", subject_id="candidate:owner", profile_type="candidate", version=1, schema_version="v0.4", profile_data={**candidate.model_dump(mode="json"), "owner_id": "owner"}),
        ProfileSnapshot(snapshot_id="intent-s1", subject_id="intent:owner", profile_type="career_intent", version=1, schema_version="v0.6", profile_data={**intent.model_dump(mode="json"), "owner_id": "owner"}),
        ProfileSnapshot(snapshot_id="role-s1", subject_id="role_instance:cluster-1", profile_type="role", version=1, schema_version="v0.5", profile_data={**role.model_dump(mode="json"), "owner_id": "owner"}),
    ]
    if two_jobs:
        second = role.model_copy(update={"role_profile_id": "role-model-2", "job_cluster_id": "cluster-2", "company": "Another"})
        snapshots.append(ProfileSnapshot(snapshot_id="role-s2", subject_id="role_instance:cluster-2", profile_type="role", version=1, schema_version="v0.5", profile_data={**second.model_dump(mode="json"), "owner_id": "owner"}))
    for snapshot in snapshots:
        repository.save_profile(snapshot)
    return ["role-s1", "role-s2"] if two_jobs else ["role-s1"]


def _runtime(tmp_path, checkpointer, *, stale=False, two_jobs=False, provider=None):
    repository = SQLiteRepository(tmp_path / "domain.sqlite3")
    job_ids = _profiles(repository, stale=stale, two_jobs=two_jobs)
    matching = SQLiteMatchingRepository(tmp_path / "domain.sqlite3")
    runtime = ProfileMatchingGraphRuntime(
        evidence_repository=repository, profile_repository=repository,
        matching_repository=matching, checkpointer=checkpointer,
        explanation_provider=provider,
    )
    return runtime, repository, matching, job_ids


def _invoke(runtime, job_ids, *, thread="matching-thread", budgets=None, output_dir=None):
    return runtime.invoke(create_profile_matching_state(
        thread_id=thread, user_id="owner", candidate_profile_snapshot_id="candidate-s1",
        career_intent_snapshot_id="intent-s1", job_instance_profile_snapshot_ids=job_ids,
        budgets=budgets, output_dir=output_dir,
    ))


def _request(result):
    return result["__interrupt__"][0].value


def _response(request, action, response_id="response-1", **payload):
    return ComparisonReviewResponse(
        response_id=response_id, request_id=request["request_id"], thread_id=request["thread_id"],
        user_id=request["user_id"], action=action, **payload,
    )


def test_v04_candidate_and_v05_role_produce_comparison_and_interrupt(tmp_path) -> None:
    runtime, _, matching, job_ids = _runtime(tmp_path, InMemorySaver())
    interrupted = _invoke(runtime, job_ids, output_dir=str(tmp_path / "report"))
    request = _request(interrupted)
    assert interrupted["status"] == "interrupted"
    assert request["interaction_type"] == "review_comparison"
    assessment = matching.list("gap_assessment", GapAssessment, owner_id="owner")[0]
    assert assessment.hard_constraint_status == "passed"
    assert assessment.core_coverage["covered_weight"] == 1.5
    assert assessment.core_coverage["eligible_weight"] == 1.5
    assert assessment.core_coverage["uncertain_weight"] == 1.5
    assert {item.gap_type for item in assessment.gaps} == {"epistemic_uncertainty"}


@pytest.mark.parametrize(
    ("action", "status"),
    [("select_targets", "selected"), ("defer_targets", "deferred"), ("reject_targets", "rejected")],
)
def test_select_defer_reject_resume_and_replay_are_idempotent(tmp_path, action, status) -> None:
    runtime, _, matching, job_ids = _runtime(tmp_path, InMemorySaver())
    interrupted = _invoke(runtime, job_ids, thread=f"thread-{action}")
    request = _request(interrupted)
    response = _response(request, action, target_decisions=[{
        "job_instance_profile_snapshot_id": "role-s1", "status": status,
        "reason_codes": ["user_reviewed"],
    }])
    completed = runtime.resume(thread_id=request["thread_id"], response=response)
    assert completed["status"] == "completed"
    replay = runtime.resume(thread_id=request["thread_id"], response=response)
    assert replay["status"] == "completed"
    decisions = matching.list("target_decision", TargetDecision, owner_id="owner")
    assert len(decisions) == 1
    assert decisions[0].status == status


def test_candidate_revision_returns_cross_domain_directive(tmp_path) -> None:
    runtime, _, matching, job_ids = _runtime(tmp_path, InMemorySaver())
    interrupted = _invoke(runtime, job_ids)
    request = _request(interrupted)
    completed = runtime.resume(thread_id=request["thread_id"], response=_response(
        request, "revise_candidate", candidate_revision={"target": "capability.python", "reason_code": "add_evidence"},
    ))
    assert completed["status"] == "reroute_required"
    directive = matching.get(completed["rebuild_directive_id"], RebuildDirective, owner_id="owner")
    assert directive.directive_type == "candidate_profile_required"


def test_same_scope_intent_revision_rematches_without_role_research(tmp_path) -> None:
    runtime, _, matching, job_ids = _runtime(tmp_path, InMemorySaver())
    interrupted = _invoke(runtime, job_ids)
    request = _request(interrupted)
    rematched = runtime.resume(thread_id=request["thread_id"], response=_response(
        request, "revise_intent", intent_revision={"requested_patch": {"salary_min": 18000}},
    ))
    assert "__interrupt__" in rematched
    assert rematched["intent_impact_assessment"]["impact"] == "rematch_only"
    directives = matching.list("rebuild_directive", RebuildDirective, owner_id="owner")
    assert [item.directive_type for item in directives] == ["rematch_required"]


def test_search_scope_change_reroutes_role_research(tmp_path) -> None:
    runtime, _, matching, job_ids = _runtime(tmp_path, InMemorySaver())
    interrupted = _invoke(runtime, job_ids)
    request = _request(interrupted)
    completed = runtime.resume(thread_id=request["thread_id"], response=_response(
        request, "revise_intent", intent_revision={"requested_patch": {"locations": ["上海"]}},
    ))
    assert completed["status"] == "reroute_required"
    directive = matching.get(completed["rebuild_directive_id"], RebuildDirective, owner_id="owner")
    assert directive.directive_type == "role_research_required"
    assert directive.requested_scope["locations"] == ["上海"]


def test_stale_role_auto_returns_refresh_directive_without_decision_interrupt(tmp_path) -> None:
    runtime, _, matching, job_ids = _runtime(tmp_path, InMemorySaver(), stale=True)
    result = _invoke(runtime, job_ids)
    assert result["status"] == "reroute_required"
    assert "__interrupt__" not in result
    directive = matching.get(result["rebuild_directive_id"], RebuildDirective, owner_id="owner")
    assert directive.directive_type == "role_refresh_required"


def test_user_requested_role_refresh_returns_directive(tmp_path) -> None:
    runtime, _, matching, job_ids = _runtime(tmp_path, InMemorySaver())
    interrupted = _invoke(runtime, job_ids)
    request = _request(interrupted)
    completed = runtime.resume(thread_id=request["thread_id"], response=_response(
        request, "refresh_role", role_refresh_target_ids=["role-s1"],
    ))
    assert completed["status"] == "reroute_required"
    directive = matching.get(completed["rebuild_directive_id"], RebuildDirective, owner_id="owner")
    assert directive.directive_type == "role_refresh_required"
    assert directive.affected_job_profile_ids == ["role-s1"]


@pytest.mark.parametrize(("action", "expected"), [("confirm_and_finish", "completed"), ("cancel", "cancelled")])
def test_confirm_and_cancel_finish_without_target_decisions(tmp_path, action, expected) -> None:
    runtime, _, matching, job_ids = _runtime(tmp_path, InMemorySaver())
    interrupted = _invoke(runtime, job_ids, thread=f"thread-{action}")
    request = _request(interrupted)
    completed = runtime.resume(thread_id=request["thread_id"], response=_response(request, action))
    assert completed["status"] == expected
    assert matching.list("target_decision", TargetDecision, owner_id="owner") == []


def test_newer_input_snapshot_rejects_decision_and_marks_old_results_stale(tmp_path) -> None:
    runtime, profiles, matching, job_ids = _runtime(tmp_path, InMemorySaver())
    interrupted = _invoke(runtime, job_ids)
    request = _request(interrupted)
    old = profiles.get_profile("candidate-s1")
    profiles.save_profile(old.model_copy(update={"snapshot_id": "candidate-s2", "version": 2}))
    with pytest.raises(ProfileMatchingWorkflowError, match="comparison_stale"):
        runtime.resume(thread_id=request["thread_id"], response=_response(
            request, "select_targets", target_decisions=[{
                "job_instance_profile_snapshot_id": "role-s1", "status": "selected",
            }],
        ))
    assert matching.list("target_decision", TargetDecision, owner_id="owner") == []
    comparison = matching.get(interrupted["comparison_set_id"], ComparisonSet, owner_id="owner")
    assert comparison.status == "stale"


def test_snapshot_owner_mismatch_is_rejected_before_assessment_write(tmp_path) -> None:
    runtime, profiles, matching, job_ids = _runtime(tmp_path, InMemorySaver())
    candidate = profiles.get_profile("candidate-s1")
    profiles.save_profile(candidate.model_copy(update={
        "snapshot_id": "candidate-foreign", "subject_id": "candidate:foreign", "version": 1,
        "profile_data": {**candidate.profile_data, "owner_id": "foreign"},
    }))
    state = create_profile_matching_state(
        thread_id="owner-mismatch", user_id="owner", candidate_profile_snapshot_id="candidate-foreign",
        career_intent_snapshot_id="intent-s1", job_instance_profile_snapshot_ids=job_ids,
    )
    with pytest.raises(ProfileMatchingWorkflowError, match="snapshot_owner_mismatch"):
        runtime.invoke(state)
    assert matching.list("gap_assessment", GapAssessment, owner_id="owner") == []


def test_sqlite_checkpoint_restart_resumes_comparison_review(tmp_path) -> None:
    checkpoint = tmp_path / "checkpoints.sqlite3"
    with open_sqlite_checkpointer(checkpoint) as saver:
        runtime, _, _, job_ids = _runtime(tmp_path, saver)
        interrupted = _invoke(runtime, job_ids, thread="restart-thread")
        request = _request(interrupted)
    with open_sqlite_checkpointer(checkpoint) as saver:
        restarted, _, matching, _ = _runtime(tmp_path, saver)
        completed = restarted.resume(thread_id="restart-thread", response=_response(
            request, "select_targets", response_id="restart-response", target_decisions=[{
                "job_instance_profile_snapshot_id": "role-s1", "status": "selected",
            }],
        ))
        assert completed["status"] == "completed"
        assert len(matching.list("target_decision", TargetDecision, owner_id="owner")) == 1


class _MutatingProvider:
    def explain(self, payload):
        return MatchExplanation(
            explanation_id="invalid", comparison_set_id=payload["comparison_set_id"],
            job_explanations=[{
                "job_profile_id": payload["entries"][0]["job_instance_profile_snapshot_id"],
                "summary": "Offer概率为99%", "fact_ids": [next(iter(payload["fact_index"]))],
                "claim_ids": [], "suggested_actions": ["review"],
            }], warnings=["coverage_is_not_offer_probability"],
        ), []


def test_invalid_llm_fact_falls_back_without_changing_comparison(tmp_path) -> None:
    runtime, _, matching, job_ids = _runtime(tmp_path, InMemorySaver(), provider=_MutatingProvider())
    interrupted = _invoke(runtime, job_ids)
    assert _request(interrupted)["warnings"] == ["coverage_is_not_offer_probability"]
    assert any(item["error_type"] == "llm_fact_mutation" for item in interrupted["errors"])
    explanation = matching.list("match_explanation", MatchExplanation, owner_id="owner")[0]
    assert "99" not in explanation.job_explanations[0].summary


def test_multi_job_order_is_stable_across_input_order(tmp_path) -> None:
    runtime, profiles, matching, job_ids = _runtime(tmp_path, InMemorySaver(), two_jobs=True)
    first = _invoke(runtime, list(reversed(job_ids)), thread="order-one")
    comparison_one = matching.get(first["comparison_set_id"], ComparisonSet, owner_id="owner")
    runtime2 = ProfileMatchingGraphRuntime(
        evidence_repository=profiles, profile_repository=profiles,
        matching_repository=matching, checkpointer=InMemorySaver(),
    )
    second = _invoke(runtime2, job_ids, thread="order-two")
    comparison_two = matching.get(second["comparison_set_id"], type(comparison_one), owner_id="owner")
    assert [item.job_instance_profile_snapshot_id for item in comparison_one.entries] == [item.job_instance_profile_snapshot_id for item in comparison_two.entries]


def test_match_round_budget_terminates_rematch_loop(tmp_path) -> None:
    runtime, _, _, job_ids = _runtime(tmp_path, InMemorySaver())
    interrupted = _invoke(runtime, job_ids, budgets={"max_match_rounds": 1, "max_explanation_calls": 1, "max_decision_interrupts": 2, "max_targets": 20})
    request = _request(interrupted)
    result = runtime.resume(thread_id=request["thread_id"], response=_response(
        request, "revise_intent", intent_revision={"requested_patch": {"salary_min": 18000}},
    ))
    assert result["status"] == "completed_with_unknowns"
    assert "__interrupt__" not in result
