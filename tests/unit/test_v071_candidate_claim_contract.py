from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from campus_job_agent.evidence import (
    CandidateProfileProjector,
    ClaimExtractorService,
    CandidateClaimValidationError,
    CandidateClaimValidator,
    parse_candidate_predicate,
)
from campus_job_agent.llm import LLMCache, LLMConfig, LLMProviderError
from campus_job_agent.runtime import ValidationReceipt
from campus_job_agent.prompts import build_claim_extractor_messages
from campus_job_agent.schemas import (
    ClaimExtractor,
    EvidenceArtifact,
    EvidenceClaim,
    EvidenceFragment,
    LLMResponse,
)
from campus_job_agent.storage import SQLiteRepository
from campus_job_agent.tools.candidate_profile import ExtractCandidateClaimsTool


def _seed_fragment(repository: SQLiteRepository, tmp_path: Path) -> EvidenceFragment:
    raw = b"safe candidate fixture"
    digest = hashlib.sha256(raw).hexdigest()
    artifact = repository.save_artifact(EvidenceArtifact(
        artifact_id="artifact-candidate-contract", owner_id="owner",
        source_type="fixture", content_type="text/plain", original_name="candidate.md",
        raw_uri=str(tmp_path / "blob"), content_hash=digest,
    ))
    return repository.save_fragment(EvidenceFragment(
        fragment_id="fragment-candidate-contract", artifact_id=artifact.artifact_id,
        locator_type="line_range", locator={"start": 1, "end": 1},
        text="safe candidate fixture", text_hash=digest,
    ))


def _claim(fragment: EvidenceFragment, predicate: str, value: object) -> EvidenceClaim:
    return EvidenceClaim(
        subject_id="candidate-owner", predicate=predicate, value=value,
        claim_type="observed_fact", evidence_fragment_ids=[fragment.fragment_id],
        confidence=0.9, extractor=ClaimExtractor(provider="fixture", model="fixture"),
        prompt_version="candidate_claim_extractor_v3", schema_version="candidate_claim_v0.7.1",
    )


def _receipt(claim: EvidenceClaim, index: int) -> ValidationReceipt:
    return ValidationReceipt(
        receipt_id=f"validation-{index}", run_id="run-candidate-contract",
        workflow="candidate_profile", node="extract_and_validate_claims",
        item_index=index, candidate_hash=claim.idempotency_key(),
        subject_ref=claim.subject_id, fragment_ids=claim.evidence_fragment_ids,
        predicate=claim.predicate, status="accepted", extractor="fixture/fixture",
        prompt_version=claim.prompt_version, schema_version_used=claim.schema_version,
    )


def test_real_model_prompt_forbids_generic_predicates_and_shows_canonical_examples(
    tmp_path: Path,
) -> None:
    repository = SQLiteRepository(tmp_path / "evidence.sqlite3")
    fragment = _seed_fragment(repository, tmp_path)
    system = build_claim_extractor_messages([fragment], "candidate-owner")[0][
        "content"
    ]

    assert "Never output generic predicates: education, skill" in system
    assert '"predicate":"education:graduate.institution"' in system
    assert '"predicate":"experience:depression-project.title"' in system
    assert '"predicate":"capability:programming.python"' in system
    assert '"confidence":0.9' in system
    assert 'never strings such as "high"' in system


def test_v071_predicate_contract_matches_projector_shape(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "evidence.sqlite3")
    fragment = _seed_fragment(repository, tmp_path)
    validator = CandidateClaimValidator(repository)

    cases = (
        ("capability:programming.python", {"level": "advanced"}, "capability"),
        ("education:undergrad.institution", "Example University", "education"),
        ("experience:campus-agent.responsibilities", ["Implemented recovery"], "experience"),
    )
    for predicate, value, kind in cases:
        parsed = parse_candidate_predicate(predicate)
        assert parsed.kind == kind
        assert validator.validate(
            _claim(fragment, predicate, value),
            {fragment.artifact_id}, expected_owner_id="owner",
        ).predicate == predicate

    for predicate, value, reason in (
        ("award:first.title", "Best Paper", "unsupported_predicate"),
        ("education.institution", "Legacy shape", "legacy_predicate_forbidden"),
        ("capability:not.in.ontology", {"level": "advanced"}, "unknown_capability_id"),
        ("experience:campus-agent.responsibilities", "not-a-list", "invalid_value_shape"),
    ):
        with pytest.raises(CandidateClaimValidationError) as captured:
            validator.validate(
                _claim(fragment, predicate, value),
                {fragment.artifact_id}, expected_owner_id="owner",
            )
        assert captured.value.reason_code == reason


