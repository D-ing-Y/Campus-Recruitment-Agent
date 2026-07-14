import pytest
from pydantic import ValidationError

from campus_job_agent.schemas import SearchGoal


def test_search_goal_validates_required_v02_fields():
    goal = SearchGoal(
        role_query="AI Agent",
        city="成都",
        graduation_year="2027",
        recruitment_type="autumn_campus",
        keywords=["AI Agent"],
        raw_text="成都 AI Agent 2027 秋招",
    )

    assert goal.role_query == "AI Agent"
    assert goal.companies == []


def test_search_goal_requires_required_fields():
    with pytest.raises(ValidationError):
        SearchGoal(city="成都", graduation_year="2027", raw_text="x")


def test_search_goal_rejects_invalid_recruitment_type():
    with pytest.raises(ValidationError):
        SearchGoal(
            role_query="AI Agent",
            city="成都",
            graduation_year="2027",
            recruitment_type="winter_campus",
            keywords=[],
            raw_text="成都 AI Agent 2027 秋招",
        )
