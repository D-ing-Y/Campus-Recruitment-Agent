"""Public v0.7.1 runtime, session, event, receipt, and handoff schemas."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field, model_validator

from campus_job_agent.schemas.evidence import ValidationReceipt


SCHEMA_VERSION = "v0.7.1"
SessionStatus = Literal["active", "interrupted", "blocked", "completed", "cancelled", "failed"]
TerminalRunStatus = Literal[
    "completed", "completed_with_unknowns", "partial", "blocked", "blocked_by_auth",
    "interrupted", "reroute_required", "awaiting_rebuild", "cancelled", "failed",
]
EventStatus = Literal[
    "running", "completed", "completed_with_unknowns", "partial", "blocked",
    "blocked_by_auth", "interrupted", "reroute_required", "awaiting_rebuild",
    "cancelled", "failed", "abandoned",
]


def utc_now() -> datetime:
    return datetime.now(UTC)


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid4()}"


def exit_code_for_error(error_type: str) -> int:
    if error_type in {"invalid_input", "config_error", "unsupported_input"}:
        return 2
    if error_type in {"contract_violation", "permission_denied", "not_found", "stale_input"}:
        return 3
    if error_type in {
        "auth_required", "rate_limited", "source_changed", "adapter_required",
        "llm_invalid_output", "llm_unavailable",
    }:
        return 4
    if error_type in {"storage_failure", "checkpoint_failure"}:
        return 5
    return 6


class ObjectRef(BaseModel):
    object_id: str
    object_type: str
    owner_id: str
    schema_version: str
    lifecycle_status: Literal["current", "historical", "stale", "superseded"] = "current"
    predecessor_ids: list[str] = Field(default_factory=list)
    successor_of: str | None = None
    canonical_hash: str | None = None


class RunSession(BaseModel):
    session_id: str = Field(default_factory=lambda: new_id("session"))
    schema_version: str = SCHEMA_VERSION
    session_version: int = 1
    user_id: str
    status: SessionStatus = "active"
    current_stage: str = "candidate"
    current_refs: dict[str, str | list[str]] = Field(default_factory=dict)
    pending_request: str | None = None
    pending_handoff_ids: list[str] = Field(default_factory=list)
    latest_run_id: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def only_navigation_state(self) -> "RunSession":
        forbidden = {"graph_state", "resume_input", "resume_text", "feedback_text", "raw_text"}
        if forbidden.intersection(self.current_refs):
            raise ValueError("session current_refs may contain object references only")
        return self


class RunManifest(BaseModel):
    run_id: str = Field(default_factory=lambda: new_id("run"))
    schema_version: str = SCHEMA_VERSION
    session_id: str
    thread_id: str
    parent_run_id: str | None = None
    workflow: str
    command: str
    status: Literal["running"] | TerminalRunStatus = "running"
    next_action: str | None = None
    input_refs: dict[str, Any] = Field(default_factory=dict)
    output_refs: dict[str, Any] = Field(default_factory=dict)
    pending_request_id: str | None = None
    pending_handoff_ids: list[str] = Field(default_factory=list)
    started_at: datetime = Field(default_factory=utc_now)
    ended_at: datetime | None = None
    artifact_paths: dict[str, str] = Field(default_factory=dict)
    software_version: str = "0.7.0"
    policy_versions: dict[str, str] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


class RunEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: new_id("event"))
    schema_version: str = SCHEMA_VERSION
    sequence: int = 0
    run_id: str
    session_id: str
    thread_id: str
    event_type: str
    occurred_at: datetime = Field(default_factory=utc_now)
    workflow: str
    node: str | None = None
    status: EventStatus
    input_refs: dict[str, Any] = Field(default_factory=dict)
    output_refs: dict[str, Any] = Field(default_factory=dict)
    counts: dict[str, int | float] = Field(default_factory=dict)
    route: str | None = None
    duration_ms: int | None = None
    reason_codes: list[str] = Field(default_factory=list)
    error_ref: str | None = None
    fallback: str | None = None


class LLMCallReceipt(BaseModel):
    receipt_id: str = Field(default_factory=lambda: new_id("llm"))
    schema_version: str = SCHEMA_VERSION
    run_id: str
    provider: str
    model: str
    prompt_version: str
    schema_version_used: str
    request_hash: str
    response_hash: str | None = None
    status: Literal["success", "failed", "fallback"]
    retry_count: int = 0
    cache_hit: bool = False
    token_usage: dict[str, int] | None = None
    latency_ms: int | None = None
    validation_result: str | None = None
    fallback: str | None = None
    error_ref: str | None = None
    integration: str | None = None
    requested_strategy: str | None = None
    effective_strategy: str | None = None
    capabilities: dict[str, Any] | None = None


class ErrorEvent(BaseModel):
    error_id: str = Field(default_factory=lambda: new_id("error"))
    schema_version: str = SCHEMA_VERSION
    run_id: str
    workflow: str
    node: str | None = None
    error_type: Literal[
        "invalid_input", "contract_violation", "permission_denied", "not_found",
        "stale_input", "auth_required", "rate_limited", "source_changed",
        "adapter_required", "llm_invalid_output", "llm_unavailable", "storage_failure",
        "checkpoint_failure", "budget_exhausted", "internal_error",
    ]
    message: str
    retryable: bool = False
    related_refs: dict[str, Any] = Field(default_factory=dict)
    recovery_hint: str
    occurred_at: datetime = Field(default_factory=utc_now)


class ArtifactEntry(BaseModel):
    logical_type: str
    object_id: str
    locator: str
    canonical_hash: str | None = None
    owner: str | None = None
    schema_version: str = SCHEMA_VERSION
    policy_version: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    sensitivity: Literal["public", "internal", "private", "secret"] = "internal"


class ArtifactIndex(BaseModel):
    schema_version: str = SCHEMA_VERSION
    run_id: str
    entries: list[ArtifactEntry] = Field(default_factory=list)


class Handoff(BaseModel):
    handoff_id: str = Field(default_factory=lambda: new_id("handoff"))
    schema_version: str = SCHEMA_VERSION
    session_id: str
    user_id: str
    handoff_type: str
    origin_run_id: str
    origin_object_refs: dict[str, Any] = Field(default_factory=dict)
    required_input_refs: dict[str, Any] = Field(default_factory=dict)
    status: Literal["pending", "processing", "resolved", "failed_retryable", "rejected", "cancelled"] = "pending"
    handler_version: str
    attempt_count: int = 0
    resolved_refs: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)
    resolved_at: datetime | None = None
