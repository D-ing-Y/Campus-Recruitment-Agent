from __future__ import annotations

from typing import Any

import pytest
from langchain_core.messages import AIMessage
from pydantic import BaseModel

from campus_job_agent.llm.langchain_provider import (
    LangChainChatProvider,
    build_llm_provider,
    resolve_structured_output_strategy,
)
from campus_job_agent.llm.base import LLMProviderError
from campus_job_agent.llm.cache import LLMCache
from campus_job_agent.llm.structured import parse_structured_output
from campus_job_agent.schemas import LLMConfig, LLMRequest, ModelCapabilities


class Answer(BaseModel):
    value: int


class FakeStructuredRunnable:
    def __init__(self, result: dict[str, Any]) -> None:
        self.result = result

    def invoke(self, messages):
        return self.result


class FailingRunnable:
    def invoke(self, messages):
        raise RuntimeError("declared strategy rejected by fixture provider")


class FakeChatModel:
    def __init__(self) -> None:
        self.methods: list[str] = []
        self.copy_updates: list[dict[str, Any]] = []

    def model_copy(self, *, update):
        self.copy_updates.append(update)
        return self

    def invoke(self, messages):
        return AIMessage(
            content='{"value": 7}',
            usage_metadata={"input_tokens": 3, "output_tokens": 4, "total_tokens": 7},
            response_metadata={"finish_reason": "stop"},
        )

    def with_structured_output(self, schema, *, method, include_raw, strict=None):
        self.methods.append(method)
        return FakeStructuredRunnable({
            "raw": AIMessage(
                content="",
                tool_calls=[{"name": "Answer", "args": {"value": 7}, "id": "call-1"}],
            ),
            "parsed": Answer(value=7),
            "parsing_error": None,
        })


class NativeFallbackChatModel(FakeChatModel):
    def with_structured_output(self, schema, *, method, include_raw, strict=None):
        if method == "json_schema":
            self.methods.append(method)
            return FailingRunnable()
        return super().with_structured_output(
            schema, method=method, include_raw=include_raw, strict=strict
        )


class RecoveringStructuredRunnable:
    def __init__(self, model: "RecoveringChatModel") -> None:
        self.model = model

    def invoke(self, messages):
        self.model.invocations += 1
        if self.model.invocations == 1:
            return {
                "raw": AIMessage(
                    content="",
                    tool_calls=[{
                        "name": "Answer",
                        "args": {"value": "not-an-integer"},
                        "id": "call-invalid",
                    }],
                ),
                "parsed": None,
                "parsing_error": ValueError("value must be an integer"),
            }
        return {
            "raw": AIMessage(
                content="",
                tool_calls=[{
                    "name": "Answer", "args": {"value": 7}, "id": "call-valid",
                }],
            ),
            "parsed": Answer(value=7),
            "parsing_error": None,
        }


class RecoveringChatModel(FakeChatModel):
    def __init__(self) -> None:
        super().__init__()
        self.invocations = 0

    def with_structured_output(self, schema, *, method, include_raw, strict=None):
        self.methods.append(method)
        return RecoveringStructuredRunnable(self)


def _capabilities(**updates) -> ModelCapabilities:
    payload = {
        "integration": "deepseek",
        "model": "deepseek-chat",
        "json_mode": True,
        "tool_calling": True,
        "strict_tool_calling": False,
        "provider_native_json_schema": False,
        "simultaneous_tools_and_structured_output": False,
        "requires_thinking_disabled_for_structured_output": True,
        "source": "preset",
    }
    payload.update(updates)
    return ModelCapabilities.model_validate(payload)


def test_strategy_matrix_is_capability_driven() -> None:
    native = _capabilities(provider_native_json_schema=True)
    assert resolve_structured_output_strategy("auto", native) == "provider_native_json_schema"
    assert resolve_structured_output_strategy("auto", _capabilities()) == "tool_calling"
    assert resolve_structured_output_strategy(
        "auto", _capabilities(tool_calling=False)
    ) == "json_mode"
    with pytest.raises(ValueError, match="unsupported_capability"):
        resolve_structured_output_strategy(
            "auto", _capabilities(tool_calling=False, json_mode=False)
        )
    with pytest.raises(ValueError, match="unsupported_capability"):
        resolve_structured_output_strategy("provider_native_json_schema", _capabilities())


