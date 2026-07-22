from langgraph.checkpoint.memory import InMemorySaver

from campus_job_agent.schemas import RoleSearchBudget, SearchScope
from campus_job_agent.sources import (
    FixtureExperienceAdapter, FixtureOfficialAdapter, FixtureRecruitmentAdapter,
    LocalCredentialStore, SQLiteRoleRepository, SourceAdapterRegistry,
)
from campus_job_agent.storage import LocalBlobStore, SQLiteRepository
from campus_job_agent.tools import build_role_profile_registry
from campus_job_agent.workflows.candidate_profile import open_sqlite_checkpointer
from campus_job_agent.workflows.role_profile import RoleProfileGraphRuntime, create_role_profile_state


JOBS = [
    {"source_url":"fixture://jobs/a1","job_id":"a1","company":"甲科技","role_title":"AI Agent工程师","city":"成都",
     "graduation_year":"2027","recruitment_type":"autumn_campus","job_description":"负责 Agent 编排与评测",
     "requirements":"熟悉 Python；掌握 RAG","requirements_normalized":["Python","RAG"],"degree_requirement":"本科"},
    {"source_url":"fixture://jobs/b1","job_id":"b1","company":"乙科技","role_title":"LLM应用开发工程师","city":"成都",
     "graduation_year":"2027","recruitment_type":"autumn_campus","job_description":"负责 LLM 应用与工具调用",
     "requirements":"熟悉 Python；了解 LangGraph","requirements_normalized":["Python","LangGraph"],"degree_requirement":"本科"},
    {"source_url":"fixture://jobs/a2","job_id":"a2","company":"甲科技","role_title":"智能体研发工程师","city":"成都",
     "graduation_year":"2027","recruitment_type":"autumn_campus","job_description":"负责多轮智能体系统",
     "requirements":"熟悉 Python；了解评测","requirements_normalized":["Python","评测"],"degree_requirement":"硕士"},
]

OFFICIAL = [
    {**JOBS[0], "source_url":"fixture://official/a1", "application_url":"https://careers.example/a1"},
    {**JOBS[1], "source_url":"fixture://official/b1", "application_url":"https://careers.example/b1"},
    {**JOBS[2], "source_url":"fixture://official/a2", "application_url":"https://careers.example/a2"},
]

EXPERIENCES = [
    {"source_url":"fixture://experience/1","title":"甲科技 AI Agent 一面","company":"甲科技","role_title":"AI Agent工程师",
     "role_family":"ai_agent_engineering","scope_level":"company_role","stage":"first_interview",
     "signals":{"interview":["追问 Agent bad case 与评测"],"tech_stack":["Python"]}},
    {"source_url":"fixture://experience/2","title":"乙科技 LLM 应用面经","company":"乙科技","role_title":"LLM应用开发工程师",
     "role_family":"ai_agent_engineering","scope_level":"company_role","stage":"first_interview",
     "signals":{"interview":["追问 RAG 召回评估"],"project_preference":["关注项目职责"]}},
]


def _build(tmp_path, checkpointer, *, experience_requires_auth=False):
    db = tmp_path / "role.sqlite3"
    evidence = SQLiteRepository(db)
    role = SQLiteRoleRepository(db)
    blob = LocalBlobStore(tmp_path / "blobs")
    credentials = LocalCredentialStore(tmp_path / "credentials")
    adapters = SourceAdapterRegistry()
    adapters.register(FixtureRecruitmentAdapter(source_id="fixture_jobs", fixture_pages={"first":JOBS}, blob_store=blob,
                                                evidence_repository=evidence, role_repository=role, owner_id="owner"))
    adapters.register(FixtureExperienceAdapter(source_id="fixture_experience", fixture_pages={"first":EXPERIENCES}, blob_store=blob,
                                               evidence_repository=evidence, role_repository=role, owner_id="owner",
                                               requires_auth=experience_requires_auth))
    adapters.register(FixtureOfficialAdapter(source_id="official_careers", fixture_pages={"first":OFFICIAL}, blob_store=blob,
                                             evidence_repository=evidence, role_repository=role, owner_id="owner"))
    registry = build_role_profile_registry(blob_store=blob, evidence_repository=evidence, profile_repository=evidence,
                                           role_repository=role, adapters=adapters, credential_store=credentials)
    runtime = RoleProfileGraphRuntime(registry=registry, evidence_repository=evidence, profile_repository=evidence,
                                      role_repository=role, checkpointer=checkpointer)
    return runtime, evidence, role, registry, adapters


