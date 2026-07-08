"""Goal and plan schemas for the v0.1 runtime."""

from typing import Any

from pydantic import BaseModel


class ParsedGoal(BaseModel):
    role_query: str
    city: str
    graduation_year: str
    raw_text: str


class PlanTask(BaseModel):
    task_id: str
    tool_name: str
    args: dict[str, Any]
    reason: str
