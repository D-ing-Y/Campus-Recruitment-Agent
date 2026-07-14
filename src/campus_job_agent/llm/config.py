"""LLM configuration loading."""

import os

from campus_job_agent.llm.base import LLMConfigError
from campus_job_agent.schemas import LLMConfig


def load_llm_config() -> LLMConfig:
    provider = os.getenv("CAMPUS_AGENT_LLM_PROVIDER", "mock")
    cache_enabled = _parse_bool(os.getenv("CAMPUS_AGENT_LLM_CACHE_ENABLED"), True)
    fallback = _parse_bool(
        os.getenv("CAMPUS_AGENT_LLM_FALLBACK_TO_RULE_PARSER"),
        False,
    )

    model = os.getenv("OPENAI_MODEL") or (
        "mock-goal-parser" if provider == "mock" else ""
    )
    config = LLMConfig(
        provider=provider,  # type: ignore[arg-type]
        base_url=os.getenv("OPENAI_BASE_URL") or None,
        api_key=os.getenv("OPENAI_API_KEY") or None,
        model=model,
        cache_enabled=cache_enabled,
        fallback_to_rule_parser=fallback,
        mock_mode=os.getenv("CAMPUS_AGENT_MOCK_LLM_MODE", "valid_json"),
    )

    if config.provider == "openai_compatible":
        missing = [
            name
            for name, value in {
                "OPENAI_API_KEY": config.api_key,
                "OPENAI_BASE_URL": config.base_url,
                "OPENAI_MODEL": config.model,
            }.items()
            if not value
        ]
        if missing:
            raise LLMConfigError(
                "Missing required LLM configuration: " + ", ".join(missing)
            )

    return config


def _parse_bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}
