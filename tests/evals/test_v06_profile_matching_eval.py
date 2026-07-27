import json
from pathlib import Path

from campus_job_agent.evals.profile_matching import ProfileMatchingEvalCase, evaluate_profile_matching


FIXTURE = Path(__file__).parents[1] / "fixtures" / "v06" / "gold.json"


def test_v06_fixed_dataset_contains_all_required_cases() -> None:
    cases = [ProfileMatchingEvalCase.model_validate(item) for item in json.loads(FIXTURE.read_text(encoding="utf-8"))]
    assert [item.name for item in cases] == [
        "match_hard_pass_partial_coverage", "match_hard_fail_with_evidence", "match_hard_unknown_not_fail",
        "match_capability_gap_confirmed", "match_evidence_gap_not_capability_gap",
        "match_unmapped_requirement_uncertainty", "match_negotiable_preference_conflict",
        "match_hard_preference_conflict", "match_same_scope_intent_rematch",
        "match_search_scope_change_role_research", "match_candidate_revision_reroute",
        "match_stale_role_refresh", "match_multi_job_stable_order", "match_decision_resume_duplicate",
        "match_checkpoint_restart", "match_llm_invalid_fact_fallback",
    ]


def test_v06_offline_eval_reaches_all_documented_thresholds() -> None:
    cases = [ProfileMatchingEvalCase.model_validate(item) for item in json.loads(FIXTURE.read_text(encoding="utf-8"))]
    report = evaluate_profile_matching(cases)
    assert report.case_count == 16
    assert report.hard_constraint_accuracy == 1
    assert report.coverage_calculation_accuracy == 1
    assert report.gap_label_accuracy == 1
    assert report.unknown_as_failure_count == 0
    assert report.assessment_evidence_trace_rate == 1
    assert report.deterministic_output_stability_rate == 1
    assert report.preference_update_isolation_rate == 1
    assert report.same_scope_update_no_research_rate == 1
    assert report.search_scope_change_reroute_accuracy == 1
    assert report.stale_assessment_invalidation_rate == 1
    assert report.decision_interrupt_resume_success_rate == 1
    assert report.decision_idempotency_violation_count == 0
    assert report.offer_probability_claim_count == 0
    assert report.llm_fact_mutation_accept_count == 0
    assert report.max_match_loop_termination_rate == 1