def _state(adapters, thread_id="role-thread"):
    scope = SearchScope(scope_id="scope-role", target_role_queries=["AI Agent"], target_role_family="ai_agent_engineering",
                        locations=["成都"], graduation_year="2027", recruitment_type="autumn_campus")
    return create_role_profile_state(thread_id=thread_id, user_id="owner", search_scope=scope,
                                     enabled_source_ids=["fixture_jobs","fixture_experience","official_careers"],
                                     source_capabilities=adapters.capabilities(),
                                     official_domains={"甲科技":["careers.example"],"乙科技":["careers.example"]})


def test_full_fixture_graph_completes_with_auditable_profiles(tmp_path):
    runtime, evidence, role, registry, adapters = _build(tmp_path, InMemorySaver())
    result = runtime.invoke(_state(adapters))
    assert result["status"] == "completed"
    assert result["next_action"] == "complete"
    assert len(result["job_cluster_ids"]) == 3
    assert len(result["job_instance_profile_snapshot_ids"]) == 3
    assert len(result["official_status_by_cluster"]) == 3
    assert all(role.get(value, __import__("campus_job_agent.schemas", fromlist=["JobIdentityLink"]).JobIdentityLink).status == "confirmed"
               for value in result["job_identity_link_ids"])
    family = evidence.get_profile(result["role_family_profile_snapshot_id"])
    assert family.profile_data["sample"]["job_instance_count"] == 3
    assert family.profile_data["sample"]["distinct_company_count"] == 2
    assert family.profile_data["sample"]["sample_status"] == "sufficient"
    assert family.profile_data["core_requirements"][0]["eligible_job_instance_count"] == 3
    assert all(evidence.get_artifact(value) is not None for value in result["raw_artifact_ids"])
    assert all(receipt["archived_count"] == receipt["received_count"] for receipt in result["source_run_receipts"] if receipt["status"] == "completed")


def test_duplicate_graph_invoke_reuses_batches_and_snapshots(tmp_path):
    runtime, evidence, role, registry, adapters = _build(tmp_path, InMemorySaver())
    first = runtime.invoke(_state(adapters, "idempotent-thread"))
    batch_count = len(role.list("source_batch", __import__("campus_job_agent.schemas", fromlist=["SourceBatch"]).SourceBatch))
    second = runtime.get_state("idempotent-thread").values
    assert second["role_family_profile_snapshot_id"] == first["role_family_profile_snapshot_id"]
    assert len(role.list("source_batch", __import__("campus_job_agent.schemas", fromlist=["SourceBatch"]).SourceBatch)) == batch_count


def test_auth_interrupt_authorized_resume_and_credential_redaction(tmp_path):
    runtime, evidence, role, registry, adapters = _build(tmp_path, InMemorySaver(), experience_requires_auth=True)
    interrupted = runtime.invoke(_state(adapters, "auth-thread"))
    request = interrupted["__interrupt__"][0].value
    assert request["interaction_type"] == "authorize_source"
    curl_file = tmp_path / "nowcoder.curl.txt"
    curl_file.write_text("curl 'https://www.nowcoder.com/search' -H 'Cookie: session=very-secret-cookie'", encoding="utf-8")
    imported = registry.run("source.import_credential", {"source_id":"fixture_experience","path":str(curl_file),"allowed_path_roots":[str(tmp_path)]})
    credential_ref = imported.records[0]["credential_ref"]
    completed = runtime.resume(thread_id="auth-thread", response={
        "request_id":request["request_id"],"thread_id":"auth-thread","user_id":"owner","source_id":"fixture_experience",
        "action":"authorized","credential_ref":credential_ref,
    })
    assert completed["status"] == "completed"
    serialized = str({"state":completed,"artifacts":[item.model_dump(mode="json") for item in [evidence.get_artifact(value) for value in completed["raw_artifact_ids"]] if item]})
    assert "very-secret-cookie" not in serialized
    assert completed["credential_refs"]["fixture_experience"] == credential_ref


