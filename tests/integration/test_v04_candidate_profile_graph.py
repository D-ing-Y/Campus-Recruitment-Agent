from __future__ import annotations

from pathlib import Path
import sqlite3

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from pypdf import PdfWriter

from campus_job_agent.evidence import ClaimExtractorService
from campus_job_agent.llm import LLMCache, LLMConfig, MockLLMProvider
from campus_job_agent.schemas import (
    HumanAnswer,
    HumanInteractionResponse,
    ProfileCorrection,
    ToolResult,
)
from campus_job_agent.storage import LocalBlobStore, SQLiteRepository
from campus_job_agent.tools import build_candidate_profile_registry
from campus_job_agent.workflows.candidate_profile import (
    CandidateProfileGraphRuntime,
    LLMSufficiencyEvaluator,
    create_candidate_profile_state,
    open_sqlite_checkpointer,
)
from campus_job_agent.workflows.candidate_profile.graph import (
    CandidateProfileWorkflowError,
)


FIXTURES = Path(__file__).parents[1] / "fixtures" / "v04"


def _runtime(tmp_path, repository, checkpointer, *, evaluator=None):
    extractor = ClaimExtractorService(
        LLMConfig(model="mock-claims", cache_enabled=False),
        MockLLMProvider(),
        LLMCache(str(tmp_path / "cache")),
    )
    registry = build_candidate_profile_registry(
        blob_store=LocalBlobStore(tmp_path / "blobs"),
        repository=repository,
        profile_repository=repository,
        claim_extractor=extractor,
    )
    return CandidateProfileGraphRuntime(
        registry=registry,
        evidence_repository=repository,
        profile_repository=repository,
        evaluator=evaluator,
        checkpointer=checkpointer,
    )


def _request(result):
    return result["__interrupt__"][0].value


def test_sufficient_material_completes_without_interrupt(tmp_path) -> None:
    repository = SQLiteRepository(tmp_path / "evidence.sqlite3")
    runtime = _runtime(tmp_path, repository, InMemorySaver())
    result = runtime.invoke(
        create_candidate_profile_state(
            thread_id="thread-sufficient",
            user_id="owner",
            candidate_id="candidate",
            input_paths=[str(FIXTURES / "candidate_sufficient.md")],
        )
    )
    assert result["status"] == "completed"
    assert result["next_action"] == "complete"
    assert "__interrupt__" not in result
    profile = repository.get_latest_profile("candidate", "candidate")
    assert profile is not None
    assert profile.schema_version == "v0.4"
    assert profile.profile_data["responsibility_boundaries"]
    assert set(profile.supporting_claim_ids) <= {
        item.claim_id for item in repository.list_claims("candidate")
    }


