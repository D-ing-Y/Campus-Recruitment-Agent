"""Shared schemas package."""
"""Shared schemas for Campus Job Agent."""

from campus_job_agent.schemas.goal import ParsedGoal, PlanTask, SearchGoal
from campus_job_agent.schemas.llm import (
    LLMCallRecord,
    LLMConfig,
    LLMRequest,
    LLMResponse,
)
from campus_job_agent.schemas.tool import ToolResult
from campus_job_agent.schemas.trace import (
    RuntimeErrorRecord,
    TraceEvent,
    VerificationResult,
)

__all__ = [
    "ParsedGoal",
    "SearchGoal",
    "PlanTask",
    "LLMConfig",
    "LLMRequest",
    "LLMResponse",
    "LLMCallRecord",
    "ToolResult",
    "TraceEvent",
    "VerificationResult",
    "RuntimeErrorRecord",
]
