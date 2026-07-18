import pytest
from pydantic import ValidationError

from campus_job_agent.schemas import (
    BudgetState,
    CounterState,
    HumanAnswer,
    HumanInteractionResponse,
    InformationGap,
    SufficiencyAssessment,
)
from campus_job_agent.workflows.candidate_profile import CandidateRoutePolicy
from campus_job_agent.workflows.candidate_profile.planner import (
    DeterministicQuestionPlanner,
)


def _gap(**updates) -> InformationGap:
    values = {
        "gap_id": "gap:responsibility",
        "target_path": "experiences[project].responsibilities",
        "category": "responsibility_boundary",
        "description": "Personal responsibility is unclear.",
        "importance": 0.9,
        "uncertainty": 0.8,
        "answerability": 0.9,
        "evidence_cost": 0.1,
        "information_value": 1.0,
        "preferred_action": "ask_user",
    }
    values.update(updates)
    return InformationGap(**values)


def _assessment(action: str = "ask_user", gaps=None) -> SufficiencyAssessment:
    return SufficiencyAssessment(
        candidate_id="candidate",
        is_sufficient=action == "complete",
        dimension_results={
            "education": "sufficient",
            "experience": "sufficient",
            "capability": "sufficient",
            "responsibility_boundary": "insufficient",
            "evidence_quality": "sufficient",
        },
        information_gaps=[_gap()] if gaps is None else gaps,
        recommended_action=action,
        reason="test",
        confidence=1,
    )


def test_information_value_is_owned_by_deterministic_code() -> None:
    gap = _gap(information_value=0.99)
    assert gap.information_value == pytest.approx(0.548)
    with pytest.raises(ValidationError):
        _gap(importance=1.1)


def test_human_response_action_payload_validation() -> None:
    with pytest.raises(ValidationError):
        HumanInteractionResponse(
            response_id="r",
            request_id="q",
            thread_id="t",
            user_id="u",
            action="answer",
        )
    response = HumanInteractionResponse(
        response_id="r",
        request_id="q",
        thread_id="t",
        user_id="u",
        action="answer",
        answers=[HumanAnswer(question_id="question", text="My responsibility")],
    )
    assert response.answers[0].text == "My responsibility"


def test_route_policy_priorities_and_hard_budget() -> None:
    policy = CandidateRoutePolicy()
    assessment = _assessment()
    budgets = BudgetState()
    assert (
        policy.decide(
            assessment=assessment,
            budgets=budgets,
            counters=CounterState(),
            pending_artifact_ids=["artifact"],
            skipped_gap_ids=[],
            asked_question_keys=[],
        )
        == "read_more"
    )
    assert (
        policy.decide(
            assessment=assessment,
            budgets=budgets,
            counters=CounterState(profile_rounds=3),
            pending_artifact_ids=[],
            skipped_gap_ids=[],
            asked_question_keys=[],
        )
        == "finalize_with_unknowns"
    )


def test_question_planner_filters_asked_and_skipped_gaps() -> None:
    planner = DeterministicQuestionPlanner()
    assessment = _assessment()
    plan, _ = planner.plan(
        assessment,
        max_questions=3,
        asked_question_keys=[],
        skipped_gap_ids=[],
    )
    assert len(plan.questions) == 1
    assert plan.questions[0].gap_id == "gap:responsibility"
    duplicate, _ = planner.plan(
        assessment,
        max_questions=3,
        asked_question_keys=["experiences.project.responsibilities"],
        skipped_gap_ids=[],
    )
    assert duplicate.questions == []
