import json
from datetime import UTC, datetime, timedelta

import pytest

from campus_job_agent.evidence.claim_validator import ClaimValidationError
from campus_job_agent.schemas import (
    EvidenceArtifact, EvidenceClaim, EvidenceFragment, JobIdentityLink,
    NormalizedJobPosting, SearchScope, SourceDocument,
)
from campus_job_agent.schemas.evidence import ClaimExtractor
from campus_job_agent.sources.processing import (
    apply_hard_scope, deduplicate_experience, deduplicate_jobs,
    link_job_identity, normalize_experience_document, normalize_job_document,
    parse_official_document, validate_official_redirect,
)
from campus_job_agent.sources.role_pipeline import RoleClaimValidator
from campus_job_agent.storage.local_blob import LocalBlobStore
from campus_job_agent.storage.sqlite import SQLiteRepository


def _job(identifier: str, company="示例科技", title="AI Agent开发工程师", city="成都", source="boss"):
    return NormalizedJobPosting(
        job_posting_id=identifier, job_id="same-id", company=company, role_title=title,
        role_family="ai_agent_engineering", city=city, graduation_year="2027",
        recruitment_type="autumn_campus", source_url=f"fixture://{source}/{identifier}", source_id=source,
        source_type="recruitment_platform", job_description="构建 Agent 系统和评测",
        requirements_raw="熟悉 Python；了解 RAG", raw_artifact_ids=[f"a-{identifier}"], supporting_fragment_ids=[f"f-{identifier}"],
    )


def _scope():
    return SearchScope(target_role_queries=["AI Agent"], target_role_family="ai_agent_engineering",
                       locations=["成都"], graduation_year="2027", recruitment_type="autumn_campus")


def test_exact_cross_source_dedup_counts_one_cluster():
    left, right = _job("j1", source="boss"), _job("j2", source="other")
    clusters, fuzzy = deduplicate_jobs([left, right])
    assert len(clusters) == 1
    assert set(clusters[0].member_job_posting_ids) == {"j1", "j2"}
    assert fuzzy == []


def test_similar_title_without_identity_is_only_fuzzy_candidate():
    left = _job("j1")
    right = _job("j2", title="AI Agent研发工程师").model_copy(update={"job_id": None, "application_url": None, "job_description": ""})
    left = left.model_copy(update={"job_id": None, "application_url": None, "job_description": ""})
    clusters, fuzzy = deduplicate_jobs([left, right])
    assert len(clusters) == 2
    assert fuzzy


def test_hard_scope_only_excludes_explicit_mismatch():
    excluded = apply_hard_scope(_job("j1", city="北京"), _scope())
    unknown = apply_hard_scope(_job("j2", city="unknown"), _scope())
    expired = apply_hard_scope(
        _job("j3").model_copy(update={"application_deadline": datetime.now(UTC) - timedelta(days=1)}),
        _scope(),
    )
    assert excluded.status == "excluded_hard_scope"
    assert unknown.status == "included"
    assert expired.status == "expired"
    assert "application_deadline_passed" in expired.notes


def test_identity_confirmed_with_company_title_location_cycle_and_content():
    discovery = _job("j1")
    official = _job("o1", source="official").model_copy(update={"source_type": "employer_official"})
    cluster = deduplicate_jobs([discovery])[0][0]
    link = link_job_identity(cluster, discovery, [official])
    assert link.status == "confirmed"


def test_unknown_recruitment_cycle_is_not_a_strong_identity_signal():
    discovery = _job("j1").model_copy(update={"graduation_year":"unknown", "recruitment_type":"unknown"})
    official = _job("o1", source="official").model_copy(update={
        "source_type":"employer_official", "graduation_year":"unknown", "recruitment_type":"unknown",
    })
    cluster = deduplicate_jobs([discovery])[0][0]
    link = link_job_identity(cluster, discovery, [official])
    assert link.match_signals["recruitment_cycle"] == "unknown"


def test_long_official_responsibilities_embedded_in_platform_text_are_strong():
    duties = "负责 AI Agent 产品规划、用户需求分析和产品方案设计，协调研发、算法与运营团队推动产品落地。" * 4
    discovery = _job("j1").model_copy(update={"job_description":"部门介绍和团队愿景。" * 10 + duties + "岗位亮点。" * 10})
    official = _job("o1", source="official").model_copy(update={
        "source_type":"employer_official", "job_description":duties,
    })
    cluster = deduplicate_jobs([discovery])[0][0]
    link = link_job_identity(cluster, discovery, [official])
    assert link.match_signals["responsibility_signature"] == "strong"
    assert link.status == "confirmed"


