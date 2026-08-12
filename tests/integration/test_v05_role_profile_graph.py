"""WP3.1 integration coverage kept at the historical v0.5 module path."""

from __future__ import annotations

import json

from langgraph.checkpoint.memory import InMemorySaver

from campus_job_agent.schemas import (
    CommunityEvidenceCoverage,
    CommunityEvidenceSegment,
    CommunitySearchAttemptReceipt,
    JobDemandProfile,
    OfficialEscalationReceipt,
    RoleFamilyDemandProfile,
    RoleIntelligenceBundle,
    RoleSearchBudget,
    SearchScope,
    SourceBatch,
    SourceDocument,
)
from campus_job_agent.sources import (
    FixtureExperienceAdapter,
    FixtureRecruitmentAdapter,
    LocalCredentialStore,
    SQLiteRoleRepository,
    SourceAdapterRegistry,
)
from campus_job_agent.storage import LocalBlobStore, SQLiteRepository
from campus_job_agent.tools import build_role_profile_registry
from campus_job_agent.workflows.candidate_profile import open_sqlite_checkpointer
from campus_job_agent.workflows.role_profile import (
    RoleProfileGraphRuntime,
    create_role_profile_state,
)


JOB_URL = "fixture://jobs/a1"
INTERVIEW_URL = "fixture://community/interview-1"
EMPLOYMENT_URL = "fixture://community/employment-1"

JOB_SEARCH = [{
    "source_url": "fixture://jobs/search",
    "document_kind": "search_page",
    "candidates": [{
        "detail_url": JOB_URL,
        "job_id": "a1",
        "company": "甲科技",
        "role_title": "AI Agent工程师",
    }],
}]

JOB_DETAIL = {
    "source_url": JOB_URL,
    "document_kind": "job_detail",
    "job_id": "a1",
    "company": "甲科技",
    "role_title": "AI Agent工程师",
    "city": "成都",
    "graduation_year": "2027",
    "recruitment_type": "autumn_campus",
    "job_description": "负责 Agent 编排、工具调用与评测",
    "requirements": "熟悉 Python；掌握 RAG；了解 LangGraph",
    "requirements_normalized": ["Python", "RAG", "LangGraph"],
    "degree_requirement": "本科",
}

COMMUNITY_SEARCH = [{
    "source_url": "fixture://community/search",
    "document_kind": "experience_search",
    "candidates": [
        {"detail_url": INTERVIEW_URL, "title": "甲科技 Agent 面经"},
        {"detail_url": EMPLOYMENT_URL, "title": "甲科技 Agent 工作体验"},
    ],
}]

INTERVIEW_DETAIL = {
    "source_url": INTERVIEW_URL,
    "document_kind": "experience_post",
    "body_text": "甲科技 AI Agent工程师面试中，面试官追问了 Agent bad case 定位和 RAG 召回评测。",
    "community_extraction": {
        "document_type": "interview_experience",
        "segments": [{
            "quote": "面试官追问了 Agent bad case 定位和 RAG 召回评测。",
            "segment_type": "interview_question",
            "scope_level": "company_role",
            "company": "甲科技",
            "role_title": "AI Agent工程师",
            "polarity": "unknown",
            "limited_summary": "Agent bad case 与 RAG 评测",
            "confidence": 0.95,
        }],
    },
}

EMPLOYMENT_DETAIL = {
    "source_url": EMPLOYMENT_URL,
    "document_kind": "experience_post",
    "body_text": "甲科技 AI Agent工程师团队氛围相对直接，但上线周的加班强度较高。",
    "community_extraction": {
        "document_type": "employment_experience",
        "segments": [
            {
                "quote": "团队氛围相对直接",
                "segment_type": "team_atmosphere",
                "scope_level": "company_only",
                "company": "甲科技",
                "polarity": "favorable",
                "limited_summary": "团队氛围直接",
                "confidence": 0.9,
            },
            {
                "quote": "上线周的加班强度较高",
                "segment_type": "work_intensity",
                "scope_level": "company_role",
                "company": "甲科技",
                "role_title": "AI Agent工程师",
                "polarity": "unfavorable",
                "limited_summary": "上线周加班强度高",
                "confidence": 0.9,
            },
        ],
    },
}


