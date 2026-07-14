"""Goal parsing and plan generation."""

from campus_job_agent.llm import (
    LLMCache,
    LLMConfigError,
    MockLLMProvider,
    StructuredOutputError,
    load_llm_config,
    parse_search_goal_with_llm,
)
from campus_job_agent.schemas import LLMCallRecord, ParsedGoal, PlanTask


def parse_goal_text(user_input: str) -> ParsedGoal:
    role_query = "unknown"
    if "AI Agent" in user_input:
        role_query = "AI Agent"
    elif "智能体" in user_input:
        role_query = "智能体"

    city = "成都" if "成都" in user_input else "unknown"
    graduation_year = "2027" if "2027" in user_input else "unknown"

    return ParsedGoal(
        role_query=role_query,
        city=city,
        graduation_year=graduation_year,
        raw_text=user_input,
    )


def parse_goal_with_llm(user_input: str) -> tuple[dict, list[dict], list[dict]]:
    try:
        config = load_llm_config()
    except LLMConfigError:
        raise

    if config.provider == "mock":
        provider = MockLLMProvider(config.mock_mode)
    else:
        from campus_job_agent.llm.openai_compatible import OpenAICompatibleProvider

        provider = OpenAICompatibleProvider(config)
    cache = LLMCache(config.cache_dir)

    try:
        search_goal, llm_calls = parse_search_goal_with_llm(
            user_input=user_input,
            config=config,
            provider=provider,
            cache=cache,
        )
        return search_goal.model_dump(), _dump_calls(llm_calls), []
    except (StructuredOutputError, LLMConfigError) as exc:
        llm_calls = _dump_calls(getattr(exc, "call_records", []))
        if config.fallback_to_rule_parser:
            fallback_goal = parse_goal_text(user_input).model_dump()
            fallback_goal.update(
                {
                    "recruitment_type": "unknown",
                    "keywords": [],
                    "companies": [],
                    "industries": [],
                    "locations": [],
                    "constraints": [],
                    "confidence": None,
                    "warnings": ["fallback_to_rule_parser"],
                }
            )
            fallback_call = LLMCallRecord(
                provider=config.provider,
                model=config.model,
                prompt_name="goal_parser",
                prompt_version="v1",
                schema_version="v0.2",
                cache_key="fallback",
                cache_hit=False,
                retry_count=config.max_retries,
                duration_ms=0,
                status="failed",
                error_type=getattr(exc, "error_type", "config_error"),
                error=f"fallback_to_rule_parser: {exc}",
                usage=None,
            )
            return fallback_goal, llm_calls + [fallback_call.model_dump()], []
        raise


def create_plan(parsed_goal: ParsedGoal) -> list[PlanTask]:
    return [
        PlanTask(
            task_id="task_001",
            tool_name="mock_job_search",
            args={
                "role_query": parsed_goal.role_query,
                "city": parsed_goal.city,
                "graduation_year": parsed_goal.graduation_year,
            },
            reason="Use the v0.1 mock job search tool to produce structured job records.",
        )
    ]


def _dump_calls(calls: list[LLMCallRecord]) -> list[dict]:
    return [call.model_dump() for call in calls]