def test_auth_skip_does_not_request_same_source_again(tmp_path):
    runtime, evidence, role, registry, adapters = _build(tmp_path, InMemorySaver(), experience_requires_auth=True)
    interrupted = runtime.invoke(_state(adapters, "skip-thread"))
    request = interrupted["__interrupt__"][0].value
    completed = runtime.resume(thread_id="skip-thread", response={
        "request_id":request["request_id"],"thread_id":"skip-thread","user_id":"owner","source_id":"fixture_experience","action":"skip_source",
    })
    assert completed["status"] == "completed_with_unknowns"
    assert "fixture_experience" in completed["skipped_source_ids"]
    assert "__interrupt__" not in completed


def test_sqlite_checkpoint_can_resume_in_new_graph_instance(tmp_path):
    checkpoint = tmp_path / "checkpoint.sqlite3"
    with open_sqlite_checkpointer(checkpoint) as saver:
        runtime, evidence, role, registry, adapters = _build(tmp_path, saver, experience_requires_auth=True)
        interrupted = runtime.invoke(_state(adapters, "restart-thread"))
        request = interrupted["__interrupt__"][0].value
        curl_file = tmp_path / "credential.curl.txt"
        curl_file.write_text("curl 'https://www.nowcoder.com/' -H 'Cookie: session=secret'", encoding="utf-8")
        ref = registry.run("source.import_credential", {"source_id":"fixture_experience","path":str(curl_file),"allowed_path_roots":[str(tmp_path)]}).records[0]["credential_ref"]
    with open_sqlite_checkpointer(checkpoint) as saver:
        runtime2, evidence2, role2, registry2, adapters2 = _build(tmp_path, saver, experience_requires_auth=True)
        completed = runtime2.resume(thread_id="restart-thread", response={
            "request_id":request["request_id"],"thread_id":"restart-thread","user_id":"owner","source_id":"fixture_experience",
            "action":"authorized","credential_ref":ref,
        })
    assert completed["status"] == "completed"


def test_budget_termination_is_explicit_unknowns(tmp_path):
    runtime, evidence, role, registry, adapters = _build(tmp_path, InMemorySaver())
    state = _state(adapters, "budget-thread")
    state["budgets"] = RoleSearchBudget(max_query_rounds=1, max_queries=1, max_documents=10, max_tool_calls=50).model_dump()
    result = runtime.invoke(state)
    assert result["status"] == "completed_with_unknowns"
    assert result["next_action"] == "finalize_with_unknowns"


def test_run_exports_replayable_source_handoff_files(tmp_path):
    runtime, evidence, role, registry, adapters = _build(tmp_path, InMemorySaver())
    state = _state(adapters, "export-thread")
    state["output_dir"] = str(tmp_path / "run-output")
    result = runtime.invoke(state)
    output = tmp_path / "run-output"
    assert result["status"] == "completed"
    for name in ["search_scope.json", "user_needs.md", "query_history.jsonl", "source_receipts.jsonl",
                 "source_index.jsonl", "jobs_normalized.jsonl", "official_verifications.jsonl",
                 "job_identity_links.jsonl", "field_resolutions.jsonl", "experience_normalized.jsonl",
                 "role_profile_report.md"]:
        assert (output / name).is_file()


def test_llm_planner_failure_uses_deterministic_fallback(tmp_path):
    class BrokenPlanner:
        name = "llm"
        def plan(self, *args, **kwargs):
            raise ValueError("invalid structured role query output")
    runtime, evidence, role, registry, adapters = _build(tmp_path, InMemorySaver())
    runtime = RoleProfileGraphRuntime(registry=registry, evidence_repository=evidence, profile_repository=evidence,
                                      role_repository=role, checkpointer=InMemorySaver(), planner=BrokenPlanner())
    result = runtime.invoke(_state(adapters, "llm-fallback-thread"))
    assert result["status"] == "completed"
    assert any(item.get("fallback") == "deterministic" and item.get("error_type") == "llm_output_error" for item in result["errors"])
