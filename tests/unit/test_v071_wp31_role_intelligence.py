from __future__ import annotations

import hashlib
import json

import pytest
from pydantic import ValidationError

from campus_job_agent.schemas import (
    CommunityEvidenceSegment,
    CommunityExtractionBatch,
    CommunityPostCandidate,
    CompanyRoleGroup,
    EvidenceArtifact,
    EvidenceFragment,
    JobPostingCluster,
    NormalizedJobPosting,
    RoleIntelligenceBundle,
    SearchScope,
    SourceDetailRequest,
    SourceDocument,
)
from campus_job_agent.sources.processing import (
    discover_community_post_candidates,
    discover_job_detail_candidates,
    normalize_job_document,
)
from campus_job_agent.sources.role_intelligence import (
    build_community_search_plan,
    build_company_role_groups,
    ensure_community_body_fragment,
    materialize_community_evidence,
)
from campus_job_agent.sources.role_intelligence_projection import (
    EvidenceUsageViolation,
    official_escalation_for_job,
    select_consumer_inputs,
)
from campus_job_agent.storage import LocalBlobStore, SQLiteRepository
from campus_job_agent.workflows.role_profile.graph import _safe_tool_result


def test_checkpoint_tool_result_omits_extracted_page_content() -> None:
    from campus_job_agent.schemas import ToolResult

    result = ToolResult(
        tool_name="source.extract_document",
        status="success",
        records=[{"fragments": [{"text": "sensitive page body" * 100_000}]}],
        evidence_ids=["fragment-1"],
        metadata={"parser_version": "html_v1"},
    )

    safe = _safe_tool_result(result)

    assert "records" not in safe
    assert safe["record_count"] == 1
    assert safe["evidence_ids"] == ["fragment-1"]
    assert "sensitive page body" not in json.dumps(safe)


def _evidence(
    tmp_path,
    raw: bytes = "<h1>甲公司 AI Agent工程师</h1><p>面试追问 RAG 评测。</p><p>团队氛围很好。</p>".encode(),
):
    repository = SQLiteRepository(tmp_path / "evidence.sqlite3")
    blob = LocalBlobStore(tmp_path / "blobs")
    digest = hashlib.sha256(raw).hexdigest()
    artifact = repository.save_artifact(EvidenceArtifact(
        owner_id="owner", source_type="community_experience", content_type="text/html",
        source_url="https://www.nowcoder.com/feed/main/detail/1", original_name="post",
        raw_uri=blob.put("post/raw", raw), content_hash=digest,
        metadata={"channel": "experience"},
    ))
    fragment = repository.save_fragment(EvidenceFragment(
        artifact_id=artifact.artifact_id, locator_type="css_selector_and_char_range",
        locator={"selector": "body", "start": 0, "end": len(raw.decode())},
        text=raw.decode(), text_hash=hashlib.sha256(raw).hexdigest(),
    ))
    document = SourceDocument(
        source_id="nowcoder_experience", channel="experience", query_id="query-1",
        source_url="https://www.nowcoder.com/feed/main/detail/1",
        document_kind="experience_post", raw_artifact_id=artifact.artifact_id,
        content_hash=digest,
    )
    return repository, document, fragment


def test_source_detail_request_is_canonical_and_channel_bound() -> None:
    first = SourceDetailRequest(
        source_id="zhaopin_jobs", channel="recruitment_discovery", query_id="q",
        candidate_id="c", parent_document_id="search", detail_url="https://www.zhaopin.com/jobdetail/1",
        expected_document_kind="job_detail",
    )
    second = SourceDetailRequest.model_validate(first.model_dump())
    assert first.detail_request_id == second.detail_request_id
    assert first.idempotency_key == second.idempotency_key
    with pytest.raises(ValidationError):
        SourceDetailRequest(
            source_id="zhaopin_jobs", channel="recruitment_discovery", query_id="q",
            candidate_id="c", parent_document_id="search", detail_url="https://example.com/1",
            expected_document_kind="experience_post",
        )


