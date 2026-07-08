"""Runtime result verification."""

from campus_job_agent.agent.state import AgentState
from campus_job_agent.schemas import VerificationResult


REQUIRED_TOOL_RESULT_FIELDS = {"tool_name", "status", "records", "error"}


def verify_state(state: AgentState) -> VerificationResult:
    parsed_goal = state.get("parsed_goal", {})
    plan = state.get("plan", [])
    tool_results = state.get("tool_results", [])

    checks = {
        "role_query_present": bool(parsed_goal.get("role_query")),
        "plan_non_empty": len(plan) > 0,
        "tool_results_non_empty": len(tool_results) > 0,
        "tool_result_fields_valid": all(
            REQUIRED_TOOL_RESULT_FIELDS.issubset(result.keys())
            for result in tool_results
        )
        if tool_results
        else False,
    }

    messages = [
        check_name
        for check_name, passed in checks.items()
        if not passed
    ]

    return VerificationResult(
        passed=all(checks.values()),
        checks=checks,
        messages=messages,
    )
