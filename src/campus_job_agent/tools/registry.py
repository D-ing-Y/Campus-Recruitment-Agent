"""Tool registry used by runtime nodes."""

from typing import Any

from campus_job_agent.schemas import ToolResult
from campus_job_agent.tools.base import Tool


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def get(self, tool_name: str) -> Tool | None:
        return self._tools.get(tool_name)

    def run(self, tool_name: str, args: dict[str, Any]) -> ToolResult:
        tool = self.get(tool_name)
        if tool is None:
            return ToolResult(
                tool_name=tool_name,
                status="failed",
                records=[],
                evidence_ids=[],
                error=f"Tool not registered: {tool_name}",
                metadata={
                    "error_type": "validation_error",
                    "retryable": False,
                    "needs_user_action": False,
                },
            )

        try:
            return tool.run(args)
        except Exception as exc:
            return ToolResult(
                tool_name=tool_name,
                status="failed",
                records=[],
                evidence_ids=[],
                error=str(exc),
                metadata={
                    "error_type": "tool_retryable_error",
                    "retryable": True,
                    "needs_user_action": False,
                },
            )