def test_langchain_provider_uses_tool_calling_and_preserves_safe_metadata() -> None:
    model = FakeChatModel()
    provider = LangChainChatProvider(
        chat_model=model,
        integration="deepseek",
        model="deepseek-chat",
        capabilities=_capabilities(),
    )
    response = provider.generate_structured(
        LLMRequest(messages=[{"role": "user", "content": "extract"}], model="deepseek-chat"),
        Answer,
        requested_strategy="auto",
    )

    assert response.parsed_json == {"value": 7}
    assert response.effective_strategy == "tool_calling"
    assert response.raw_metadata["tool_call_ids"] == ["call-1"]
    assert model.methods == ["function_calling"]
    assert model.copy_updates == [{
        "extra_body": {"thinking": {"type": "disabled"}}
    }]


def test_factory_routes_deepseek_and_reasoner_capabilities(monkeypatch) -> None:
    created: list[dict[str, Any]] = []

    class FakeDeepSeek:
        def __init__(self, **kwargs) -> None:
            created.append(kwargs)

    monkeypatch.setattr("campus_job_agent.llm.langchain_provider.ChatDeepSeek", FakeDeepSeek)
    provider = build_llm_provider(LLMConfig(
        provider="openai_compatible", integration="deepseek",
        base_url="https://api.deepseek.com", api_key="secret",
        model="deepseek-chat", timeout_seconds=90,
    ))
    assert provider.capabilities.tool_calling is True
    assert provider.capabilities.provider_native_json_schema is False
    assert created[0]["api_key"] == "secret"

    reasoner = build_llm_provider(LLMConfig(
        provider="openai_compatible", integration="deepseek",
        base_url="https://api.deepseek.com", api_key="secret",
        model="deepseek-reasoner",
    ))
    assert reasoner.capabilities.tool_calling is False
    assert reasoner.capabilities.json_mode is False


def test_auto_strategy_records_bounded_runtime_fallback() -> None:
    model = NativeFallbackChatModel()
    provider = LangChainChatProvider(
        chat_model=model,
        integration="deepseek",
        model="deepseek-chat",
        capabilities=_capabilities(provider_native_json_schema=True),
    )

    response = provider.generate_structured(
        LLMRequest(messages=[{"role": "user", "content": "extract"}], model="deepseek-chat"),
        Answer,
        requested_strategy="auto",
    )

    assert response.effective_strategy == "tool_calling"
    assert response.fallback_reason == "provider_native_json_schema:provider_error"
    assert model.methods == ["json_schema", "function_calling"]


def test_explicit_strategy_never_silently_falls_back() -> None:
    model = NativeFallbackChatModel()
    provider = LangChainChatProvider(
        chat_model=model,
        integration="deepseek",
        model="deepseek-chat",
        capabilities=_capabilities(provider_native_json_schema=True),
    )

    with pytest.raises(LLMProviderError):
        provider.generate_structured(
            LLMRequest(messages=[{"role": "user", "content": "extract"}], model="deepseek-chat"),
            Answer,
            requested_strategy="provider_native_json_schema",
        )
    assert model.methods == ["json_schema"]


def test_provider_schema_error_reaches_shared_bounded_retry(tmp_path) -> None:
    model = RecoveringChatModel()
    provider = LangChainChatProvider(
        chat_model=model,
        integration="deepseek",
        model="deepseek-chat",
        capabilities=_capabilities(),
    )
    config = LLMConfig(
        provider="openai_compatible",
        integration="deepseek",
        base_url="https://api.deepseek.com",
        api_key="synthetic-secret",
        model="deepseek-chat",
        cache_enabled=False,
        cache_dir=str(tmp_path),
        max_retries=1,
    )

    answer, calls = parse_structured_output(
        messages=[{"role": "user", "content": "return an integer"}],
        output_model=Answer,
        config=config,
        provider=provider,
        cache=LLMCache(str(tmp_path)),
        prompt_name="answer",
        prompt_version="answer_v1",
        schema_version="answer_v1",
        retry_builder=lambda previous, error: [{
            "role": "user",
            "content": f"Fix {previous}. Validation error: {error}",
        }],
    )

    assert answer.value == 7
    assert calls[0].retry_count == 1
    assert model.invocations == 2
    assert model.methods == ["function_calling", "function_calling"]
