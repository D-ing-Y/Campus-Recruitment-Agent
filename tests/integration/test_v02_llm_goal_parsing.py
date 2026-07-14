import json
from pathlib import Path

from campus_job_agent.agent import run_agent


def test_v02_mock_provider_full_graph_and_cache(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CAMPUS_AGENT_LLM_PROVIDER", "mock")
    monkeypatch.setenv("CAMPUS_AGENT_LLM_CACHE_ENABLED", "true")
    monkeypatch.setenv("CAMPUS_AGENT_MOCK_LLM_MODE", "valid_json")

    first = run_agent("成都 AI Agent 2027 秋招")
    second = run_agent("成都 AI Agent 2027 秋招")

    assert first["parsed_goal"]["recruitment_type"] == "autumn_campus"
    assert first["parsed_goal"]["keywords"] == ["AI Agent", "LLM", "智能体"]

    run_id = first["run_id"]
    assert Path(f"data/runs/{run_id}/state.json").exists()
    assert Path(f"data/runs/{run_id}/trace.json").exists()
    assert Path(f"data/runs/{run_id}/llm_calls.json").exists()
    assert Path(f"data/reports/{run_id}.md").exists()

    trace = json.loads(Path(f"data/runs/{run_id}/trace.json").read_text())
    parse_goal = next(event for event in trace if event["node"] == "parse_goal")
    assert parse_goal["output_summary"]["llm_calls"][0]["provider"] == "mock"

    report = Path(f"data/reports/{run_id}.md").read_text(encoding="utf-8")
    assert "## LLM Calls" in report

    assert first["llm_calls"][0]["cache_hit"] is False
    assert second["llm_calls"][0]["cache_hit"] is True


def test_v02_fallback_explicitly_enabled(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CAMPUS_AGENT_LLM_PROVIDER", "mock")
    monkeypatch.setenv("CAMPUS_AGENT_MOCK_LLM_MODE", "provider_error")
    monkeypatch.setenv("CAMPUS_AGENT_LLM_FALLBACK_TO_RULE_PARSER", "true")

    state = run_agent("成都 AI Agent 2027 秋招")

    assert state["parsed_goal"]["role_query"] == "AI Agent"
    assert state["parsed_goal"]["warnings"] == ["fallback_to_rule_parser"]
    assert state["verification"]["passed"] is True
