from __future__ import annotations

import pytest
from pydantic import BaseModel

from campus_job_agent.schemas import ToolResult, ToolSpec
from campus_job_agent.tools import ToolRegistry


class EchoArgs(BaseModel):
    value: int


class EchoTool:
    name = "test.echo"

    def run(self, args):
        return ToolResult(
            tool_name=self.name,
            status="success",
            records=[{"value": args["value"]}],
            evidence_ids=[],
        )


def test_legacy_tool_is_internal_only_until_explicitly_described() -> None:
    registry = ToolRegistry()
    registry.register(EchoTool())
    assert registry.model_tools() == ()


def test_tool_spec_exports_validated_langchain_structured_tool() -> None:
    registry = ToolRegistry()
    registry.register(EchoTool(), spec=ToolSpec(
        name="test.echo",
        wire_name="test_echo",
        description="Echo one validated integer for an offline contract test.",
        args_schema=EchoArgs,
        exposure="model_read",
        side_effect="none",
    ))

    tool = registry.model_tools()[0]
    assert tool.name == "test_echo"
    assert tool.invoke({"value": 3})["records"] == [{"value": 3}]
    with pytest.raises(Exception):
        tool.invoke({"value": "not-an-integer"})


def test_write_tool_requires_confirmation_before_registration() -> None:
    registry = ToolRegistry()
    with pytest.raises(ValueError, match="confirmation"):
        registry.register(EchoTool(), spec=ToolSpec(
            name="test.echo",
            wire_name="test_echo",
            description="Pretend to write an external system.",
            args_schema=EchoArgs,
            exposure="model_action",
            side_effect="external_write",
            requires_confirmation=False,
        ))
