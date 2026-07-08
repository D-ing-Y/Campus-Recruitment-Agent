import json
from pathlib import Path

from campus_job_agent.agent import run_agent


REQUIRED_NODES = [
    "parse_goal",
    "plan_tasks",
    "run_mock_tool",
    "verify_result",
    "write_report",
]


def test_mini_runtime_writes_outputs(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    state = run_agent("成都 AI Agent 2027 秋招")
    run_id = state["run_id"]

    state_path = Path("data/runs") / run_id / "state.json"
    trace_path = Path("data/runs") / run_id / "trace.json"
    report_path = Path("data/reports") / f"{run_id}.md"

    assert state_path.exists()
    assert trace_path.exists()
    assert report_path.exists()

    trace = json.loads(trace_path.read_text(encoding="utf-8"))
    assert [event["node"] for event in trace] == REQUIRED_NODES

    report = report_path.read_text(encoding="utf-8")
    for section in [
        "# Mini Runtime Report",
        "## User Goal",
        "## Parsed Goal",
        "## Plan",
        "## Tool Results",
        "## Verification",
        "## Trace Summary",
        "## Errors",
    ]:
        assert section in report
