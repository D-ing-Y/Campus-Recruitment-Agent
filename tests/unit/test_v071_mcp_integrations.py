from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from campus_job_agent.integrations.mcp import MCPToolCatalog
from campus_job_agent.schemas import MCPServerConfig


FIXTURE_SERVER = Path(__file__).resolve().parents[1] / "fixtures" / "mcp_math_server.py"


def test_stdio_mcp_server_loads_only_allowlisted_tools() -> None:
    catalog = MCPToolCatalog([
        MCPServerConfig(
            server_id="math",
            transport="stdio",
            command=sys.executable,
            args=[str(FIXTURE_SERVER)],
            allowed_tools=["add"],
        )
    ])
    tools = asyncio.run(catalog.load_tools())
    assert [tool.name for tool in tools] == ["add"]
    result = asyncio.run(tools[0].ainvoke({"a": 2, "b": 5}))
    assert result[0]["type"] == "text"
    assert result[0]["text"] == "7"
    assert tools[0].metadata["source"] == "mcp:math"


def test_mcp_tool_name_collision_is_rejected() -> None:
    catalog = MCPToolCatalog([
        MCPServerConfig(
            server_id="math",
            transport="stdio",
            command=sys.executable,
            args=[str(FIXTURE_SERVER)],
            allowed_tools=["add"],
        )
    ], reserved_tool_names={"add"})
    with pytest.raises(ValueError, match="collision"):
        asyncio.run(catalog.load_tools())


def test_empty_mcp_allowlist_denies_all_tools() -> None:
    catalog = MCPToolCatalog([
        MCPServerConfig(
            server_id="math",
            transport="stdio",
            command=sys.executable,
            args=[str(FIXTURE_SERVER)],
            allowed_tools=[],
        )
    ])
    assert asyncio.run(catalog.load_tools()) == []


def test_unreachable_server_is_isolated_with_safe_diagnostic() -> None:
    catalog = MCPToolCatalog([
        MCPServerConfig(
            server_id="unreachable",
            transport="stdio",
            command="/definitely/missing/mcp-server",
            allowed_tools=["secret_tool"],
        ),
        MCPServerConfig(
            server_id="math",
            transport="stdio",
            command=sys.executable,
            args=[str(FIXTURE_SERVER)],
            allowed_tools=["add"],
        ),
    ])

    tools = asyncio.run(catalog.load_tools())

    assert [tool.name for tool in tools] == ["add"]
    assert catalog.diagnostics == [{
        "server_id": "unreachable",
        "error_type": "external_dependency",
        "exception_type": "FileNotFoundError",
        "retryable": True,
    }]
    assert "secret_tool" not in str(catalog.diagnostics)
