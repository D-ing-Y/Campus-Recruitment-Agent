from __future__ import annotations

import json

import pytest

from campus_job_agent.runtime import Handoff, ObjectRef, RuntimeFactory
from campus_job_agent.schemas import (
    CommunitySearchAttemptReceipt, CommunitySearchDiagnostic, SearchScope,
)
from campus_job_agent.sources import (
    FixtureExperienceAdapter,
    FixtureRecruitmentAdapter,
)


JOB_URL = "https://jobs.zhaopin.com/job-1.htm"
INTERVIEW_URL = "https://www.nowcoder.com/discuss/1001"
EMPLOYMENT_URL = "https://www.nowcoder.com/discuss/1002"


def _runtime(tmp_path, monkeypatch, *, requires_auth: bool = False):
    monkeypatch.setenv("CAMPUS_AGENT_LLM_PROVIDER", "mock")
    monkeypatch.setenv("CAMPUS_AGENT_LLM_CACHE_ENABLED", "false")
    runtime = RuntimeFactory(data_root=tmp_path / "data").build(owner_id="role-owner")
    job = FixtureRecruitmentAdapter(
        source_id="zhaopin_jobs",
        fixture_pages={"first": [{
            "source_url": "fixture://zhaopin/search",
            "document_kind": "search_page",
            "candidates": [{
                "detail_url": JOB_URL, "job_id": "job-1",
                "company": "甲科技", "role_title": "AI Agent工程师",
            }],
        }]},
        detail_pages={JOB_URL: {
            "source_url": JOB_URL, "document_kind": "job_detail",
            "job_id": "job-1", "company": "甲科技",
            "role_title": "AI Agent工程师", "city": "成都",
            "graduation_year": "2027", "recruitment_type": "autumn_campus",
            "job_description": "负责 Agent 编排与评测",
            "requirements": "熟悉 Python 和 RAG",
            "requirements_normalized": ["Python", "RAG"],
            "degree_requirement": "本科",
        }},
        blob_store=runtime.blob_store,
        evidence_repository=runtime.evidence_repository,
        role_repository=runtime.role_repository,
        owner_id="role-owner",
    )
    community = FixtureExperienceAdapter(
        source_id="nowcoder_experience",
        fixture_pages={"first": [{
            "source_url": "fixture://nowcoder/search",
            "document_kind": "experience_search",
            "candidates": [
                {"detail_url": INTERVIEW_URL, "title": "Agent 面经"},
                {"detail_url": EMPLOYMENT_URL, "title": "Agent 工作体验"},
            ],
        }]},
        detail_pages={
            INTERVIEW_URL: {
                "source_url": INTERVIEW_URL,
                "body_text": "甲科技 AI Agent工程师面试询问了 RAG 召回评估方法。",
                "community_extraction": {
                    "document_type": "interview_experience",
                    "segments": [{
                        "quote": "面试询问了 RAG 召回评估方法。",
                        "segment_type": "interview_question",
                        "scope_level": "company_role", "company": "甲科技",
                        "role_title": "AI Agent工程师", "confidence": 0.9,
                    }],
                },
            },
            EMPLOYMENT_URL: {
                "source_url": EMPLOYMENT_URL,
                "body_text": "甲科技 AI Agent工程师团队氛围好，但上线周强度较高。",
                "community_extraction": {
                    "document_type": "employment_experience",
                    "segments": [{
                        "quote": "上线周强度较高",
                        "segment_type": "work_intensity",
                        "scope_level": "company_role", "company": "甲科技",
                        "role_title": "AI Agent工程师",
                        "polarity": "unfavorable", "confidence": 0.9,
                    }],
                },
            },
        },
        blob_store=runtime.blob_store,
        evidence_repository=runtime.evidence_repository,
        role_repository=runtime.role_repository,
        owner_id="role-owner", requires_auth=requires_auth,
    )
    job.capabilities = job.capabilities.model_copy(update={"live_enabled": True})
    community.capabilities = community.capabilities.model_copy(
        update={"live_enabled": True}
    )
    runtime.source_adapter_registry.register(job)
    runtime.source_adapter_registry.register(community)

    session = runtime.session_service.start(user_id="role-owner")
    intent_id = "intent-snapshot-role"
    runtime.session_repository.register_ref(ObjectRef(
        object_id=intent_id, object_type="career_intent_snapshot",
        owner_id=session.user_id, schema_version="v0.7.1",
    ))
    session = runtime.session_repository.set_current_ref(
        session.session_id, key="career_intent_snapshot_id",
        object_id=intent_id, expected_version=session.session_version,
    )
    scope = runtime.intent_repository.save(
        "search_scope",
        SearchScope(
            scope_id="scope-role-app", career_intent_snapshot_id=intent_id,
            target_role_queries=["AI Agent"],
            target_role_family="ai_agent_engineering", locations=["成都"],
            graduation_year="2027", recruitment_type="autumn_campus",
        ),
        owner_id=session.user_id,
    )
    handoff = runtime.session_repository.save_handoff(Handoff(
        handoff_id="handoff-role-app", session_id=session.session_id,
        user_id=session.user_id, handoff_type="role_research_required",
        origin_run_id="run-intent-origin",
        origin_object_refs={"career_intent_snapshot_id": intent_id},
        required_input_refs={
            "career_intent_snapshot_id": intent_id,
            "search_scope_id": scope.scope_id,
        },
        handler_version="role_research_handoff_v2",
    ))
    session = runtime.session_repository.update_navigation(
        session.session_id, expected_version=session.session_version,
        operation="intent_completed_for_role_test", status="active",
        current_stage="role", pending_handoff_ids=[handoff.handoff_id],
    )
    return runtime, session, handoff


