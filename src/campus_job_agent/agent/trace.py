"""Trace helpers for runtime nodes."""

from collections.abc import Callable
from datetime import UTC, datetime
from time import perf_counter
from typing import Any

from campus_job_agent.agent.state import AgentState
from campus_job_agent.schemas import TraceEvent


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def summarize_state(state: AgentState) -> dict[str, Any]:
    return {
        "run_id": state.get("run_id"),
        "has_parsed_goal": bool(state.get("parsed_goal")),
        "plan_count": len(state.get("plan", [])),
        "tool_result_count": len(state.get("tool_results", [])),
        "verification_passed": state.get("verification", {}).get("passed"),
        "error_count": len(state.get("errors", [])),
        "llm_call_count": len(state.get("llm_calls", [])),
        "trace_count": len(state.get("trace", [])),
    }


def summarize_update(update: dict[str, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for key, value in update.items():
        if key == "llm_calls" and isinstance(value, list):
            summary[key] = [_summarize_llm_call(call) for call in value[-3:]]
        elif isinstance(value, list):
            summary[key] = {"count": len(value)}
        elif isinstance(value, dict):
            summary[key] = {"keys": sorted(value.keys())}
        else:
            summary[key] = value
    return summary


def _summarize_llm_call(call: dict[str, Any]) -> dict[str, Any]:
    return {
        "provider": call.get("provider"),
        "model": call.get("model"),
        "cache_hit": call.get("cache_hit"),
        "retry_count": call.get("retry_count"),
        "status": call.get("status"),
        "error_type": call.get("error_type"),
    }


def build_trace_event(
    node_name: str,
    status: str,
    started_at: str,
    ended_at: str,
    duration_ms: int,
    input_summary: dict[str, Any],
    output_summary: dict[str, Any],
    error: str | None = None,
) -> dict[str, Any]:
    return TraceEvent(
        node=node_name,
        status=status,  # type: ignore[arg-type]
        started_at=started_at,
        ended_at=ended_at,
        duration_ms=duration_ms,
        input_summary=input_summary,
        output_summary=output_summary,
        error=error,
    ).model_dump()


def run_node_with_trace(
    node_name: str,
    state: AgentState,
    fn: Callable[[AgentState], dict[str, Any]],
) -> dict[str, Any]:
    started_at = utc_now_iso()
    started = perf_counter()
    input_summary = summarize_state(state)

    try:
        update = fn(state)
    except Exception as exc:
        ended_at = utc_now_iso()
        duration_ms = int((perf_counter() - started) * 1000)
        trace = list(state.get("trace", []))
        trace.append(
            build_trace_event(
                node_name=node_name,
                status="failed",
                started_at=started_at,
                ended_at=ended_at,
                duration_ms=duration_ms,
                input_summary=input_summary,
                output_summary={},
                error=str(exc),
            )
        )
        return {
            "trace": trace,
            "errors": list(state.get("errors", []))
            + [{"node": node_name, "message": str(exc), "recoverable": False}],
        }

    ended_at = utc_now_iso()
    duration_ms = int((perf_counter() - started) * 1000)
    trace = list(state.get("trace", []))
    trace.append(
        build_trace_event(
            node_name=node_name,
            status="success",
            started_at=started_at,
            ended_at=ended_at,
            duration_ms=duration_ms,
            input_summary=input_summary,
            output_summary=summarize_update(update),
        )
    )
    return {**update, "trace": trace}
