import pytest

from campus_job_agent.llm import MockLLMProvider
from campus_job_agent.llm.base import LLMProviderError
from campus_job_agent.schemas import LLMRequest


def _request() -> LLMRequest:
    return LLMRequest(messages=[{"role": "user", "content": "x"}], model="mock")


def test_mock_provider_valid_json():
    provider = MockLLMProvider("valid_json")

    response = provider.generate(_request())

    assert response.provider == "mock"
    assert "AI Agent" in response.text


def test_mock_provider_invalid_json_then_valid():
    provider = MockLLMProvider("invalid_json_then_valid")

    assert provider.generate(_request()).text == "{not valid json"
    assert "AI Agent" in provider.generate(_request()).text


def test_mock_provider_schema_error_then_valid():
    provider = MockLLMProvider("schema_error_then_valid")

    assert provider.generate(_request()).text == '{"role_query": "AI Agent"}'
    assert "AI Agent" in provider.generate(_request()).text


def test_mock_provider_always_invalid_json():
    provider = MockLLMProvider("always_invalid_json")

    assert provider.generate(_request()).text == "{not valid json"
    assert provider.generate(_request()).text == "{not valid json"


def test_mock_provider_error():
    provider = MockLLMProvider("provider_error")

    with pytest.raises(LLMProviderError):
        provider.generate(_request())
