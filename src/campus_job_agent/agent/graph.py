"""LangGraph workflow for the Mini Agent Runtime."""

from time import perf_counter

from langgraph.graph import END, START, StateGraph

from campus_job_agent.agent.executor import execute_plan
from campus_job_agent.agent.planner import create_plan, parse_goal_with_llm
from campus_job_agent.agent.report_writer import write_runtime_outputs
from campus_job_agent.agent.state import AgentState, create_initial_state
from campus_job_agent.agent.trace import (
    build_trace_event,
    summarize_state,
    summarize_update,
    run_node_with_trace,
    utc_now_iso,
)
from campus_job_agent.agent.verifier import verify_state
from campus_job_agent.schemas import ParsedGoal, PlanTask
from campus_job_agent.tools import MockJobSearchTool, ToolRegistry


def build_default_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(MockJobSearchTool())
    return registry


def build_graph(registry: ToolRegistry | None = None):
    tool_registry = registry or build_default_registry()
    graph = StateGraph(AgentState)

    graph.add_node("parse_goal", _parse_goal_node)
    graph.add_node("plan_tasks", _plan_tasks_node)
    graph.add_node(
        "run_mock_tool",
        lambda state: _run_mock_tool_node(state, tool_registry),
    )
    graph.add_node("verify_result", _verify_result_node)
    graph.add_node("write_report", _write_report_node)

    graph.add_edge(START, "parse_goal")
    graph.add_edge("parse_goal", "plan_tasks")
    graph.add_edge("plan_tasks", "run_mock_tool")
    graph.add_edge("run_mock_tool", "verify_result")
    graph.add_edge("verify_result", "write_report")
    graph.add_edge("write_report", END)

    return graph.compile()


def run_agent(user_input: str) -> AgentState:
    app = build_graph()
    return app.invoke(create_initial_state(user_input))


def _parse_goal_node(state: AgentState) -> dict:
    def parse(state: AgentState) -> dict:
        parsed_goal, llm_calls, errors = parse_goal_with_llm(state["user_input"])
        return {
            "parsed_goal": parsed_goal,
            "llm_calls": list(state.get("llm_calls", [])) + llm_calls,
            "errors": list(state.get("errors", [])) + errors,
        }

    return run_node_with_trace("parse_goal", state, parse)


def _plan_tasks_node(state: AgentState) -> dict:
    def plan(state: AgentState) -> dict:
        parsed_goal = ParsedGoal(**state["parsed_goal"])
        tasks = create_plan(parsed_goal)
        return {"plan": [task.model_dump() for task in tasks]}

    return run_node_with_trace("plan_tasks", state, plan)


def _run_mock_tool_node(state: AgentState, registry: ToolRegistry) -> dict:
    def run_tools(state: AgentState) -> dict:
        plan = [PlanTask(**task) for task in state.get("plan", [])]
        results = execute_plan(plan, registry)
        result_dicts = [result.model_dump() for result in results]
        tool_errors = [
            {
                "node": "run_mock_tool",
                "message": result.error or f"Tool failed: {result.tool_name}",
                "recoverable": True,
            }
            for result in results
            if result.status == "failed"
        ]
        return {
            "tool_results": result_dicts,
            "errors": list(state.get("errors", [])) + tool_errors,
        }

    return run_node_with_trace("run_mock_tool", state, run_tools)


def _verify_result_node(state: AgentState) -> dict:
    def verify(state: AgentState) -> dict:
        verification = verify_state(state)
        errors = list(state.get("errors", []))
        if not verification.passed:
            errors.append(
                {
                    "node": "verify_result",
                    "message": "; ".join(verification.messages),
                    "recoverable": True,
                }
            )
        return {"verification": verification.model_dump(), "errors": errors}

    return run_node_with_trace("verify_result", state, verify)


def _write_report_node(state: AgentState) -> dict:
    started_at = utc_now_iso()
    started = perf_counter()
    input_summary = summarize_state(state)
    run_id = state["run_id"]
    report_path = f"data/reports/{run_id}.md"

    try:
        output_summary = summarize_update({"report_path": report_path})
        ended_at = utc_now_iso()
        duration_ms = int((perf_counter() - started) * 1000)
        trace = list(state.get("trace", []))
        trace.append(
            build_trace_event(
                node_name="write_report",
                status="success",
                started_at=started_at,
                ended_at=ended_at,
                duration_ms=duration_ms,
                input_summary=input_summary,
                output_summary=output_summary,
            )
        )
        final_state = {**state, "report_path": report_path, "trace": trace}
        write_runtime_outputs(final_state)
        return {"report_path": report_path, "trace": trace}
    except Exception as exc:
        ended_at = utc_now_iso()
        duration_ms = int((perf_counter() - started) * 1000)
        trace = list(state.get("trace", []))
        trace.append(
            build_trace_event(
                node_name="write_report",
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
            + [{"node": "write_report", "message": str(exc), "recoverable": False}],
        }
