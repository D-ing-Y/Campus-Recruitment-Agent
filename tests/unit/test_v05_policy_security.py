from pathlib import Path

import httpx
import pytest

from campus_job_agent.schemas import RoleCoverageAssessment, RoleSearchBudget, RoleSearchCounter, SearchScope
from campus_job_agent.sources import LocalCredentialStore, SQLiteRoleRepository, ZhaopinJobsAdapter
from campus_job_agent.sources.processing import extract_archived_document, normalize_job_document
from campus_job_agent.storage import LocalBlobStore, SQLiteRepository
from campus_job_agent.workflows.role_profile.policy import RoleRoutePolicy


def _assessment(action="change_query", sufficient=False):
    return RoleCoverageAssessment(scope_id="s", is_sufficient=sufficient,
                                  dimension_results={}, recommended_action="complete" if sufficient else action,
                                  reason="test", confidence=1)


def test_live_adapter_is_opt_in_and_default_is_policy_blocked(tmp_path):
    from campus_job_agent.schemas import SourceQuery
    db = tmp_path / "db.sqlite"
    adapter = ZhaopinJobsAdapter(blob_store=LocalBlobStore(tmp_path / "blob"), evidence_repository=SQLiteRepository(db),
                              role_repository=SQLiteRoleRepository(db), owner_id="u")
    query = SourceQuery(channel="recruitment_discovery", source_id="zhaopin_jobs", keywords=["AI Agent"],
                        role_family="ai_agent_engineering", graduation_year="2027", recruitment_type="autumn_campus")
    assert adapter.collect(query).status == "policy_blocked"


def _live_zhaopin(tmp_path, **kwargs):
    from campus_job_agent.schemas import SourceQuery
    db = tmp_path / "live.sqlite"
    adapter = ZhaopinJobsAdapter(blob_store=LocalBlobStore(tmp_path / "live-blob"), evidence_repository=SQLiteRepository(db),
                              role_repository=SQLiteRoleRepository(db), owner_id="u", live_enabled=True, **kwargs)
    query = SourceQuery(channel="recruitment_discovery", source_id="zhaopin_jobs", keywords=["AI Agent"],
                        role_family="ai_agent_engineering", graduation_year="2027", recruitment_type="autumn_campus")
    return adapter, query


def test_live_adapter_classifies_rate_limit(tmp_path):
    adapter, query = _live_zhaopin(tmp_path)
    adapter._request = lambda url, headers: httpx.Response(429, request=httpx.Request("GET", url))
    batch = adapter.collect(query)
    assert batch.status == "rate_limited"
    assert batch.documents[0].raw_artifact_id
    assert batch.documents[0].access_status == "rate_limited"


def test_live_adapter_classifies_source_changed(tmp_path):
    adapter, query = _live_zhaopin(tmp_path)
    adapter._request = lambda url, headers: httpx.Response(200, headers={"x-source-changed":"true"}, request=httpx.Request("GET", url))
    batch = adapter.collect(query)
    assert batch.status == "source_changed"
    assert batch.documents[0].raw_artifact_id
    assert batch.documents[0].access_status == "source_changed"


def test_zhaopin_live_adapter_uses_archivable_search_page(tmp_path):
    adapter, query = _live_zhaopin(tmp_path)
    query.location = "成都"
    captured = {}
    def respond(url, headers):
        captured.update({"url": url, "headers": headers})
        return httpx.Response(200, text='<a href="https://www.zhaopin.com/jobdetail/CC1.htm">AI Agent</a>',
                              request=httpx.Request("GET", url))
    adapter._request = respond
    batch = adapter.collect(query)
    assert batch.status == "success"
    assert captured["url"].startswith("https://sou.zhaopin.com/?")
    assert "jl=%E6%88%90%E9%83%BD" in captured["url"]
    assert captured["headers"]["Referer"] == "https://www.zhaopin.com/"
    assert batch.documents[0].document_kind == "search_page"


def test_zhaopin_live_adapter_stops_on_verification_page(tmp_path):
    adapter, query = _live_zhaopin(tmp_path)
    adapter._request = lambda url, headers: httpx.Response(
        200, text="请完成安全验证", request=httpx.Request("GET", url)
    )
    batch = adapter.collect(query)
    assert batch.status == "authentication_required"
    assert batch.documents[0].raw_artifact_id


def test_zhaopin_login_links_inside_real_results_are_not_a_login_wall(tmp_path):
    adapter, query = _live_zhaopin(tmp_path)
    adapter._request = lambda url, headers: httpx.Response(
        200,
        text='<a href="https://passport.zhaopin.com/login">登录</a>'
             '<a href="https://www.zhaopin.com/jobdetail/CC1.htm">AI Agent</a>',
        request=httpx.Request("GET", url),
    )
    assert adapter.collect(query).status == "success"