def test_role_application_consumes_handoff_and_advances_to_matching(
    tmp_path, monkeypatch,
):
    runtime, session, handoff = _runtime(tmp_path, monkeypatch)
    payload = runtime.application_services["role"].research(
        session_id=session.session_id, handoff_id=handoff.handoff_id,
    )

    assert payload["status"] == "completed_with_unknowns"
    assert payload["next_action"] == "match.run"
    assert payload["metrics"]["search_only_projection_count"] == 0
    assert payload["metrics"]["detail_artifact_trace_rate"] == 1.0
    assert payload["metrics"]["community_usage_crossover_count"] == 0
    diagnostics = runtime.role_repository.list(
        "community_search_diagnostic", CommunitySearchDiagnostic
    )
    attempts = runtime.role_repository.list(
        "community_search_attempt_receipt", CommunitySearchAttemptReceipt
    )
    assert diagnostics
    assert all(item.outcome == "post_candidates_found" for item in diagnostics)
    assert all(item.diagnostic_id for item in attempts if item.status != "blocked")
    assert all(
        not item.diagnostic_id and item.reason_codes
        for item in attempts if item.status == "blocked"
    )
    current = runtime.session_service.status(session.session_id)
    assert current.current_stage == "matching"
    assert current.pending_handoff_ids == []
    assert len(current.current_refs["role_intelligence_bundle_ids"]) == 1
    assert runtime.session_repository.get_handoff(
        handoff.handoff_id
    ).status == "resolved"

    safe_state = (
        runtime.paths.run_root / payload["run_id"] / "state.json"
    ).read_text(encoding="utf-8")
    assert "RAG 召回评估方法" not in safe_state
    assert "上线周强度" not in safe_state
    shown = runtime.application_services["role"].show(
        payload["output_refs"]["role_intelligence_bundle_id"]
    )
    assert shown["bundle"]["search_scope_id"] == "scope-role-app"


