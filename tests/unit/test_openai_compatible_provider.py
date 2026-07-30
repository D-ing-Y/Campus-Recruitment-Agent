from __future__ import annotations

import httpx
import pytest

from campus_job_agent.llm import LLMProviderError, OpenAICompatibleProvider
from campus_job_agent.schemas import LLMConfig, LLMRequest


def test_read_timeout_is_classified_as_retryable_network_timeout(monkeypatch) -> None:
    request = httpx.Request("POST", "https://llm.example/chat/completions")

    def timeout(*args, **kwargs):
        raise httpx.ReadTimeout("read timed out", request=request)

    monkeypatch.setattr(httpx, "post", timeout)
    provider = OpenAICompatibleProvider(LLMConfig(
        provider="openai_compatible", base_url="https://llm.example",
        api_key="secret", model="example", timeout_seconds=90,
    ))

    with pytest.raises(LLMProviderError) as captured:
        provider.generate(LLMRequest(
            messages=[{"role": "user", "content": "health"}],
            model="example", timeout_seconds=90,
        ))

    assert captured.value.error_type == "network_timeout"
    assert captured.value.retryable is True
    assert "secret" not in str(captured.value)