def _build(
    tmp_path, checkpointer, *, experience_requires_auth: bool = False,
    community_search=None, community_details=None,
):
    database = tmp_path / "role.sqlite3"
    evidence = SQLiteRepository(database)
    role = SQLiteRoleRepository(database)
    blob = LocalBlobStore(tmp_path / "blobs")
    credentials = LocalCredentialStore(tmp_path / "credentials")
    adapters = SourceAdapterRegistry()
    adapters.register(FixtureRecruitmentAdapter(
        source_id="fixture_jobs", fixture_pages={"first": JOB_SEARCH},
        detail_pages={JOB_URL: JOB_DETAIL}, blob_store=blob,
        evidence_repository=evidence, role_repository=role, owner_id="owner",
    ))
    adapters.register(FixtureExperienceAdapter(
        source_id="fixture_experience", fixture_pages={"first": community_search or COMMUNITY_SEARCH},
        detail_pages=community_details or {
            INTERVIEW_URL: INTERVIEW_DETAIL,
            EMPLOYMENT_URL: EMPLOYMENT_DETAIL,
        },
        blob_store=blob, evidence_repository=evidence,
        role_repository=role, owner_id="owner",
        requires_auth=experience_requires_auth,
    ))
    registry = build_role_profile_registry(
        blob_store=blob, evidence_repository=evidence,
        profile_repository=evidence, role_repository=role,
        adapters=adapters, credential_store=credentials,
    )
    runtime = RoleProfileGraphRuntime(
        registry=registry, evidence_repository=evidence,
        profile_repository=evidence, role_repository=role,
        checkpointer=checkpointer,
    )
    return runtime, evidence, role, registry, adapters


def _state(adapters, thread_id: str = "role-thread", budgets=None):
    scope = SearchScope(
        scope_id="scope-role", target_role_queries=["AI Agent"],
        target_role_family="ai_agent_engineering", locations=["成都"],
        graduation_year="2027", recruitment_type="autumn_campus",
    )
    return create_role_profile_state(
        thread_id=thread_id, user_id="owner", search_scope=scope,
        enabled_source_ids=["fixture_jobs", "fixture_experience"],
        source_capabilities=adapters.capabilities(),
        budgets=budgets,
    )


def test_detail_first_graph_builds_separated_profiles_and_bundle(tmp_path):
    runtime, evidence, role, _registry, adapters = _build(tmp_path, InMemorySaver())
    result = runtime.invoke(_state(adapters))

    assert result["status"] == "completed_with_unknowns"
    assert len(result["recruitment_detail_candidate_ids"]) == 1
    assert len(result["recruitment_detail_document_ids"]) == 1
    assert len(result["job_demand_profile_ids"]) == 1
    assert len(result["job_reputation_profile_ids"]) == 1
    assert len(result["company_reputation_profile_ids"]) == 1
    assert len(result["community_evidence_segment_ids"]) == 3
    assert set(result["missing_sections"]) == {
        "community_interview_insufficient",
        "community_reputation_insufficient",
    }

    demand = role.get(result["job_demand_profile_ids"][0], JobDemandProfile)
    family = role.get(result["role_family_demand_profile_id"], RoleFamilyDemandProfile)
    bundle = role.get(result["role_intelligence_bundle_id"], RoleIntelligenceBundle)
    assert demand is not None and demand.source_document_ids == result["recruitment_detail_document_ids"]
    assert len(demand.assessment_signals) == 1
    assert family is not None and family.denominator.accepted_job_count == 1
    assert family.denominator.accepted_interview_document_count == 1
    assert bundle is not None and bundle.search_scope_id == "scope-role"
    detail_artifacts = {
        str(role.get(value, SourceDocument).raw_artifact_id)
        for value in [
            *result["recruitment_detail_document_ids"],
            *result["community_detail_document_ids"],
        ]
    }
    assert set(bundle.raw_evidence_refs) == detail_artifacts
    assert all(evidence.get_artifact(value) is not None for value in result["raw_artifact_ids"])

    segments = [
        role.get(value, CommunityEvidenceSegment)
        for value in result["community_evidence_segment_ids"]
    ]
    assert {item.usage for item in segments if item is not None} == {
        "demand_assessment", "reputation_company", "reputation_job",
    }
    for item in segments:
        assert item is not None
        fragment = evidence.get_fragment(item.fragment_id)
        assert fragment is not None
        assert fragment.text == evidence.get_fragment(item.fragment_id).text
        assert item.quote_end - item.quote_start == len(fragment.text)

    receipts = [
        role.get(value, OfficialEscalationReceipt)
        for value in result["official_escalation_receipt_ids"]
    ]
    assert len(receipts) == 1
    assert receipts[0].trigger == "not_required"
    assert result["official_verification_plan_ids"] == []
    assert result["job_instance_profile_snapshot_ids"] == []
    assert result["role_family_profile_snapshot_id"] is None
    assert all("records" not in item for item in result["tool_results"])
    assert len(json.dumps(result["tool_results"], ensure_ascii=False)) < 50_000
    assert INTERVIEW_DETAIL["body_text"] not in json.dumps(
        result["tool_results"], ensure_ascii=False
    )
    attempts = role.list(
        "community_search_attempt_receipt", CommunitySearchAttemptReceipt
    )
    assert result["counters"]["community_searches"] == 6
    assert len(attempts) == 6
    assert {
        (item.evidence_purpose, item.round_index) for item in attempts
    } == {
        (purpose, round_index)
        for purpose in ("interview_experience", "employment_experience")
        for round_index in (1, 2, 3)
    }


