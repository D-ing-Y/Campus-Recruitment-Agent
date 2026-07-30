"""Contracts for model, tool, and MCP integration boundaries."""

from __future__ import annotations

import re
from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, model_validator


ModelIntegration = Literal["mock", "deepseek", "openai_compatible"]
StructuredOutputStrategy = Literal[
    "auto",
    "provider_native_json_schema",
    "tool_calling",
    "json_mode",
]
EffectiveStructuredOutputStrategy = Literal[
    "mock",
    "provider_native_json_schema",
    "tool_calling",
    "json_mode",
]


class ModelCapabilities(BaseModel):
    """Verified or conservatively inferred capabilities of one model profile."""

    integration: ModelIntegration
    model: str
    json_mode: bool = False
    tool_calling: bool = False
    strict_tool_calling: bool = False
    provider_native_json_schema: bool = False
    simultaneous_tools_and_structured_output: bool = False
    requires_thinking_disabled_for_structured_output: bool = False
    source: Literal["preset", "provider_profile", "explicit", "probe"]
    schema_version: str = "v0.7.1"


_WIRE_TOOL_NAME = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class ToolSpec(BaseModel):
    """Explicit opt-in contract for exposing an internal tool to a model."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    name: str
    wire_name: str | None = None
    description: str
    args_schema: type[BaseModel] = Field(exclude=True)
    exposure: Literal["internal_only", "model_read", "model_action"] = "internal_only"
    side_effect: Literal[
        "none", "local_read", "local_write", "external_read", "external_write"
    ] = "none"
    requires_confirmation: bool = False
    source: str = "local"
    schema_version: str = "v0.7.1"

    @model_validator(mode="after")
    def validate_model_exposure(self) -> "ToolSpec":
        wire_name = self.wire_name or self.name
        if self.exposure != "internal_only" and not self.description.strip():
            raise ValueError("model-visible tools require a description")
        if self.exposure != "internal_only" and not _WIRE_TOOL_NAME.fullmatch(wire_name):
            raise ValueError(
                "model-visible tool wire_name must contain only letters, digits, "
                "underscore, or hyphen"
            )
        if (
            self.exposure == "model_action"
            or self.side_effect in {"local_write", "external_write"}
        ) and not self.requires_confirmation:
            raise ValueError("model actions and write tools require confirmation")
        return self

    @property
    def effective_wire_name(self) -> str:
        return self.wire_name or self.name


class MCPServerConfig(BaseModel):
    """Safe, allowlist-first connection contract for one MCP server."""

    server_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
    transport: Literal["stdio", "http", "streamable_http"]
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    url: str | None = None
    credential_ref: str | None = None
    allowed_tools: list[str] = Field(default_factory=list)
    enabled: bool = True
    timeout_seconds: float = Field(default=30.0, gt=0)

    @model_validator(mode="after")
    def validate_transport(self) -> "MCPServerConfig":
        if self.transport == "stdio":
            if not self.command:
                raise ValueError("stdio MCP server requires command")
            if not self.command.startswith("/"):
                raise ValueError("stdio MCP command must be an absolute path")
            if self.url is not None:
                raise ValueError("stdio MCP server must not define url")
            return self
        if not self.url:
            raise ValueError("HTTP MCP server requires url")
        parsed = urlparse(self.url)
        is_local = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        if parsed.scheme != "https" and not (parsed.scheme == "http" and is_local):
            raise ValueError("remote MCP URLs must use HTTPS")
        if self.command is not None or self.args:
            raise ValueError("HTTP MCP server must not define command or args")
        return self
