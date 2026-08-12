from __future__ import annotations

import pytest
from pydantic import ValidationError

from campus_job_agent.schemas import (
    CareerIntent,
    ExperienceEvidenceRecord,
    IntentConstraint,
    JobPostingCluster,
    NormalizedJobPosting,
    SearchScope,
    SourceDocument,
    role_target_bindings_for_roles,
)
from campus_job_agent.sources import (
    assess_role_detail_evidence,
    classify_role_family,
    experience_link_applies,
    link_experience_scope,
)
from campus_job_agent.workflows.profile_matching.service import (
    MatchingServiceError,
    project_search_scope,
    project_search_scopes,
)


def _scope(family: str = "backend_engineering") -> SearchScope:
    return SearchScope(
        scope_id=f"scope-{family}",
        target_role_queries=["后端开发"],
        target_role_family=family,
        graduation_year="2027",
        recruitment_type="autumn_campus",
    )


def _job(
    job_id: str,
    *,
    role_title: str = "Java 后端工程师",
    role_family: str = "backend_engineering",
    company: str = "甲科技",
    artifact_id: str | None = None,
) -> NormalizedJobPosting:
    return NormalizedJobPosting(
        job_posting_id=job_id,
        job_id=job_id,
        company=company,
        role_title=role_title,
        role_family=role_family,
        source_url=f"fixture://jobs/{job_id}",
        source_id="fixture_jobs",
        source_type="fixture",
        raw_artifact_ids=[artifact_id or f"artifact-{job_id}"],
        supporting_fragment_ids=[f"fragment-{job_id}"],
    )


def _cluster(cluster_id: str, job_id: str) -> JobPostingCluster:
    return JobPostingCluster(
        cluster_id=cluster_id,
        canonical_job_posting_id=job_id,
        member_job_posting_ids=[job_id],
        merge_method="not_merged",
        confidence=1.0,
    )


def test_career_intent_projects_one_search_scope_per_role_family() -> None:
    roles = ["Java 后端工程师", "服务端开发", "AI 应用开发"]
    intent = CareerIntent(
        user_id="owner",
        schema_version="v0.7.1",
        target_roles=roles,
        target_role_families=["backend_engineering", "ai_agent_engineering"],
        role_target_bindings=role_target_bindings_for_roles(roles),
        constraints=[
            IntentConstraint(
                constraint_id="graduation-year",
                key="graduation_year",
                value="2027",
                kind="hard",
                affects_search_scope=True,
                status="confirmed",
                source_ref="fragment-intent#/graduation_year",
            ),
            IntentConstraint(
                constraint_id="recruitment-type",
                key="recruitment_type",
                value="autumn_campus",
                kind="hard",
                affects_search_scope=True,
                status="confirmed",
                source_ref="fragment-intent#/recruitment_type",
            ),
        ],
        graduation_year="2027",
        recruitment_type="autumn_campus",
        confirmed=True,
        raw_artifact_ids=["artifact-intent"],
        source_fragment_ids=["fragment-intent"],
    )

    scopes = project_search_scopes(intent, "intent-snapshot-1")

    assert {item.target_role_family for item in scopes} == {
        "backend_engineering",
        "ai_agent_engineering",
    }
    backend = next(item for item in scopes if item.target_role_family == "backend_engineering")
    assert backend.target_role_queries == ["Java 后端工程师", "服务端开发"]
    assert all("AI 应用开发" not in item.target_role_queries for item in [backend])
    legacy = intent.model_copy(update={
        "schema_version": "v0.6",
        "role_target_bindings": [],
    })
    assert {
        item.target_role_family for item in project_search_scopes(
            legacy, "legacy-intent-snapshot",
        )
    } == {"backend_engineering", "ai_agent_engineering"}
    try:
        project_search_scope(intent, "intent-snapshot-1")
    except MatchingServiceError as exc:
        assert str(exc) == "multiple_search_scopes_required"
    else:
        raise AssertionError("singular SearchScope compatibility API accepted multiple families")


def test_role_family_membership_rejects_cross_family_job() -> None:
    job = _job(
        "frontend-1",
        role_title="前端开发工程师",
        role_family="frontend_engineering",
    )

    membership = classify_role_family(job, _scope())

    assert membership.status == "rejected"
    assert membership.reason_codes == ["role_family_mismatch"]
    assert membership.primary_role_family == "frontend_engineering"


def test_role_family_membership_rejects_a_family_match_outside_hard_scope() -> None:
    job = _job("backend-outside-scope").model_copy(update={
        "status": "excluded_hard_scope",
        "exclusion_code": "location_mismatch",
        "exclusion_evidence_fragment_ids": ["fragment-backend-outside-scope"],
    })

    membership = classify_role_family(job, _scope())

    assert membership.status == "rejected"
    assert membership.reason_codes == ["job_scope_status_excluded_hard_scope"]


def test_role_target_binding_contract_rejects_duplicate_primary_mapping() -> None:
    bindings = role_target_bindings_for_roles(["后端开发"])
    with pytest.raises(ValidationError, match="multiple primary family"):
        CareerIntent(
            user_id="owner",
            target_roles=["后端开发"],
            target_role_families=["backend_engineering"],
            role_target_bindings=[bindings[0], bindings[0].model_copy(update={
                "binding_id": "duplicate-binding",
            })],
        )


class _ArtifactLookup:
    def __init__(self, artifact_ids: set[str]) -> None:
        self.artifact_ids = artifact_ids

    def get_artifact(self, artifact_id: str):
        return object() if artifact_id in self.artifact_ids else None


