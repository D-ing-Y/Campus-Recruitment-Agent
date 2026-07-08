"""Tool protocol for the v0.1 runtime."""

from typing import Any, Protocol

from campus_job_agent.schemas import ToolResult


class Tool(Protocol):
    name: str

    def run(self, args: dict[str, Any]) -> ToolResult:
        ...
