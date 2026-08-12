"""Opt-in WP3.1.2 L1/L2 acceptance against Zhaopin, Nowcoder and Xiaohongshu."""

from __future__ import annotations

import json
import os
from pathlib import Path
from uuid import uuid4

import pytest
from campus_job_agent.integrations.social_media import (
    MediaCrawlerSidecarClient, MediaCrawlerSidecarConfig,
)
from campus_job_agent.runtime import RuntimeFactory
from campus_job_agent.schemas import (
    CommunityEvidenceCoverage,
    CommunityEvidenceSegment,
    CommunitySearchDiagnostic,
    JobDemandProfile,
    SearchScope,
    SourceDocument,
    RoleIntelligenceBundle,
)
from campus_job_agent.schemas.role_intelligence import (
    INTERVIEW_SEGMENT_TYPES,
    REPUTATION_SEGMENT_TYPES,
)
from campus_job_agent.workflows.role_profile import create_role_profile_state


pytestmark = pytest.mark.live


def _enabled() -> bool:
    required = (
        "CAMPUS_AGENT_RUN_LIVE_TESTS",
        "CAMPUS_AGENT_ENABLE_LIVE_SOURCES",
        "CAMPUS_AGENT_LIVE_SESSION_ID",
        "CAMPUS_AGENT_LIVE_HANDOFF_ID",
    )
    return all(os.getenv(key) for key in required) and (
        os.getenv("CAMPUS_AGENT_RUN_LIVE_TESTS", "").casefold()
        in {"1", "true", "yes"}
    ) and (
        os.getenv("CAMPUS_AGENT_ENABLE_LIVE_SOURCES", "").casefold()
        in {"1", "true", "yes"}
    )


@pytest.mark.skipif(not _enabled(), reason="WP3.1 live sources are opt-in")
def test_wp32_live_mediacrawler_rest_health() -> None:
    health = MediaCrawlerSidecarClient(
        MediaCrawlerSidecarConfig.from_env()
    ).health()
    assert health["status"] in {"idle", "running"}
    assert health["sidecar_commit"] == os.environ["CAMPUS_AGENT_MEDIACRAWLER_COMMIT"]


