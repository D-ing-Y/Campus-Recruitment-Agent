"""Goal and plan schemas for the v0.1 runtime."""

from typing import Any, Literal

from pydantic import BaseModel, Field


class ParsedGoal(BaseModel):
    role_query: str
    city: str
    graduation_year: str
    raw_text: str


class SearchGoal(BaseModel):
    role_query: str
    city: str
    graduation_year: str
    recruitment_type: Literal[
        "autumn_campus",
        "spring_campus",
        "internship",
        "unknown",
    ] = "unknown"
    keywords: list[str] = Field(default_factory=list)
    raw_text: str
    companies: list[str] = Field(default_factory=list)
    industries: list[str] = Field(default_factory=list)
    locations: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    confidence: float | None = None
    warnings: list[str] = Field(default_factory=list)


class PlanTask(BaseModel):
    task_id: str
    tool_name: str
    args: dict[str, Any]
    reason: str
