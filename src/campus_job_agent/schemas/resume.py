"""Structured, human-confirmed resume evidence contracts."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Annotated, Any, Literal, TypedDict
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from campus_job_agent.schemas.candidate_graph import append_items
from campus_job_agent.schemas.evidence import utc_now


ResumeSectionType = Literal[
    "personal_information",
    "personal_advantage",
    "career_expectations",
    "work_experiences",
    "project_experiences",
    "education_experiences",
    "professional_skills",
    "custom_sections",
]
ResumeSectionStatus = Literal["pending", "confirmed", "corrected", "confirmed_empty"]
ResumeDraftStatus = Literal[
    "extracting", "awaiting_review", "finalized", "cancelled", "failed"
]
ResumeReviewAction = Literal["confirm", "correct", "remove", "retry", "cancel"]
ResumeReviewTargetKind = Literal["block", "record", "section_complete"]

RESUME_SECTION_ORDER: tuple[ResumeSectionType, ...] = (
    "personal_information",
    "personal_advantage",
    "career_expectations",
    "work_experiences",
    "project_experiences",
    "education_experiences",
    "professional_skills",
    "custom_sections",
)
LIST_SECTIONS: frozenset[ResumeSectionType] = frozenset({
    "career_expectations",
    "work_experiences",
    "project_experiences",
    "education_experiences",
    "custom_sections",
})


class ResumeSourceRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_id: str = Field(min_length=1)
    fragment_id: str = Field(min_length=1)
    page_number: int = Field(ge=1)
    text_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    start_offset: int | None = Field(default=None, ge=0)
    end_offset: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def valid_span(self) -> "ResumeSourceRef":
        if (
            self.start_offset is not None
            and self.end_offset is not None
            and self.end_offset < self.start_offset
        ):
            raise ValueError("source end_offset must not precede start_offset")
        return self


class PersonalInformation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    gender: str | None = None
    birth_date: str | None = None
    job_search_status: str | None = None
    identity: str | None = None
    phone: str | None = None
    wechat: str | None = None
    email: str | None = None
    birthplace: str | None = None


class ResumeTextBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str | None = None


class CareerExpectationRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    record_id: str = Field(min_length=1)
    employment_type: str | None = None
    role: str | None = None
    salary: str | None = None
    city: str | None = None


class WorkExperienceRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    record_id: str = Field(min_length=1)
    organization: str | None = None
    position: str | None = None
    role: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    content: str | None = None


class ProjectExperienceRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    record_id: str = Field(min_length=1)
    name: str | None = None
    role: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    raw_subtype: str | None = None
    content: str | None = None


class EducationExperienceRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    record_id: str = Field(min_length=1)
    institution: str | None = None
    degree: str | None = None
    major: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    courses_or_research: str | None = None


class CustomSectionRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    record_id: str = Field(min_length=1)
    section_title: str | None = None
    section_type: Literal[
        "award", "certificate", "campus_activity", "organization",
        "volunteering", "other",
    ] = "other"
    name: str | None = None
    role: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    content: str | None = None


class ResumeData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    personal_information: PersonalInformation = Field(default_factory=PersonalInformation)
    personal_advantage: ResumeTextBlock = Field(default_factory=ResumeTextBlock)
    career_expectations: list[CareerExpectationRecord] = Field(default_factory=list)
    work_experiences: list[WorkExperienceRecord] = Field(default_factory=list)
    project_experiences: list[ProjectExperienceRecord] = Field(default_factory=list)
    education_experiences: list[EducationExperienceRecord] = Field(default_factory=list)
    professional_skills: ResumeTextBlock = Field(default_factory=ResumeTextBlock)
    custom_sections: list[CustomSectionRecord] = Field(default_factory=list)


class ExtractedTextBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str | None = None
    evidence_fragment_ids: list[str] = Field(default_factory=list)


class _ExtractedRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_fragment_ids: list[str] = Field(min_length=1)


class ExtractedCareerExpectation(_ExtractedRecord):
    employment_type: str | None = None
    role: str | None = None
    salary: str | None = None
    city: str | None = None


class ExtractedWorkExperience(_ExtractedRecord):
    organization: str | None = None
    position: str | None = None
    role: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    content: str | None = None


class ExtractedProjectExperience(_ExtractedRecord):
    name: str | None = None
    role: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    raw_subtype: str | None = None
    content: str | None = None


class ExtractedEducationExperience(_ExtractedRecord):
    institution: str | None = None
    degree: str | None = None
    major: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    courses_or_research: str | None = None


class ExtractedCustomSection(_ExtractedRecord):
    section_title: str | None = None
    section_type: Literal[
        "award", "certificate", "campus_activity", "organization",
        "volunteering", "other",
    ] = "other"
    name: str | None = None
    role: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    content: str | None = None


class ResumeExtractionBatch(BaseModel):
    """PII-free structured output exposed to the model."""

    model_config = ConfigDict(extra="forbid")

    personal_advantage: ExtractedTextBlock = Field(default_factory=ExtractedTextBlock)
    career_expectations: list[ExtractedCareerExpectation] = Field(default_factory=list)
    work_experiences: list[ExtractedWorkExperience] = Field(default_factory=list)
    project_experiences: list[ExtractedProjectExperience] = Field(default_factory=list)
    education_experiences: list[ExtractedEducationExperience] = Field(default_factory=list)
    professional_skills: ExtractedTextBlock = Field(default_factory=ExtractedTextBlock)
    custom_sections: list[ExtractedCustomSection] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def normalize_observed_provider_aliases(cls, value: Any) -> Any:
        """Repair only lossless, observed Tool-call aliases before validation."""

        if not isinstance(value, dict):
            return value
        payload = dict(value)
        if (
            "education_experiences" not in payload
            and isinstance(payload.get("educations"), list)
        ):
            payload["education_experiences"] = payload.pop("educations")

        for field in ("personal_advantage", "professional_skills"):
            block = payload.get(field)
            if block is None:
                payload[field] = {}
            elif (
                isinstance(block, dict)
                and "text" not in block
                and "content" in block
            ):
                normalized = dict(block)
                normalized["text"] = normalized.pop("content")
                payload[field] = normalized

        for field in (
            "work_experiences", "project_experiences", "custom_sections"
        ):
            records = payload.get(field)
            if not isinstance(records, list):
                continue
            normalized_records: list[Any] = []
            for record in records:
                if (
                    isinstance(record, dict)
                    and "content" not in record
                    and "text" in record
                ):
                    normalized = dict(record)
                    normalized["content"] = normalized.pop("text")
                    normalized_records.append(normalized)
                else:
                    normalized_records.append(record)
            payload[field] = normalized_records

        educations = payload.get("education_experiences")
        if isinstance(educations, list):
            normalized_educations: list[Any] = []
            for record in educations:
                if (
                    isinstance(record, dict)
                    and "major" not in record
                    and "field" in record
                ):
                    normalized = dict(record)
                    normalized["major"] = normalized.pop("field")
                    normalized_educations.append(normalized)
                else:
                    normalized_educations.append(record)
            payload["education_experiences"] = normalized_educations
        return payload


class PdfExtractionDiagnostics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    selected_parser: str
    attempted_parsers: list[str] = Field(min_length=1)
    total_non_whitespace_chars: int = Field(ge=0)
    nonempty_page_ratio: float = Field(ge=0.0, le=1.0)
    invalid_character_ratio: float = Field(ge=0.0, le=1.0)
    quality_passed: bool


def default_section_statuses() -> dict[ResumeSectionType, ResumeSectionStatus]:
    return {section: "pending" for section in RESUME_SECTION_ORDER}


class ResumeDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    draft_id: str = Field(default_factory=lambda: f"resume-draft-{uuid4()}")
    owner_id: str = Field(min_length=1)
    candidate_id: str = Field(min_length=1)
    artifact_id: str = Field(min_length=1)
    predecessor_draft_id: str | None = None
    candidate_claim_count_at_import: int = Field(default=0, ge=0)
    revision: int = Field(default=1, ge=1)
    status: ResumeDraftStatus = "extracting"
    data: ResumeData = Field(default_factory=ResumeData)
    field_sources: dict[str, list[ResumeSourceRef]] = Field(default_factory=dict)
    section_statuses: dict[ResumeSectionType, ResumeSectionStatus] = Field(
        default_factory=default_section_statuses
    )
    reviewed_record_ids: dict[ResumeSectionType, list[str]] = Field(default_factory=dict)
    review_receipt_ids: list[str] = Field(default_factory=list)
    extraction_diagnostics: PdfExtractionDiagnostics
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def all_sections_present(self) -> "ResumeDraft":
        if set(self.section_statuses) != set(RESUME_SECTION_ORDER):
            raise ValueError("section_statuses must contain every standard resume section")
        return self


class ResumeReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(min_length=1)
    thread_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    candidate_id: str = Field(min_length=1)
    draft_id: str = Field(min_length=1)
    draft_revision: int = Field(ge=1)
    section: ResumeSectionType
    target_kind: ResumeReviewTargetKind
    record_id: str | None = None
    allowed_actions: list[ResumeReviewAction] = Field(min_length=1)
    source_pages: list[int] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def record_target_has_id(self) -> "ResumeReviewRequest":
        if self.target_kind == "record" and not self.record_id:
            raise ValueError("record review target requires record_id")
        return self


class ResumeReviewResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    response_id: str = Field(min_length=1)
    request_id: str = Field(min_length=1)
    thread_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    action: ResumeReviewAction
    patch: dict[str, Any] | None = None
    attests_pdf_source: bool = False
    submitted_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def action_payload(self) -> "ResumeReviewResponse":
        if self.action == "correct":
            if self.patch is None:
                raise ValueError("correct action requires patch")
            if not self.attests_pdf_source:
                raise ValueError("correct action requires PDF-source attestation")
        elif self.patch is not None:
            raise ValueError("patch is only allowed for correct action")
        return self


class ResumeReviewReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    receipt_id: str = Field(default_factory=lambda: f"resume-review-{uuid4()}")
    response_id: str = Field(min_length=1)
    request_id: str = Field(min_length=1)
    draft_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    section: ResumeSectionType
    target_kind: ResumeReviewTargetKind
    record_id: str | None = None
    action: ResumeReviewAction
    before_hash: str
    after_hash: str
    previous_revision: int = Field(ge=1)
    result_revision: int = Field(ge=1)
    result_status: Literal["reviewed", "retried", "cancelled"]
    response_artifact_id: str | None = None
    response_fragment_id: str | None = None
    created_at: datetime = Field(default_factory=utc_now)


class ResumeEvidenceSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    resume_evidence_id: str = Field(default_factory=lambda: f"resume-evidence-{uuid4()}")
    draft_id: str = Field(min_length=1)
    owner_id: str = Field(min_length=1)
    candidate_id: str = Field(min_length=1)
    artifact_id: str = Field(min_length=1)
    version: int = Field(ge=1)
    schema_version: Literal["v0.7.1"] = "v0.7.1"
    status: Literal["confirmed"] = "confirmed"
    data: ResumeData
    field_sources: dict[str, list[ResumeSourceRef]]
    review_receipt_ids: list[str] = Field(min_length=1)
    extraction_diagnostics: PdfExtractionDiagnostics
    confirmed_at: datetime = Field(default_factory=utc_now)


class ResumeEvidenceGraphState(TypedDict, total=False):
    run_id: str
    session_id: str
    thread_id: str
    user_id: str
    candidate_id: str
    input_path: str | None
    allowed_path_roots: list[str]
    force_reparse: bool
    artifact_id: str | None
    extraction_fragment_ids: list[str]
    draft_id: str | None
    resume_evidence_id: str | None
    candidate_claim_count_at_start: int
    pending_interaction: dict[str, Any] | None
    resume_input: dict[str, Any] | None
    status: str
    next_action: str | None
    llm_calls: Annotated[list[dict[str, Any]], append_items]
    trace: Annotated[list[dict[str, Any]], append_items]
    errors: Annotated[list[dict[str, Any]], append_items]


def canonical_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def resume_response_hash(response: ResumeReviewResponse) -> str:
    """Hash semantic response content, excluding transport submission time."""

    return canonical_hash(
        response.model_dump(mode="json", exclude={"submitted_at"})
    )
