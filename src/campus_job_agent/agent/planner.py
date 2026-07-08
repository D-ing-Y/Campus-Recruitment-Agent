"""Rule-based goal parsing and plan generation for v0.1."""

from campus_job_agent.schemas import ParsedGoal, PlanTask


def parse_goal_text(user_input: str) -> ParsedGoal:
    role_query = "unknown"
    if "AI Agent" in user_input:
        role_query = "AI Agent"
    elif "智能体" in user_input:
        role_query = "智能体"

    city = "成都" if "成都" in user_input else "unknown"
    graduation_year = "2027" if "2027" in user_input else "unknown"

    return ParsedGoal(
        role_query=role_query,
        city=city,
        graduation_year=graduation_year,
        raw_text=user_input,
    )


def create_plan(parsed_goal: ParsedGoal) -> list[PlanTask]:
    return [
        PlanTask(
            task_id="task_001",
            tool_name="mock_job_search",
            args={
                "role_query": parsed_goal.role_query,
                "city": parsed_goal.city,
                "graduation_year": parsed_goal.graduation_year,
            },
            reason="Use the v0.1 mock job search tool to produce structured job records.",
        )
    ]