def test_official_not_found_keeps_explicit_status():
    discovery = _job("j1")
    cluster = deduplicate_jobs([discovery])[0][0]
    link = link_job_identity(cluster, discovery, [], verification_status="official_not_found")
    assert link.status == "official_not_found"
    assert link.official_job_posting_id is None


def test_redirect_must_remain_on_allowlist():
    from campus_job_agent.schemas import OfficialVerificationPlan
    plan = OfficialVerificationPlan(job_cluster_id="c", canonical_company="A", candidate_role_title="AI",
                                    allowed_domains=["careers.example.com"])
    with pytest.raises(ValueError):
        validate_official_redirect("https://careers.example.com/jobs", "https://evil.example/jobs", plan)


def test_json_ld_is_first_official_parser():
    html = '<script type="application/ld+json">{"@type":"JobPosting","title":"AI Agent工程师","hiringOrganization":{"name":"示例科技"},"jobLocation":{"address":{"addressLocality":"成都"}},"description":"Agent评测"}</script>'
    fragment = EvidenceFragment(artifact_id="a", locator_type="selector", locator={"selector":"body"}, text=html,
                                text_hash=__import__("hashlib").sha256(html.encode()).hexdigest())
    document = SourceDocument(source_id="official", channel="employer_official", query_id="q", source_url="https://careers.example.com/j/1",
                              document_kind="official_job_detail", raw_artifact_id="a", content_hash="0"*64)
    jobs, method, spec = parse_official_document(document, [fragment], _scope())
    assert method == "json_ld" and jobs[0].company == "示例科技" and spec is None


def test_unknown_official_site_returns_declarative_spec_not_code():
    text = "window.instructions = 'run python crawler';"
    fragment = EvidenceFragment(artifact_id="a", locator_type="selector", locator={"selector":"body"}, text=text,
                                text_hash=__import__("hashlib").sha256(text.encode()).hexdigest())
    document = SourceDocument(source_id="official", channel="employer_official", query_id="q", source_url="https://careers.example.com/unknown",
                              document_kind="official_job_detail", raw_artifact_id="a", content_hash="0"*64)
    jobs, method, spec = parse_official_document(document, [fragment], _scope())
    assert jobs == [] and method == "adapter_required" and spec is not None
    assert "python" not in spec.model_dump_json().lower()


def test_zhaopin_html_search_response_normalizes_jobs():
    text = """
    <div class="joblist-box__item clearfix">
      <div class="jobinfo__top"><a href="http://www.zhaopin.com/jobdetail/CC123.htm">AI Agent工程师</a></div>
      <div class="companyinfo__top"><a>示例科技</a></div>
      <div class="job-salary">20-30K·14薪</div>
      <div class="job-area">成都-高新区</div>
      <div class="companyinfo__staff-name">不应进入规范化数据的人名</div>
    </div></div>
    """
    fragment = EvidenceFragment(artifact_id="a", locator_type="selector", locator={"selector":"body"}, text=text,
                                text_hash=__import__("hashlib").sha256(text.encode()).hexdigest())
    document = SourceDocument(source_id="zhaopin_jobs", channel="recruitment_discovery", query_id="q",
                              source_url="https://sou.zhaopin.com/?jl=成都&kw=AI%20Agent&p=1", document_kind="search_page",
                              raw_artifact_id="a", content_hash="0"*64, content_type="text/html")
    jobs = normalize_job_document(document, [fragment], _scope())
    assert len(jobs) == 1 and jobs[0].company == "示例科技" and jobs[0].salary_min == 20
    assert jobs[0].application_url == "https://www.zhaopin.com/jobdetail/CC123.htm"
    assert "不应进入规范化数据的人名" not in jobs[0].model_dump_json()


