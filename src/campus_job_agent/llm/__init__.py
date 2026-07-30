"""LLM provider infrastructure."""

from campus_job_agent.llm.base import LLMConfigError, LLMProvider, LLMProviderError
from campus_job_agent.llm.cache import LLMCache
from campus_job_agent.llm.config import load_llm_config
from campus_job_agent.llm.mock import MockLLMProvider
from campus_job_agent.llm.langchain_provider import (
    LangChainChatProvider,
    build_llm_provider,
    infer_model_capabilities,
    infer_model_integration,
    resolve_structured_output_strategy,
)
from campus_job_agent.llm.structured import (
    StructuredOutputError,
    parse_search_goal_with_llm,
    parse_structured_output,
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
    "LangChainChatProvider",
    "build_llm_provider",
    "infer_model_capabilities",
    "infer_model_integration",
    "resolve_structured_output_strategy",
    "OpenAICompatibleProvider",
    "StructuredOutputError",
    "parse_search_goal_with_llm",
    "parse_structured_output",
]


def __getattr__(name: str):
    if name == "OpenAICompatibleProvider":
        from campus_job_agent.llm.openai_compatible import OpenAICompatibleProvider

        return OpenAICompatibleProvider
    raise AttributeError(name)
