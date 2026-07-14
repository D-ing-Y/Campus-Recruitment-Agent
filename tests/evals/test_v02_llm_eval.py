import json
from pathlib import Path

import pytest

from campus_job_agent.agent import run_agent
from campus_job_agent.llm import LLMCache, MockLLMProvider
from campus_job_agent.llm.structured import (
    StructuredOutputError,
    parse_search_goal_with_llm,
)
from campus_job_agent.schemas import LLMConfig, SearchGoal


def test_v02_eval_rules(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CAMPUS_AGENT_LLM_PROVIDER", "mock")
    monkeypatch.setenv("CAMPUS_AGENT_LLM_CACHE_ENABLED", "true")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-secret-should-not-appear")

    first = run_agent("成都 AI Agent 2027 秋招")
    second = run_agent("成都 AI Agent 2027 秋招")

    llm_provider_configured = first["llm_calls"][0]["provider"] == "mock"
    structured_goal_valid = bool(SearchGoal.model_validate(first["parsed_goal"]))
    retry_attempted = _retry_attempted(tmp_path)
    json_parse_error_captured = _error_type_captured(tmp_path, "always_invalid_json")
    schema_validation_error_captured = _schema_error_captured(tmp_path)
    cache_hit_visible = second["llm_calls"][0]["cache_hit"] is True

    output_text = ""
    for path in Path("data").rglob("*"):
        if path.is_file():
            output_text += path.read_text(encoding="utf-8", errors="ignore")
    api_key_not_logged = "sk-secret-should-not-appear" not in output_text

    assert llm_provider_configured
    assert structured_goal_valid
    assert json_parse_error_captured
    assert schema_validation_error_captured
    assert retry_attempted
    assert cache_hit_visible
    assert api_key_not_logged


def _retry_attempted(tmp_path) -> bool:
    goal, calls = parse_search_goal_with_llm(
        "成都 AI Agent 2027 秋招",
        LLMConfig(cache_dir=str(tmp_path / "retry")),
        MockLLMProvider("invalid_json_then_valid"),
        LLMCache(str(tmp_path / "retry")),
    )
    return goal.role_query == "AI Agent" and calls[0].retry_count == 1


def _error_type_captured(tmp_path, mode: str) -> bool:
    with pytest.raises(StructuredOutputError) as exc:
        parse_search_goal_with_llm(
            "成都 AI Agent 2027 秋招",
            LLMConfig(cache_dir=str(tmp_path / mode)),
            MockLLMProvider(mode),
            LLMCache(str(tmp_path / mode)),
        )
    return exc.value.error_type == "json_parse_error"


def _schema_error_captured(tmp_path) -> bool:
    class AlwaysSchemaErrorProvider(MockLLMProvider):
        def generate(self, request):
            self.call_count += 1
            return self._response(json.dumps({"role_query": "AI Agent"}), request.model)

    with pytest.raises(StructuredOutputError) as exc:
        parse_search_goal_with_llm(
            "成都 AI Agent 2027 秋招",
            LLMConfig(cache_dir=str(tmp_path / "schema")),
            AlwaysSchemaErrorProvider("valid_json"),
            LLMCache(str(tmp_path / "schema")),
        )
    return exc.value.error_type == "schema_validation_error"