def test_two_independent_details_do_not_satisfy_three_cluster_floor(tmp_path):
    interview_2_url = "fixture://community/interview-2"
    employment_2_url = "fixture://community/employment-2"
    interview_2 = {
        **INTERVIEW_DETAIL,
        "source_url": interview_2_url,
        "body_text": "甲科技 AI Agent工程师二面要求设计 Agent 工具调用失败后的降级方案。",
        "community_extraction": {
            "document_type": "interview_experience",
            "segments": [{
                "quote": "二面要求设计 Agent 工具调用失败后的降级方案。",
                "segment_type": "interview_question", "scope_level": "company_role",
                "company": "甲科技", "role_title": "AI Agent工程师",
                "polarity": "unknown", "limited_summary": "工具调用降级",
                "confidence": 0.95,
            }],
        },
    }
    employment_2 = {
        **EMPLOYMENT_DETAIL,
        "source_url": employment_2_url,
        "body_text": "甲科技 AI Agent工程师团队有固定技术分享，项目迭代节奏较快。",
        "community_extraction": {
            "document_type": "employment_experience",
            "segments": [{
                "quote": "团队有固定技术分享",
                "segment_type": "growth", "scope_level": "company_role",
                "company": "甲科技", "role_title": "AI Agent工程师",
                "polarity": "favorable", "limited_summary": "固定技术分享",
                "confidence": 0.9,
            }],
        },
    }
    search = [{
        **COMMUNITY_SEARCH[0],
        "candidates": [
            *COMMUNITY_SEARCH[0]["candidates"],
            {"detail_url": interview_2_url, "title": "甲科技二面"},
            {"detail_url": employment_2_url, "title": "甲科技工作体验二"},
        ],
    }]
    runtime, _evidence, role, _registry, adapters = _build(
        tmp_path, InMemorySaver(), community_search=search,
        community_details={
            INTERVIEW_URL: INTERVIEW_DETAIL, EMPLOYMENT_URL: EMPLOYMENT_DETAIL,
            interview_2_url: interview_2, employment_2_url: employment_2,
        },
    )
    result = runtime.invoke(_state(
        adapters, "early-stop-thread",
        budgets={"max_community_detail_documents_per_query": 4},
    ))
    attempts = role.list(
        "community_search_attempt_receipt", CommunitySearchAttemptReceipt
    )
    coverages = role.list("community_evidence_coverage", CommunityEvidenceCoverage)
    assert result["counters"]["community_searches"] == 6
    assert len(attempts) == 6
    assert {item.round_index for item in attempts} == {1, 2, 3}
    assert not [item for item in coverages if item.status == "sufficient"]


