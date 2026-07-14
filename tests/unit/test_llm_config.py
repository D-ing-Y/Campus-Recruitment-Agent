import pytest

from campus_job_agent.llm import LLMConfigError, load_llm_config


def test_default_llm_config_is_mock(monkeypatch):
    monkeypatch.delenv("CAMPUS_AGENT_LLM_PROVIDER", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)

    config = load_llm_config()

    assert config.provider == "mock"
    assert config.model == "mock-goal-parser"


def test_openai_compatible_missing_config_raises_config_error(monkeypatch):
    monkeypatch.setenv("CAMPUS_AGENT_LLM_PROVIDER", "openai_compatible")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)

    with pytest.raises(LLMConfigError) as exc:
        load_llm_config()

    assert "OPENAI_API_KEY" in str(exc.value)
    assert "OPENAI_BASE_URL" in str(exc.value)
    assert "OPENAI_MODEL" in str(exc.value)


def test_llm_config_bool_envs(monkeypatch):
    monkeypatch.setenv("CAMPUS_AGENT_LLM_PROVIDER", "mock")
    monkeypatch.setenv("CAMPUS_AGENT_LLM_CACHE_ENABLED", "false")
    monkeypatch.setenv("CAMPUS_AGENT_LLM_FALLBACK_TO_RULE_PARSER", "true")

    config = load_llm_config()

    assert config.cache_enabled is False
    assert config.fallback_to_rule_parser is True
