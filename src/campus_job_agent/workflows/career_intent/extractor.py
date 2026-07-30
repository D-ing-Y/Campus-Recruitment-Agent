"""Structured CareerIntent candidate extraction through the shared LLM gateway."""

from __future__ import annotations

from campus_job_agent.llm import LLMCache, LLMProvider, parse_structured_output
from campus_job_agent.prompts import (
    INTENT_PROMPT_NAME,
    INTENT_PROMPT_VERSION,
    INTENT_SCHEMA_VERSION,
    build_career_intent_messages,
    build_career_intent_retry_messages,
)
from campus_job_agent.schemas import (
    CareerIntentCandidate,
    EvidenceFragment,
    LLMCallRecord,
    LLMConfig,
)


class IntentCandidateExtractor:
    def __init__(self, config: LLMConfig, provider: LLMProvider, cache: LLMCache) -> None:
        self.config = config
        self.provider = provider
        self.cache = cache

    def extract(
        self, fragment: EvidenceFragment
    ) -> tuple[CareerIntentCandidate, list[LLMCallRecord]]:
        return parse_structured_output(
            messages=build_career_intent_messages(fragment),
            output_model=CareerIntentCandidate,
            config=self.config,
            provider=self.provider,
            cache=self.cache,
            prompt_name=INTENT_PROMPT_NAME,
            prompt_version=INTENT_PROMPT_VERSION,
            schema_version=INTENT_SCHEMA_VERSION,
            retry_builder=lambda previous, error: build_career_intent_retry_messages(
                fragment, previous, error
            ),
        )


__all__ = ["IntentCandidateExtractor"]
