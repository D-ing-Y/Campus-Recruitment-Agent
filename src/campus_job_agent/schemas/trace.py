"""Trace, verification, and runtime error schemas."""

from typing import Any, Literal

from pydantic import BaseModel


class TraceEvent(BaseModel):
    node: str
    status: Literal["success", "failed"]
    started_at: str
    ended_at: str
    duration_ms: int
    input_summary: dict[str, Any]
    output_summary: dict[str, Any]
    error: str | None = None


class VerificationResult(BaseModel):
    passed: bool
    checks: dict[str, bool]
    messages: list[str]


class RuntimeErrorRecord(BaseModel):
    node: str
    message: str
    recoverable: bool = True