def test_three_independent_clusters_per_purpose_complete_in_two_searches(tmp_path):
    candidates = []
    details = {}
    for index in range(3):
        interview_url = f"fixture://community/interview-{index + 10}"
        employment_url = f"fixture://community/employment-{index + 10}"
        candidates.extend([
            {"detail_url": interview_url, "title": f"甲科技面经 {index}"},
            {"detail_url": employment_url, "title": f"甲科技工作体验 {index}"},
        ])
        interview_quote = f"第 {index + 1} 轮面试询问了 Agent 评测主题 {index}。"
        employment_quote = f"团队在主题 {index} 上的工作节奏与协作体验。"
        details[interview_url] = {
            "source_url": interview_url,
            "body_text": f"甲科技 AI Agent工程师{interview_quote}",
            "community_extraction": {
                "document_type": "interview_experience",
                "segments": [{
                    "quote": interview_quote,
                    "segment_type": "interview_question",
                    "scope_level": "company_role", "company": "甲科技",
                    "role_title": "AI Agent工程师", "confidence": 0.9,
                }],
            },
        }
        details[employment_url] = {
            "source_url": employment_url,
            "body_text": f"甲科技 AI Agent工程师{employment_quote}",
            "community_extraction": {
                "document_type": "employment_experience",
                "segments": [{
                    "quote": employment_quote,
                    "segment_type": "team_atmosphere",
                    "scope_level": "company_role", "company": "甲科技",
                    "role_title": "AI Agent工程师",
                    "polarity": "mixed", "confidence": 0.9,
                }],
            },
        }
    search = [{
        **COMMUNITY_SEARCH[0], "candidates": candidates,
    }]
    runtime, _evidence, role, _registry, adapters = _build(
        tmp_path, InMemorySaver(), community_search=search,
        community_details=details,
    )
    result = runtime.invoke(_state(
        adapters, "three-cluster-thread",
        budgets={"max_community_detail_documents_per_query": 6},
    ))
    coverages = role.list(
        "community_evidence_coverage", CommunityEvidenceCoverage
    )
    sufficient = [item for item in coverages if item.status == "sufficient"]
    assert result["status"] == "completed"
    assert result["counters"]["community_searches"] == 2
    assert len(sufficient) == 2
    assert all(item.independent_cluster_count == 3 for item in sufficient)


def test_skipping_blocked_primary_source_switches_to_backup(tmp_path):
    runtime, _evidence, role, _registry, adapters = _build(
        tmp_path, InMemorySaver(), experience_requires_auth=True,
    )
    primary = adapters.get("fixture_experience")
    backup = FixtureExperienceAdapter(
        source_id="fixture_experience_backup", fixture_pages={"first": COMMUNITY_SEARCH},
        detail_pages={INTERVIEW_URL: INTERVIEW_DETAIL, EMPLOYMENT_URL: EMPLOYMENT_DETAIL},
        blob_store=primary.blob_store, evidence_repository=primary.evidence_repository,
        role_repository=primary.role_repository, owner_id="owner",
    )
    adapters.register(backup)
    state = _state(adapters, "source-switch-thread")
    state["enabled_source_ids"] = [
        "fixture_jobs", "fixture_experience", "fixture_experience_backup",
    ]
    state["source_capabilities"] = adapters.capabilities()
    interrupted = runtime.invoke(state)
    request = interrupted["__interrupt__"][0].value
    assert request["source_id"] == "fixture_experience"
    result = runtime.resume(thread_id="source-switch-thread", response={
        "response_id": "skip-primary", "request_id": request["request_id"],
        "thread_id": request["thread_id"], "user_id": request["user_id"],
        "source_id": request["source_id"], "action": "skip_source",
    })
    attempts = role.list(
        "community_search_attempt_receipt", CommunitySearchAttemptReceipt
    )
    assert result["status"] in {"completed", "completed_with_unknowns"}
    assert any(item.source_id == "fixture_experience_backup" for item in attempts)
    assert "fixture_experience" in result["skipped_source_ids"]


