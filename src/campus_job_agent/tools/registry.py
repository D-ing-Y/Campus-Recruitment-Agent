"""Tool registry used by runtime nodes."""

from typing import Any

from langchain_core.tools import BaseTool, StructuredTool

from campus_job_agent.schemas import ToolResult, ToolSpec
from campus_job_agent.tools.base import Tool


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}
        self._specs: dict[str, ToolSpec] = {}

    def register(self, tool: Tool, *, spec: ToolSpec | None = None) -> None:
        if spec is not None and spec.name != tool.name:
            raise ValueError("ToolSpec name must match the internal tool name")
        self._tools[tool.name] = tool
        if spec is None:
            self._specs.pop(tool.name, None)
        else:
            self._specs[tool.name] = spec

    def get(self, tool_name: str) -> Tool | None:
        return self._tools.get(tool_name)

    def values(self) -> tuple[Tool, ...]:
        """Expose registered tools to production composition roots without mutation."""
        return tuple(self._tools.values())

    def model_tools(
        self, allowed_names: set[str] | None = None
    ) -> tuple[BaseTool, ...]:
        """Export only explicitly described model-visible tools."""

        exported: list[BaseTool] = []
        wire_names: set[str] = set()
        for name, tool in self._tools.items():
            spec = self._specs.get(name)
            if spec is None or spec.exposure == "internal_only":
                continue
            if allowed_names is not None and name not in allowed_names:
                continue
            wire_name = spec.effective_wire_name
            if wire_name in wire_names:
                raise ValueError(f"model tool name collision: {wire_name}")
            wire_names.add(wire_name)

            def invoke_tool(_tool: Tool = tool, **kwargs: Any) -> dict[str, Any]:
                return _tool.run(kwargs).model_dump(mode="json")

            exported.append(StructuredTool.from_function(
                func=invoke_tool,
                name=wire_name,
                description=spec.description,
                args_schema=spec.args_schema,
                metadata={
                    "internal_name": spec.name,
                    "source": spec.source,
                    "side_effect": spec.side_effect,
                    "requires_confirmation": spec.requires_confirmation,
                },
            ))
        return tuple(exported)

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
