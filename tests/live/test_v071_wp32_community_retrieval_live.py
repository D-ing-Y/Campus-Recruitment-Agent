"""Opt-in WP3.2 Brave discovery and Crawl4AI public-detail smoke."""

from __future__ import annotations

import os
from uuid import uuid4

import pytest

from campus_job_agent.runtime import RuntimeFactory
from campus_job_agent.schemas import SourceDetailRequest, SourceQuery
from campus_job_agent.schemas import (
    CommunityContentCluster,
    CommunityEvidenceCoverage,
    CommunityEvidenceSegment,
    SearchScope,
)
from campus_job_agent.schemas.role_intelligence import (
    INTERVIEW_SEGMENT_TYPES,
    REPUTATION_SEGMENT_TYPES,
)
from campus_job_agent.sources.processing import (
    discover_community_post_candidates, extract_archived_document,
)
from campus_job_agent.workflows.role_profile import create_role_profile_state


pytestmark = pytest.mark.live
NOWCODER_PROFILE_REF = (
    "local-browser-profile://nowcoder_experience/default"
)
XHS_PROFILE_REF = (
    "local-browser-profile://xiaohongshu_experience/default"
)


def _enabled() -> bool:
    return os.getenv(
        "CAMPUS_AGENT_RUN_WP32_COMMUNITY_LIVE", ""
    ).casefold() in {"1", "true", "yes"}


def _assert_archived_main_body(runtime, detail) -> None:
    assert detail.raw_artifact_id
    assert runtime.evidence_repository.get_artifact(
        str(detail.raw_artifact_id)
    ) is not None
    body_fragments = [
        item for item in runtime.evidence_repository.list_fragments(
            str(detail.raw_artifact_id)
        )
        if item.metadata.get("parser_version") == "nowcoder_main_body_v1"
    ]
    assert len(body_fragments) == 1
    assert len(body_fragments[0].text) >= 80


@pytest.mark.skipif(not _enabled(), reason="WP3.2 community live smoke is opt-in")
def test_brave_discovery_and_crawl4ai_public_nowcoder_detail() -> None:
    runtime = RuntimeFactory().build(owner_id="local-user")
    adapter = runtime.source_adapter_registry.get("nowcoder_experience")
    assert adapter is not None
    credential_ref = "local-secret://nowcoder_experience/default"
    runtime.credential_resolver.validate_ref(
        credential_ref, source_id="nowcoder_experience"
    )
    query = SourceQuery(
        query_id=f"wp32-live-{uuid4()}", channel="experience",
        source_id="nowcoder_experience",
        keywords=["美团", "后端开发", "面经"], company="美团",
        role_family="backend_engineering", graduation_year="2027",
        recruitment_type="autumn_campus", page_size=10,
    )
    search_batch = adapter.collect(query, credential_ref)
    if search_batch.status != "success" or not search_batch.documents:
        pytest.xfail(
            f"Brave live partial: {search_batch.error_type or search_batch.status}"
        )
    search_document = search_batch.documents[0]
    assert search_document.raw_artifact_id
    _, fragments = extract_archived_document(
        search_document, blob_store=runtime.blob_store,
        repository=runtime.evidence_repository,
    )
    candidates = discover_community_post_candidates(
        search_document, fragments,
    )
    if not candidates:
        pytest.xfail("Brave returned no allowlisted Nowcoder detail URL")

    requests = [
        SourceDetailRequest(
            source_id="nowcoder_experience", channel="experience",
            query_id=query.query_id, candidate_id=item.candidate_id,
            parent_document_id=search_document.source_document_id,
            detail_url=item.detail_url,
            expected_document_kind="experience_post",
            external_locator_ref=item.external_locator_ref,
        )
        for item in candidates[:3]
    ]
    batches = adapter.fetch_details(
        requests, max_concurrency=2,
        browser_profile_ref=NOWCODER_PROFILE_REF,
    )
    successful = [
        batch.documents[0]
        for batch in batches
        if batch.status == "success" and batch.documents
    ]
    if not successful:
        outcomes = sorted({
            str(batch.error_type or batch.status) for batch in batches
        })
        pytest.xfail(
            "Crawl4AI live partial: " + ",".join(outcomes)
        )
    _assert_archived_main_body(runtime, successful[0])


