from campus_job_agent.agent.planner import parse_goal_text


def test_parse_goal_success():
    parsed = parse_goal_text("成都 AI Agent 2027 秋招")

    assert parsed.role_query == "AI Agent"
    assert parsed.city == "成都"
    assert parsed.graduation_year == "2027"
    assert parsed.raw_text == "成都 AI Agent 2027 秋招"


def test_parse_goal_unknown_fields():
    parsed = parse_goal_text("杭州 后端 暑期实习")

    assert parsed.role_query == "unknown"
    assert parsed.city == "unknown"
    assert parsed.graduation_year == "unknown"


def test_parse_goal_chinese_agent_role():
    parsed = parse_goal_text("成都 智能体 2027 秋招")

    assert parsed.role_query == "智能体"