def test_answer_is_archived_before_profile_update_and_skip_is_not_reasked(
    tmp_path,
) -> None:
    repository = SQLiteRepository(tmp_path / "evidence.sqlite3")
    runtime = _runtime(tmp_path, repository, InMemorySaver())
    result = runtime.invoke(
        create_candidate_profile_state(
            thread_id="thread-answer",
            user_id="owner",
            candidate_id="candidate",
            input_paths=[str(FIXTURES / "candidate_missing_responsibility.md")],
        )
    )
    request = _request(result)
    assert result["next_action"] == "ask_user"
    question = request["questions"][0]
    before_version = repository.get_latest_profile("candidate", "candidate").version
    response = HumanInteractionResponse(
        response_id="response-answer",
        request_id=request["request_id"],
        thread_id="thread-answer",
        user_id="owner",
        action="answer",
        answers=[
            HumanAnswer(
                question_id=question["question_id"],
                text="我负责 LangGraph 工作流、评估与恢复测试，未负责爬虫。",
            )
        ],
    )
    completed = runtime.resume(thread_id="thread-answer", response=response)
    assert completed["status"] == "completed"
    response_artifacts = [
        item
        for item in [
            repository.get_artifact(value)
            for value in completed["active_artifact_ids"]
        ]
        if item is not None and item.content_type == "conversation_response"
    ]
    assert len(response_artifacts) == 1
    response_fragments = repository.list_fragments(
        response_artifacts[0].artifact_id
    )
    assert response_fragments[0].locator_type == "json_pointer"
    claims = repository.list_claims("candidate")
    answer_claims = [item for item in claims if item.claim_type == "user_reported"]
    assert answer_claims
    assert answer_claims[0].evidence_fragment_ids == [
        response_fragments[0].fragment_id
    ]
    assert repository.get_latest_profile("candidate", "candidate").version > before_version

    second_repository = SQLiteRepository(tmp_path / "skip-evidence.sqlite3")
    skip_runtime = _runtime(tmp_path / "skip", second_repository, InMemorySaver())
    interrupted = skip_runtime.invoke(
        create_candidate_profile_state(
            thread_id="thread-skip",
            user_id="owner",
            candidate_id="candidate-skip",
            input_paths=[str(FIXTURES / "candidate_missing_responsibility.md")],
        )
    )
    skip_request = _request(interrupted)
    skipped = skip_runtime.resume(
        thread_id="thread-skip",
        response=HumanInteractionResponse(
            response_id="response-skip",
            request_id=skip_request["request_id"],
            thread_id="thread-skip",
            user_id="owner",
            action="skip",
            skipped_ids=[skip_request["questions"][0]["question_id"]],
        ),
    )
    assert skipped["status"] == "completed_with_unknowns"
    assert "__interrupt__" not in skipped
    assert "gap:experience.project.responsibility" in skipped["skipped_gap_ids"]


def test_wrong_request_id_is_rejected_without_evidence_write(tmp_path) -> None:
    repository = SQLiteRepository(tmp_path / "evidence.sqlite3")
    runtime = _runtime(tmp_path, repository, InMemorySaver())
    result = runtime.invoke(
        create_candidate_profile_state(
            thread_id="thread-invalid",
            user_id="owner",
            candidate_id="candidate",
            input_paths=[str(FIXTURES / "candidate_missing_responsibility.md")],
        )
    )
    request = _request(result)
    count = len(repository.list_claims("candidate"))
    with pytest.raises(CandidateProfileWorkflowError, match="request_id"):
        runtime.resume(
            thread_id="thread-invalid",
            response=HumanInteractionResponse(
                response_id="bad-response",
                request_id="wrong-request",
                thread_id="thread-invalid",
                user_id="owner",
                action="answer",
                answers=[
                    HumanAnswer(
                        question_id=request["questions"][0]["question_id"],
                        text="No write should occur.",
                    )
                ],
            ),
        )
    assert len(repository.list_claims("candidate")) == count


def test_sqlite_checkpoint_recovers_across_graph_instances_and_resume_is_idempotent(
    tmp_path,
) -> None:
    repository = SQLiteRepository(tmp_path / "evidence.sqlite3")
    checkpoint_path = tmp_path / "checkpoints.sqlite3"
    with open_sqlite_checkpointer(checkpoint_path) as saver:
        runtime = _runtime(tmp_path, repository, saver)
        interrupted = runtime.invoke(
            create_candidate_profile_state(
                thread_id="thread-restart",
                user_id="owner",
                candidate_id="candidate",
                input_paths=[str(FIXTURES / "candidate_missing_responsibility.md")],
            )
        )
        request = _request(interrupted)
    response = HumanInteractionResponse(
        response_id="response-restart",
        request_id=request["request_id"],
        thread_id="thread-restart",
        user_id="owner",
        action="answer",
        answers=[
            HumanAnswer(
                question_id=request["questions"][0]["question_id"],
                text="I owned the graph and checkpoint recovery tests.",
            )
        ],
    )
    with open_sqlite_checkpointer(checkpoint_path) as saver:
        restarted = _runtime(tmp_path, repository, saver)
        completed = restarted.resume(thread_id="thread-restart", response=response)
        assert completed["status"] == "completed"
        counts = (
            len(repository.list_claims("candidate")),
            len(repository.list_profiles("candidate", "candidate")),
        )
        replayed = restarted.resume(thread_id="thread-restart", response=response)
        assert replayed["status"] == "completed"
        assert counts == (
            len(repository.list_claims("candidate")),
            len(repository.list_profiles("candidate", "candidate")),
        )
        with pytest.raises(
            CandidateProfileWorkflowError, match="idempotency_conflict"
        ):
            restarted.resume(
                thread_id="thread-restart",
                response=response.model_copy(
                    update={
                        "answers": [
                            HumanAnswer(
                                question_id=request["questions"][0]["question_id"],
                                text="A conflicting replay payload.",
                            )
                        ]
                    }
                ),
            )