def test_search_result_cannot_satisfy_role_detail_gate() -> None:
    job = _job("backend-1", artifact_id="artifact-search")
    cluster = _cluster("cluster-backend-1", job.job_posting_id)
    search_document = SourceDocument(
        source_document_id="document-search",
        source_id="fixture_jobs",
        channel="recruitment_discovery",
        query_id="query-1",
        source_url=job.source_url,
        document_kind="search_page",
        raw_artifact_id="artifact-search",
        content_hash="hash-search",
    )

    receipt = assess_role_detail_evidence(
        scope_id="scope-backend",
        cluster=cluster,
        jobs=[job],
        documents=[search_document],
        repository=_ArtifactLookup({"artifact-search"}),
    )

    assert receipt.status == "missing"
    assert receipt.detail_artifact_ids == []
    assert receipt.reason_codes == ["detail_evidence_missing"]


def test_archived_job_detail_makes_cluster_eligible() -> None:
    job = _job("backend-1", artifact_id="artifact-detail")
    cluster = _cluster("cluster-backend-1", job.job_posting_id)
    detail_document = SourceDocument(
        source_document_id="document-detail",
        source_id="fixture_jobs",
        channel="recruitment_discovery",
        query_id="query-1",
        source_url=job.source_url,
        document_kind="job_detail",
        raw_artifact_id="artifact-detail",
        content_hash="hash-detail",
    )

    receipt = assess_role_detail_evidence(
        scope_id="scope-backend",
        cluster=cluster,
        jobs=[job],
        documents=[detail_document],
        repository=_ArtifactLookup({"artifact-detail"}),
    )

    assert receipt.status == "eligible"
    assert receipt.detail_document_ids == ["document-detail"]
    assert receipt.detail_artifact_ids == ["artifact-detail"]


def test_ambiguous_job_instance_experience_is_not_projectable() -> None:
    first = _job("backend-1")
    second = _job("backend-2")
    record = ExperienceEvidenceRecord(
        experience_record_id="experience-1",
        platform="fixture",
        query_id="query-experience",
        content_type="interview_experience",
        source_url="fixture://experience/1",
        title="甲科技 Java 后端面经",
        company="甲科技",
        role_title="Java 后端工程师",
        role_family="backend_engineering",
        scope_level="job_instance",
        raw_artifact_id="artifact-experience",
        supporting_fragment_ids=["fragment-experience"],
    )

    link = link_experience_scope(
        record,
        _scope(),
        [_cluster("cluster-1", first.job_posting_id), _cluster("cluster-2", second.job_posting_id)],
        {first.job_posting_id: first, second.job_posting_id: second},
    )

    assert link.status == "ambiguous"
    assert link.job_cluster_id is None
    assert link.match_signals["candidate_count"] == "2"
    assert not experience_link_applies(link, cluster_id="cluster-1", job=first)


def test_confirmed_family_and_company_experience_links_have_narrow_scope() -> None:
    first = _job("backend-1", company="甲科技")
    other_company = _job("backend-2", company="乙科技")
    family_record = ExperienceEvidenceRecord(
        experience_record_id="experience-family",
        platform="fixture",
        query_id="query-family",
        content_type="industry_summary",
        source_url="fixture://experience/family",
        title="后端校招面经汇总",
        role_family="backend_engineering",
        scope_level="role_family",
        raw_artifact_id="artifact-family",
        supporting_fragment_ids=["fragment-family"],
    )
    company_record = ExperienceEvidenceRecord(
        experience_record_id="experience-company",
        platform="fixture",
        query_id="query-company",
        content_type="interview_experience",
        source_url="fixture://experience/company",
        title="甲科技后端面经",
        company="甲科技",
        role_title="Java 后端工程师",
        role_family="backend_engineering",
        scope_level="company_role",
        raw_artifact_id="artifact-company",
        supporting_fragment_ids=["fragment-company"],
    )
    job_record = company_record.model_copy(update={
        "experience_record_id": "experience-job",
        "query_id": "query-job",
        "source_url": "fixture://experience/job",
        "scope_level": "job_instance",
        "raw_artifact_id": "artifact-job",
        "supporting_fragment_ids": ["fragment-job"],
    })
    clusters = [
        _cluster("cluster-1", first.job_posting_id),
        _cluster("cluster-2", other_company.job_posting_id),
    ]
    jobs = {first.job_posting_id: first, other_company.job_posting_id: other_company}

    family_link = link_experience_scope(family_record, _scope(), clusters, jobs)
    company_link = link_experience_scope(company_record, _scope(), clusters, jobs)
    job_link = link_experience_scope(job_record, _scope(), clusters, jobs)

    assert family_link.status == "confirmed"
    assert experience_link_applies(family_link, cluster_id="cluster-1", job=first)
    assert experience_link_applies(family_link, cluster_id="cluster-2", job=other_company)
    assert company_link.status == "confirmed"
    assert experience_link_applies(company_link, cluster_id="cluster-1", job=first)
    assert not experience_link_applies(
        company_link, cluster_id="cluster-2", job=other_company,
    )
    assert job_link.status == "confirmed"
    assert job_link.job_cluster_id == "cluster-1"
    assert experience_link_applies(job_link, cluster_id="cluster-1", job=first)
    assert not experience_link_applies(
        job_link, cluster_id="cluster-2", job=other_company,
    )
