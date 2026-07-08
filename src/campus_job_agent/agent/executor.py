"""Plan execution through ToolRegistry."""

from campus_job_agent.schemas import PlanTask, ToolResult
from campus_job_agent.tools import ToolRegistry


def execute_plan(plan: list[PlanTask], registry: ToolRegistry) -> list[ToolResult]:
    results: list[ToolResult] = []
    for task in plan:
        results.append(registry.run(task.tool_name, task.args))
    return results
