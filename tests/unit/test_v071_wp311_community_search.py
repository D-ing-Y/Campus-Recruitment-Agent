from __future__ import annotations

import json
import hashlib
from pathlib import Path

import httpx
import pytest

from campus_job_agent.integrations.social_media import (
    MediaCrawlerSidecarClient,
    MediaCrawlerSidecarConfig,
    SocialBridgeError,
)
from campus_job_agent.schemas import (
    CommunitySearchDiagnostic,
    CommunityEvidenceCoverage,
    CompanyRoleGroup,
    EvidenceFragment,
    SourceDetailRequest,
    SourceCapabilities,
    SourceDocument,
    SourceQuery,
)
from campus_job_agent.sources import SQLiteRoleRepository, XiaohongshuExperienceAdapter
from campus_job_agent.sources.processing import (
    diagnose_community_search,
    discover_community_post_candidates,
    extract_archived_document,
)
from campus_job_agent.sources.role_intelligence import (
    COMMUNITY_SOURCE_CASCADES,
    build_community_search_query,
    role_family_display_name,
)
from campus_job_agent.storage import LocalBlobStore, SQLiteRepository


def _group() -> CompanyRoleGroup:
    return CompanyRoleGroup(
        group_id="group-1", search_scope_id="scope-1", company_key="甲公司",
        company_display_name="甲公司", role_family_id="ai_agent_engineering",
        job_instance_ids=["job-1"], exact_role_terms=["大模型应用开发工程师"],
    )


def _fake_sidecar_install(path: Path, commit: str = "1234567890abcdef") -> None:
    path.mkdir()
    (path / ".git").mkdir()
    (path / ".git" / "HEAD").write_text(commit, encoding="utf-8")


def test_three_round_queries_use_display_terms_and_stable_lineage() -> None:
    group = _group()
    previous = None
    values = []
    for round_index in (1, 2, 3):
        query = build_community_search_query(
            group, evidence_purpose="interview_experience",
            source_id="nowcoder_experience", round_index=round_index,
            source_priority=1, parent_query_id=previous,
        )
        values.append(query)
        previous = query.query_id
    assert values[0].query_text == "甲公司 大模型应用开发工程师 面经"
    assert values[1].query_text == "甲公司 AI Agent 开发 面经"
    assert values[2].query_text == "甲公司 面经"
    assert [item.relaxation_level for item in values] == [
        "exact_role", "role_family", "company_only",
    ]
    assert values[1].parent_query_id == values[0].query_id
    assert "ai_agent_engineering" not in " ".join(item.query_text for item in values)
    assert COMMUNITY_SOURCE_CASCADES["employment_experience"] == (
        "xiaohongshu_experience", "nowcoder_experience",
    )
    assert role_family_display_name("ai_agent_engineering") == "AI Agent 开发"


def test_three_round_queries_use_only_a_verified_company_alias() -> None:
    group = CompanyRoleGroup(
        group_id="brand-group", search_scope_id="scope-1",
        company_key="北京三快在线科技有限公司",
        company_display_name="北京三快在线科技有限公司",
        company_search_term="美团", verified_company_aliases=["美团"],
        role_family_id="ai_agent_engineering", job_instance_ids=["job-1"],
        exact_role_terms=["大模型应用开发工程师"],
    )
    query = build_community_search_query(
        group, evidence_purpose="interview_experience",
        source_id="nowcoder_experience", round_index=1, source_priority=1,
    )
    assert query.query_text == "美团 大模型应用开发工程师 面经"
    with pytest.raises(ValueError, match="verified alias"):
        group.model_copy(update={"company_search_term": "自由生成别名"}).model_validate(
            {**group.model_dump(), "company_search_term": "自由生成别名"}
        )


def test_query_rejects_wrong_source_priority() -> None:
    with pytest.raises(ValueError, match="cascade"):
        build_community_search_query(
            _group(), evidence_purpose="employment_experience",
            source_id="nowcoder_experience", round_index=1, source_priority=1,
        )


