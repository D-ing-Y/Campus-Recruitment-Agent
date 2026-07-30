"""Allowlist-first MCP tool discovery through LangChain adapters."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from langchain_core.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient

from campus_job_agent.schemas import MCPServerConfig


CredentialHeaderResolver = Callable[[str], dict[str, str]]


class MCPToolCatalog:
    """Load configured MCP tools without making arbitrary tools model-visible."""

    def __init__(
        self,
        servers: list[MCPServerConfig],
        *,
        reserved_tool_names: set[str] | None = None,
        credential_resolver: CredentialHeaderResolver | None = None,
        strict: bool = False,
    ) -> None:
        self.servers = tuple(servers)
        self.reserved_tool_names = set(reserved_tool_names or set())
        self.credential_resolver = credential_resolver
        self.strict = strict
        self.diagnostics: list[dict[str, Any]] = []

    async def load_tools(self) -> list[BaseTool]:
        self.diagnostics = []
        loaded: list[BaseTool] = []
        names = set(self.reserved_tool_names)
        for server in self.servers:
            if not server.enabled or not server.allowed_tools:
                continue
            connection = self._connection(server)
            client = MultiServerMCPClient({server.server_id: connection})
            try:
                available = await client.get_tools(server_name=server.server_id)
            except Exception as exc:
                diagnostic = {
                    "server_id": server.server_id,
                    "error_type": "external_dependency",
                    "exception_type": type(exc).__name__,
                    "retryable": True,
                }
                self.diagnostics.append(diagnostic)
                if self.strict:
                    raise ValueError(
                        f"MCP server unavailable: {server.server_id}"
                    ) from exc
                continue
            by_name = {tool.name: tool for tool in available}
            missing = sorted(set(server.allowed_tools) - set(by_name))
            if missing:
                raise ValueError(
                    f"MCP allowlist references unavailable tools on {server.server_id}: "
                    f"{', '.join(missing)}"
                )
            for tool_name in server.allowed_tools:
                if tool_name in names:
                    raise ValueError(f"MCP tool name collision: {tool_name}")
                names.add(tool_name)
                tool = by_name[tool_name]
                metadata = dict(tool.metadata or {})
                metadata.update({
                    "source": f"mcp:{server.server_id}",
                    "server_id": server.server_id,
                    "transport": server.transport,
                })
                loaded.append(tool.model_copy(update={"metadata": metadata}))
        return loaded

    def _connection(self, server: MCPServerConfig) -> dict[str, Any]:
        if server.transport == "stdio":
            return {
                "transport": "stdio",
                "command": str(server.command),
                "args": list(server.args),
            }
        headers: dict[str, str] | None = None
        if server.credential_ref:
            if self.credential_resolver is None:
                raise ValueError(
                    f"authorization_required: no credential resolver for {server.server_id}"
                )
            headers = self.credential_resolver(server.credential_ref)
        return {
            "transport": "streamable_http",
            "url": str(server.url),
            "headers": headers,
            "timeout": server.timeout_seconds,
        }