@pytest.mark.skipif(
    not _enabled() or not os.getenv("CAMPUS_AGENT_WP32_NOWCODER_DETAIL_URL"),
    reason="WP3.2 standalone Crawl4AI detail smoke needs an explicit public URL",
)
def test_crawl4ai_standalone_public_nowcoder_detail() -> None:
    runtime = RuntimeFactory().build(owner_id="local-user")
    adapter = runtime.source_adapter_registry.get("nowcoder_experience")
    assert adapter is not None
    detail_url = str(os.environ["CAMPUS_AGENT_WP32_NOWCODER_DETAIL_URL"])
    request = SourceDetailRequest(
        source_id="nowcoder_experience", channel="experience",
        query_id=f"wp32-detail-live-{uuid4()}", candidate_id="explicit-live-url",
        parent_document_id="external-search-discovery",
        detail_url=detail_url, expected_document_kind="experience_post",
    )
    batch = adapter.fetch_detail(
        request, browser_profile_ref=NOWCODER_PROFILE_REF
    )
    if batch.status != "success" or not batch.documents:
        pytest.xfail(
            f"Crawl4AI live partial: {batch.error_type or batch.status}"
        )
    _assert_archived_main_body(runtime, batch.documents[0])


@pytest.mark.skipif(not _enabled(), reason="WP3.2.1 XHS live smoke is opt-in")
def test_mediacrawler_authenticated_xiaohongshu_search_and_detail() -> None:
    runtime = RuntimeFactory().build(owner_id="local-user")
    adapter = runtime.source_adapter_registry.get("xiaohongshu_experience")
    assert adapter is not None
    query = SourceQuery(
        query_id=f"wp321-xhs-live-{uuid4()}",
        channel="experience",
        source_id="xiaohongshu_experience",
        keywords=["美团", "后端开发", "工作体验"],
        company="美团",
        role_family="backend_engineering",
        graduation_year="2027",
        recruitment_type="autumn_campus",
        page_size=3,
    )
    search_batch = adapter.collect(
        query, browser_profile_ref=XHS_PROFILE_REF
    )
    if search_batch.status != "success" or not search_batch.documents:
        pytest.xfail(
            "MediaCrawler search live partial: "
            + str(search_batch.error_type or search_batch.status)
        )
    search_document = search_batch.documents[0]
    assert search_document.raw_artifact_id
    _, fragments = extract_archived_document(
        search_document,
        blob_store=runtime.blob_store,
        repository=runtime.evidence_repository,
    )
    candidates = discover_community_post_candidates(
        search_document, fragments,
        intended_document_types=["employment_experience"],
        company_hint="美团",
        role_family_hint="backend_engineering",
    )
    if not candidates:
        pytest.xfail("MediaCrawler search produced no valid opaque detail candidate")
    candidate = candidates[0]
    request = SourceDetailRequest(
        source_id="xiaohongshu_experience",
        channel="experience",
        query_id=query.query_id,
        candidate_id=candidate.candidate_id,
        parent_document_id=search_document.source_document_id,
        detail_url=candidate.detail_url,
        external_locator_ref=candidate.external_locator_ref,
        expected_document_kind="experience_post",
    )
    detail_batch = adapter.fetch_detail(
        request, browser_profile_ref=XHS_PROFILE_REF
    )
    if detail_batch.status != "success" or not detail_batch.documents:
        pytest.xfail(
            "MediaCrawler detail live partial: "
            + str(detail_batch.error_type or detail_batch.status)
        )
    detail = detail_batch.documents[0]
    assert detail.raw_artifact_id
    assert runtime.evidence_repository.get_artifact(
        str(detail.raw_artifact_id)
    ) is not None


