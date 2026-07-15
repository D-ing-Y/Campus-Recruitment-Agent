import hashlib

from campus_job_agent.evals import evaluate_evidence
from campus_job_agent.schemas import ClaimExtractor, EvidenceArtifact, EvidenceClaim, EvidenceFragment


def test_evidence_eval_detects_unsupported_claim_and_bad_locator() -> None:
    artifact = EvidenceArtifact(
        owner_id="owner", source_type="fixture", content_type="text/plain",
        original_name="x", raw_uri="file:///tmp/x",
        content_hash=hashlib.sha256(b"x").hexdigest(),
    )
    fragment = EvidenceFragment(
        artifact_id=artifact.artifact_id, locator_type="char_range",
        locator={"start": 0, "end": 99}, text="x",
        text_hash=hashlib.sha256(b"x").hexdigest(),
    )
    claim = EvidenceClaim(
        subject_id="candidate", predicate="capability:Python", value=True,
        claim_type="model_inference", evidence_fragment_ids=["missing"], confidence=0.5,
        extractor=ClaimExtractor(provider="mock", model="mock"), prompt_version="v0.3.0",
    )
    report = evaluate_evidence(
        artifacts=[artifact], fragments=[fragment], claims=[claim], profile=None
    )
    assert report.unsupported_claim_count == 1
    assert report.evidence_trace_rate == 0.0
    assert report.valid_locator_rate == 0.0
