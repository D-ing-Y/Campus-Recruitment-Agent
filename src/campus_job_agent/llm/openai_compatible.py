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
        except Exception as exc:
            raise LLMProviderError(f"OpenAI-compatible provider error: {exc}") from exc

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
