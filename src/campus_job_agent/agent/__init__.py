"""Agent runtime package."""

from campus_job_agent.agent.state import AgentState, create_initial_state

__all__ = ["AgentState", "build_graph", "create_initial_state", "run_agent"]


def __getattr__(name: str):
    if name in {"build_graph", "run_agent"}:
        from campus_job_agent.agent.graph import build_graph, run_agent

        return {"build_graph": build_graph, "run_agent": run_agent}[name]
    raise AttributeError(name)
