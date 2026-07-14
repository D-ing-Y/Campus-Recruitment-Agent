import pytest

from campus_job_agent.llm import LLMCache, MockLLMProvider
from campus_job_agent.llm.structured import (
    StructuredOutputError,
    parse_search_goal_with_llm,
)
from campus_job_agent.schemas import LLMConfig


def _config(tmp_path):
    return LLMConfig(cache_dir=str(tmp_path))


def test_structured_output_parses_search_goal(tmp_path):
    provider = MockLLMProvider("valid_json")

    goal, calls = parse_search_goal_with_llm(
        "成都 AI Agent 2027 秋招",
        _config(tmp_path),
        provider,
        LLMCache(str(tmp_path)),
    )

    assert goal.role_query == "AI Agent"
    assert goal.recruitment_type == "autumn_campus"
    assert calls[0].status == "success"


def test_structured_output_retries_json_parse_error(tmp_path):
    provider = MockLLMProvider("invalid_json_then_valid")

    goal, calls = parse_search_goal_with_llm(
        "成都 AI Agent 2027 秋招",
        _config(tmp_path),
        provider,
        LLMCache(str(tmp_path)),
    )

    assert goal.city == "成都"
    assert calls[0].retry_count == 1
    assert provider.call_count == 2


def test_structured_output_retries_schema_validation_error(tmp_path):
    provider = MockLLMProvider("schema_error_then_valid")

    goal, calls = parse_search_goal_with_llm(
        "成都 AI Agent 2027 秋招",
        _config(tmp_path),
        provider,
        LLMCache(str(tmp_path)),
    )

    assert goal.graduation_year == "2027"
    assert calls[0].retry_count == 1


def test_structured_output_raises_after_retry_failure(tmp_path):
    provider = MockLLMProvider("always_invalid_json")

    with pytest.raises(StructuredOutputError) as exc:
        parse_search_goal_with_llm(
            "成都 AI Agent 2027 秋招",
            _config(tmp_path),
            provider,
            LLMCache(str(tmp_path)),
        )

    assert exc.value.error_type == "json_parse_error"
    assert exc.value.call_records[0].status == "failed"


def test_structured_output_cache_hit_does_not_call_provider(tmp_path):
    cache = LLMCache(str(tmp_path))
    config = _config(tmp_path)
    provider = MockLLMProvider("valid_json")

    parse_search_goal_with_llm("成都 AI Agent 2027 秋招", config, provider, cache)
    first_count = provider.call_count
    _, calls = parse_search_goal_with_llm("成都 AI Agent 2027 秋招", config, provider, cache)

    assert provider.call_count == first_count
    assert calls[0].cache_hit is True
