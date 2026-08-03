from __future__ import annotations

from datetime import UTC, datetime, timedelta
import hashlib
import json
import sqlite3

import pytest

from campus_job_agent.evidence.claim_resolution import (
    relation_for_values,
    resolve_candidate_claims,
)
from campus_job_agent.schemas import ClaimExtractor, EvidenceClaim
from campus_job_agent.schemas import EvidenceArtifact, EvidenceFragment
from campus_job_agent.evidence.claim_validator import ClaimValidationError, ClaimValidator
from campus_job_agent.storage import SQLiteRepository
from campus_job_agent.cli import _next_action


NOW = datetime(2026, 8, 2, tzinfo=UTC)


def _claim(
    claim_id: str,
    predicate: str,
    value: object,
    *,
    origin_kind: str = "legacy",
    origin_ref: str | None = None,
    claim_type: str = "user_reported",
    provider: str = "human",
    created_offset: int = 0,
    supersedes: list[str] | None = None,
) -> EvidenceClaim:
    created_at = NOW + timedelta(minutes=created_offset)
    return EvidenceClaim(
        claim_id=claim_id,
        subject_id="candidate-1",
        predicate=predicate,
        value=value,
        claim_type=claim_type,
        evidence_fragment_ids=[f"fragment-{claim_id}"],
        confidence=1.0,
        extractor=ClaimExtractor(provider=provider, model="test"),
        prompt_version="test",
        schema_version="candidate_claim_v0.7.1.3",
        origin_kind=origin_kind,
        origin_ref=origin_ref,
        effective_at=created_at,
        created_at=created_at,
        supersedes_claim_ids=supersedes or [],
    )


def test_legacy_claim_defaults_effective_time_and_single_predecessor() -> None:
    claim = EvidenceClaim.model_validate(
        {
            "claim_id": "legacy",
            "subject_id": "candidate-1",
            "predicate": "capability:programming.python",
            "value": {"level": "advanced"},
            "claim_type": "model_inference",
            "evidence_fragment_ids": ["fragment-legacy"],
            "confidence": 0.8,
            "extractor": {"provider": "deepseek", "model": "old"},
            "prompt_version": "old",
            "created_at": NOW,
            "supersedes_claim_id": "older",
        }
    )

    assert claim.origin_kind == "legacy"
    assert claim.effective_at == claim.created_at
    assert claim.all_supersedes_claim_ids == ["older"]


def test_resolution_uses_current_resume_and_keeps_long_term_overlays() -> None:
    stale = _claim(
        "stale", "capability:programming.python", {"level": "advanced"},
        origin_kind="resume_evidence", origin_ref="resume-v1", provider="deepseek",
    )
    current = _claim(
        "current", "capability:programming.python", {"level": "expert"},
        origin_kind="resume_evidence", origin_ref="resume-v2", provider="deepseek",
    )
    conversation = _claim(
        "conversation", "education:edu-1.major", "软件工程",
        origin_kind="conversation_response", origin_ref="response-artifact",
    )
    feedback = _claim(
        "feedback", "capability:database.sql", {"level": "intermediate"},
        origin_kind="feedback_event", origin_ref="feedback-1",
        claim_type="feedback_signal", provider="deterministic",
    )
    legacy_model = _claim(
        "legacy-model", "education:edu-1.degree", "本科",
        claim_type="model_inference", provider="deepseek",
    )

    result = resolve_candidate_claims(
        [stale, current, conversation, feedback, legacy_model],
        current_resume_evidence_id="resume-v2",
    )

    assert {claim.claim_id for claim in result.selected_claims} == {
        "current", "conversation", "feedback"
    }
    assert result.summary.exclusion_reasons == {
        "legacy-model": "legacy_model_isolated",
        "stale": "stale_resume_evidence",
    }