def test_search_documents_only_create_candidates() -> None:
    raw = '{"candidates":[{"detail_url":"https://www.zhaopin.com/jobdetail/1","company":"甲","role_title":"AI Agent"}]}'
    fragment = EvidenceFragment(
        artifact_id="artifact", locator_type="char", locator={"start": 0, "end": len(raw)},
        text=raw, text_hash=hashlib.sha256(raw.encode()).hexdigest(),
    )
    document = SourceDocument(
        source_id="zhaopin_jobs", channel="recruitment_discovery", query_id="q",
        source_url="https://sou.zhaopin.com/", document_kind="search_page",
        raw_artifact_id="artifact", content_hash=hashlib.sha256(raw.encode()).hexdigest(),
    )
    candidates = discover_job_detail_candidates(document, [fragment])
    assert [item.detail_url for item in candidates] == ["https://www.zhaopin.com/jobdetail/1"]
    assert candidates[0].search_document_id == document.source_document_id


def test_search_candidate_discovery_preserves_archived_platform_order() -> None:
    raw = json.dumps({"candidates": [
        {"detail_url": "https://www.zhaopin.com/jobdetail/z", "company": "甲", "role_title": "A", "city": "北京"},
        {"detail_url": "https://www.zhaopin.com/jobdetail/a", "company": "乙", "role_title": "B", "city": "成都市"},
        {"detail_url": "https://www.zhaopin.com/jobdetail/z", "company": "重复", "role_title": "C"},
    ]})
    digest = hashlib.sha256(raw.encode()).hexdigest()
    fragment = EvidenceFragment(
        artifact_id="artifact-order", locator_type="char",
        locator={"start": 0, "end": len(raw)}, text=raw, text_hash=digest,
    )
    document = SourceDocument(
        source_id="zhaopin_jobs", channel="recruitment_discovery", query_id="q-order",
        source_url="https://sou.zhaopin.com/", document_kind="search_page",
        raw_artifact_id="artifact-order", content_hash=digest,
    )

    candidates = discover_job_detail_candidates(document, [fragment])

    assert [item.detail_url.rsplit("/", 1)[-1] for item in candidates] == ["z", "a"]
    preferred = discover_job_detail_candidates(
        document, [fragment], preferred_locations=["成都"],
    )
    assert [item.detail_url.rsplit("/", 1)[-1] for item in preferred] == ["a", "z"]
    assert preferred[0].location_hint == "成都市"


def test_current_zhaopin_detail_initial_state_is_normalized() -> None:
    payload = {
        "jobNumber": "CC-test",
        "jobDetail": {
            "detailedCompany": {
                "companyName": "甲科技", "financingStageName": "已上市",
            },
            "detailedPosition": {
                "positionUrl": "https://jobs.zhaopin.com/CC-test.htm",
                "positionNumber": "CC-test", "positionName": "AI Agent工程师",
                "positionWorkCity": "成都", "workAddress": "高新区",
                "description": "负责 Agent 编排与 RAG 评测",
                "skillLabel": ["Python", "RAG"], "education": "本科",
                "salary": "20-30K", "positionStatus": 1,
            },
        },
    }
    raw = "<script>__INITIAL_STATE__=" + __import__("json").dumps(
        payload, ensure_ascii=False
    ) + "</script>"
    fragment = EvidenceFragment(
        artifact_id="artifact-detail", locator_type="char",
        locator={"start": 0, "end": len(raw)}, text=raw,
        text_hash=hashlib.sha256(raw.encode()).hexdigest(),
    )
    document = SourceDocument(
        source_id="zhaopin_jobs", channel="recruitment_discovery",
        query_id="q-detail", source_url="https://jobs.zhaopin.com/CC-test.htm",
        document_kind="job_detail", raw_artifact_id="artifact-detail",
        content_hash=fragment.text_hash,
    )
    scope = SearchScope(
        scope_id="scope-detail", target_role_queries=["AI Agent"],
        target_role_family="ai_agent_engineering", graduation_year="2027",
        recruitment_type="autumn_campus",
    )
    jobs = normalize_job_document(document, [fragment], scope)
    assert len(jobs) == 1
    assert jobs[0].role_family == "ai_agent_engineering"
    assert jobs[0].requirements_normalized == ["Python", "RAG"]
    assert jobs[0].raw_artifact_ids == ["artifact-detail"]


