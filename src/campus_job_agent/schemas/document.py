"""Persisted derived-text metadata used by real local document tools."""

from typing import Any

from pydantic import BaseModel, Field


class ExtractionUnit(BaseModel):
    index: int = Field(ge=1)
    start: int = Field(ge=0)
    end: int = Field(ge=0)
    locator: dict[str, Any]


class DocumentExtraction(BaseModel):
    artifact_id: str
    parser_name: str
    parser_version: str
    text_uri: str
    text_hash: str
    locator_type: str
    units: list[ExtractionUnit] = Field(default_factory=list)
