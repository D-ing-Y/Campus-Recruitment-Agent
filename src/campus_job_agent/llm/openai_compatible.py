"""OpenAI-compatible Chat Completions provider."""

import httpx

from campus_job_agent.llm.base import LLMProviderError
from campus_job_agent.schemas import LLMConfig, LLMRequest, LLMResponse


class OpenAICompatibleProvider:
    name = "openai_compatible"

    def __init__(self, config: LLMConfig) -> None:
        self.base_url = (config.base_url or "").rstrip("/")
        self.api_key = config.api_key or ""
        self.model = config.model
        self.timeout_seconds = config.timeout_seconds

    def generate(self, request: LLMRequest) -> LLMResponse:
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": request.model,
            "messages": request.messages,
            "temperature": request.temperature,
            "response_format": request.response_format,
        }

        try:
            response = httpx.post(
                url,
                headers=headers,
                json=payload,
                timeout=request.timeout_seconds,
            )
            response.raise_for_status()
            data = response.json()
            text = data["choices"][0]["message"]["content"]
        except httpx.TimeoutException as exc:
            raise LLMProviderError(
                f"OpenAI-compatible provider timeout: {exc}",
                error_type="network_timeout",
                retryable=True,
            ) from exc
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            if status_code in {401, 403}:
                error_type, retryable = "auth_required", False
            elif status_code == 429:
                error_type, retryable = "rate_limited", True
            else:
                error_type, retryable = "provider_error", status_code >= 500
            raise LLMProviderError(
                f"OpenAI-compatible provider HTTP status: {status_code}",
                error_type=error_type,
                retryable=retryable,
            ) from exc
        except httpx.HTTPError as exc:
            raise LLMProviderError(
                f"OpenAI-compatible provider network error: {exc}",
                error_type="provider_error",
                retryable=True,
            ) from exc
        except (KeyError, TypeError, ValueError) as exc:
            raise LLMProviderError(
                "OpenAI-compatible provider returned an invalid response",
                error_type="provider_error",
                retryable=False,
            ) from exc

        return LLMResponse(
            text=text,
            provider=self.name,
            model=request.model,
            usage=data.get("usage"),
            raw_metadata={
                "response_id": data.get("id"),
                "object": data.get("object"),
            },
        )
