"""LangGraph state contract and initialization helpers."""

import os
from pathlib import Path
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
    llm_calls: list[dict]
    errors: list[dict]
    report_path: str | None
    output_dir: str


def create_initial_state(user_input: str, *, data_root: str | Path | None = None) -> AgentState:
    run_id = str(uuid4())
    root = Path(data_root or os.getenv("CAMPUS_AGENT_DATA_ROOT") or "data").expanduser().resolve()
    return {
        "run_id": run_id,
        "user_input": user_input,
        "parsed_goal": {},
        "plan": [],
        "tool_results": [],
        "verification": {},
        "trace": [],
        "llm_calls": [],
        "errors": [],
        "report_path": None,
        "output_dir": str(root / "runs" / run_id),
    }
