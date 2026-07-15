"""Generic, cached and retryable structured LLM output."""

from __future__ import annotations

import json
from collections.abc import Callable
from time import perf_counter
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from campus_job_agent.llm.base import LLMProvider, LLMProviderError
from campus_job_agent.llm.cache import LLMCache
from campus_job_agent.prompts import (
    PROMPT_NAME,
    PROMPT_VERSION,
    SCHEMA_VERSION,
    build_goal_parser_messages,
    build_goal_parser_retry_messages,
)
from campus_job_agent.schemas import LLMCallRecord, LLMConfig, LLMRequest, SearchGoal

StructuredT = TypeVar("StructuredT", bound=BaseModel)
RetryBuilder = Callable[[str, str], list[dict[str, str]]]
Normalizer = Callable[[StructuredT], StructuredT]


class StructuredOutputError(Exception):
    def __init__(
        self,
        error_type: str,
        message: str,
        call_records: list[LLMCallRecord] | None = None,
    ) -> None:
        super().__init__(message)
        self.error_type = error_type
        self.call_records = call_records or []


def parse_structured_output(
    *,
    messages: list[dict[str, str]],
    output_model: type[StructuredT],
    config: LLMConfig,
    provider: LLMProvider,
    cache: LLMCache,
    prompt_name: str,
    prompt_version: str,
    schema_version: str,
    retry_builder: RetryBuilder | None = None,
    normalizer: Normalizer[StructuredT] | None = None,
) -> tuple[StructuredT, list[LLMCallRecord]]:
    """Parse a Pydantic model while preserving v0.2 cache/retry semantics."""

    return _parse_with_messages(
        messages=messages,
        output_model=output_model,
        config=config,
        provider=provider,
        cache=cache,
        prompt_name=prompt_name,
        prompt_version=prompt_version,
        schema_version=schema_version,
        retry_builder=retry_builder,
        normalizer=normalizer,
        retry_count=0,
    )


def parse_search_goal_with_llm(
    user_input: str,
    config: LLMConfig,
    provider: LLMProvider,
    cache: LLMCache,
) -> tuple[SearchGoal, list[LLMCallRecord]]:
    def normalize(goal: SearchGoal) -> SearchGoal:
        if goal.raw_text == user_input:
            return goal
        data = goal.model_dump()
        data["raw_text"] = user_input
        return SearchGoal.model_validate(data)

    def retry(previous_output: str, error: str) -> list[dict[str, str]]:
        return build_goal_parser_retry_messages(
            user_input=user_input,
            previous_output=previous_output,
            error_summary=error,
        )

    return parse_structured_output(
        messages=build_goal_parser_messages(user_input),
        output_model=SearchGoal,
        config=config,
        provider=provider,
        cache=cache,
        prompt_name=PROMPT_NAME,
        prompt_version=PROMPT_VERSION,
        schema_version=SCHEMA_VERSION,
        retry_builder=retry,
        normalizer=normalize,
    )


def _parse_with_messages(
    *,
    messages: list[dict[str, str]],
    output_model: type[StructuredT],
    config: LLMConfig,
    provider: LLMProvider,
    cache: LLMCache,
    prompt_name: str,
    prompt_version: str,
    schema_version: str,
    retry_builder: RetryBuilder | None,
    normalizer: Normalizer[StructuredT] | None,
    retry_count: int,
) -> tuple[StructuredT, list[LLMCallRecord]]:
    started = perf_counter()
    cache_key = cache.make_cache_key(
        provider=provider.name,
        model=config.model,
        prompt_name=prompt_name,
        prompt_version=prompt_version,
        schema_version=schema_version,
        messages=messages,
    )
    cache_value: dict[str, Any] | None = None
    cache_error: str | None = None
    if config.cache_enabled:
        cache_value, cache_error = cache.read(cache_key)

    usage: dict[str, Any] | None = None
    cache_hit = cache_value is not None
    if cache_value is not None:
        response_text = str(cache_value.get("raw_output", ""))
        usage = cache_value.get("usage")
    else:
        request = LLMRequest(
            messages=messages,
            model=config.model,
            temperature=config.temperature,
            timeout_seconds=config.timeout_seconds,
        )
        try:
            response = provider.generate(request)
        except LLMProviderError as exc:
            record = _record(
                config,
                provider.name,
                prompt_name,
                prompt_version,
                schema_version,
                cache_key,
                False,
                retry_count,
                started,
                "failed",
                "provider_error",
                str(exc),
                None,
            )
            raise StructuredOutputError("provider_error", str(exc), [record]) from exc
        response_text = response.text
        usage = response.usage

    parsed, validation_error = _parse_and_validate(response_text, output_model)
    if parsed is not None:
        if normalizer is not None:
            parsed = normalizer(parsed)
        if config.cache_enabled and not cache_hit:
            write_error = cache.write(
                cache_key=cache_key,
                provider=provider.name,
                model=config.model,
                prompt_name=prompt_name,
                prompt_version=prompt_version,
                schema_version=schema_version,
                raw_output=response_text,
                parsed_json=parsed.model_dump(),
                usage=usage,
            )
            cache_error = cache_error or write_error
        return parsed, [
            _record(
                config,
                provider.name,
                prompt_name,
                prompt_version,
                schema_version,
                cache_key,
                cache_hit,
                retry_count,
                started,
                "success",
                "cache_error" if cache_error else None,
                cache_error,
                usage,
            )
        ]

    if retry_count < config.max_retries and retry_builder is not None:
        return _parse_with_messages(
            messages=retry_builder(response_text, validation_error),
            output_model=output_model,
            config=config,
            provider=provider,
            cache=cache,
            prompt_name=prompt_name,
            prompt_version=prompt_version,
            schema_version=schema_version,
            retry_builder=retry_builder,
            normalizer=normalizer,
            retry_count=retry_count + 1,
        )

    error_type = _classify_validation_error(validation_error)
    record = _record(
        config,
        provider.name,
        prompt_name,
        prompt_version,
        schema_version,
        cache_key,
        cache_hit,
        retry_count,
        started,
        "failed",
        error_type,
        validation_error,
        usage,
    )
    raise StructuredOutputError(error_type, validation_error, [record])


def _parse_and_validate(
    response_text: str, output_model: type[StructuredT]
) -> tuple[StructuredT | None, str]:
    try:
        parsed_json = json.loads(response_text)
    except json.JSONDecodeError as exc:
        return None, f"json_parse_error: {exc}"
    try:
        return output_model.model_validate(parsed_json), ""
    except ValidationError as exc:
        return None, f"schema_validation_error: {exc.errors()}"


def _classify_validation_error(error: str) -> str:
    if error.startswith("json_parse_error"):
        return "json_parse_error"
    return "schema_validation_error"


def _record(
    config: LLMConfig,
    provider: str,
    prompt_name: str,
    prompt_version: str,
    schema_version: str,
    cache_key: str,
    cache_hit: bool,
    retry_count: int,
    started: float,
    status: str,
    error_type: str | None,
    error: str | None,
    usage: dict[str, Any] | None,
) -> LLMCallRecord:
    return LLMCallRecord(
        provider=provider,
        model=config.model,
        prompt_name=prompt_name,
        prompt_version=prompt_version,
        schema_version=schema_version,
        cache_key=cache_key,
        cache_hit=cache_hit,
        retry_count=retry_count,
        duration_ms=int((perf_counter() - started) * 1000),
        status=status,  # type: ignore[arg-type]
        error_type=error_type,  # type: ignore[arg-type]
        error=error,
        usage=usage,
    )
