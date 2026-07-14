import json

from campus_job_agent.llm.cache import LLMCache


def test_cache_key_is_stable_and_sensitive(tmp_path):
    cache = LLMCache(str(tmp_path))
    messages = [{"role": "user", "content": "成都 AI Agent 2027 秋招"}]

    key = cache.make_cache_key("mock", "m1", "goal_parser", "v1", "v0.2", messages)

    assert key == cache.make_cache_key(
        "mock", "m1", "goal_parser", "v1", "v0.2", messages
    )
    assert key != cache.make_cache_key(
        "openai_compatible", "m1", "goal_parser", "v1", "v0.2", messages
    )
    assert key != cache.make_cache_key(
        "mock", "m2", "goal_parser", "v1", "v0.2", messages
    )
    assert key != cache.make_cache_key(
        "mock", "m1", "goal_parser", "v2", "v0.2", messages
    )
    assert key != cache.make_cache_key(
        "mock", "m1", "goal_parser", "v1", "v0.3", messages
    )


def test_cache_write_and_read_without_api_key(tmp_path):
    cache = LLMCache(str(tmp_path))
    key = "abc"
    cache.write(
        cache_key=key,
        provider="mock",
        model="mock-goal-parser",
        prompt_name="goal_parser",
        prompt_version="v1",
        schema_version="v0.2",
        raw_output="{}",
        parsed_json={"role_query": "AI Agent"},
        usage=None,
    )

    value, error = cache.read(key)

    assert error is None
    assert value is not None
    assert "api_key" not in json.dumps(value).lower()


def test_cache_corruption_is_miss_with_error(tmp_path):
    cache = LLMCache(str(tmp_path))
    tmp_path.joinpath("bad.json").write_text("{not json", encoding="utf-8")

    value, error = cache.read("bad")

    assert value is None
    assert "cache_error" in error
