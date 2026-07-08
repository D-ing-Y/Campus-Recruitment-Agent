from pathlib import Path

from campus_job_agent.agent import run_agent


REQUIRED_NODES = {
    "parse_goal",
    "plan_tasks",
    "run_mock_tool",
    "verify_result",
    "write_report",
}


def test_v01_eval_rules(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    state = run_agent("成都 AI Agent 2027 秋招")
    trace_nodes = {event["node"] for event in state["trace"]}

    graph_completed = state.get("report_path") is not None
    required_nodes_present = REQUIRED_NODES.issubset(trace_nodes)
    mock_tool_called = any(
        result["tool_name"] == "mock_job_search"
        for result in state.get("tool_results", [])
    )
    report_generated = Path(state["report_path"]).exists()
    verification_passed = state.get("verification", {}).get("passed") is True

    assert graph_completed
    assert required_nodes_present
    assert mock_tool_called
    assert report_generated
    assert verification_passed
