from campus_job_agent.agent.planner import create_plan, parse_goal_text


def test_create_plan_contains_mock_job_search():
    parsed = parse_goal_text("成都 AI Agent 2027 秋招")
    plan = create_plan(parsed)

    assert len(plan) == 1
    assert plan[0].tool_name == "mock_job_search"
    assert plan[0].args == {
        "role_query": "AI Agent",
        "city": "成都",
        "graduation_year": "2027",
    }
