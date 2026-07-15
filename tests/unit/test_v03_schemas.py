import hashlib

import pytest
from pydantic import ValidationError

from campus_job_agent.schemas import (
    CandidateProfile,
    EvidenceArtifact,
    EvidenceClaim,
    EvidenceFragment,
    ClaimExtractor,
)


def test_evidence_schema_and_claim_idempotency() -> None:
    digest = hashlib.sha256(b"resume").hexdigest()
    artifact = EvidenceArtifact(
        owner_id="owner",
        source_type="fixture",
        content_type="text/plain",
        original_name="resume.txt",
        raw_uri="file:///tmp/resume.txt",
        content_hash=digest.upper(),
    )
    fragment = EvidenceFragment(
        artifact_id=artifact.artifact_id,
        locator_type="char_range",
        locator={"start": 0, "end": 6},
        text="resume",
        text_hash=digest,
    )
    values = dict(
        subject_id="candidate",
        predicate="capability:Python",
        value={"level": "intermediate"},
        claim_type="observed_fact",
        evidence_fragment_ids=[fragment.fragment_id],
        confidence=0.8,
        extractor=ClaimExtractor(provider="mock", model="mock"),
        prompt_version="v0.3.0",
    )
    first = EvidenceClaim(**values)
    second = EvidenceClaim(**values)
    assert artifact.content_hash == digest
    assert first.idempotency_key() == second.idempotency_key()
    assert CandidateProfile(candidate_id="candidate").schema_version == "v0.3"


def test_invalid_hash_and_confidence_are_rejected() -> None:
    with pytest.raises(ValidationError):
        EvidenceArtifact(
            owner_id="owner",
            source_type="fixture",
            content_type="text/plain",
            original_name="x",
            raw_uri="file:///tmp/x",
            content_hash="not-a-hash",
        )
