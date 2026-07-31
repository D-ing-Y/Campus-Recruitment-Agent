from __future__ import annotations

from pathlib import Path
import hashlib
import sqlite3
from uuid import uuid4

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from campus_job_agent.evidence import ClaimExtractorService
from campus_job_agent.llm import LLMCache, LLMConfig, MockLLMProvider
from campus_job_agent.schemas import (
    EvidenceArtifact,
    EvidenceFragment,
    CustomSectionRecord,
    HumanAnswer,
    HumanInteractionResponse,
    PdfExtractionDiagnostics,
    ProfileCorrection,
    ResumeData,
    ResumeDraft,
    ResumeEvidenceSnapshot,
    ResumeSourceRef,
    ToolResult,
)
from campus_job_agent.schemas.resume import default_section_statuses
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


def _resume_evidence(
    tmp_path: Path,
    repository: SQLiteRepository,
    *,
    owner_id: str,
    candidate_id: str,
    input_paths: list[str],
) -> str:
    """Seed the confirmed-evidence boundary for legacy Candidate behavior tests."""

    source_texts = [Path(value).read_text(encoding="utf-8") for value in input_paths]
    text = "\n".join(source_texts)
    digest = hashlib.sha256(text.encode()).hexdigest()
    artifact_id = f"artifact-resume-{uuid4()}"
    uri = LocalBlobStore(tmp_path / "blobs").put(
        f"fixtures/{artifact_id}.txt", text.encode()
    )
    repository.save_artifact(EvidenceArtifact(
        artifact_id=artifact_id, owner_id=owner_id, source_type="confirmed_resume_fixture",
        content_type="application/pdf", original_name="confirmed-resume.pdf",
        raw_uri=uri, content_hash=digest,
    ))
    fragments = []
    for index, source_text in enumerate(source_texts):
        source_digest = hashlib.sha256(source_text.encode()).hexdigest()
        fragments.append(repository.save_fragment(EvidenceFragment(
            fragment_id=f"fragment-resume-{uuid4()}", artifact_id=artifact_id,
            locator_type="page_and_char_range",
            locator={"page": index + 1, "document_start": 0, "document_end": len(source_text)},
            text=source_text, text_hash=source_digest,
        )))
    diagnostics = PdfExtractionDiagnostics(
        selected_parser="fixture", attempted_parsers=["fixture"],
        total_non_whitespace_chars=sum(not char.isspace() for char in text),
        nonempty_page_ratio=1.0 if text else 0.0,
        invalid_character_ratio=0.0, quality_passed=bool(text),
    )
    statuses = default_section_statuses()
    statuses.update({key: "confirmed_empty" for key in statuses})
    if source_texts:
        statuses["custom_sections"] = "confirmed"
    data = ResumeData(custom_sections=[
        CustomSectionRecord(record_id=f"fixture-{index}", content=source_text)
        for index, source_text in enumerate(source_texts)
    ])
    field_sources = {
        f"/custom_sections/{index}/content": [ResumeSourceRef(
            artifact_id=artifact_id, fragment_id=fragment.fragment_id,
            page_number=index + 1, text_hash=fragment.text_hash,
            start_offset=0, end_offset=len(fragment.text),
        )]
        for index, fragment in enumerate(fragments)
    }
    draft = repository.save_resume_draft(ResumeDraft(
        owner_id=owner_id, candidate_id=candidate_id, artifact_id=artifact_id,
        status="finalized", data=data,
        field_sources=field_sources,
        section_statuses=statuses, review_receipt_ids=["fixture-confirmation"],
        extraction_diagnostics=diagnostics,
    ))
    snapshot = repository.save_resume_evidence_snapshot(ResumeEvidenceSnapshot(
        draft_id=draft.draft_id, owner_id=owner_id, candidate_id=candidate_id,
        artifact_id=artifact_id, version=1, data=data,
        field_sources=draft.field_sources,
        review_receipt_ids=draft.review_receipt_ids,
        extraction_diagnostics=diagnostics,
    ))
    return snapshot.resume_evidence_id


def _candidate_state(
    tmp_path: Path,
    repository: SQLiteRepository,
    *,
    thread_id: str,
    user_id: str,
    candidate_id: str,
    source_paths: list[str],
    **kwargs,
):
    return create_candidate_profile_state(
        thread_id=thread_id, user_id=user_id, candidate_id=candidate_id,
        resume_evidence_id=_resume_evidence(
            tmp_path, repository, owner_id=user_id,
            candidate_id=candidate_id, input_paths=source_paths,
        ),
        input_paths=[], **kwargs,
    )