def test_claims_and_validation_receipts_commit_atomically(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "evidence.sqlite3")
    fragment = _seed_fragment(repository, tmp_path)
    validator = CandidateClaimValidator(repository)
    claims = [
        validator.validate(
            _claim(fragment, "capability:programming.python", {"level": "advanced"}),
            {fragment.artifact_id}, expected_owner_id="owner",
        ),
        validator.validate(
            _claim(fragment, "education:undergrad.institution", "Example University"),
            {fragment.artifact_id}, expected_owner_id="owner",
        ),
    ]
    saved, receipts = repository.save_candidate_claim_batch(
        [(claim, _receipt(claim, index)) for index, claim in enumerate(claims)],
        rejected_receipts=[],
    )
    assert [item.status for item in receipts] == ["accepted", "accepted"]
    assert {item.persisted_claim_id for item in receipts} == {item.claim_id for item in saved}
    assert len(repository.list_validation_receipts(subject_ref="candidate-owner")) == 2

    duplicate_claims, duplicate_receipts = repository.save_candidate_claim_batch(
        [(claim, _receipt(claim, index + 2)) for index, claim in enumerate(claims)],
        rejected_receipts=[],
    )
    assert {item.claim_id for item in duplicate_claims} == {item.claim_id for item in saved}
    assert [item.status for item in duplicate_receipts] == ["duplicate", "duplicate"]
    assert len(repository.list_claims("candidate-owner")) == 2


def test_middle_claim_storage_failure_rolls_back_claims_and_receipts(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "evidence.sqlite3")
    fragment = _seed_fragment(repository, tmp_path)
    validator = CandidateClaimValidator(repository)
    claims = [
        validator.validate(
            _claim(fragment, "capability:programming.python", {"level": "advanced"}),
            {fragment.artifact_id}, expected_owner_id="owner",
        ),
        validator.validate(
            _claim(fragment, "education:undergrad.institution", "Example University"),
            {fragment.artifact_id}, expected_owner_id="owner",
        ),
    ]
    with sqlite3.connect(repository.database_path) as connection:
        connection.execute(
            "CREATE TRIGGER fail_second_claim BEFORE INSERT ON claims "
            "WHEN NEW.predicate LIKE 'education:%' BEGIN "
            "SELECT RAISE(ABORT, 'injected middle claim failure'); END"
        )

    with pytest.raises(sqlite3.DatabaseError, match="injected middle claim failure"):
        repository.save_candidate_claim_batch(
            [(claim, _receipt(claim, index)) for index, claim in enumerate(claims)],
            rejected_receipts=[],
        )
    assert repository.list_claims("candidate-owner") == []
    assert repository.list_validation_receipts(subject_ref="candidate-owner") == []


def test_mixed_model_batch_has_one_receipt_per_item_and_no_silent_claim(tmp_path: Path) -> None:
    class MixedProvider:
        name = "fixture"

        def generate(self, request: object) -> LLMResponse:
            return LLMResponse(
                provider=self.name, model="mixed", usage=None,
                text=json.dumps({"claims": [
                    {
                        "predicate": "capability:programming.python",
                        "value": {"level": "advanced"}, "claim_type": "observed_fact",
                        "evidence_fragment_ids": ["fragment-candidate-contract"], "confidence": 0.9,
                    },
                    {
                        "predicate": "education:undergrad.institution",
                        "value": "Example University", "claim_type": "observed_fact",
                        "evidence_fragment_ids": ["fragment-candidate-contract"], "confidence": 0.9,
                    },
                    {
                        "predicate": "experience:campus-agent.responsibilities",
                        "value": "not-a-list", "claim_type": "observed_fact",
                        "evidence_fragment_ids": ["fragment-candidate-contract"], "confidence": 0.9,
                    },
                    {
                        "predicate": "award:first.title", "value": "Unsupported",
                        "claim_type": "observed_fact",
                        "evidence_fragment_ids": ["fragment-candidate-contract"], "confidence": 0.9,
                    },
                ]}),
            )

    repository = SQLiteRepository(tmp_path / "evidence.sqlite3")
    fragment = _seed_fragment(repository, tmp_path)
    extractor = ClaimExtractorService(
        LLMConfig(model="mixed", cache_enabled=False), MixedProvider(),
        LLMCache(str(tmp_path / "cache")),
    )
    result = ExtractCandidateClaimsTool(repository, extractor).run({
        "run_id": "run-mixed", "subject_id": "candidate-owner", "owner_id": "owner",
        "fragment_ids": [fragment.fragment_id],
    })
    assert result.status == "success"
    receipts = result.records[0]["validation_receipts"]
    assert [item["status"] for item in receipts] == [
        "accepted", "accepted", "rejected", "rejected",
    ]
    assert [item["reason_codes"] for item in receipts[2:]] == [
        ["invalid_value_shape"], ["unsupported_predicate"],
    ]
    assert len(repository.list_claims("candidate-owner")) == 2
    assert len(repository.list_validation_receipts(run_id="run-mixed")) == 4
    assert result.records[0]["fragment_processing"] == {
        fragment.fragment_id: "processed_with_accepted_claims"
    }


