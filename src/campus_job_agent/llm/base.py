"""Provider boundary for v0.2 LLM calls."""

from typing import Protocol

from campus_job_agent.schemas import LLMRequest, LLMResponse


class LLMProvider(Protocol):
    name: str

    def generate(self, request: LLMRequest) -> LLMResponse:
        ...


class LLMProviderError(Exception):
    """Raised when a provider cannot return a usable response."""


class LLMConfigError(Exception):
    """Raised when provider configuration is invalid."""
