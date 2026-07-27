from __future__ import annotations

import pytest
from pydantic import ValidationError

from campus_job_agent.schemas import (
    ComparisonEntry,
    ComparisonSet,
    GapAssessment,
    MatchingBudget,
    MatchingInputSet,
    TargetDecision,
)
from campus_job_agent.schemas.candidate_graph import stable_union
from campus_job_agent.workflows.profile_matching.repository import SQLiteMatchingRepository


def test_repository_reuses_immutable_record_by_idempotency_key(tmp_path) -> None:
    repository = SQLiteMatchingRepository(tmp_path / "matching.sqlite3")
    first = MatchingInputSet(
        user_id="owner", candidate_profile_snapshot_id="c", career_intent_snapshot_id="i",
        job_instance_profile_snapshot_ids=["r"], snapshot_hashes={"c": "one"},
    )
    second = first.model_copy(update={"input_set_id": "different-id"})
    saved = repository.save("matching_input", first, owner_id="owner", idempotency_key=first.canonical_input_hash)
    reused = repository.save("matching_input", second, owner_id="owner", idempotency_key=first.canonical_input_hash)
    assert reused.input_set_id == saved.input_set_id
    assert len(repository.list("matching_input", MatchingInputSet, owner_id="owner")) == 1


def test_repository_enforces_owner_read_boundary(tmp_path) -> None:
    repository = SQLiteMatchingRepository(tmp_path / "matching.sqlite3")
    item = MatchingInputSet(user_id="owner", candidate_profile_snapshot_id="c", career_intent_snapshot_id="i", job_instance_profile_snapshot_ids=["r"])
    repository.save("matching_input", item, owner_id="owner")
    assert repository.get(item.input_set_id, MatchingInputSet, owner_id="other") is None


def test_lifecycle_update_changes_status_not_comparison_facts(tmp_path) -> None:
    repository = SQLiteMatchingRepository(tmp_path / "matching.sqlite3")
    comparison = ComparisonSet(
        comparison_set_id="cmp", input_set_id="input", canonical_hash="hash",
        entries=[ComparisonEntry(job_instance_profile_snapshot_id="r", gap_assessment_id="g", recommended_tier="review_first", hard_rank=0, blocking_preference_conflict_count=0, core_coverage=1, uncertainty_weight=0, stable_tie_breaker="r")],
    )
    repository.save("comparison", comparison, owner_id="owner")
    stale = repository.replace_lifecycle("cmp", ComparisonSet, "stale")
    assert stale.status == "stale"
    assert stale.entries == comparison.entries


def _decision(decision_id: str, response_id: str, job: str) -> TargetDecision:
    return TargetDecision(
        decision_id=decision_id, user_id="owner", comparison_set_id="cmp",
        job_instance_profile_snapshot_id=job, status="selected",
        created_from_response_id=response_id,
    )


def test_decision_batch_is_atomic_and_duplicate_replay_is_idempotent(tmp_path) -> None:
    repository = SQLiteMatchingRepository(tmp_path / "matching.sqlite3")
    batch = [_decision("d1", "response", "r1"), _decision("d2", "response", "r2")]
    first = repository.save_decision_batch(batch, owner_id="owner", response_id="response", payload_hash="same")
    replay = repository.save_decision_batch(batch, owner_id="owner", response_id="response", payload_hash="same")
    assert [item.decision_id for item in first] == [item.decision_id for item in replay]
    assert len(repository.list("target_decision", TargetDecision, owner_id="owner")) == 2


def test_response_id_with_changed_payload_is_rejected(tmp_path) -> None:
    repository = SQLiteMatchingRepository(tmp_path / "matching.sqlite3")
    repository.save_decision_batch([_decision("d1", "response", "r1")], owner_id="owner", response_id="response", payload_hash="one")
    with pytest.raises(ValueError, match="idempotency_conflict"):
        repository.save_decision_batch([_decision("d2", "response", "r2")], owner_id="owner", response_id="response", payload_hash="two")
    assert len(repository.list("target_decision", TargetDecision, owner_id="owner")) == 1


def test_budget_is_frozen_and_validated() -> None:
    budget = MatchingBudget()
    with pytest.raises(ValidationError):
        MatchingBudget(max_match_rounds=0)
    with pytest.raises(ValidationError):
        budget.max_targets = 100


def test_stable_union_reducer_deduplicates_processed_response_ids() -> None:
    assert stable_union(["response-1"], ["response-1", "response-2"]) == ["response-1", "response-2"]


def test_gap_assessment_v06_stores_exact_snapshot_refs() -> None:
    assessment = GapAssessment(
        assessment_id="g", schema_version="v0.6", input_set_id="input",
        candidate_profile_snapshot_id="c", career_intent_snapshot_id="i",
        job_instance_profile_snapshot_id="r", role_profile_snapshot_id="r",
        hard_constraint_status="unknown", status="current",
    )
    assert assessment.candidate_profile_snapshot_id == "c"
    assert assessment.career_intent_snapshot_id == "i"
    assert assessment.job_instance_profile_snapshot_id == "r"