def test_max_profile_round_budget_terminates(tmp_path) -> None:
    repository = SQLiteRepository(tmp_path / "evidence.sqlite3")
    runtime = _runtime(tmp_path, repository, InMemorySaver())
    result = runtime.invoke(
        create_candidate_profile_state(
            thread_id="thread-budget",
            user_id="owner",
            candidate_id="candidate",
            input_paths=[str(FIXTURES / "candidate_missing_responsibility.md")],
            budgets={
                "max_profile_rounds": 1,
                "max_questions_per_interrupt": 3,
                "max_llm_calls": 12,
                "max_tool_calls": 30,
            },
        )
    )
    assert result["status"] == "completed_with_unknowns"
    assert result["next_action"] == "finalize_with_unknowns"
    assert result["counters"]["profile_rounds"] == 1


def test_uploaded_material_is_reingested_then_completes(tmp_path) -> None:
    repository = SQLiteRepository(tmp_path / "evidence.sqlite3")
    runtime = _runtime(tmp_path, repository, InMemorySaver())
    interrupted = runtime.invoke(
        create_candidate_profile_state(
            thread_id="thread-upload",
            user_id="owner",
            candidate_id="candidate",
            input_paths=[],
        )
    )
    request = _request(interrupted)
    assert request["interaction_type"] == "provide_materials"
    completed = runtime.resume(
        thread_id="thread-upload",
        response=HumanInteractionResponse(
            response_id="response-upload",
            request_id=request["request_id"],
            thread_id="thread-upload",
            user_id="owner",
            action="upload",
            file_paths=[str(FIXTURES / "candidate_sufficient.md")],
        ),
    )
    assert completed["status"] == "completed"
    assert completed["next_action"] == "complete"
    assert len(completed["active_artifact_ids"]) == 2
    assert completed["input_paths"] == []


def test_correction_supersedes_claims_resolves_conflict_and_has_version_diff(
    tmp_path,
) -> None:
    repository = SQLiteRepository(tmp_path / "evidence.sqlite3")
    runtime = _runtime(tmp_path, repository, InMemorySaver())
    interrupted = runtime.invoke(
        create_candidate_profile_state(
            thread_id="thread-correction",
            user_id="owner",
            candidate_id="candidate",
            input_paths=[
                str(FIXTURES / "candidate_conflict_a.md"),
                str(FIXTURES / "candidate_conflict_b.md"),
            ],
        )
    )
    before_id = interrupted["candidate_profile_snapshot_id"]
    before = repository.get_profile(before_id)
    assert before is not None
    assert before.profile_data["conflicts"]
    request = _request(interrupted)
    conflict = before.profile_data["conflicts"][0]
    corrected = runtime.resume(
        thread_id="thread-correction",
        response=HumanInteractionResponse(
            response_id="response-correction",
            request_id=request["request_id"],
            thread_id="thread-correction",
            user_id="owner",
            action="correct",
            corrections=[
                ProfileCorrection(
                    correction_id="correction-1",
                    candidate_id="candidate",
                    target_path=conflict["predicate"],
                    operation="replace",
                    new_value="Implemented the evaluation tests only.",
                    reason="The material overstated the candidate's scope.",
                    supersedes_claim_ids=conflict["claim_ids"],
                )
            ],
        ),
    )
    assert corrected["status"] == "completed"
    latest = repository.get_latest_profile("candidate", "candidate")
    assert latest is not None and latest.snapshot_id != before_id
    assert latest.profile_data["conflicts"] == []
    old_claims = [repository.get_claim(value) for value in conflict["claim_ids"]]
    assert all(item is not None and item.status == "superseded" for item in old_claims)
    correction_claims = [
        item
        for item in repository.list_claims("candidate")
        if item.supersedes_claim_id in conflict["claim_ids"]
    ]
    assert len(correction_claims) == len(conflict["claim_ids"])
    registry = runtime.registry
    diff = registry.run(
        "profile.diff_candidate_versions",
        {
            "from_snapshot_id": before_id,
            "to_snapshot_id": latest.snapshot_id,
        },
    )
    assert diff.status == "success"
    assert f"conflict:{conflict['predicate']}" in diff.records[0]["resolved_conflicts"]