@pytest.mark.skipif(not _enabled(), reason="WP3.1 live sources are opt-in")
def test_wp312_live_zhaopin_and_bounded_multi_platform_community() -> None:
    bootstrap = RuntimeFactory().build(owner_id="local-user")
    session_id = os.environ["CAMPUS_AGENT_LIVE_SESSION_ID"]
    handoff_id = os.environ["CAMPUS_AGENT_LIVE_HANDOFF_ID"]
    session = bootstrap.session_repository.get(session_id)
    runtime = RuntimeFactory().build(owner_id=session.user_id)
    handoff = runtime.session_repository.get_handoff(
        handoff_id, user_id=session.user_id
    )
    assert handoff.session_id == session.session_id
    assert handoff.handoff_type == "role_research_required"
    scope_id = handoff.required_input_refs.get("search_scope_id")
    assert isinstance(scope_id, str)
    scope = runtime.intent_repository.get(
        scope_id, SearchScope, owner_id=session.user_id
    )
    assert scope is not None

    credentials: dict[str, str] = {}
    for source_id in ("zhaopin_jobs", "nowcoder_experience"):
        ref = f"local-secret://{source_id}/default"
        try:
            runtime.credential_resolver.validate_ref(ref, source_id=source_id)
        except ValueError:
            continue
        credentials[source_id] = ref
    community_source_ids = [
        item.strip()
        for item in os.getenv(
            "CAMPUS_AGENT_LIVE_COMMUNITY_SOURCES",
            "nowcoder_experience,xiaohongshu_experience",
        ).split(",")
        if item.strip()
    ]
    assert set(community_source_ids).issubset({
        "nowcoder_experience", "xiaohongshu_experience",
    })
    thread_id = f"live-wp31-{uuid4()}"
    state = create_role_profile_state(
        thread_id=thread_id, user_id=session.user_id, search_scope=scope,
        enabled_source_ids=["zhaopin_jobs", *community_source_ids],
        source_capabilities=runtime.source_adapter_registry.capabilities(),
        credential_refs=credentials,
        budgets={
            "max_queries": 12,
            "max_recruitment_detail_documents": 3,
            "max_community_groups": 1,
            "max_community_queries_per_group": 12,
            "max_community_rounds_per_source": 3,
            "max_community_sources_per_purpose": 2,
            "community_target_documents_per_purpose": 2,
            "community_target_clusters_per_purpose": 3,
            "max_community_detail_documents_per_query": 3,
        },
    )
    with runtime.open_workflow("role") as workflow:
        result = workflow.invoke(state)
        interrupt_count = 0
        while (interrupts := result.get("__interrupt__", [])):
            interrupt_count += 1
            assert interrupt_count <= 2
            request = interrupts[0].value
            result = workflow.resume(thread_id=thread_id, response={
                "response_id": f"live-skip-{uuid4()}",
                "request_id": request["request_id"],
                "thread_id": thread_id,
                "user_id": session.user_id,
                "source_id": request["source_id"],
                "action": "skip_source",
            })

    detail_documents = [
        item
        for value in result.get("recruitment_detail_document_ids", [])
        if (
            item := runtime.role_repository.get(value, SourceDocument)
        ) is not None
    ]
    if not detail_documents:
        pytest.xfail(
            "L1 blocked/partial: Zhaopin returned no accepted detail document; "
            "inspect source receipts for login, risk-control, source drift or empty results"
        )
    assert 1 <= len(detail_documents) <= 3
    assert all(
        item.document_kind == "job_detail" and item.raw_artifact_id
        for item in detail_documents
    )
    demand_profiles = [
        item
        for value in result.get("job_demand_profile_ids", [])
        if (
            item := runtime.role_repository.get(value, JobDemandProfile)
        ) is not None
    ]
    assert demand_profiles
    projected_documents = {
        source_id for profile in demand_profiles for source_id in profile.source_document_ids
    }
    assert projected_documents.issubset({item.source_document_id for item in detail_documents})
    assert all(
        runtime.evidence_repository.get_artifact(str(item.raw_artifact_id)) is not None
        for item in detail_documents
    )
    assert result.get("role_family_demand_profile_id")

    bundle = runtime.role_repository.get(
        str(result.get("role_intelligence_bundle_id")), RoleIntelligenceBundle
    )
    assert bundle is not None
    search_artifact_ids = {
        str(item.raw_artifact_id)
        for value in result.get("community_search_document_ids", [])
        if (item := runtime.role_repository.get(value, SourceDocument)) is not None
        and item.raw_artifact_id
    }
    assert search_artifact_ids.isdisjoint(bundle.raw_evidence_refs)
    diagnostics = runtime.role_repository.list(
        "community_search_diagnostic", CommunitySearchDiagnostic
    )
    if not diagnostics:
        pytest.xfail(
            "L2 blocked before a community search Raw Artifact was archived; "
            "inspect authorization and risk-control receipts"
        )
    assert all(item.query_id and item.reason_codes for item in diagnostics)

    segments = [
        item
        for value in result.get("community_evidence_segment_ids", [])
        if (
            item := runtime.role_repository.get(value, CommunityEvidenceSegment)
        ) is not None and item.validation_status == "accepted"
    ]
    interview = [item for item in segments if item.segment_type in INTERVIEW_SEGMENT_TYPES]
    reputation = [item for item in segments if item.segment_type in REPUTATION_SEGMENT_TYPES]
    coverages = runtime.role_repository.list(
        "community_evidence_coverage", CommunityEvidenceCoverage
    )
    latest = {}
    for item in coverages:
        latest[item.evidence_purpose] = item
    if (
        not interview or not reputation
        or any(
            latest.get(purpose) is None
            or latest[purpose].independent_cluster_count < 3
            for purpose in ("interview_experience", "employment_experience")
        )
    ):
        pytest.xfail(
            "L2 partial: each purpose did not reach three independent content clusters; "
            "fixture/search snippets/model knowledge were not substituted"
        )
    for item in segments:
        fragment = runtime.evidence_repository.get_fragment(item.fragment_id)
        assert fragment is not None
        assert item.quote_end - item.quote_start == len(fragment.text)
        assert (
            item.usage == "demand_assessment"
            if item.segment_type in INTERVIEW_SEGMENT_TYPES
            else item.usage in {"reputation_job", "reputation_company"}
        )