def test_sufficient_material_completes_without_interrupt(tmp_path) -> None:
    repository = SQLiteRepository(tmp_path / "evidence.sqlite3")
    runtime = _runtime(tmp_path, repository, InMemorySaver())
    result = runtime.invoke(
        _candidate_state(tmp_path, repository,
            thread_id="thread-sufficient",
            user_id="owner",
            candidate_id="candidate",
            source_paths=[str(FIXTURES / "candidate_sufficient.md")],
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
        _candidate_state(tmp_path, repository,
            thread_id="thread-answer",
            user_id="owner",
            candidate_id="candidate",
            source_paths=[str(FIXTURES / "candidate_missing_responsibility.md")],
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
    answer_claims = [
        item for item in claims
        if response_fragments[0].fragment_id in item.evidence_fragment_ids
    ]
    assert answer_claims
    assert answer_claims[0].evidence_fragment_ids == [
        response_fragments[0].fragment_id
    ]
    assert repository.get_latest_profile("candidate", "candidate").version > before_version

    second_repository = SQLiteRepository(tmp_path / "skip-evidence.sqlite3")
    skip_runtime = _runtime(tmp_path / "skip", second_repository, InMemorySaver())
    interrupted = skip_runtime.invoke(
        _candidate_state(tmp_path / "skip", second_repository,
            thread_id="thread-skip",
            user_id="owner",
            candidate_id="candidate-skip",
            source_paths=[str(FIXTURES / "candidate_missing_responsibility.md")],
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
    assert skip_request["questions"][0]["gap_id"] in skipped["skipped_gap_ids"]


def test_wrong_request_id_is_rejected_without_evidence_write(tmp_path) -> None:
    repository = SQLiteRepository(tmp_path / "evidence.sqlite3")
    runtime = _runtime(tmp_path, repository, InMemorySaver())
    result = runtime.invoke(
        _candidate_state(tmp_path, repository,
            thread_id="thread-invalid",
            user_id="owner",
            candidate_id="candidate",
            source_paths=[str(FIXTURES / "candidate_missing_responsibility.md")],
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
                _candidate_state(tmp_path, repository,
                thread_id="thread-restart",
                user_id="owner",
                candidate_id="candidate",
                    source_paths=[str(FIXTURES / "candidate_missing_responsibility.md")],
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
        _candidate_state(tmp_path, repository,
            thread_id="thread-budget",
            user_id="owner",
            candidate_id="candidate",
            source_paths=[str(FIXTURES / "candidate_missing_responsibility.md")],
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
        _candidate_state(tmp_path, repository,
            thread_id="thread-upload",
            user_id="owner",
            candidate_id="candidate",
            source_paths=[],
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
    assert len(completed["active_artifact_ids"]) == 3
    assert completed["input_paths"] == []


def test_correction_supersedes_claims_resolves_conflict_and_has_version_diff(
    tmp_path,
) -> None:
    repository = SQLiteRepository(tmp_path / "evidence.sqlite3")
    runtime = _runtime(tmp_path, repository, InMemorySaver())
    interrupted = runtime.invoke(
        _candidate_state(tmp_path, repository,
            thread_id="thread-correction",
            user_id="owner",
            candidate_id="candidate",
            source_paths=[
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
        _candidate_state(tmp_path, repository,
            thread_id="thread-llm-fallback",
            user_id="owner",
            candidate_id="candidate",
            source_paths=[str(FIXTURES / "candidate_sufficient.md")],
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
        _candidate_state(tmp_path, repository,
            thread_id="thread-storage-failure",
            user_id="owner",
            candidate_id="candidate",
            source_paths=[str(FIXTURES / "candidate_sufficient.md")],
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
            _candidate_state(tmp_path, repository,
                thread_id="thread-checkpoint-failure",
                user_id="owner",
                candidate_id="candidate",
                source_paths=[str(FIXTURES / "candidate_sufficient.md")],
            )
        )
    # Evidence writes are independently idempotent facts; checkpoint failure
    # must not be presented as recoverable, but it need not erase valid facts.
    assert all(
        item.evidence_fragment_ids for item in repository.list_claims("candidate")
    )


def test_candidate_rejects_missing_resume_evidence(tmp_path) -> None:
    repository = SQLiteRepository(tmp_path / "evidence.sqlite3")
    runtime = _runtime(tmp_path, repository, InMemorySaver())
    with pytest.raises(CandidateProfileWorkflowError, match="confirmed ResumeEvidence"):
        runtime.invoke(create_candidate_profile_state(
            thread_id="thread-scan",
            user_id="owner",
            candidate_id="candidate",
            resume_evidence_id="resume-evidence-missing", input_paths=[],
        ))
    assert repository.list_claims("candidate") == []


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
        _candidate_state(tmp_path / "llm-budget", repository,
            thread_id="thread-llm-budget",
            user_id="owner",
            candidate_id="candidate",
            source_paths=[str(FIXTURES / "candidate_sufficient.md")],
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
        _candidate_state(tmp_path / "tool-budget", tool_repository,
            thread_id="thread-tool-budget",
            user_id="owner",
            candidate_id="candidate-tool",
            source_paths=[str(FIXTURES / "candidate_sufficient.md")],
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
        _candidate_state(tmp_path, repository,
            thread_id="thread-path-boundary",
            user_id="owner",
            candidate_id="candidate",
            source_paths=[],
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