def test_all_rejected_batch_is_recorded_as_processed_without_active_claims(tmp_path: Path) -> None:
    class RejectedProvider:
        name = "fixture"

        def generate(self, request: object) -> LLMResponse:
            return LLMResponse(
                provider=self.name, model="rejected", usage=None,
                text=json.dumps({"claims": [{
                    "predicate": "award:first.title", "value": "Unsupported",
                    "claim_type": "observed_fact",
                    "evidence_fragment_ids": ["fragment-candidate-contract"],
                    "confidence": 0.9,
                }]}),
            )

    repository = SQLiteRepository(tmp_path / "evidence.sqlite3")
    fragment = _seed_fragment(repository, tmp_path)
    extractor = ClaimExtractorService(
        LLMConfig(model="rejected", cache_enabled=False), RejectedProvider(),
        LLMCache(str(tmp_path / "cache")),
    )
    result = ExtractCandidateClaimsTool(repository, extractor).run({
        "run_id": "run-all-rejected", "subject_id": "candidate-owner",
        "owner_id": "owner", "fragment_ids": [fragment.fragment_id],
    })
    assert result.status == "success"
    assert repository.list_active_claims("candidate-owner") == []
    assert result.records[0]["validation_receipts"][0]["status"] == "rejected"
    assert result.records[0]["fragment_processing"] == {
        fragment.fragment_id: "processed_all_rejected"
    }


def test_missing_fragment_is_a_fatal_validation_processing_state(tmp_path: Path) -> None:
    class UnusedProvider:
        name = "fixture"

        def generate(self, request: object) -> LLMResponse:
            raise AssertionError("provider must not run for an unknown fragment")

    repository = SQLiteRepository(tmp_path / "evidence.sqlite3")
    extractor = ClaimExtractorService(
        LLMConfig(model="unused", cache_enabled=False), UnusedProvider(),
        LLMCache(str(tmp_path / "cache")),
    )
    result = ExtractCandidateClaimsTool(repository, extractor).run({
        "run_id": "run-fatal", "subject_id": "candidate-owner",
        "owner_id": "owner", "fragment_ids": ["missing-fragment"],
    })
    assert result.status == "failed"
    assert result.metadata["error_type"] == "validation_error"
    assert result.records[0]["fragment_processing"] == {
        "missing-fragment": "fatal_validation_failure"
    }


def test_provider_timeout_remains_retryable_external_failure(tmp_path: Path) -> None:
    class TimeoutProvider:
        name = "timeout"

        def generate(self, request: object):
            raise LLMProviderError(
                "read timed out", error_type="network_timeout", retryable=True
            )

    repository = SQLiteRepository(tmp_path / "evidence.sqlite3")
    fragment = _seed_fragment(repository, tmp_path)
    extractor = ClaimExtractorService(
        LLMConfig(model="timeout", cache_enabled=False), TimeoutProvider(),
        LLMCache(str(tmp_path / "cache")),
    )
    result = ExtractCandidateClaimsTool(repository, extractor).run({
        "run_id": "run-timeout", "subject_id": "candidate-owner",
        "owner_id": "owner", "fragment_ids": [fragment.fragment_id],
    })
    assert result.status == "failed"
    assert result.metadata["error_type"] == "network_timeout"
    assert result.metadata["retryable"] is True
    assert result.records[0]["fragment_processing"] == {
        fragment.fragment_id: "retryable_extraction_failure"
    }


def test_projector_supports_stable_multiple_education_and_experience_ids(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "evidence.sqlite3")
    fragment = _seed_fragment(repository, tmp_path)
    validator = CandidateClaimValidator(repository)
    values = (
        ("education:undergrad.institution", "Example University"),
        ("education:undergrad.degree", "BEng"),
        ("education:graduate.institution", "Graduate University"),
        ("experience:campus-agent.kind", "project"),
        ("experience:campus-agent.title", "Campus Job Agent"),
        ("experience:campus-agent.responsibilities", ["Implemented recovery"]),
    )
    claims = [
        validator.validate(
            _claim(fragment, predicate, value),
            {fragment.artifact_id}, expected_owner_id="owner",
        )
        for predicate, value in values
    ]
    repository.save_candidate_claim_batch(
        [(claim, _receipt(claim, index)) for index, claim in enumerate(claims)],
        rejected_receipts=[],
    )
    snapshot = CandidateProfileProjector(repository).project(
        "candidate-owner", repository.list_active_claims("candidate-owner")
    )
    assert [item["education_id"] for item in snapshot.profile_data["education"]] == [
        "graduate", "undergrad",
    ]
    assert snapshot.profile_data["experiences"][0]["experience_id"] == "campus-agent"
    assert set(snapshot.supporting_claim_ids) == {item.claim_id for item in claims}