def test_community_search_snippet_only_creates_post_candidate() -> None:
    raw = '{"web":{"results":[{"url":"https://www.nowcoder.com/feed/main/detail/1","title":"甲公司面经","description":"只用于发现"}]}}'
    fragment = EvidenceFragment(
        artifact_id="artifact", locator_type="char", locator={"start": 0, "end": len(raw)},
        text=raw, text_hash=hashlib.sha256(raw.encode()).hexdigest(),
    )
    document = SourceDocument(
        source_id="nowcoder_experience", channel="experience", query_id="q",
        source_url="https://api.search.brave.com/res/v1/web/search",
        document_kind="experience_search",
        raw_artifact_id="artifact", content_hash=hashlib.sha256(raw.encode()).hexdigest(),
    )
    candidates = discover_community_post_candidates(
        document, [fragment], intended_document_types=["interview_experience"],
        company_hint="甲公司", role_family_hint="ai_agent_engineering",
    )
    assert len(candidates) == 1
    assert candidates[0].intended_document_types == ["interview_experience"]


def test_mixed_community_detail_creates_two_exact_quote_segments(tmp_path) -> None:
    repository, document, source_fragment = _evidence(tmp_path)
    body = ensure_community_body_fragment(document, source_fragment, repository)
    group = CompanyRoleGroup(
        group_id="group-1", search_scope_id="scope", company_key="甲公司",
        company_display_name="甲公司", role_family_id="ai_agent_engineering",
        job_instance_ids=["cluster-1"], exact_role_terms=["AI Agent工程师"],
    )
    batch = CommunityExtractionBatch.model_validate({
        "document_type": "mixed",
        "segments": [
            {"quote": "面试追问 RAG 评测。", "segment_type": "interview_question", "scope_level": "company_role", "company": "甲公司"},
            {"quote": "团队氛围很好。", "segment_type": "team_atmosphere", "scope_level": "company_role", "company": "甲公司", "polarity": "favorable"},
        ],
    })
    evidence_document, receipt, segments = materialize_community_evidence(
        document=document, body_fragment=body, extraction=batch, repository=repository,
        group=group, provider="mock", model="fixture",
    )
    assert evidence_document.document_type == "mixed"
    assert receipt.rejected_segment_count == 0
    assert {item.usage for item in segments} == {"demand_assessment", "reputation_job"}
    assert all(repository.get_fragment(item.fragment_id).text_hash == item.quote_hash for item in segments)


def test_duplicate_or_missing_quote_is_not_projectable(tmp_path) -> None:
    repository, document, source_fragment = _evidence(
        tmp_path, "<p>重复评价</p><p>重复评价</p>".encode()
    )
    body = ensure_community_body_fragment(document, source_fragment, repository)
    batch = CommunityExtractionBatch.model_validate({
        "document_type": "employment_experience",
        "segments": [{
            "quote": "重复评价", "segment_type": "work_intensity",
            "scope_level": "company_only", "company": "甲公司",
        }],
    })
    group = CompanyRoleGroup(
        group_id="group-1", search_scope_id="scope", company_key="甲公司",
        company_display_name="甲公司", role_family_id="ai_agent_engineering",
        job_instance_ids=["cluster-1"],
    )
    evidence_document, receipt, segments = materialize_community_evidence(
        document=document, body_fragment=body, extraction=batch, repository=repository,
        group=group, provider="mock", model="fixture",
    )
    assert evidence_document.document_type == "unknown"
    assert receipt.accepted_segment_ids == []
    assert receipt.rejected_segment_count == 1
    assert segments == []


def test_query_group_does_not_prove_role_scope_without_detail_text(tmp_path) -> None:
    repository, document, source_fragment = _evidence(
        tmp_path, "<h1>甲公司面试记录</h1><p>面试追问 RAG 评测。</p>".encode()
    )
    body = ensure_community_body_fragment(document, source_fragment, repository)
    group = CompanyRoleGroup(
        group_id="group-scope", search_scope_id="scope", company_key="甲公司",
        company_display_name="甲公司", role_family_id="ai_agent_engineering",
        job_instance_ids=["cluster-1"], exact_role_terms=["AI Agent工程师"],
    )
    batch = CommunityExtractionBatch.model_validate({
        "document_type": "interview_experience",
        "segments": [{
            "quote": "面试追问 RAG 评测。", "segment_type": "interview_question",
            "scope_level": "company_role", "company": "甲公司",
        }],
    })
    evidence_document, receipt, segments = materialize_community_evidence(
        document=document, body_fragment=body, extraction=batch,
        repository=repository, group=group, provider="mock", model="fixture",
    )
    assert evidence_document.document_type == "unknown"
    assert evidence_document.role_family_id is None
    assert receipt.accepted_segment_ids == []
    assert segments[0].validation_status == "ambiguous"
    assert "community_role_scope_unconfirmed" in segments[0].reason_codes


