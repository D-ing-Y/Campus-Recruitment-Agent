"""Shared schemas package."""
"""Shared schemas for Campus Job Agent."""

from campus_job_agent.schemas.goal import ParsedGoal, PlanTask
from campus_job_agent.schemas.tool import ToolResult
from campus_job_agent.schemas.trace import (
    RuntimeErrorRecord,
    TraceEvent,
    VerificationResult,
)

__all__ = [
    "ParsedGoal",
    "PlanTask",
    "ToolResult",
    "TraceEvent",
    "VerificationResult",
    "RuntimeErrorRecord",
]
