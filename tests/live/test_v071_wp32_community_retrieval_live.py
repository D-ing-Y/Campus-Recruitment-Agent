"""Opt-in WP3.2 Brave discovery and Crawl4AI public-detail smoke."""

from __future__ import annotations

import os
from uuid import uuid4

import pytest

from campus_job_agent.runtime import RuntimeFactory
from campus_job_agent.schemas import SourceDetailRequest, SourceQuery
from campus_job_agent.sources.processing import (
    discover_community_post_candidates, extract_archived_document,
)


pytestmark = pytest.mark.live


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
    batches = adapter.fetch_details(requests, max_concurrency=2)
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
    batch = adapter.fetch_detail(request)
    if batch.status != "success" or not batch.documents:
        pytest.xfail(
            f"Crawl4AI live partial: {batch.error_type or batch.status}"
        )
    _assert_archived_main_body(runtime, batch.documents[0])