def test_calibration_maps_ranked_sources_to_seventy_thirty_detail_budget(tmp_path):
    runtime, _evidence, role, _registry, adapters = _build(
        tmp_path, InMemorySaver(),
    )
    primary = adapters.get("fixture_experience")
    backup = FixtureExperienceAdapter(
        source_id="fixture_experience_backup",
        fixture_pages={"first": COMMUNITY_SEARCH},
        detail_pages={
            INTERVIEW_URL: INTERVIEW_DETAIL,
            EMPLOYMENT_URL: EMPLOYMENT_DETAIL,
        },
        blob_store=primary.blob_store,
        evidence_repository=primary.evidence_repository,
        role_repository=primary.role_repository, owner_id="owner",
    )
    adapters.register(backup)
    state = _state(adapters, "allocation-thread")
    state["enabled_source_ids"] = [
        "fixture_jobs", "fixture_experience", "fixture_experience_backup",
    ]
    state["source_capabilities"] = adapters.capabilities()
    result = runtime.invoke(state)

    allocations = result["community_source_allocations_by_purpose"]
    assert allocations
    assert all(
        sorted(value.values()) == [0.3, 0.7]
        for value in allocations.values()
    )
    attempts = {
        item.query_id: item for item in role.list(
            "community_search_attempt_receipt", CommunitySearchAttemptReceipt
        )
    }
    for query in result["query_history"]:
        attempt = attempts.get(query.get("query_id"))
        if attempt is None or attempt.round_index == 1:
            continue
        expected = max(1, round(
            3 * allocations[attempt.evidence_purpose][attempt.source_id]
        ))
        assert query["page_size"] == expected
def test_duplicate_graph_invoke_reuses_detail_and_profile_records(tmp_path):
    runtime, _evidence, role, _registry, adapters = _build(tmp_path, InMemorySaver())
    first = runtime.invoke(_state(adapters, "first-thread"))
    counts = {
        "batch": len(role.list("source_batch", SourceBatch)),
        "demand": len(role.list("job_demand_profile", JobDemandProfile)),
        "bundle": len(role.list("role_intelligence_bundle", RoleIntelligenceBundle)),
    }
    second = runtime.invoke(_state(adapters, "second-thread"))

    assert first["role_intelligence_bundle_id"] == second["role_intelligence_bundle_id"]
    assert len(role.list("source_batch", SourceBatch)) == counts["batch"]
    assert len(role.list("job_demand_profile", JobDemandProfile)) == counts["demand"]
    assert len(role.list("role_intelligence_bundle", RoleIntelligenceBundle)) == counts["bundle"]


def test_auth_interrupt_authorized_resume_redacts_credential(tmp_path):
    runtime, evidence, _role, registry, adapters = _build(
        tmp_path, InMemorySaver(), experience_requires_auth=True,
    )
    interrupted = runtime.invoke(_state(adapters, "auth-thread"))
    request = interrupted["__interrupt__"][0].value
    assert request["interaction_type"] == "authorize_source"

    credential_file = tmp_path / "nowcoder.curl.txt"
    credential_file.write_text(
        "curl 'https://fixture.invalid/authorize' -H 'Cookie: session=very-secret-cookie'",
        encoding="utf-8",
    )
    imported = registry.run("source.import_credential", {
        "source_id": "fixture_experience", "path": str(credential_file),
        "allowed_path_roots": [str(tmp_path)],
    })
    credential_ref = imported.records[0]["credential_ref"]
    completed = runtime.resume(thread_id="auth-thread", response={
        "request_id": request["request_id"], "thread_id": "auth-thread",
        "user_id": "owner", "source_id": "fixture_experience",
        "action": "authorized", "credential_ref": credential_ref,
    })

    assert completed["status"] == "completed_with_unknowns"
    serialized = str({
        "state": completed,
        "artifacts": [
            item.model_dump(mode="json")
            for value in completed["raw_artifact_ids"]
            if (item := evidence.get_artifact(value)) is not None
        ],
    })
    assert "very-secret-cookie" not in serialized
    assert completed["credential_refs"]["fixture_experience"] == credential_ref


def test_auth_skip_publishes_demand_and_marks_reputation_missing(tmp_path):
    runtime, _evidence, _role, _registry, adapters = _build(
        tmp_path, InMemorySaver(), experience_requires_auth=True,
    )
    interrupted = runtime.invoke(_state(adapters, "skip-thread"))
    request = interrupted["__interrupt__"][0].value
    completed = runtime.resume(thread_id="skip-thread", response={
        "request_id": request["request_id"], "thread_id": "skip-thread",
        "user_id": "owner", "source_id": "fixture_experience",
        "action": "skip_source",
    })

    assert completed["status"] == "completed_with_unknowns"
    assert len(completed["job_demand_profile_ids"]) == 1
    assert set(completed["missing_sections"]) == {
        "interview_assessment", "job_reputation", "company_reputation",
        "community_interview_insufficient", "community_reputation_insufficient",
    }
    assert "fixture_experience" in completed["skipped_source_ids"]


