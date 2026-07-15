"""Deterministic fragment creation with verifiable character locators."""

import hashlib
from uuid import NAMESPACE_URL, uuid5

from campus_job_agent.schemas import EvidenceArtifact, EvidenceFragment


class DeterministicFragmenter:
    def __init__(self, max_chars: int = 1200) -> None:
        if max_chars < 1:
            raise ValueError("max_chars must be positive")
        self.max_chars = max_chars

    def fragment(self, artifact: EvidenceArtifact, text: str) -> list[EvidenceFragment]:
        fragments: list[EvidenceFragment] = []
        start = 0
        while start < len(text):
            end = min(start + self.max_chars, len(text))
            if end < len(text):
                split = text.rfind("\n", start, end)
                if split > start:
                    end = split + 1
            value = text[start:end]
            if value:
                fragment_id = str(
                    uuid5(NAMESPACE_URL, f"{artifact.artifact_id}:{start}:{end}")
                )
                fragments.append(
                    EvidenceFragment(
                        fragment_id=fragment_id,
                        artifact_id=artifact.artifact_id,
                        locator_type="char_range",
                        locator={"start": start, "end": end},
                        text=value,
                        text_hash=hashlib.sha256(value.encode("utf-8")).hexdigest(),
                    )
                )
            start = end
        return fragments
