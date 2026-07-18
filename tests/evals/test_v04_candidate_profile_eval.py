from campus_job_agent.evals import (
    CandidateProfileEvalCase,
    evaluate_candidate_profile,
)


def test_v04_fixed_fixture_metrics_meet_acceptance_targets() -> None:
    responsibility_gap = "gap:experience.project.responsibility"
    cases = [
        CandidateProfileEvalCase(
            name="candidate_sufficient",
            predicted_action="complete",
            gold_action="complete",
            factual_field_count=8,
            supported_factual_field_count=8,
        ),
        CandidateProfileEvalCase(
            name="candidate_missing_responsibility",
            predicted_action="ask_user",
            gold_action="ask_user",
            predicted_gap_ids=[responsibility_gap],
            gold_high_value_gap_ids=[responsibility_gap],
            question_count=1,
            actionable_question_count=1,
            interrupt_expected=True,
            interrupt_resumed=True,
        ),
        CandidateProfileEvalCase(
            name="candidate_unprocessed_material",
            predicted_action="read_more",
            gold_action="read_more",
        ),
        CandidateProfileEvalCase(
            name="candidate_scanned_pdf",
            predicted_action="request_more_materials",
            gold_action="request_more_materials",
            interrupt_expected=True,
            interrupt_resumed=True,
        ),
        CandidateProfileEvalCase(
            name="candidate_conflicting_claims",
            predicted_action="ask_user",
            gold_action="ask_user",
            question_count=1,
            actionable_question_count=1,
        ),
        CandidateProfileEvalCase(
            name="candidate_user_correction",
            predicted_action="complete",
            gold_action="complete",
            correction_expected=True,
            correction_traced=True,
            factual_field_count=1,
            supported_factual_field_count=1,
        ),
        CandidateProfileEvalCase(
            name="candidate_user_skip",
            predicted_action="finalize_with_unknowns",
            gold_action="finalize_with_unknowns",
        ),
        CandidateProfileEvalCase(
            name="candidate_budget_exhausted",
            predicted_action="finalize_with_unknowns",
            gold_action="finalize_with_unknowns",
            max_loop_termination_expected=True,
            max_loop_terminated=True,
        ),
        CandidateProfileEvalCase(
            name="candidate_resume_duplicate",
            predicted_action="complete",
            gold_action="complete",
            resume_idempotency_violations=0,
        ),
        CandidateProfileEvalCase(
            name="candidate_checkpoint_restart",
            predicted_action="complete",
            gold_action="complete",
            interrupt_expected=True,
            interrupt_resumed=True,
            checkpoint_recovery_expected=True,
            checkpoint_recovered=True,
        ),
    ]
    report = evaluate_candidate_profile(cases)
    assert report.case_count == 10
    assert report.candidate_route_accuracy == 1.0
    assert report.high_value_gap_recall == 1.0
    assert report.question_actionability_rate == 1.0
    assert report.redundant_question_rate == 0.0
    assert report.interrupt_resume_success_rate == 1.0
    assert report.checkpoint_recovery_rate == 1.0
    assert report.profile_evidence_coverage_rate == 1.0
    assert report.profile_correction_trace_rate == 1.0
    assert report.resume_idempotency_violation_count == 0
    assert report.max_loop_termination_rate == 1.0
