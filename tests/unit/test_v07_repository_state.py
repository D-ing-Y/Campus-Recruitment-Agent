from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError

from campus_job_agent.schemas import (
    FeedbackBudget, FeedbackDirective, FeedbackInput, LearningPlan, PreparationBudget,
    PreparationConstraints, PreparationInputSet,
)
from campus_job_agent.storage import LocalBlobStore, SQLiteRepository
from campus_job_agent.workflows.feedback.ingestion import FeedbackIngestionError, FeedbackIngestor
from campus_job_agent.workflows.feedback.repository import SQLiteFeedbackRepository
from campus_job_agent.workflows.preparation_plan.repository import SQLitePreparationRepository


def _constraints(hours=10):
    return PreparationConstraints(horizon_start=date(2026, 8, 1), horizon_end=date(2026, 8, 10),
                                  weekly_hours=hours, daily_max_hours=4)


def test_preparation_repository_reuses_same_immutable_object(tmp_path):
    repository = SQLitePreparationRepository(tmp_path / "domain.sqlite3")
    first = _constraints()
    saved = repository.save("constraints", first, owner_id="owner")
    replay = repository.save("constraints", first, owner_id="owner")
    assert saved.constraints_id == replay.constraints_id
    assert repository.count("constraints", owner_id="owner") == 1


def test_same_record_id_with_different_payload_is_conflict(tmp_path):
    repository = SQLitePreparationRepository(tmp_path / "domain.sqlite3")
    first = _constraints()
    repository.save("constraints", first, owner_id="owner")
    changed = _constraints(hours=5).model_copy(update={"constraints_id": first.constraints_id})
    with pytest.raises(ValueError, match="idempotency_conflict"):
        repository.save("constraints", changed, owner_id="owner")
    batch_new = _constraints(hours=8)
    with pytest.raises(ValueError, match="idempotency_conflict"):
        repository.save_batch([
            ("constraints", batch_new, "owner", None),
            ("constraints", changed, "owner", None),
        ])
    assert repository.get(batch_new.constraints_id, PreparationConstraints, owner_id="owner") is None


def test_repository_enforces_owner_read_boundary(tmp_path):
    repository = SQLitePreparationRepository(tmp_path / "domain.sqlite3")
    item = repository.save("constraints", _constraints(), owner_id="owner")
    assert repository.get(item.constraints_id, PreparationConstraints, owner_id="other") is None


def test_lifecycle_update_does_not_change_plan_facts(tmp_path):
    repository = SQLitePreparationRepository(tmp_path / "domain.sqlite3")
    plan = LearningPlan(
        learning_plan_id="plan-1", user_id="owner", input_set_id="input", constraints_id="constraints",
        package_id="package", objective_ids=["o"], activity_ids=["a"], priority_factor_ids=["p"],
        schedule=[], schedule_hash="sha256:schedule", canonical_hash="sha256:plan",
    )
    repository.save("learning_plan", plan, owner_id="owner")
    stale = repository.replace_lifecycle("plan-1", LearningPlan, "stale")
    assert stale.status == "stale"
    assert stale.activity_ids == plan.activity_ids
    assert stale.canonical_hash == plan.canonical_hash


def test_response_receipt_replay_and_conflict(tmp_path):
    repository = SQLiteFeedbackRepository(tmp_path / "domain.sqlite3")
    assert repository.save_response_result("response", "same", {"record_ids": ["one"]}) == {"record_ids": ["one"]}
    assert repository.save_response_result("response", "same", {"record_ids": ["two"]}) == {"record_ids": ["one"]}
    with pytest.raises(ValueError, match="idempotency_conflict"):
        repository.save_response_result("response", "changed", {})


def test_directive_resolution_receipt_is_once_only(tmp_path):
    repository = SQLiteFeedbackRepository(tmp_path / "domain.sqlite3")
    payload = {"directive_id": "d", "resolved_refs": ["s2"]}
    assert repository.save_resolution("d", "r", payload) == payload
    assert repository.save_resolution("d", "r", payload) == payload
    with pytest.raises(ValueError, match="idempotency_conflict"):
        repository.save_resolution("d", "other", payload)


@pytest.mark.parametrize("model,field", [
    (PreparationBudget(max_plan_rounds=1), "max_plan_rounds"),
    (FeedbackBudget(max_feedback_items=1), "max_feedback_items"),
])
def test_budgets_are_frozen(model, field):
    with pytest.raises(ValidationError):
        setattr(model, field, 100)


def test_feedback_file_must_be_inside_authorized_root(tmp_path):
    evidence = SQLiteRepository(tmp_path / "domain.sqlite3")
    feedback = SQLiteFeedbackRepository(tmp_path / "domain.sqlite3")
    path = tmp_path / "private.txt"
    path.write_text("feedback", encoding="utf-8")
    ingestor = FeedbackIngestor(blob_store=LocalBlobStore(tmp_path / "blobs"), evidence_repository=evidence,
                                feedback_repository=feedback)
    value = FeedbackInput(feedback_type="other", source_kind="imported_document",
                          occurred_at=datetime.now(UTC), file_path=str(path))
    with pytest.raises(FeedbackIngestionError, match="permission_denied"):
        ingestor.ingest(owner_id="owner", feedback_input=value, allowed_path_roots=[str(tmp_path / "allowed")],
                        plan_id=None, activity_id=None, target_job_profile_ids=[])
    assert feedback.count("feedback_event", owner_id="owner") == 0
    event, artifact, fragments = ingestor.ingest(
        owner_id="owner", feedback_input=value, allowed_path_roots=[str(tmp_path)],
        plan_id=None, activity_id=None, target_job_profile_ids=[],
    )
    assert event.raw_artifact_ids == [artifact.artifact_id]
    assert fragments[0].artifact_id == artifact.artifact_id
    other_event, other_artifact, _ = ingestor.ingest(
        owner_id="other", feedback_input=value, allowed_path_roots=[str(tmp_path)],
        plan_id=None, activity_id=None, target_job_profile_ids=[],
    )
    assert other_event.user_id == "other"
    assert other_artifact.artifact_id != artifact.artifact_id


class _FailingBlobStore:
    def put(self, key, data):
        raise OSError("disk full")


def test_raw_archive_failure_prevents_fragment_event_and_interpretation(tmp_path):
    evidence = SQLiteRepository(tmp_path / "domain.sqlite3")
    feedback = SQLiteFeedbackRepository(tmp_path / "domain.sqlite3")
    ingestor = FeedbackIngestor(blob_store=_FailingBlobStore(), evidence_repository=evidence,
                                feedback_repository=feedback)
    value = FeedbackInput(feedback_type="other", source_kind="self_reported",
                          occurred_at=datetime.now(UTC), text="private feedback")
    with pytest.raises(FeedbackIngestionError, match="feedback_raw_archive_failed"):
        ingestor.ingest(owner_id="owner", feedback_input=value, allowed_path_roots=[],
                        plan_id=None, activity_id=None, target_job_profile_ids=[])
    assert feedback.count("feedback_event", owner_id="owner") == 0


def test_feedback_report_contract_objects_do_not_store_raw_text_in_directive(tmp_path):
    repository = SQLiteFeedbackRepository(tmp_path / "domain.sqlite3")
    directive = FeedbackDirective(
        directive_id="directive-1", directive_type="replan_required",
        originating_feedback_event_id="feedback-1", reason_codes=["replan"],
    )
    saved = repository.save("feedback_directive", directive, owner_id="owner")
    assert "private feedback" not in saved.model_dump_json()
