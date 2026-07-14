"""Deterministic mock LLM provider for tests and local smoke runs."""

import json

from campus_job_agent.llm.base import LLMProviderError
from campus_job_agent.schemas import LLMRequest, LLMResponse


class MockLLMProvider:
    name = "mock"

    def __init__(self, mode: str = "valid_json") -> None:
        self.mode = mode
        self.call_count = 0

    def generate(self, request: LLMRequest) -> LLMResponse:
        self.call_count += 1
        if self.mode == "provider_error":
            raise LLMProviderError("Mock provider error")
        if self.mode == "invalid_json_then_valid" and self.call_count == 1:
            return self._response("{not valid json", request.model)
        if self.mode == "schema_error_then_valid" and self.call_count == 1:
            return self._response(json.dumps({"role_query": "AI Agent"}), request.model)
        if self.mode == "always_invalid_json":
            return self._response("{not valid json", request.model)
        return self._response(json.dumps(_valid_goal(), ensure_ascii=False), request.model)

    def _response(self, text: str, model: str) -> LLMResponse:
        return LLMResponse(
            text=text,
            provider=self.name,
            model=model,
            usage=None,
            raw_metadata={"mock_mode": self.mode, "call_count": self.call_count},
        )


def _valid_goal() -> dict:
    return {
        "role_query": "AI Agent",
        "city": "成都",
        "graduation_year": "2027",
        "recruitment_type": "autumn_campus",
        "keywords": ["AI Agent", "LLM", "智能体"],
        "raw_text": "成都 AI Agent 2027 秋招",
        "companies": [],
        "industries": [],
        "locations": ["成都"],
        "constraints": [],
        "confidence": 0.95,
        "warnings": [],
    }