def test_coverage_requires_three_unique_clusters() -> None:
    value = CommunityEvidenceCoverage(
        coverage_id="coverage", company_role_group_id="group",
        evidence_purpose="interview_experience",
        accepted_document_ids=["a", "b", "c"], independent_document_count=3,
        accepted_cluster_ids=["ca", "cb", "cc"], independent_cluster_count=3,
        status="sufficient", next_action="next_purpose",
    )
    assert value.target_cluster_count == 3
    with pytest.raises(ValueError, match="target"):
        CommunityEvidenceCoverage(
            coverage_id="coverage", company_role_group_id="group",
            evidence_purpose="interview_experience", accepted_document_ids=["a"],
            independent_document_count=1, accepted_cluster_ids=["ca"],
            independent_cluster_count=1, status="sufficient",
            next_action="next_purpose",
        )


def test_authorization_mode_is_backward_compatible() -> None:
    old = SourceCapabilities(
        source_id="old", channel="experience", source_type="community_experience",
        adapter_version="v1", requires_auth=True,
    )
    assert old.authorization_mode == "credential_ref"
    external = old.model_copy(update={"authorization_mode": "external_session"})
    assert external.authorization_mode == "external_session"


def test_sidecar_search_and_detail_keep_sensitive_locator_local(tmp_path: Path) -> None:
    install = tmp_path / "MediaCrawler"
    _fake_sidecar_install(install)
    cache = install / ".campus-agent-bridge-cache"
    state = {"task": "", "starts": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/crawler/status":
            return httpx.Response(200, json={"status": "idle"})
        if path == "/api/crawler/start":
            payload = json.loads(request.content)
            assert payload["platform"] == "xhs"
            assert payload["enable_comments"] is False
            assert payload["enable_sub_comments"] is False
            assert payload["creator_ids"] == ""
            assert payload["save_option"] == "json"
            state["task"] = payload["crawler_type"]
            state["starts"] += 1
            return httpx.Response(200, json={"ok": True})
        if path == "/api/data/files":
            assert request.url.params["platform"] == "xhs"
            assert request.url.params["file_type"] == "json"
            files = [{
                "name": "old_contents_2026.json", "path": "xhs/json/old_contents_2026.json",
                "modified_at": "0", "size": 10,
            }]
            if state["starts"]:
                files.append({
                    "name": f"{state['task']}_contents_2026.json",
                    "path": f"xhs/json/{state['task']}_contents_2026.json",
                    "modified_at": str(state["starts"]), "size": 10,
                })
            return httpx.Response(200, json={"files": files})
        if path.endswith("/xhs/json/old_contents_2026.json"):
            raise AssertionError("old sidecar output must not be read")
        if path.endswith("/xhs/json/search_contents_2026.json"):
            assert request.url.params["preview"] == "false"
            return httpx.Response(200, json=[{
                "note_id": "stale-note", "title": "旧记录", "desc": "不应进入本轮",
                "source_keyword": "上一轮关键词",
            }, {
                "note_id": "note-1", "title": "面试记录", "desc": "摘要",
                "note_url": "https://www.xiaohongshu.com/explore/note-1?xsec_token=secret",
                "xsec_token": "secret",
                "source_keyword": "甲公司 AI Agent 开发 面经",
            }])
        if path.endswith("/xhs/json/detail_contents_2026.json"):
            assert request.url.params["preview"] == "false"
            return httpx.Response(200, json=[{
                "note_id": "another-note", "title": "其他记录", "desc": "不应返回",
            }, {
                "note_id": "note-1", "title": "面试记录",
                "desc": "甲公司 AI Agent 开发岗位面试询问了 RAG。",
                "xsec_token": "secret",
            }])
        raise AssertionError(path)

    client = MediaCrawlerSidecarClient(
        MediaCrawlerSidecarConfig(
            base_url="http://127.0.0.1:8080/api", installation_path=install,
            pinned_commit="1234567890abcdef", license_accepted=True,
            candidate_cache_root=cache, poll_interval_seconds=0,
        ),
        transport=httpx.MockTransport(handler),
    )
    search = client.search_posts(keywords="甲公司 AI Agent 开发 面经", limit=2)
    rendered = json.dumps(search, ensure_ascii=False)
    assert "secret" not in rendered and "xsec_token" not in rendered
    candidate = search["candidates"][0]
    assert candidate["canonical_url"].endswith("/note-1")
    detail = client.fetch_post_detail(candidate_ref=candidate["candidate_ref"])
    assert detail["body"].endswith("RAG。")
    assert "secret" not in json.dumps(detail, ensure_ascii=False)


def _community_document(source_id: str, text: str) -> tuple[SourceDocument, EvidenceFragment]:
    digest = hashlib.sha256(text.encode()).hexdigest()
    document = SourceDocument(
        source_document_id=f"doc-{source_id}", source_id=source_id,
        channel="experience", query_id=f"query-{source_id}",
        source_url="https://api.search.brave.com/res/v1/web/search",
        document_kind="experience_search", raw_artifact_id=f"artifact-{source_id}",
        content_hash=digest,
    )
    fragment = EvidenceFragment(
        fragment_id=f"fragment-{source_id}", artifact_id=f"artifact-{source_id}",
        locator_type="css_selector_and_char_range", locator={"start": 0, "end": len(text)},
        text=text, text_hash=digest,
    )
    return document, fragment


def test_brave_diagnostic_distinguishes_non_detail_results_from_parser_change() -> None:
    text = json.dumps({"web": {"results": [{
        "url": "https://www.nowcoder.com/", "title": "牛客首页",
    }]}})
    document, fragment = _community_document("nowcoder_experience", text)
    diagnostic = diagnose_community_search(document, [fragment], [])
    assert isinstance(diagnostic, CommunitySearchDiagnostic)
    assert diagnostic.outcome == "non_post_cards_only"
    assert diagnostic.raw_record_count == 1
    assert diagnostic.post_candidate_count == 0
    assert diagnostic.non_post_record_count == 1

    drift_text = '{"web":{"results":"changed"}}'
    drift_document, drift_fragment = _community_document("nowcoder_experience", drift_text)
    drift = diagnose_community_search(drift_document, [drift_fragment], [])
    assert drift.outcome == "parser_changed"


def test_xiaohongshu_diagnostic_reports_an_explicit_empty_candidate_list() -> None:
    text = json.dumps({"platform": "xiaohongshu", "candidates": []})
    document, fragment = _community_document("xiaohongshu_experience", text)
    diagnostic = diagnose_community_search(document, [fragment], [])
    assert diagnostic.outcome == "search_empty"


def test_sidecar_task_timeout_is_bounded(tmp_path: Path) -> None:
    install = tmp_path / "MediaCrawler"
    _fake_sidecar_install(install)
    started = {"value": False}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/crawler/status":
            return httpx.Response(200, json={
                "status": "running" if started["value"] else "idle"
            })
        if request.url.path == "/api/data/files":
            return httpx.Response(200, json={"files": []})
        if request.url.path == "/api/crawler/start":
            started["value"] = True
            return httpx.Response(200, json={"ok": True})
        raise AssertionError(request.url.path)

    client = MediaCrawlerSidecarClient(
        MediaCrawlerSidecarConfig(
            base_url="http://localhost:8080/api", installation_path=install,
            pinned_commit="1234567890abcdef", license_accepted=True,
            candidate_cache_root=install / ".campus-agent-bridge-cache",
            poll_interval_seconds=0, max_poll_seconds=0.001,
        ),
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(SocialBridgeError) as raised:
        client.search_posts(keywords="甲公司 面经")
    assert raised.value.code == "network_timeout"


def test_sidecar_failed_subprocess_is_not_reported_as_empty(tmp_path: Path) -> None:
    install = tmp_path / "MediaCrawler"
    _fake_sidecar_install(install)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/crawler/status":
            return httpx.Response(200, json={"status": "idle"})
        if request.url.path == "/api/data/files":
            return httpx.Response(200, json={"files": []})
        if request.url.path == "/api/crawler/start":
            return httpx.Response(200, json={"ok": True})
        if request.url.path == "/api/crawler/logs":
            assert request.url.params["limit"] == "50"
            return httpx.Response(200, json={"logs": [{
                "level": "warning",
                "message": "Page.goto: net::ERR_TIMED_OUT at https://example.invalid",
            }, {
                "level": "warning", "message": "Crawler exited with code: 1",
            }]})
        raise AssertionError(request.url.path)

    client = MediaCrawlerSidecarClient(
        MediaCrawlerSidecarConfig(
            base_url="http://localhost:8080/api", installation_path=install,
            pinned_commit="1234567890abcdef", license_accepted=True,
            candidate_cache_root=install / ".campus-agent-bridge-cache",
            poll_interval_seconds=0,
        ),
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(SocialBridgeError) as raised:
        client.search_posts(keywords="甲公司 面经")
    assert raised.value.code == "network_timeout"


def test_xiaohongshu_adapter_archives_search_before_candidate_and_detail(tmp_path: Path) -> None:
    class FakeBridge:
        def health(self):
            return {"status": "idle"}

        def search_posts(self, *, keywords, limit):
            return {
                "platform": "xiaohongshu", "candidates": [{
                    "candidate_ref": "xhs-candidate:" + "a" * 24,
                    "platform_post_id": "note-1",
                    "canonical_url": "https://www.xiaohongshu.com/explore/note-1",
                    "title": "甲公司面经",
                }], "sensitive_parameters_exposed": False,
            }

        def fetch_post_detail(self, *, candidate_ref):
            return {
                "platform": "xiaohongshu", "candidate_ref": candidate_ref,
                "platform_post_id": "note-1",
                "canonical_url": "https://www.xiaohongshu.com/explore/note-1",
                "title": "甲公司 AI Agent 开发面经",
                "body": "甲公司 AI Agent 开发岗位面试询问了 RAG。",
                "sensitive_parameters_exposed": False,
            }

        def auth_status(self):
            return {"status": "external_session_available"}

    class FakeProfileManager:
        def resolve_cdp(self, value, *, source_id):
            assert value == (
                "local-browser-profile://xiaohongshu_experience/default"
            )
            assert source_id == "xiaohongshu_experience"
            return "http://127.0.0.1:9222"

        def mark_authenticated_verified(self, value, *, verified_at):
            return None

    evidence = SQLiteRepository(tmp_path / "evidence.sqlite3")
    role = SQLiteRoleRepository(tmp_path / "role.sqlite3")
    blob = LocalBlobStore(tmp_path / "blobs")
    adapter = XiaohongshuExperienceAdapter(
        bridge_client=FakeBridge(), blob_store=blob,
        evidence_repository=evidence, role_repository=role,
        owner_id="owner", live_enabled=True,
        browser_profile_manager=FakeProfileManager(),
    )
    query = SourceQuery(
        query_id="query-xhs", channel="experience",
        source_id="xiaohongshu_experience", keywords=["甲公司 AI Agent 开发 面经"],
        company="甲公司", role_family="ai_agent_engineering",
        graduation_year="2027", recruitment_type="autumn_campus", page_size=2,
    )
    profile_ref = (
        "local-browser-profile://xiaohongshu_experience/default"
    )
    search_batch = adapter.collect(
        query, browser_profile_ref=profile_ref
    )
    assert search_batch.status == "success"
    search_document = search_batch.documents[0]
    assert search_document.document_kind == "experience_search"
    _, fragments = extract_archived_document(
        search_document, blob_store=blob, repository=evidence,
    )
    candidates = discover_community_post_candidates(
        search_document, fragments,
        intended_document_types=["interview_experience"],
        company_hint="甲公司", role_family_hint="ai_agent_engineering",
    )
    assert len(candidates) == 1
    assert candidates[0].external_locator_ref == "xhs-candidate:" + "a" * 24
    detail_batch = adapter.fetch_detail(SourceDetailRequest(
        source_id=adapter.source_id, channel="experience", query_id=query.query_id,
        candidate_id=candidates[0].candidate_id,
        parent_document_id=search_document.source_document_id,
        detail_url=candidates[0].detail_url,
        external_locator_ref=candidates[0].external_locator_ref,
        expected_document_kind="experience_post",
    ), browser_profile_ref=profile_ref)
    assert detail_batch.status == "success"
    assert detail_batch.documents[0].document_kind == "experience_post"
    assert evidence.get_artifact(str(detail_batch.documents[0].raw_artifact_id)) is not None


@pytest.mark.parametrize(
    "update,code",
    [
        ({"base_url": "https://example.com"}, "policy_blocked"),
        ({"license_accepted": False}, "license_not_accepted"),
        ({"pinned_commit": "main"}, "unpinned_sidecar"),
    ],
)
def test_sidecar_configuration_is_fail_closed(tmp_path: Path, update: dict, code: str) -> None:
    install = tmp_path / "MediaCrawler"
    _fake_sidecar_install(install)
    values = {
        "base_url": "http://localhost:8080/api", "installation_path": install,
        "pinned_commit": "1234567890abcdef", "license_accepted": True,
        "candidate_cache_root": install / ".campus-agent-bridge-cache",
        **update,
    }
    with pytest.raises(SocialBridgeError) as raised:
        MediaCrawlerSidecarConfig(**values).validate()
    assert raised.value.code == code
