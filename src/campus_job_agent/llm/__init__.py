"""LLM provider infrastructure."""

from campus_job_agent.llm.base import LLMConfigError, LLMProvider, LLMProviderError
from campus_job_agent.llm.cache import LLMCache
from campus_job_agent.llm.config import load_llm_config
from campus_job_agent.llm.mock import MockLLMProvider
from campus_job_agent.llm.structured import (
    StructuredOutputError,
    parse_search_goal_with_llm,
)
from campus_job_agent.schemas import LLMConfig

__all__ = [
    "LLMProvider",
    "LLMProviderError",
    "LLMConfigError",
    "LLMConfig",
    "LLMCache",
    "load_llm_config",
    "MockLLMProvider",
    "OpenAICompatibleProvider",
    "StructuredOutputError",
    "parse_search_goal_with_llm",
]


def __getattr__(name: str):
    if name == "OpenAICompatibleProvider":
        from campus_job_agent.llm.openai_compatible import OpenAICompatibleProvider

        return OpenAICompatibleProvider
    raise AttributeError(name)