def test_zhaopin_response_is_archived_before_card_normalization(tmp_path):
    adapter, query = _live_zhaopin(tmp_path)
    html = b'''<div class="joblist-box__item clearfix">
      <div class="jobinfo__top"><a href="https://www.zhaopin.com/jobdetail/CC123.htm">AI Agent\xe5\xb7\xa5\xe7\xa8\x8b\xe5\xb8\x88</a></div>
      <div class="companyinfo__top"><a>\xe7\xa4\xba\xe4\xbe\x8b\xe7\xa7\x91\xe6\x8a\x80</a></div>
      <div class="job-salary">20-30K</div><div class="job-area">\xe6\x88\x90\xe9\x83\xbd-\xe9\xab\x98\xe6\x96\xb0\xe5\x8c\xba</div>
    </div></div>'''
    adapter._request = lambda url, headers: httpx.Response(
        200, content=html, headers={"content-type": "text/html; charset=utf-8"},
        request=httpx.Request("GET", url),
    )
    batch = adapter.collect(query)
    document = batch.documents[0]
    extraction, fragments = extract_archived_document(
        document, blob_store=adapter.blob_store, repository=adapter.evidence_repository,
    )
    scope = SearchScope(
        target_role_queries=["AI Agent"], target_role_family="ai_agent_engineering",
        locations=["成都"], graduation_year="2027", recruitment_type="autumn_campus",
    )
    jobs = normalize_job_document(document, fragments, scope)
    assert document.raw_artifact_id and extraction.artifact_id == document.raw_artifact_id
    assert len(jobs) == 1 and jobs[0].company == "示例科技"
    assert jobs[0].raw_artifact_ids == [document.raw_artifact_id]


def test_live_adapter_classifies_network_timeout(tmp_path):
    adapter, query = _live_zhaopin(tmp_path)
    def timeout(url, headers): raise httpx.ReadTimeout("timeout", request=httpx.Request("GET", url))
    adapter._request = timeout
    batch = adapter.collect(query)
    assert batch.status == "failed" and batch.error_type == "network_timeout"


def test_live_adapter_stops_when_robots_policy_disallows(tmp_path):
    adapter, query = _live_zhaopin(tmp_path, robots_allowed=False)
    assert adapter.collect(query).status == "robots_disallowed"


def test_credential_import_returns_only_ref_and_source(tmp_path):
    path = tmp_path / "request.curl"
    path.write_text("curl 'https://www.nowcoder.com/' -H 'Cookie: secret-cookie-value'", encoding="utf-8")
    store = LocalCredentialStore(tmp_path / "secrets")
    ref = store.import_curl(source_id="nowcoder_experience", path=path, allowed_path_roots=[str(tmp_path)])
    assert "secret-cookie-value" not in ref.model_dump_json()
    assert store.resolve(ref.credential_ref, source_id="nowcoder_experience")["cookie"] == "secret-cookie-value"


def test_credential_ref_cannot_cross_source(tmp_path):
    path = tmp_path / "request.curl"
    path.write_text("curl 'https://www.nowcoder.com/' -H 'Cookie: secret'", encoding="utf-8")
    store = LocalCredentialStore(tmp_path / "secrets")
    ref = store.import_curl(source_id="nowcoder_experience", path=path, allowed_path_roots=[str(tmp_path)])
    with pytest.raises(ValueError):
        store.resolve(ref.credential_ref, source_id="zhaopin_jobs")


def test_non_curl_credential_file_is_rejected(tmp_path):
    path = tmp_path / "secret.txt"
    path.write_text("Cookie: secret", encoding="utf-8")
    with pytest.raises(ValueError):
        LocalCredentialStore(tmp_path / "secrets").import_curl(source_id="zhaopin_jobs", path=path, allowed_path_roots=[str(tmp_path)])


def test_route_policy_prefers_complete_when_sufficient():
    action = RoleRoutePolicy().decide(assessment=_assessment(sufficient=True), budgets=RoleSearchBudget(),
                                      counters=RoleSearchCounter(), has_fatal_error=False,
                                      pending_auth_source_id=None, has_official_plans=True, has_next_cursor=False)
    assert action == "complete"


def test_route_policy_prioritizes_auth_before_search():
    action = RoleRoutePolicy().decide(assessment=_assessment("search_more"), budgets=RoleSearchBudget(),
                                      counters=RoleSearchCounter(), has_fatal_error=False,
                                      pending_auth_source_id="nowcoder", has_official_plans=True, has_next_cursor=True)
    assert action == "await_user_auth"


def test_route_policy_uses_pagination_when_available():
    action = RoleRoutePolicy().decide(assessment=_assessment("search_more"), budgets=RoleSearchBudget(),
                                      counters=RoleSearchCounter(), has_fatal_error=False,
                                      pending_auth_source_id=None, has_official_plans=True, has_next_cursor=True)
    assert action == "search_more"


def test_route_policy_falls_back_from_missing_cursor():
    action = RoleRoutePolicy().decide(assessment=_assessment("search_more"), budgets=RoleSearchBudget(),
                                      counters=RoleSearchCounter(), has_fatal_error=False,
                                      pending_auth_source_id=None, has_official_plans=True, has_next_cursor=False)
    assert action == "change_query"


def test_route_policy_stops_at_hard_budget():
    budget = RoleSearchBudget(max_query_rounds=1)
    action = RoleRoutePolicy().decide(assessment=_assessment("change_query"), budgets=budget,
                                      counters=RoleSearchCounter(query_rounds=1), has_fatal_error=False,
                                      pending_auth_source_id=None, has_official_plans=True, has_next_cursor=False)
    assert action == "finalize_with_unknowns"


def test_fatal_storage_error_routes_fail():
    action = RoleRoutePolicy().decide(assessment=_assessment(), budgets=RoleSearchBudget(),
                                      counters=RoleSearchCounter(), has_fatal_error=True,
                                      pending_auth_source_id="nowcoder", has_official_plans=True, has_next_cursor=True)
    assert action == "fail"