def test_multiple_role_handoffs_complete_one_family_at_a_time(
    tmp_path, monkeypatch,
):
    runtime, session, first_handoff = _runtime(tmp_path, monkeypatch)
    intent_id = str(session.current_refs["career_intent_snapshot_id"])
    second_scope = runtime.intent_repository.save(
        "search_scope",
        SearchScope(
            scope_id="scope-role-app-2", career_intent_snapshot_id=intent_id,
            target_role_queries=["LLM 应用开发"],
            target_role_family="ai_agent_engineering", locations=["成都"],
            graduation_year="2027", recruitment_type="autumn_campus",
        ),
        owner_id=session.user_id,
    )
    second_handoff = runtime.session_repository.save_handoff(Handoff(
        handoff_id="handoff-role-app-2", session_id=session.session_id,
        user_id=session.user_id, handoff_type="role_research_required",
        origin_run_id="run-intent-origin",
        origin_object_refs={"career_intent_snapshot_id": intent_id},
        required_input_refs={
            "career_intent_snapshot_id": intent_id,
            "search_scope_id": second_scope.scope_id,
        },
        handler_version="role_research_handoff_v2",
    ))
    session = runtime.session_repository.update_navigation(
        session.session_id, expected_version=session.session_version,
        operation="add_second_role_handoff",
        pending_handoff_ids=[first_handoff.handoff_id, second_handoff.handoff_id],
    )

    first = runtime.application_services["role"].research(
        session_id=session.session_id, handoff_id=first_handoff.handoff_id,
    )
    assert first["next_action"] == "role.research"
    middle = runtime.session_service.status(session.session_id)
    assert middle.current_stage == "role"
    assert middle.pending_handoff_ids == [second_handoff.handoff_id]

    second = runtime.application_services["role"].research(
        session_id=session.session_id, handoff_id=second_handoff.handoff_id,
    )
    assert second["next_action"] == "match.run"
    current = runtime.session_service.status(session.session_id)
    assert current.current_stage == "matching"
    assert len(current.current_refs["role_intelligence_bundle_ids"]) == 2


def test_role_authorization_resume_is_idempotent(tmp_path, monkeypatch):
    runtime, session, handoff = _runtime(
        tmp_path, monkeypatch, requires_auth=True
    )
    interrupted = runtime.application_services["role"].research(
        session_id=session.session_id, handoff_id=handoff.handoff_id,
    )
    assert interrupted["status"] == "interrupted"
    assert interrupted["pending_request"]["source_id"] == "nowcoder_experience"

    ref = runtime.credential_resolver.save_source_api_key(
        source_id="nowcoder_experience", api_key="safe-test-key",
    ).credential_ref
    completed = runtime.application_services["role"].resume(
        session_id=session.session_id, action="authorized",
        response_id="role-response-1", credential_ref=ref,
    )
    duplicate = runtime.application_services["role"].resume(
        session_id=session.session_id, action="authorized",
        response_id="role-response-1", credential_ref=ref,
    )

    assert completed["status"] == "completed_with_unknowns"
    assert duplicate["deduplicated"] is True
    assert duplicate["output_refs"]["role_intelligence_bundle_id"] == (
        completed["output_refs"]["role_intelligence_bundle_id"]
    )
    with pytest.raises(Exception, match="idempotency_conflict"):
        runtime.application_services["role"].resume(
            session_id=session.session_id, action="cancel",
            response_id="role-response-1",
        )


def test_role_skip_source_keeps_demand_bundle_and_missing_reputation(
    tmp_path, monkeypatch,
):
    runtime, session, handoff = _runtime(
        tmp_path, monkeypatch, requires_auth=True
    )
    interrupted = runtime.application_services["role"].research(
        session_id=session.session_id, handoff_id=handoff.handoff_id,
    )
    skipped = runtime.application_services["role"].resume(
        session_id=session.session_id, action="skip-source",
        response_id="role-response-skip",
    )

    assert interrupted["status"] == "interrupted"
    assert skipped["status"] == "completed_with_unknowns"
    assert skipped["output_refs"]["role_intelligence_bundle_id"]
    manifest = json.loads(
        (runtime.paths.run_root / skipped["run_id"] / "run_manifest.json")
        .read_text(encoding="utf-8")
    )
    assert set(manifest["warnings"]) == set()
    assert runtime.session_repository.get_handoff(handoff.handoff_id).status == "resolved"


def test_role_cancel_preserves_pending_handoff_without_publishing_bundle(
    tmp_path, monkeypatch,
):
    runtime, session, handoff = _runtime(
        tmp_path, monkeypatch, requires_auth=True
    )
    interrupted = runtime.application_services["role"].research(
        session_id=session.session_id, handoff_id=handoff.handoff_id,
    )
    cancelled = runtime.application_services["role"].resume(
        session_id=session.session_id, action="cancel",
        response_id="role-response-cancel",
    )

    assert interrupted["status"] == "interrupted"
    assert cancelled["status"] == "cancelled"
    assert cancelled["output_refs"]["role_intelligence_bundle_id"] is None
    current = runtime.session_service.status(session.session_id)
    assert current.status == "active"
    assert current.current_stage == "role"
    assert current.pending_handoff_ids == [handoff.handoff_id]
    assert runtime.session_repository.get_handoff(handoff.handoff_id).status == "pending"