def test_sqlite_checkpoint_resumes_in_new_process_boundary(tmp_path):
    checkpoint = tmp_path / "checkpoint.sqlite3"
    with open_sqlite_checkpointer(checkpoint) as saver:
        runtime, _evidence, _role, registry, adapters = _build(
            tmp_path, saver, experience_requires_auth=True,
        )
        interrupted = runtime.invoke(_state(adapters, "restart-thread"))
        request = interrupted["__interrupt__"][0].value
        credential_file = tmp_path / "credential.curl.txt"
        credential_file.write_text(
            "curl 'https://www.nowcoder.com/' -H 'Cookie: session=secret'",
            encoding="utf-8",
        )
        credential_ref = registry.run("source.import_credential", {
            "source_id": "fixture_experience", "path": str(credential_file),
            "allowed_path_roots": [str(tmp_path)],
        }).records[0]["credential_ref"]
    with open_sqlite_checkpointer(checkpoint) as saver:
        runtime, _evidence, _role, _registry, _adapters = _build(
            tmp_path, saver, experience_requires_auth=True,
        )
        completed = runtime.resume(thread_id="restart-thread", response={
            "request_id": request["request_id"], "thread_id": "restart-thread",
            "user_id": "owner", "source_id": "fixture_experience",
            "action": "authorized", "credential_ref": credential_ref,
        })
    assert completed["status"] == "completed_with_unknowns"


def test_search_page_without_detail_never_projects_demand(tmp_path):
    runtime, _evidence, _role, _registry, adapters = _build(tmp_path, InMemorySaver())
    adapters.get("fixture_jobs").detail_pages = {}
    result = runtime.invoke(_state(adapters, "search-only-thread"))

    assert result["recruitment_detail_candidate_ids"]
    assert result["recruitment_detail_document_ids"] == []
    assert result["eligible_job_cluster_ids"] == []
    assert result["job_demand_profile_ids"] == []
    assert result["role_intelligence_bundle_id"] is None
    assert result["status"] == "failed"


def test_small_community_budget_preserves_demand_partial_bundle(tmp_path):
    runtime, _evidence, _role, _registry, adapters = _build(tmp_path, InMemorySaver())
    state = _state(adapters, "budget-thread")
    state["budgets"] = RoleSearchBudget(
        max_queries=1, max_community_groups=0,
        max_community_queries_per_group=0,
        max_community_detail_documents_per_query=0,
    ).model_dump()
    result = runtime.invoke(state)

    assert result["status"] == "completed_with_unknowns"
    assert len(result["job_demand_profile_ids"]) == 1
    assert result["community_evidence_segment_ids"] == []


def test_run_exports_only_safe_wp31_summary(tmp_path):
    runtime, _evidence, _role, _registry, adapters = _build(tmp_path, InMemorySaver())
    state = _state(adapters, "export-thread")
    state["output_dir"] = str(tmp_path / "run-output")
    result = runtime.invoke(state)

    report = tmp_path / "run-output" / "role_intelligence_report.json"
    assert result["status"] == "completed_with_unknowns"
    assert report.is_file()
    text = report.read_text(encoding="utf-8")
    assert "role_intelligence_bundle_id" in text
    assert "Agent bad case" not in text
    assert "上线周" not in text


def test_llm_planner_failure_uses_deterministic_fallback(tmp_path):
    class BrokenPlanner:
        def plan(self, *args, **kwargs):
            raise ValueError("invalid structured role query output")

    _runtime, evidence, role, registry, adapters = _build(tmp_path, InMemorySaver())
    runtime = RoleProfileGraphRuntime(
        registry=registry, evidence_repository=evidence,
        profile_repository=evidence, role_repository=role,
        checkpointer=InMemorySaver(), planner=BrokenPlanner(),
    )
    result = runtime.invoke(_state(adapters, "llm-fallback-thread"))

    assert result["status"] == "completed_with_unknowns"
    assert any(
        item.get("fallback") == "deterministic"
        and item.get("error_type") == "llm_output_error"
        for item in result["errors"]
    )
