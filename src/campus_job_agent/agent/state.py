"""LangGraph state contract and initialization helpers."""

from typing import TypedDict
from uuid import uuid4


class AgentState(TypedDict, total=False):
    run_id: str
    user_input: str
    parsed_goal: dict
    plan: list[dict]
    tool_results: list[dict]
    verification: dict
    trace: list[dict]
    errors: list[dict]
    report_path: str | None
    output_dir: str


def create_initial_state(user_input: str) -> AgentState:
    run_id = str(uuid4())
    return {
        "run_id": run_id,
        "user_input": user_input,
        "parsed_goal": {},
        "plan": [],
        "tool_results": [],
        "verification": {},
        "trace": [],
        "errors": [],
        "report_path": None,
        "output_dir": f"data/runs/{run_id}",
    }
