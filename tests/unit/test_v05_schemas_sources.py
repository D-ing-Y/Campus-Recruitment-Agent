import json
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from campus_job_agent.schemas import (
    FieldResolution, JobIdentityLink, NormalizedJobPosting, OfficialSiteAdapterSpec,
    OfficialVerificationPlan, RoleCoverageGap, SearchScope, SourceDocument, SourceQuery,
    SourceRunReceipt,
)


def scope() -> SearchScope:
    return SearchScope(
        scope_id="scope-v05", target_role_queries=["AI Agent"],
        target_role_family="ai_agent_engineering", locations=["成都"],
        graduation_year="2027", recruitment_type="autumn_campus",
    )


def test_search_scope_has_stable_fingerprint():
    first = scope()
    second = scope().model_copy(update={"created_at": first.created_at})
    assert first.fingerprint() == second.fingerprint()


def test_source_query_fingerprint_changes_with_cursor():
    base = dict(channel="recruitment_discovery", source_id="fixture_jobs", keywords=["AI Agent"],
                role_family="ai_agent_engineering", graduation_year="2027", recruitment_type="autumn_campus")
    assert SourceQuery(**base).fingerprint != SourceQuery(**base, cursor="page-2").fingerprint


def test_source_query_rejects_forged_fingerprint():
    with pytest.raises(ValidationError):
        SourceQuery(channel="experience", source_id="fixture_exp", keywords=["面经"], role_family="ai_agent_engineering",
                    graduation_year="2027", recruitment_type="autumn_campus", fingerprint="wrong")


def test_success_document_requires_archived_raw():
    with pytest.raises(ValidationError):
        SourceDocument(source_id="fixture", channel="experience", query_id="q", source_url="fixture://x",
                       document_kind="experience_post", access_status="success")


def test_receipt_rejects_secret_material():
    with pytest.raises(ValidationError):
        SourceRunReceipt(run_id="r", source_id="s", channel="experience", adapter_version="v1",
                         warnings=["Authorization: Bearer secret"])


def test_hard_scope_exclusion_requires_evidence():
    with pytest.raises(ValidationError):
        NormalizedJobPosting(company="A", role_title="AI", role_family="ai", source_url="fixture://a",
                             source_id="s", source_type="recruitment_platform", status="excluded_hard_scope",
                             exclusion_code="location_mismatch", raw_artifact_ids=["a"], supporting_fragment_ids=["f"])


def test_confirmed_identity_requires_strong_signals():
    with pytest.raises(ValidationError):
        JobIdentityLink(job_cluster_id="c", official_job_posting_id="o", status="confirmed",
                        match_signals={"company": "exact"}, supporting_fragment_ids=["f"])


def test_adapter_spec_cannot_expand_domain_or_budget():
    plan = OfficialVerificationPlan(job_cluster_id="c", canonical_company="A", candidate_role_title="AI",
                                    allowed_domains=["careers.example.com"], max_pages=2, max_depth=1)
    spec = OfficialSiteAdapterSpec(allowed_domains=["evil.example"], stop_conditions={"max_pages": 3, "max_depth": 2})
    with pytest.raises(ValueError):
        spec.validate_against_plan(plan)


def test_role_gap_information_value_is_deterministic():
    gap = RoleCoverageGap(gap_id="g", category="job_count", description="x", importance=.8,
                          uncertainty=.9, retrievability=.5, collection_cost=.1, information_value=.99,
                          preferred_action="search_more")
    assert gap.information_value == .26


def test_field_resolution_preserves_conflicts():
    resolution = FieldResolution(job_identity_link_id="l", predicate="qualification.degree",
                                 chosen_claim_id="official", conflicting_claim_ids=["third"],
                                 resolution_status="resolved", reason="official_primary_and_newer", authority="primary")
    assert resolution.conflicting_claim_ids == ["third"]