def test_active_successor_excludes_all_predecessors() -> None:
    first = _claim("first", "capability:database.sql", {"level": "beginner"})
    second = _claim("second", "capability:database.sql", {"level": "advanced"})
    successor = _claim(
        "successor", "capability:database.sql", {"level": "intermediate"},
        origin_kind="conversation_response", origin_ref="response-1",
        supersedes=["first", "second"], created_offset=1,
    )

    result = resolve_candidate_claims(
        [first, second, successor], current_resume_evidence_id="resume-v2"
    )

    assert [claim.claim_id for claim in result.selected_claims] == ["successor"]
    assert result.summary.exclusion_reasons == {
        "first": "superseded_by_active_claim",
        "second": "superseded_by_active_claim",
    }


def test_semantic_equivalence_and_date_refinement_are_not_conflicts() -> None:
    assert relation_for_values(
        "capability:database.sql",
        {"level": "intermediate"},
        {"level": "intermediate", "raw_label": "SQL", "raw_level": "熟悉"},
    ) == "equivalent"
    assert relation_for_values(
        "capability:database.sql", "熟悉", {"level": "intermediate", "raw_label": "SQL"}
    ) == "equivalent"
    assert relation_for_values(
        "experience:project-1.kind", "项目经历",
        {"kind": "project", "context": "unspecified", "raw_label": "项目经历"},
    ) == "equivalent"
    assert relation_for_values(
        "education:edu-1.graduation_year", "2024", "2024-07"
    ) == "refinement"
    assert relation_for_values(
        "education:edu-1.graduation_year", "2024-06", "2024-07"
    ) == "conflict"


def test_cross_source_different_capability_levels_remain_conflicted() -> None:
    resume = _claim(
        "resume", "capability:programming.python", {"level": "advanced"},
        origin_kind="resume_evidence", origin_ref="resume-v2", provider="deepseek",
    )
    feedback = _claim(
        "feedback", "capability:programming.python", {"level": "expert"},
        origin_kind="feedback_event", origin_ref="feedback-1",
        claim_type="feedback_signal", provider="deterministic", created_offset=1,
    )

    result = resolve_candidate_claims(
        [resume, feedback], current_resume_evidence_id="resume-v2"
    )

    assert set(result.summary.conflicted_claim_ids) == {"resume", "feedback"}


def _repository_with_fragment(tmp_path) -> SQLiteRepository:
    repository = SQLiteRepository(tmp_path / "evidence.sqlite3")
    digest = hashlib.sha256(b"correction evidence").hexdigest()
    repository.save_artifact(EvidenceArtifact(
        artifact_id="artifact-correction", owner_id="owner-1", source_type="conversation_response",
        content_type="text/plain", original_name="response.txt", raw_uri="memory://response",
        content_hash=digest,
    ))
    repository.save_fragment(EvidenceFragment(
        fragment_id="fragment-correction", artifact_id="artifact-correction",
        locator_type="json_pointer", locator={"pointer": "/correction"},
        text="correction evidence", text_hash=digest,
    ))
    return repository


def test_multi_predecessor_correction_is_atomic(tmp_path) -> None:
    repository = _repository_with_fragment(tmp_path)
    first = _claim("first", "capability:database.sql", {"level": "beginner"}).model_copy(
        update={"evidence_fragment_ids": ["fragment-correction"]}
    )
    second = _claim("second", "capability:database.sql", {"level": "advanced"}).model_copy(
        update={"evidence_fragment_ids": ["fragment-correction"]}
    )
    repository.save_claim(first)
    repository.save_claim(second)
    successor = _claim(
        "successor", "capability:database.sql", {"level": "intermediate"},
        origin_kind="conversation_response", origin_ref="artifact-correction",
        supersedes=["first", "second"], created_offset=1,
    ).model_copy(update={"evidence_fragment_ids": ["fragment-correction"]})

    saved = ClaimValidator(repository).validate_and_save_superseding(
        successor, {"artifact-correction"}, expected_owner_id="owner-1"
    )

    assert saved.claim_id == "successor"
    assert repository.get_claim("first").status == "superseded"
    assert repository.get_claim("second").status == "superseded"
    assert [item.claim_id for item in repository.list_active_claims("candidate-1")] == [
        "successor"
    ]


