import json
from pathlib import Path

import pytest

from campus_job_agent.evals.preparation_feedback import COUNT_METRICS, RATE_METRICS, V07EvalCaseResult, aggregate_v07_metrics


GOLD = json.loads((Path(__file__).parents[1] / "fixtures" / "v07" / "gold.json").read_text(encoding="utf-8"))
CASE_IDS = [item["case_id"] for item in GOLD["cases"]]


def test_v07_gold_contains_all_documented_cases_once():
    assert len(CASE_IDS) == len(set(CASE_IDS)) == 21
    assert CASE_IDS == [
        "plan_selected_target_quick_evidence_win", "plan_core_capability_gap",
        "plan_unaddressable_hard_blocker", "plan_multi_target_transfer_value",
        "plan_bonus_deprioritized", "plan_capacity_insufficient_partial",
        "plan_dependency_cycle_rejected", "plan_stale_matching_input", "plan_no_selected_target",
        "plan_review_constraints_revision", "feedback_task_progress_only",
        "feedback_explicit_evaluator_candidate_signal", "feedback_rejection_without_diagnosis",
        "feedback_job_specific_hiring_signal", "feedback_single_event_no_family_mutation",
        "feedback_user_rejects_attribution", "feedback_profile_update_replan",
        "feedback_stale_plan_superseded", "feedback_duplicate_event_resume",
        "feedback_checkpoint_restart", "feedback_llm_causality_fallback",
    ]


@pytest.mark.parametrize("case", GOLD["cases"], ids=CASE_IDS)
def test_v07_fixed_case_has_complete_gold_and_is_accepted_by_metric_contract(case):
    result = V07EvalCaseResult(
        case_id=case["case_id"], checks={metric: True for metric in case.get("checks", [])},
        violation_counts={metric: 0 for metric in case.get("zero_counts", [])},
        expected_scope=case.get("scope"), predicted_scope=case.get("scope"),
    )
    metrics = aggregate_v07_metrics([result])
    assert metrics["passed_case_count"] == 1
    for metric in case.get("checks", []):
        assert metrics[metric] == 1.0
    for metric in case.get("zero_counts", []):
        assert metrics[metric] == 0


def test_v07_full_metric_aggregation_meets_offline_thresholds():
    results = [V07EvalCaseResult(
        case_id=case["case_id"], checks={metric: True for metric in case.get("checks", [])},
        violation_counts={metric: 0 for metric in case.get("zero_counts", [])},
        expected_scope=case.get("scope"), predicted_scope=case.get("scope"),
    ) for case in GOLD["cases"]]
    metrics = aggregate_v07_metrics(results)
    assert metrics["case_count"] == metrics["passed_case_count"] == 21
    assert all(metrics[item] in {1.0, None} for item in RATE_METRICS)
    assert all(metrics[item] == 0 for item in COUNT_METRICS)
    assert all(set(row) == {scope} and row[scope] >= 1
               for scope, row in metrics["feedback_scope_confusion_matrix"].items())
