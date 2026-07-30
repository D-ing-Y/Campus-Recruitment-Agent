"""LLM provider and observability schemas."""

from typing import Any, Literal

from pydantic import BaseModel, Field

from campus_job_agent.schemas.integrations import (
    ModelCapabilities,
    ModelIntegration,
    StructuredOutputStrategy,
)


LLMErrorType = Literal[
    "provider_error",
    "network_timeout",
    "rate_limited",
    "auth_required",
    "json_parse_error",
    "schema_validation_error",
    "cache_error",
    "config_error",
    "unsupported_capability",
    "tool_input_error",
    "external_dependency",
    "authorization_required",
]


class LLMConfig(BaseModel):
    provider: Literal["mock", "openai_compatible"] = "mock"
    integration: ModelIntegration | None = None
    base_url: str | None = None
    api_key: str | None = None
    model: str = "mock-goal-parser"
    timeout_seconds: float = Field(default=30.0, gt=0)
    temperature: float = 0.0
    max_retries: int = 1
    cache_enabled: bool = True
    cache_dir: str = "data/cache/llm"
    fallback_to_rule_parser: bool = False
    mock_mode: str = "valid_json"
    structured_output_strategy: StructuredOutputStrategy = "auto"
    model_capabilities: ModelCapabilities | None = None


class LLMRequest(BaseModel):
    messages: list[dict[str, str]]
    model: str
    temperature: float = 0.0
    response_format: dict[str, str] | None = Field(
        default_factory=lambda: {"type": "json_object"}
    )
    timeout_seconds: float = Field(default=30.0, gt=0)


class LLMResponse(BaseModel):
    text: str
    provider: str
    model: str
    usage: dict[str, Any] | None = None
    raw_metadata: dict[str, Any] = Field(default_factory=dict)
    parsed_json: dict[str, Any] | None = None
    requested_strategy: StructuredOutputStrategy | None = None
    effective_strategy: str | None = None
    fallback_reason: str | None = None


class LLMCallRecord(BaseModel):
    provider: str
    model: str
    prompt_name: str
    prompt_version: str
    schema_version: str
    cache_key: str
    cache_hit: bool
    retry_count: int
    duration_ms: int
    status: Literal["success", "failed"]
    error_type: LLMErrorType | None = None
    error: str | None = None
    usage: dict[str, Any] | None = None
    integration: ModelIntegration | None = None
    requested_strategy: StructuredOutputStrategy | None = None
    effective_strategy: str | None = None
    fallback_reason: str | None = None
    capabilities: dict[str, Any] | None = None
