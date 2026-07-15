"""Capability canonical-name and alias resolver."""

import json
from pathlib import Path

from pydantic import BaseModel, Field


class CapabilityEntry(BaseModel):
    capability_id: str
    canonical_name: str
    aliases: list[str] = Field(default_factory=list)


class CapabilityResolution(BaseModel):
    raw_label: str
    capability_id: str | None = None
    canonical_name: str | None = None
    matched: bool = False


class CapabilityOntology(BaseModel):
    version: str
    capabilities: list[CapabilityEntry]

    @classmethod
    def load_default(cls) -> "CapabilityOntology":
        path = Path(__file__).with_name("capability_ontology.v0.3.json")
        return cls.model_validate_json(path.read_text(encoding="utf-8"))

    def resolve(self, raw_label: str) -> CapabilityResolution:
        normalized = _normalize(raw_label)
        for item in self.capabilities:
            names = [item.canonical_name, *item.aliases]
            if normalized in {_normalize(name) for name in names}:
                return CapabilityResolution(
                    raw_label=raw_label,
                    capability_id=item.capability_id,
                    canonical_name=item.canonical_name,
                    matched=True,
                )
        return CapabilityResolution(raw_label=raw_label)


def _normalize(value: str) -> str:
    return "".join(value.casefold().split())