def test_supersede_rejects_cross_subject_or_predicate(tmp_path) -> None:
    repository = _repository_with_fragment(tmp_path)
    previous = _claim("previous", "capability:database.sql", {"level": "beginner"}).model_copy(
        update={"evidence_fragment_ids": ["fragment-correction"]}
    )
    repository.save_claim(previous)
    validator = ClaimValidator(repository)

    for update in (
        {"subject_id": "candidate-2"},
        {"predicate": "capability:programming.python"},
    ):
        successor = _claim(
            "successor", "capability:database.sql", {"level": "intermediate"},
            origin_kind="conversation_response", origin_ref="artifact-correction",
            supersedes=["previous"], created_offset=1,
        ).model_copy(update={"evidence_fragment_ids": ["fragment-correction"], **update})
        with pytest.raises(ClaimValidationError, match="subject and predicate"):
            validator.validate(successor, {"artifact-correction"}, "owner-1")


def test_session_recovery_next_action_uses_status_and_current_refs() -> None:
    assert _next_action("candidate", status="failed") == "session.resume"
    assert _next_action(
        "candidate",
        current_refs={"resume_evidence_snapshot_id": "resume-v2"},
    ) == "candidate.build"
    assert _next_action(
        "candidate",
        pending_request="request-candidate-1",
        current_refs={"resume_evidence_snapshot_id": "resume-v2"},
    ) == "candidate.resume"
    assert _next_action("candidate", current_refs={}) == "resume.import"


def test_reading_legacy_claim_does_not_rewrite_stored_payload(tmp_path) -> None:
    repository = _repository_with_fragment(tmp_path)
    payload = {
        "claim_id": "legacy-stored",
        "subject_id": "candidate-1",
        "predicate": "capability:database.sql",
        "value": {"level": "advanced"},
        "claim_type": "model_inference",
        "evidence_fragment_ids": ["fragment-correction"],
        "source_evidence_ids": [],
        "confidence": 0.8,
        "extractor": {"provider": "deepseek", "model": "legacy-model"},
        "prompt_version": "legacy",
        "schema_version": "candidate_claim_v0.7.1",
        "status": "active",
        "created_at": NOW.isoformat(),
        "supersedes_claim_id": None,
    }
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    with sqlite3.connect(repository.database_path) as connection:
        connection.execute(
            "INSERT INTO claims VALUES (?, ?, ?, ?, ?, ?)",
            ("legacy-stored", "candidate-1", "capability:database.sql", "legacy-key", raw, NOW.isoformat()),
        )
        connection.execute(
            "INSERT INTO claim_fragments VALUES (?, ?)",
            ("legacy-stored", "fragment-correction"),
        )

    loaded = repository.get_claim("legacy-stored")
    assert loaded.origin_kind == "legacy"
    assert loaded.effective_at == NOW
    resolve_candidate_claims([loaded], current_resume_evidence_id="resume-v2")
    with sqlite3.connect(repository.database_path) as connection:
        stored = connection.execute(
            "SELECT payload_json FROM claims WHERE claim_id = ?", ("legacy-stored",)
        ).fetchone()[0]
    assert stored == raw


def test_supersede_lineage_cycle_is_rejected(tmp_path) -> None:
    repository = _repository_with_fragment(tmp_path)
    previous = _claim(
        "previous", "capability:database.sql", {"level": "beginner"},
        supersedes=["future"],
    ).model_copy(update={"evidence_fragment_ids": ["fragment-correction"]})
    repository.save_claim(previous)
    future = _claim(
        "future", "capability:database.sql", {"level": "intermediate"},
        origin_kind="conversation_response", origin_ref="artifact-correction",
        supersedes=["previous"], created_offset=1,
    ).model_copy(update={"evidence_fragment_ids": ["fragment-correction"]})

    with pytest.raises(ClaimValidationError, match="cycle"):
        ClaimValidator(repository).validate(
            future, {"artifact-correction"}, expected_owner_id="owner-1"
        )
