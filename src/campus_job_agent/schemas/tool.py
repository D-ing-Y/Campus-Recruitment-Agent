"""Tool result schema for the v0.1 runtime."""

from typing import Any, Literal

from pydantic import BaseModel, Field


class ToolResult(BaseModel):
    tool_name: str
    status: Literal["success", "failed"]
    records: list[dict[str, Any]]
    evidence_ids: list[str]
    error: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
