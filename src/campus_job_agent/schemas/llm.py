"""LLM provider and observability schemas."""

from typing import Any, Literal

from pydantic import BaseModel, Field


LLMErrorType = Literal[
    "provider_error",
    "json_parse_error",
    "schema_validation_error",
    "cache_error",
    "config_error",
]


class LLMConfig(BaseModel):
    provider: Literal["mock", "openai_compatible"] = "mock"
    base_url: str | None = None
    api_key: str | None = None
    model: str = "mock-goal-parser"
    timeout_seconds: float = 30.0
    temperature: float = 0.0
    max_retries: int = 1
    cache_enabled: bool = True
    cache_dir: str = "data/cache/llm"
    fallback_to_rule_parser: bool = False
    mock_mode: str = "valid_json"


class LLMRequest(BaseModel):
    messages: list[dict[str, str]]
    model: str
    temperature: float = 0.0
    response_format: dict[str, str] | None = Field(
        default_factory=lambda: {"type": "json_object"}
    )
    timeout_seconds: float = 30.0


class LLMResponse(BaseModel):
    text: str
    provider: str
    model: str
    usage: dict[str, Any] | None = None
    raw_metadata: dict[str, Any] = Field(default_factory=dict)


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
