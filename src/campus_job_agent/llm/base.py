"""Provider boundary for v0.2 LLM calls."""

from typing import Protocol

from campus_job_agent.schemas import LLMRequest, LLMResponse


class LLMProvider(Protocol):
    name: str

    def generate(self, request: LLMRequest) -> LLMResponse:
        ...


class LLMProviderError(Exception):
    """Raised when a provider cannot return a usable response."""

    def __init__(
        self,
        message: str,
        *,
        error_type: str = "provider_error",
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.error_type = error_type
        self.retryable = retryable


class LLMConfigError(Exception):
    """Raised when provider configuration is invalid."""