def test_llm_sufficiency_failure_uses_deterministic_fallback(tmp_path) -> None:
    repository = SQLiteRepository(tmp_path / "evidence.sqlite3")
    evaluator = LLMSufficiencyEvaluator(
        LLMConfig(
            model="broken-sufficiency",
            cache_enabled=False,
            max_retries=1,
        ),
        MockLLMProvider("always_invalid_json"),
        LLMCache(str(tmp_path / "sufficiency-cache")),
    )
    runtime = _runtime(
        tmp_path,
        repository,
        InMemorySaver(),
        evaluator=evaluator,
    )
    result = runtime.invoke(
        create_candidate_profile_state(
            thread_id="thread-llm-fallback",
            user_id="owner",
            candidate_id="candidate",
            input_paths=[str(FIXTURES / "candidate_sufficient.md")],
        )
    )
    assert result["status"] == "completed"
    assert any(
        item.get("fallback") == "deterministic" for item in result["errors"]
    )
    assert any(item["status"] == "failed" for item in result["llm_calls"])
    assert result["counters"]["llm_calls"] == 3


def test_storage_tool_failure_is_fatal_and_does_not_create_profile(tmp_path) -> None:
    repository = SQLiteRepository(tmp_path / "evidence.sqlite3")
    runtime = _runtime(tmp_path, repository, InMemorySaver())

    class BrokenProfileTool:
        name = "profile.project_candidate"

        def run(self, args):
            return ToolResult(
                tool_name=self.name,
                status="failed",
                records=[],
                evidence_ids=[],
                error="database unavailable",
                metadata={
                    "error_type": "storage_error",
                    "retryable": False,
                    "needs_user_action": False,
                },
            )

    runtime.registry.register(BrokenProfileTool())
    result = runtime.invoke(
        create_candidate_profile_state(
            thread_id="thread-storage-failure",
            user_id="owner",
            candidate_id="candidate",
            input_paths=[str(FIXTURES / "candidate_sufficient.md")],
        )
    )
    assert result["status"] == "failed"
    assert repository.get_latest_profile("candidate", "candidate") is None
    assert any(item.get("error_type") == "storage_error" for item in result["errors"])


def test_checkpoint_failure_is_reported_and_not_claimed_recoverable(tmp_path) -> None:
    repository = SQLiteRepository(tmp_path / "evidence.sqlite3")

    class BrokenCheckpointer(InMemorySaver):
        def put(self, *args, **kwargs):
            raise sqlite3.OperationalError("checkpoint disk unavailable")

    runtime = _runtime(tmp_path, repository, BrokenCheckpointer())
    with pytest.raises(CandidateProfileWorkflowError, match="checkpoint_error"):
        runtime.invoke(
            create_candidate_profile_state(
                thread_id="thread-checkpoint-failure",
                user_id="owner",
                candidate_id="candidate",
                input_paths=[str(FIXTURES / "candidate_sufficient.md")],
            )
        )
    # Evidence writes are independently idempotent facts; checkpoint failure
    # must not be presented as recoverable, but it need not erase valid facts.
    assert all(
        item.evidence_fragment_ids for item in repository.list_claims("candidate")
    )


