"""LangChain-backed provider integration with capability-aware structured output."""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlparse

from langchain_core.messages import AIMessage, BaseMessage
from pydantic import BaseModel, ValidationError

from campus_job_agent.llm.base import LLMConfigError, LLMProviderError
from campus_job_agent.llm.mock import MockLLMProvider
from campus_job_agent.schemas import (
    LLMConfig,
    LLMRequest,
    LLMResponse,
    ModelCapabilities,
    ModelIntegration,
    StructuredOutputStrategy,
)


# Public injection points retained for provider-factory tests and custom
# integrations.  ``None`` means the optional provider SDK has not been loaded.
ChatDeepSeek: Any | None = None
ChatOpenAI: Any | None = None


def infer_model_integration(config: LLMConfig) -> ModelIntegration:
    """Resolve old profiles without mutating their persisted representation."""

    if config.provider == "mock":
        return "mock"
    if config.integration is not None:
        return config.integration
    host = urlparse(config.base_url or "").hostname or ""
    if host == "api.deepseek.com" or config.model.lower().startswith("deepseek-"):
        return "deepseek"
    return "openai_compatible"


def infer_model_capabilities(
    config: LLMConfig,
    integration: ModelIntegration | None = None,
) -> ModelCapabilities:
    """Return explicit capabilities or conservative preset knowledge."""

    resolved = integration or infer_model_integration(config)
    if config.model_capabilities is not None:
        explicit = config.model_capabilities
        if explicit.integration != resolved or explicit.model != config.model:
            raise LLMConfigError(
                "model capabilities must match the configured integration and model"
            )
        return explicit
    if resolved == "mock":
        return ModelCapabilities(
            integration="mock",
            model=config.model,
            json_mode=True,
            source="preset",
        )
    if resolved == "deepseek":
        is_reasoner = "reasoner" in config.model.lower()
        return ModelCapabilities(
            integration="deepseek",
            model=config.model,
            json_mode=not is_reasoner,
            tool_calling=not is_reasoner,
            strict_tool_calling=False,
            provider_native_json_schema=False,
            simultaneous_tools_and_structured_output=False,
            requires_thinking_disabled_for_structured_output=True,
            source="preset",
        )
    return ModelCapabilities(
        integration="openai_compatible",
        model=config.model,
        json_mode=True,
        tool_calling=False,
        strict_tool_calling=False,
        provider_native_json_schema=False,
        simultaneous_tools_and_structured_output=False,
        source="preset",
    )


def resolve_structured_output_strategy(
    requested: StructuredOutputStrategy,
    capabilities: ModelCapabilities,
) -> str:
    supported = {
        "provider_native_json_schema": capabilities.provider_native_json_schema,
        "tool_calling": capabilities.tool_calling,
        "json_mode": capabilities.json_mode,
    }
    if requested == "auto":
        for candidate in (
            "provider_native_json_schema",
            "tool_calling",
            "json_mode",
        ):
            if supported[candidate]:
                return candidate
        raise ValueError(
            "unsupported_capability: model exposes no supported structured-output strategy"
        )
    if not supported[requested]:
        raise ValueError(
            f"unsupported_capability: {requested} is not supported by "
            f"{capabilities.integration}/{capabilities.model}"
        )
    return requested


class LangChainChatProvider:
    """Project provider protocol implemented through LangChain chat models."""

    def __init__(
        self,
        *,
        chat_model: Any,
        integration: ModelIntegration,
        model: str,
        capabilities: ModelCapabilities,
    ) -> None:
        self.chat_model = chat_model
        self.integration = integration
        self.model = model
        self.capabilities = capabilities
        self.name = f"langchain_{integration}"

    def generate(self, request: LLMRequest) -> LLMResponse:
        try:
            message = self.chat_model.invoke(request.messages)
        except Exception as exc:
            raise _provider_error(exc) from exc
        return LLMResponse(
            text=_message_text(message),
            provider=self.name,
            model=self.model,
            usage=_usage(message),
            raw_metadata=_safe_metadata(message),
        )

    def generate_structured(
        self,
        request: LLMRequest,
        output_model: type[BaseModel],
        *,
        requested_strategy: StructuredOutputStrategy = "auto",
    ) -> LLMResponse:
        try:
            selected = resolve_structured_output_strategy(
                requested_strategy, self.capabilities
            )
        except ValueError as exc:
            raise LLMProviderError(
                str(exc), error_type="unsupported_capability", retryable=False
            ) from exc
        candidates = [selected]
        if requested_strategy == "auto":
            ordered = (
                "provider_native_json_schema", "tool_calling", "json_mode"
            )
            candidates = [
                strategy for strategy in ordered
                if getattr(self.capabilities, {
                    "provider_native_json_schema": "provider_native_json_schema",
                    "tool_calling": "tool_calling",
                    "json_mode": "json_mode",
                }[strategy])
            ]
        first_error: LLMProviderError | None = None
        fallback_reason: str | None = None
        for effective in candidates:
            try:
                validated, raw = self._invoke_structured(
                    request, output_model, effective
                )
                break
            except LLMProviderError as exc:
                if requested_strategy != "auto" or effective == candidates[-1]:
                    raise
                first_error = first_error or exc
                fallback_reason = (
                    f"{effective}:{exc.error_type}"
                )
        else:  # pragma: no cover - candidates are non-empty after resolution
            raise first_error or LLMProviderError("structured output failed")
        parsed_json = validated.model_dump(mode="json")
        metadata = _safe_metadata(raw)
        tool_call_ids = _tool_call_ids(raw)
        if tool_call_ids:
            metadata["tool_call_ids"] = tool_call_ids
        return LLMResponse(
            text=json.dumps(parsed_json, ensure_ascii=False),
            provider=self.name,
            model=self.model,
            usage=_usage(raw),
            raw_metadata=metadata,
            parsed_json=parsed_json,
            requested_strategy=requested_strategy,
            effective_strategy=effective,
            fallback_reason=fallback_reason,
        )

    def _invoke_structured(
        self,
        request: LLMRequest,
        output_model: type[BaseModel],
        effective: str,
    ) -> tuple[BaseModel, Any]:
        method = {
            "provider_native_json_schema": "json_schema",
            "tool_calling": "function_calling",
            "json_mode": "json_mode",
        }[effective]
        strict = True if (
            effective == "provider_native_json_schema"
            or (effective == "tool_calling" and self.capabilities.strict_tool_calling)
        ) else None
        chat_model = self.chat_model
        if self.capabilities.requires_thinking_disabled_for_structured_output:
            model_copy = getattr(chat_model, "model_copy", None)
            if callable(model_copy):
                existing = dict(getattr(chat_model, "extra_body", None) or {})
                existing["thinking"] = {"type": "disabled"}
                chat_model = model_copy(update={"extra_body": existing})
        try:
            runnable = chat_model.with_structured_output(
                output_model, method=method, include_raw=True, strict=strict
            )
            result = runnable.invoke(request.messages)
            parsed = result.get("parsed") if isinstance(result, dict) else result
            parsing_error = result.get("parsing_error") if isinstance(result, dict) else None
            raw = result.get("raw") if isinstance(result, dict) else None
            if parsing_error is not None:
                raise LLMProviderError(
                    f"schema_validation_error: {parsing_error}",
                    error_type="schema_validation_error",
                    retryable=False,
                )
            validated = (
                parsed if isinstance(parsed, output_model)
                else output_model.model_validate(parsed)
            )
            return validated, raw
        except LLMProviderError:
            raise
        except ValidationError as exc:
            raise LLMProviderError(
                f"schema_validation_error: {exc.errors()}",
                error_type="schema_validation_error",
                retryable=False,
            ) from exc
        except Exception as exc:
            raise _provider_error(exc) from exc