def test_company_groups_and_dual_type_queries_are_bounded() -> None:
    scope = SearchScope(
        scope_id="scope", target_role_queries=["AI Agent"],
        target_role_family="ai_agent_engineering", graduation_year="2027",
        recruitment_type="autumn_campus",
    )
    job = NormalizedJobPosting(
        job_posting_id="job-1", job_id="1", company="甲公司",
        role_title="AI Agent工程师", role_family="ai_agent_engineering",
        source_url="fixture://job/1", source_id="fixture_jobs", source_type="fixture",
        raw_artifact_ids=["artifact"], supporting_fragment_ids=["fragment"],
    )
    cluster = JobPostingCluster(
        cluster_id="cluster-1", canonical_job_posting_id="job-1",
        member_job_posting_ids=["job-1"], merge_method="not_merged", confidence=1.0,
    )
    groups = build_company_role_groups(scope, [cluster], {"job-1": job})
    plan = build_community_search_plan(groups[0], detail_budget=3)
    assert {item.query_kind for item in plan.queries} == {"company_exact_role", "company_reputation"}
    assert all(item.search_budget == 1 and item.detail_budget == 3 for item in plan.queries)
    excluded = job.model_copy(update={
        "status": "excluded_hard_scope", "exclusion_code": "location_mismatch",
        "exclusion_evidence_fragment_ids": ["fragment"],
    })
    assert build_company_role_groups(
        scope, [cluster], {"job-1": excluded}
    ) == []


def test_usage_validator_rejects_reputation_as_demand() -> None:
    with pytest.raises(ValidationError):
        CommunityEvidenceSegment(
            segment_id="segment", document_id="document", fragment_id="fragment",
            quote_start=0, quote_end=4, quote_hash="0" * 64,
            segment_type="work_intensity", usage="demand_assessment",
            company_key="甲", validation_status="accepted",
        )


@pytest.mark.parametrize(
    ("update", "requested", "expected"),
    [
        ({}, False, "not_required"),
        ({"job_description": "", "requirements_raw": ""}, False, "missing_critical_fields"),
        ({"status": "closed"}, False, "suspected_stale_or_closed"),
        ({}, True, "user_priority_request"),
    ],
)
def test_official_verification_is_conditional(update, requested, expected) -> None:
    job = NormalizedJobPosting(
        job_posting_id="job-official", company="甲公司",
        role_title="AI Agent工程师", role_family="ai_agent_engineering",
        job_description="负责 Agent 开发", requirements_raw="Python",
        requirements_normalized=["Python"], source_url="fixture://job",
        source_id="fixture_jobs", source_type="fixture",
        raw_artifact_ids=["artifact"], supporting_fragment_ids=["fragment"],
    ).model_copy(update=update)
    cluster = JobPostingCluster(
        cluster_id="cluster-official", canonical_job_posting_id=job.job_posting_id,
        member_job_posting_ids=[job.job_posting_id], merge_method="not_merged",
        confidence=1.0,
    )
    receipt = official_escalation_for_job(
        cluster, job, user_requested=requested
    )
    assert receipt.trigger == expected
    assert receipt.status == (
        "not_requested" if expected == "not_required" else "adapter_required"
    )


def test_consumer_selector_never_feeds_reputation_to_matching() -> None:
    bundle = RoleIntelligenceBundle(
        bundle_id="bundle", search_scope_id="scope",
        role_family_demand_profile_id="family-demand",
        job_demand_profile_ids=["job-demand"],
        job_reputation_profile_ids=["job-reputation"],
        company_reputation_profile_ids=["company-reputation"],
    )
    selected = select_consumer_inputs(bundle, consumer="matching")
    assert selected == {
        "role_family_demand_profile_id": "family-demand",
        "job_demand_profile_ids": ["job-demand"],
    }
    assert "reputation" not in str(selected)
    with pytest.raises(EvidenceUsageViolation):
        select_consumer_inputs(bundle, consumer="unknown-consumer")