def test_scanned_pdf_requests_material_then_skip_finishes_with_unknowns(
    tmp_path,
) -> None:
    scan = tmp_path / "scan.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    with scan.open("wb") as handle:
        writer.write(handle)
    repository = SQLiteRepository(tmp_path / "evidence.sqlite3")
    runtime = _runtime(tmp_path, repository, InMemorySaver())
    interrupted = runtime.invoke(
        create_candidate_profile_state(
            thread_id="thread-scan",
            user_id="owner",
            candidate_id="candidate",
            input_paths=[str(scan)],
        )
    )
    request = _request(interrupted)
    assert interrupted["next_action"] == "request_more_materials"
    assert request["interaction_type"] == "provide_materials"
    assert interrupted["unsupported_artifact_ids"]
    completed = runtime.resume(
        thread_id="thread-scan",
        response=HumanInteractionResponse(
            response_id="response-scan-skip",
            request_id=request["request_id"],
            thread_id="thread-scan",
            user_id="owner",
            action="skip",
            skipped_ids=[request["requested_materials"][0]["material_id"]],
        ),
    )
    assert completed["status"] == "completed_with_unknowns"


def test_llm_and_tool_call_hard_budgets_never_overrun(tmp_path) -> None:
    repository = SQLiteRepository(tmp_path / "llm-evidence.sqlite3")
    evaluator = LLMSufficiencyEvaluator(
        LLMConfig(model="broken", cache_enabled=False, max_retries=1),
        MockLLMProvider("always_invalid_json"),
        LLMCache(str(tmp_path / "llm-budget-cache")),
    )
    runtime = _runtime(
        tmp_path / "llm-budget",
        repository,
        InMemorySaver(),
        evaluator=evaluator,
    )
    result = runtime.invoke(
        create_candidate_profile_state(
            thread_id="thread-llm-budget",
            user_id="owner",
            candidate_id="candidate",
            input_paths=[str(FIXTURES / "candidate_sufficient.md")],
            budgets={
                "max_profile_rounds": 3,
                "max_questions_per_interrupt": 1,
                "max_llm_calls": 2,
                "max_tool_calls": 30,
            },
        )
    )
    assert result["status"] == "completed_with_unknowns"
    assert result["counters"]["llm_calls"] == 2

    tool_repository = SQLiteRepository(tmp_path / "tool-evidence.sqlite3")
    tool_runtime = _runtime(
        tmp_path / "tool-budget",
        tool_repository,
        InMemorySaver(),
    )
    tool_result = tool_runtime.invoke(
        create_candidate_profile_state(
            thread_id="thread-tool-budget",
            user_id="owner",
            candidate_id="candidate-tool",
            input_paths=[str(FIXTURES / "candidate_sufficient.md")],
            budgets={
                "max_profile_rounds": 3,
                "max_questions_per_interrupt": 1,
                "max_llm_calls": 12,
                "max_tool_calls": 2,
            },
        )
    )
    assert tool_result["status"] == "completed_with_unknowns"
    assert tool_result["counters"]["tool_calls"] == 2


def test_uploaded_path_outside_authorized_roots_is_rejected_before_archive(
    tmp_path,
) -> None:
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    outside = tmp_path / "outside.md"
    outside.write_text("Skills: Python", encoding="utf-8")
    repository = SQLiteRepository(tmp_path / "evidence.sqlite3")
    runtime = _runtime(tmp_path, repository, InMemorySaver())
    interrupted = runtime.invoke(
        create_candidate_profile_state(
            thread_id="thread-path-boundary",
            user_id="owner",
            candidate_id="candidate",
            input_paths=[],
            allowed_path_roots=[str(allowed)],
        )
    )
    request = _request(interrupted)
    with pytest.raises(
        CandidateProfileWorkflowError, match="allowed roots"
    ):
        runtime.resume(
            thread_id="thread-path-boundary",
            response=HumanInteractionResponse(
                response_id="response-outside-root",
                request_id=request["request_id"],
                thread_id="thread-path-boundary",
                user_id="owner",
                action="upload",
                file_paths=[str(outside)],
            ),
        )
    assert repository.list_claims("candidate") == []
    assert not list((tmp_path / "blobs").glob("responses/**/*"))
