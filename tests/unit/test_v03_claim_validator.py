import hashlib
import json
from pathlib import Path

import pytest

from campus_job_agent.evidence import ClaimValidationError, ClaimValidator
from campus_job_agent.schemas import ClaimExtractor, EvidenceArtifact, EvidenceClaim, EvidenceFragment
from campus_job_agent.storage import SQLiteRepository


FIXTURE = Path(__file__).parents[1] / "fixtures" / "v03" / "unsupported_claim.json"


def test_claim_validator_rejects_unsupported_and_cross_artifact_claims(tmp_path) -> None:
    repository = SQLiteRepository(tmp_path / "evidence.sqlite3")
    validator = ClaimValidator(repository)
    unsupported = EvidenceClaim.model_validate(json.loads(FIXTURE.read_text()))
    with pytest.raises(ClaimValidationError, match="unknown evidence fragment"):
        validator.validate_and_save(unsupported)

    artifact = repository.save_artifact(
        EvidenceArtifact(
            owner_id="owner", source_type="fixture", content_type="text/plain",
            original_name="x", raw_uri="file:///tmp/x",
            content_hash=hashlib.sha256(b"x").hexdigest(),
        )
    )
    fragment = repository.save_fragment(
        EvidenceFragment(
            artifact_id=artifact.artifact_id, locator_type="char_range",
            locator={"start": 0, "end": 1}, text="x",
            text_hash=hashlib.sha256(b"x").hexdigest(),
        )
    )
    claim = EvidenceClaim(
        subject_id="candidate", predicate="capability:Python", value=True,
        claim_type="observed_fact", evidence_fragment_ids=[fragment.fragment_id],
        confidence=0.7, extractor=ClaimExtractor(provider="mock", model="mock"),
        prompt_version="v0.3.0",
    )
    with pytest.raises(ClaimValidationError, match="outside"):
        validator.validate(claim, {"another-artifact"})
    assert validator.validate_and_save(claim).claim_id == claim.claim_id