@pytest.mark.skipif(not _enabled(), reason="WP3.2.1 strict L2 is opt-in")
def test_wp321_strict_meituan_backend_role_graph() -> None:
    runtime = RuntimeFactory().build(owner_id="local-user")
    scope = SearchScope(
        target_role_queries=["美团 后端开发"],
        target_role_family="backend_engineering",
        locations=["北京", "上海"],
        graduation_year="2027",
        recruitment_type="autumn_campus",
        companies=["美团"],
    )
    thread_id = f"live-wp321-strict-{uuid4()}"
    state = create_role_profile_state(
        thread_id=thread_id,
        user_id="local-user",
        search_scope=scope,
        enabled_source_ids=[
            "zhaopin_jobs",
            "nowcoder_experience",
            "xiaohongshu_experience",
        ],
        source_capabilities=runtime.source_adapter_registry.capabilities(),
        credential_refs={
            "nowcoder_experience": (
                "local-secret://nowcoder_experience/default"
            ),
        },
        browser_profile_refs={
            "nowcoder_experience": NOWCODER_PROFILE_REF,
            "xiaohongshu_experience": XHS_PROFILE_REF,
        },
        budgets={
            "max_query_rounds": 3,
            "max_queries": 12,
            "max_documents": 60,
            "max_llm_calls": 30,
            "max_tool_calls": 160,
            "max_recruitment_detail_documents": 5,
            "max_community_groups": 1,
            "max_community_queries_per_group": 12,
            "max_community_rounds_per_source": 3,
            "max_community_sources_per_purpose": 2,
            "community_target_documents_per_purpose": 3,
            "community_target_clusters_per_purpose": 3,
            "max_community_detail_documents_per_query": 3,
        },
    )
    with runtime.open_workflow("role") as workflow:
        result = workflow.invoke(state)

    if result.get("__interrupt__"):
        request = result["__interrupt__"][0].value
        pytest.xfail(
            "strict L2 blocked by authorization: "
            + "/".join((
                str(request.get("source_id")),
                str(request.get("operation")),
                str(request.get("authorization_mode")),
            ))
        )
    if result.get("status") == "failed":
        errors = result.get("errors", [])
        error_type = (
            str(errors[-1].get("error_type"))
            if errors and isinstance(errors[-1], dict)
            else "unknown"
        )
        pytest.xfail(
            "strict L2 Graph stopped before community acceptance: "
            f"error_type={error_type}"
        )
    coverages = runtime.role_repository.list(
        "community_evidence_coverage", CommunityEvidenceCoverage
    )
    current_coverage_ids = set(result.get("community_coverage_ids", []))
    current_coverages = [
        item for item in coverages if item.coverage_id in current_coverage_ids
    ]
    latest = {
        item.evidence_purpose: item for item in current_coverages
    }
    missing = [
        purpose
        for purpose in (
            "interview_experience", "employment_experience",
        )
        if purpose not in latest
        or latest[purpose].status != "sufficient"
        or latest[purpose].independent_cluster_count < 3
    ]
    if missing:
        pytest.xfail(
            "strict L2 partial: missing three independent clusters for "
            + ",".join(missing)
        )
    assert result["status"] == "completed"
    assert result.get("role_intelligence_bundle_id")

    clusters = [
        item
        for value in result.get("community_content_cluster_ids", [])
        if (
            item := runtime.role_repository.get(value, CommunityContentCluster)
        ) is not None
    ]
    segments = [
        item
        for value in result.get("community_evidence_segment_ids", [])
        if (
            item := runtime.role_repository.get(value, CommunityEvidenceSegment)
        ) is not None and item.validation_status == "accepted"
    ]
    purpose_by_segment: dict[str, str] = {}
    for cluster in clusters:
        for segment_id in cluster.member_segment_ids:
            previous = purpose_by_segment.setdefault(
                segment_id, cluster.evidence_purpose
            )
            assert previous == cluster.evidence_purpose
    assert any(item.segment_type in INTERVIEW_SEGMENT_TYPES for item in segments)
    assert any(item.segment_type in REPUTATION_SEGMENT_TYPES for item in segments)
    for segment in segments:
        fragment = runtime.evidence_repository.get_fragment(segment.fragment_id)
        assert fragment is not None
        assert segment.quote_end - segment.quote_start == len(fragment.text)
