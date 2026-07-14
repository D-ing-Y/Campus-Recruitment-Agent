"""Write runtime outputs."""

import json
from pathlib import Path
from typing import Any

from campus_job_agent.agent.state import AgentState


REPORT_SECTIONS = [
    "# Mini Runtime Report",
    "## User Goal",
    "## Parsed Goal",
    "## LLM Calls",
    "## Plan",
    "## Tool Results",
    "## Verification",
    "## Trace Summary",
    "## Errors",
]


def write_runtime_outputs(state: AgentState) -> str:
    run_id = state["run_id"]
    output_dir = Path(state.get("output_dir", f"data/runs/{run_id}"))
    report_path = Path(state.get("report_path") or f"data/reports/{run_id}.md")

    output_dir.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)

    serializable_state = dict(state)
    serializable_state["report_path"] = str(report_path)

    (output_dir / "state.json").write_text(
        json.dumps(serializable_state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (output_dir / "trace.json").write_text(
        json.dumps(serializable_state.get("trace", []), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (output_dir / "llm_calls.json").write_text(
        json.dumps(
            serializable_state.get("llm_calls", []),
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    report_path.write_text(render_markdown_report(serializable_state), encoding="utf-8")

    return str(report_path)


def render_markdown_report(state: dict[str, Any]) -> str:
    lines = [
        "# Mini Runtime Report",
        "",
        "## User Goal",
        "",
        state.get("user_input", ""),
        "",
        "## Parsed Goal",
        "",
        _json_block(state.get("parsed_goal", {})),
        "",
        "## LLM Calls",
        "",
        _render_llm_calls(state.get("llm_calls", [])),
        "",
        "## Plan",
        "",
        _json_block(state.get("plan", [])),
        "",
        "## Tool Results",
        "",
        _render_tool_results(state.get("tool_results", [])),
        "",
        "## Verification",
        "",
        _json_block(state.get("verification", {})),
        "",
        "## Trace Summary",
        "",
        _render_trace_summary(state.get("trace", [])),
        "",
        "## Errors",
        "",
        _json_block(state.get("errors", [])),
        "",
    ]
    return "\n".join(lines)


def _json_block(value: Any) -> str:
    return "```json\n" + json.dumps(value, ensure_ascii=False, indent=2) + "\n```"


def _render_tool_results(tool_results: list[dict[str, Any]]) -> str:
    if not tool_results:
        return "No tool results."

    lines: list[str] = []
    for result in tool_results:
        lines.append(f"- `{result.get('tool_name')}`: {result.get('status')}")
        lines.append(f"  - records: {len(result.get('records', []))}")
        if result.get("error"):
            lines.append(f"  - error: {result['error']}")
    return "\n".join(lines)


def _render_llm_calls(llm_calls: list[dict[str, Any]]) -> str:
    if not llm_calls:
        return "No LLM calls."

    lines: list[str] = []
    for call in llm_calls:
        lines.append(
            "- "
            f"provider: `{call.get('provider')}`, "
            f"model: `{call.get('model')}`, "
            f"prompt: `{call.get('prompt_name')}@{call.get('prompt_version')}`, "
            f"schema: `{call.get('schema_version')}`, "
            f"cache_hit: `{call.get('cache_hit')}`, "
            f"retry_count: `{call.get('retry_count')}`, "
            f"status: `{call.get('status')}`"
        )
        if call.get("error_type"):
            lines.append(f"  - error_type: `{call.get('error_type')}`")
    return "\n".join(lines)


def _render_trace_summary(trace: list[dict[str, Any]]) -> str:
    if not trace:
        return "No trace events."

    return "\n".join(
        f"- `{event.get('node')}`: {event.get('status')} ({event.get('duration_ms')} ms)"
        for event in trace
    )
