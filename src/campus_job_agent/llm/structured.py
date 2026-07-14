"""Structured output orchestration for SearchGoal parsing."""

import json
from time import perf_counter
from typing import Any

from pydantic import ValidationError

from campus_job_agent.llm.base import LLMProvider, LLMProviderError
from campus_job_agent.llm.cache import LLMCache
from campus_job_agent.prompts import (
    PROMPT_NAME,
    PROMPT_VERSION,
    SCHEMA_VERSION,
    build_goal_parser_messages,
    build_goal_parser_retry_messages,
)
from campus_job_agent.schemas import (
    LLMCallRecord,
    LLMConfig,
    LLMRequest,
    SearchGoal,
)


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


def parse_search_goal_with_llm(
    user_input: str,
    config: LLMConfig,
    provider: LLMProvider,
    cache: LLMCache,
) -> tuple[SearchGoal, list[LLMCallRecord]]:
    messages = build_goal_parser_messages(user_input)
    return _parse_with_messages(
        user_input=user_input,
        config=config,
        provider=provider,
        cache=cache,
        messages=messages,
        retry_count=0,
        previous_output=None,
        previous_error=None,
    )


def _parse_with_messages(
    user_input: str,
    config: LLMConfig,
    provider: LLMProvider,
    cache: LLMCache,
    messages: list[dict[str, str]],
    retry_count: int,
    previous_output: str | None,
    previous_error: str | None,
) -> tuple[SearchGoal, list[LLMCallRecord]]:
    started = perf_counter()
    cache_key = cache.make_cache_key(
        provider=provider.name,
        model=config.model,
        prompt_name=PROMPT_NAME,
        prompt_version=PROMPT_VERSION,
        schema_version=SCHEMA_VERSION,
        messages=messages,
    )

    cache_value: dict[str, Any] | None = None
    cache_error: str | None = None
    if config.cache_enabled:
        cache_value, cache_error = cache.read(cache_key)

    response_text: str
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
            duration_ms = int((perf_counter() - started) * 1000)
            record = _record(
                config=config,
                provider=provider.name,
                cache_key=cache_key,
                cache_hit=False,
                retry_count=retry_count,
                duration_ms=duration_ms,
                status="failed",
                error_type="provider_error",
                error=str(exc),
                usage=None,
            )
            raise StructuredOutputError("provider_error", str(exc), [record]) from exc
        response_text = response.text
        usage = response.usage

    parsed_json, validation_error = _parse_and_validate(response_text, user_input)
    duration_ms = int((perf_counter() - started) * 1000)

    if isinstance(parsed_json, SearchGoal):
        if config.cache_enabled and not cache_hit:
            write_error = cache.write(
                cache_key=cache_key,
                provider=provider.name,
                model=config.model,
                prompt_name=PROMPT_NAME,
                prompt_version=PROMPT_VERSION,
                schema_version=SCHEMA_VERSION,
                raw_output=response_text,
                parsed_json=parsed_json.model_dump(),
                usage=usage,
            )
            cache_error = cache_error or write_error

        record = _record(
            config=config,
            provider=provider.name,
            cache_key=cache_key,
            cache_hit=cache_hit,
            retry_count=retry_count,
            duration_ms=duration_ms,
            status="success",
            error_type="cache_error" if cache_error else None,
            error=cache_error,
            usage=usage,
        )
        return parsed_json, [record]

    if retry_count < config.max_retries:
        retry_messages = build_goal_parser_retry_messages(
            user_input=user_input,
            previous_output=previous_output or response_text,
            error_summary=previous_error or validation_error,
        )
        goal, records = _parse_with_messages(
            user_input=user_input,
            config=config,
            provider=provider,
            cache=cache,
            messages=retry_messages,
            retry_count=retry_count + 1,
            previous_output=response_text,
            previous_error=validation_error,
        )
        return goal, records

    error_type = _classify_validation_error(validation_error)
    record = _record(
        config=config,
        provider=provider.name,
        cache_key=cache_key,
        cache_hit=cache_hit,
        retry_count=retry_count,
        duration_ms=duration_ms,
        status="failed",
        error_type=error_type,
        error=validation_error,
        usage=usage,
    )
    raise StructuredOutputError(error_type, validation_error, [record])


def _parse_and_validate(
    response_text: str,
    user_input: str,
) -> tuple[SearchGoal | None, str]:
    try:
        parsed = json.loads(response_text)
    except json.JSONDecodeError as exc:
        return None, f"json_parse_error: {exc}"

    try:
        goal = SearchGoal.model_validate(parsed)
    except ValidationError as exc:
        return None, f"schema_validation_error: {exc.errors()}"

    if goal.raw_text != user_input:
        data = goal.model_dump()
        data["raw_text"] = user_input
        goal = SearchGoal.model_validate(data)
    return goal, ""


def _classify_validation_error(error: str) -> str:
    if error.startswith("json_parse_error"):
        return "json_parse_error"
    return "schema_validation_error"


def _record(
    config: LLMConfig,
    provider: str,
    cache_key: str,
    cache_hit: bool,
    retry_count: int,
    duration_ms: int,
    status: str,
    error_type: str | None,
    error: str | None,
    usage: dict[str, Any] | None,
) -> LLMCallRecord:
    return LLMCallRecord(
        provider=provider,
        model=config.model,
        prompt_name=PROMPT_NAME,
        prompt_version=PROMPT_VERSION,
        schema_version=SCHEMA_VERSION,
        cache_key=cache_key,
        cache_hit=cache_hit,
        retry_count=retry_count,
        duration_ms=duration_ms,
        status=status,  # type: ignore[arg-type]
        error_type=error_type,  # type: ignore[arg-type]
        error=error,
        usage=usage,
    )