def test_zhaopin_embedded_state_precedes_partial_search_card():
    state = {"positionList": [{
        "number": "CC120J13882", "name": "AI Agent工程师 (J13882)",
        "companyName": "曙光信息产业（北京）有限公司", "propertyName": "国企",
        "workCity": "天津", "cityDistrict": "西青", "education": "本科",
        "salary60": "3-6万", "publishTime": "2026-07-21 17:47:56",
        "positionUrl": "http://www.zhaopin.com/jobdetail/CC120J13882.htm",
        "showSkillTags": ["Golang", "Python"],
        "jobDetailData": {"position": {
            "base": {"positionName": "AI Agent工程师 (J13882)"},
            "desc": {"description": "负责 Agent 平台研发。任职资格：本科及以上。"},
            "workLocation": {"workAddress": "天津市西青区"},
        }},
        "staffCard": {"staffName": "不应进入规范化数据的人名"},
    }]}
    text = """
    <div class="joblist-box__item clearfix">
      <div class="jobinfo__top"><a href="http://www.zhaopin.com/jobdetail/CC120J13882.htm">AI Agent工程师 (J13882)</a></div>
    </div></div>
    <script>__INITIAL_STATE__=""" + json.dumps(state, ensure_ascii=False) + "</script>"
    fragment = EvidenceFragment(artifact_id="a", locator_type="selector", locator={"selector":"body"}, text=text,
                                text_hash=__import__("hashlib").sha256(text.encode()).hexdigest())
    document = SourceDocument(source_id="zhaopin_jobs", channel="recruitment_discovery", query_id="q",
                              source_url="https://sou.zhaopin.com/?kw=AI%20Agent", document_kind="search_page",
                              raw_artifact_id="a", content_hash="0"*64, content_type="text/html")
    jobs = normalize_job_document(document, [fragment], _scope())
    assert len(jobs) == 1
    assert jobs[0].company == "曙光信息产业（北京）有限公司"
    assert jobs[0].city == "天津" and jobs[0].job_id == "CC120J13882"
    assert jobs[0].salary_min == 30 and "Agent 平台研发" in jobs[0].job_description
    assert "不应进入规范化数据的人名" not in jobs[0].model_dump_json()


def test_nowcoder_html_embedded_state_normalizes_without_user_profile():
    state = {"app": {"180": {"records": [{"title": "<em>京东</em> AI Agent 一面面经", "data": {
        "userBrief": {"nickname": "private-name"},
        "momentData": {"uuid": "post-1", "newTitle": "京东 AI Agent 一面面经", "newContent": "项目中追问 Redis 和 Agent 评测"},
    }}]}}}
    text = "<html><script>window.__INITIAL_STATE__=" + json.dumps(state, ensure_ascii=False) + "</script></html>"
    fragment = EvidenceFragment(artifact_id="a", locator_type="selector", locator={"selector":"body"}, text=text,
                                text_hash=__import__("hashlib").sha256(text.encode()).hexdigest())
    document = SourceDocument(source_id="nowcoder_experience", channel="experience", query_id="q",
                              source_url="https://www.nowcoder.com/search/all", document_kind="experience_search",
                              raw_artifact_id="a", content_hash="0"*64)
    records = normalize_experience_document(document, [fragment], "ai_agent_engineering")
    assert len(records) == 1 and records[0].company == "京东" and "Redis" in records[0].signals.tech_stack
    assert "private-name" not in records[0].model_dump_json()


def test_community_cannot_create_hard_qualification(tmp_path):
    repo = SQLiteRepository(tmp_path / "db.sqlite")
    blob = LocalBlobStore(tmp_path / "blob")
    raw = b"must have a masters degree"
    digest = __import__("hashlib").sha256(raw).hexdigest()
    artifact = repo.save_artifact(EvidenceArtifact(owner_id="u", source_type="community_experience", content_type="text/plain",
                                                    original_name="post", raw_uri=blob.put("post", raw), content_hash=digest,
                                                    metadata={"channel":"experience"}))
    fragment = repo.save_fragment(EvidenceFragment(artifact_id=artifact.artifact_id, locator_type="char", locator={"start":0,"end":len(raw)},
                                                   text=raw.decode(), text_hash=digest))
    claim = EvidenceClaim(subject_id="role:x", predicate="qualification.degree", value="硕士", claim_type="observed_fact",
                          evidence_fragment_ids=[fragment.fragment_id], confidence=.8,
                          extractor=ClaimExtractor(provider="deterministic", model="test"), prompt_version="v1", schema_version="v0.5")
    with pytest.raises(ClaimValidationError):
        RoleClaimValidator(repo).validate_and_save(claim, owner_id="u")
