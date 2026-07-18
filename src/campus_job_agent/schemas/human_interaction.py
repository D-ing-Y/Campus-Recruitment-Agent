"""Validated interrupt and resume payloads for the v0.4 graph."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from campus_job_agent.schemas.candidate_graph import ProfileCorrection, QuestionItem
from campus_job_agent.schemas.evidence import utc_now


class RequestedMaterial(BaseModel):
    material_id: str
    gap_id: str
    description: str
    accepted_content_types: list[str] = Field(default_factory=list)
    required: bool = False
    reason: str


class HumanInteractionRequest(BaseModel):
    request_id: str = Field(min_length=1)
    schema_version: Literal["v0.4"] = "v0.4"
    thread_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    interaction_type: Literal[
        "answer_questions", "provide_materials", "review_profile"
    ]
    reason: str
    questions: list[QuestionItem] = Field(default_factory=list)
    requested_materials: list[RequestedMaterial] = Field(default_factory=list)
    profile_snapshot_id: str | None = None
    target_paths: list[str] = Field(default_factory=list)
    related_artifact_ids: list[str] = Field(default_factory=list)
    related_claim_ids: list[str] = Field(default_factory=list)
    allowed_actions: list[
        Literal["answer", "upload", "correct", "confirm", "skip", "cancel"]
    ] = Field(min_length=1)
    expires_at: datetime | None = None
    created_at: datetime = Field(default_factory=utc_now)


class HumanAnswer(BaseModel):
    question_id: str
    text: str = ""
    declined: bool = False

    @model_validator(mode="after")
    def require_text_unless_declined(self) -> "HumanAnswer":
        if not self.declined and not self.text.strip():
            raise ValueError("answer text is required unless declined")
        return self


class HumanInteractionResponse(BaseModel):
    response_id: str = Field(min_length=1)
    schema_version: Literal["v0.4"] = "v0.4"
    request_id: str = Field(min_length=1)
    thread_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    action: Literal["answer", "upload", "correct", "confirm", "skip", "cancel"]
    answers: list[HumanAnswer] = Field(default_factory=list)
    file_paths: list[str] = Field(default_factory=list)
    corrections: list[ProfileCorrection] = Field(default_factory=list)
    confirmation: bool | None = None
    skipped_ids: list[str] = Field(default_factory=list)
    submitted_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def validate_action_payload(self) -> "HumanInteractionResponse":
        if self.action == "answer" and not self.answers:
            raise ValueError("answer action requires at least one answer")
        if self.action == "upload" and not self.file_paths:
            raise ValueError("upload action requires at least one file path")
        if self.action == "correct" and not self.corrections:
            raise ValueError("correct action requires at least one correction")
        if self.action == "confirm" and self.confirmation is not True:
            raise ValueError("confirm action requires confirmation=true")
        if any("\x00" in path for path in self.file_paths):
            raise ValueError("file path contains a NUL byte")
        return self