def build_llm_provider(
    config: LLMConfig,
    *,
    chat_model: Any | None = None,
) -> MockLLMProvider | LangChainChatProvider:
    global ChatDeepSeek, ChatOpenAI

    integration = infer_model_integration(config)
    if integration == "mock":
        return MockLLMProvider(config.mock_mode)
    if not config.api_key or not config.base_url or not config.model:
        raise LLMConfigError("configured model provider is incomplete")
    capabilities = infer_model_capabilities(config, integration)
    if chat_model is None:
        common = {
            "model": config.model,
            "api_key": config.api_key,
            "base_url": config.base_url,
            "temperature": config.temperature,
            "timeout": config.timeout_seconds,
            "max_retries": config.max_retries,
        }
        if integration == "deepseek":
            # Keep provider SDK imports off the CLI startup path.  Most local
            # commands (and the mock test runtime) never construct a network
            # model, while eagerly importing both integrations loads hundreds
            # of OpenAI schema modules in every short-lived CLI process.
            if ChatDeepSeek is None:
                from langchain_deepseek import ChatDeepSeek as _ChatDeepSeek

                ChatDeepSeek = _ChatDeepSeek

            chat_model = ChatDeepSeek(**common)
        else:
            if ChatOpenAI is None:
                from langchain_openai import ChatOpenAI as _ChatOpenAI

                ChatOpenAI = _ChatOpenAI

            chat_model = ChatOpenAI(**common)
    return LangChainChatProvider(
        chat_model=chat_model,
        integration=integration,
        model=config.model,
        capabilities=capabilities,
    )


def _message_text(message: Any) -> str:
    content = getattr(message, "content", message)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        return "".join(parts)
    return str(content)


def _usage(message: Any) -> dict[str, Any] | None:
    usage = getattr(message, "usage_metadata", None)
    return dict(usage) if isinstance(usage, dict) else None


def _safe_metadata(message: Any) -> dict[str, Any]:
    metadata = getattr(message, "response_metadata", None)
    if not isinstance(metadata, dict):
        return {}
    allowed = ("finish_reason", "model_name", "system_fingerprint")
    return {key: metadata[key] for key in allowed if key in metadata}


def _tool_call_ids(message: Any) -> list[str]:
    calls = getattr(message, "tool_calls", None)
    if not isinstance(calls, list):
        return []
    return [
        str(call["id"])
        for call in calls
        if isinstance(call, dict) and call.get("id")
    ]


def _provider_error(exc: Exception) -> LLMProviderError:
    if isinstance(exc, LLMProviderError):
        return exc
    status = getattr(exc, "status_code", None)
    response = getattr(exc, "response", None)
    status = status or getattr(response, "status_code", None)
    name = type(exc).__name__.lower()
    message = str(exc).lower()
    if status in {401, 403}:
        return LLMProviderError("provider authorization failed", error_type="auth_required")
    if status == 429:
        return LLMProviderError("provider rate limit exceeded", error_type="rate_limited", retryable=True)
    if isinstance(status, int) and status >= 500:
        return LLMProviderError("provider service unavailable", retryable=True)
    if "timeout" in name:
        return LLMProviderError("provider request timed out", error_type="network_timeout", retryable=True)
    if status == 400 and ("does not support" in message or "tool_choice" in message):
        return LLMProviderError(
            "provider rejected the requested model capability",
            error_type="unsupported_capability",
            retryable=False,
        )
    return LLMProviderError(f"provider request failed: {type(exc).__name__}")
