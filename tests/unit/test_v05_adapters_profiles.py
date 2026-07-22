import hashlib

import httpx
import pytest

from campus_job_agent.schemas import (
    JobIdentityLink, JobPostingCluster, NormalizedJobPosting, SearchScope, SourceQuery,
)
from campus_job_agent.sources.adapters import (
    FixtureExperienceAdapter, FixtureRecruitmentAdapter, MeituanOfficialCareersAdapter,
)
from campus_job_agent.sources.processing import extract_archived_document, normalize_job_document
from campus_job_agent.sources.repository import SQLiteRoleRepository
from campus_job_agent.sources.role_pipeline import RoleProfileProjector, extract_recruitment_claims, resolve_fields
from campus_job_agent.storage.local_blob import LocalBlobStore
from campus_job_agent.storage.sqlite import SQLiteRepository


def _runtime(tmp_path):
    db = tmp_path / "data.sqlite"
    return LocalBlobStore(tmp_path / "blob"), SQLiteRepository(db), SQLiteRoleRepository(db)


def _scope():
    return SearchScope(scope_id="scope", target_role_queries=["AI Agent"], target_role_family="ai_agent_engineering",
                       locations=["成都"], graduation_year="2027", recruitment_type="autumn_campus")


def _query(source="fixture_jobs", channel="recruitment_discovery", cursor=None):
    return SourceQuery(channel=channel, source_id=source, keywords=["AI Agent"], location="成都",
                       role_family="ai_agent_engineering", graduation_year="2027",
                       recruitment_type="autumn_campus", cursor=cursor)


def test_fixture_adapter_archives_before_success_and_is_idempotent(tmp_path):
    blob, evidence, role = _runtime(tmp_path)
    adapter = FixtureRecruitmentAdapter(source_id="fixture_jobs", fixture_pages={"first":[{"source_url":"fixture://job/1","company":"A","role_title":"AI Agent"}]},
                                        blob_store=blob, evidence_repository=evidence, role_repository=role, owner_id="u")
    first = adapter.collect(_query())
    second = adapter.collect(_query())
    assert first.batch_id == second.batch_id
    document = first.documents[0]
    assert evidence.get_artifact(document.raw_artifact_id) is not None
    assert blob.exists(evidence.get_artifact(document.raw_artifact_id).raw_uri)


def test_fixture_adapter_pagination(tmp_path):
    blob, evidence, role = _runtime(tmp_path)
    adapter = FixtureRecruitmentAdapter(source_id="fixture_jobs", fixture_pages={"first":[{"company":"A","role_title":"AI"}], "page-2":[{"company":"B","role_title":"AI"}]},
                                        blob_store=blob, evidence_repository=evidence, role_repository=role, owner_id="u")
    first = adapter.collect(_query())
    second = adapter.collect(_query(cursor=first.next_cursor))
    assert first.next_cursor == "page-2" and second.next_cursor is None


def test_auth_fixture_can_resume_same_batch_after_credential_ref(tmp_path):
    blob, evidence, role = _runtime(tmp_path)
    adapter = FixtureExperienceAdapter(source_id="fixture_exp", fixture_pages={"first":[{"title":"面经"}]},
                                       blob_store=blob, evidence_repository=evidence, role_repository=role, owner_id="u", requires_auth=True)
    query = _query("fixture_exp", "experience")
    assert adapter.collect(query).status == "authentication_required"
    assert adapter.collect(query, "local-secret://fixture_exp/default").status == "success"


def test_meituan_official_adapter_accepts_only_public_detail_and_classifies_json(tmp_path):
    blob, evidence, role = _runtime(tmp_path)
    adapter = MeituanOfficialCareersAdapter(
        blob_store=blob, evidence_repository=evidence, role_repository=role,
        owner_id="u", live_enabled=True,
    )
    good = SourceQuery(
        channel="employer_official", source_id=adapter.source_id,
        keywords=["https://zhaopin.meituan.com/web/position/detail?jobUnionId=4613923553"],
        role_family="official_verification", graduation_year="unknown", recruitment_type="unknown",
    )
    assert "jobUnionId=4613923553" in adapter.build_url(good)
    bad = good.model_copy(update={"keywords":["https://evil.example/job?jobUnionId=4613923553"]})
    with pytest.raises(ValueError):
        adapter.build_url(bad)
    response = httpx.Response(
        200, json={"status":1,"data":{"jobUnionId":"4613923553"}},
        request=httpx.Request("POST", "https://zhaopin.meituan.com/api/official/job/getJobDetail"),
    )
    assert adapter.classify_response(response) == "success"


def test_raw_write_failure_never_returns_parsed_success(tmp_path):
    class FailingBlob:
        def put(self, key, data): raise OSError("disk full")
        def get(self, uri): raise AssertionError("parser must not run")
        def exists(self, uri): return False
        def delete(self, uri): pass
    db = tmp_path / "db.sqlite"
    evidence, role = SQLiteRepository(db), SQLiteRoleRepository(db)
    adapter = FixtureRecruitmentAdapter(source_id="fixture_jobs", fixture_pages={"first":[{"company":"A","role_title":"AI"}]},
                                        blob_store=FailingBlob(), evidence_repository=evidence, role_repository=role, owner_id="u")
    with pytest.raises(OSError):
        adapter.collect(_query())
    assert evidence.find_artifact_by_hash(hashlib.sha256(b"x").hexdigest(), "u") is None


def test_archived_json_can_be_replayed_and_normalized(tmp_path):
    blob, evidence, role = _runtime(tmp_path)
    adapter = FixtureRecruitmentAdapter(source_id="fixture_jobs", fixture_pages={"first":[{
        "source_url":"fixture://job/1", "company":"A", "role_title":"AI Agent", "city":"成都",
        "graduation_year":"2027", "recruitment_type":"autumn_campus", "requirements":"Python"
    }]}, blob_store=blob, evidence_repository=evidence, role_repository=role, owner_id="u")
    document = adapter.collect(_query()).documents[0]
    extraction, fragments = extract_archived_document(document, blob_store=blob, repository=evidence)
    jobs = normalize_job_document(document, fragments, _scope())
    assert extraction.artifact_id == document.raw_artifact_id
    assert jobs[0].requirements_raw == "Python"


def _job(identifier, company, requirement, fragment_id):
    return NormalizedJobPosting(job_posting_id=identifier, job_id=identifier, company=company, role_title="AI Agent工程师",
                                role_family="ai_agent_engineering", city="成都", graduation_year="2027", recruitment_type="autumn_campus",
                                source_url=f"fixture://{identifier}", source_id="fixture", source_type="recruitment_platform",
                                requirements_raw=requirement, raw_artifact_ids=[f"a-{identifier}"], supporting_fragment_ids=[fragment_id], confidence=.9)


def test_field_resolution_official_wins_and_third_party_salary_survives(tmp_path):
    blob, evidence, role = _runtime(tmp_path)
    # Use adapters so every claim has real archived fragments and channel metadata.
    discovery_adapter = FixtureRecruitmentAdapter(source_id="fixture_jobs", fixture_pages={"first":[{
        "source_url":"fixture://boss/1","job_id":"j1","company":"A","role_title":"AI Agent工程师","city":"成都",
        "graduation_year":"2027","recruitment_type":"autumn_campus","degree_requirement":"本科",
        "salary_min":20,"salary_max":30,"requirements":"Python"}]}, blob_store=blob, evidence_repository=evidence, role_repository=role, owner_id="u")
    official_adapter = FixtureRecruitmentAdapter(source_id="fixture_official", fixture_pages={"first":[{
        "source_url":"fixture://official/1","job_id":"j1","company":"A","role_title":"AI Agent工程师","city":"成都",
        "graduation_year":"2027","recruitment_type":"autumn_campus","degree_requirement":"硕士","requirements":"Python"}]},
        blob_store=blob, evidence_repository=evidence, role_repository=role, owner_id="u")
    discovery_doc = discovery_adapter.collect(_query()).documents[0]
    official_doc = official_adapter.collect(_query("fixture_official")).documents[0].model_copy(update={"channel":"employer_official"})
    # Artifact metadata is immutable, so set official metadata through a dedicated official adapter in integration tests.
    _, dfrags = extract_archived_document(discovery_doc, blob_store=blob, repository=evidence)
    _, ofrags = extract_archived_document(official_doc, blob_store=blob, repository=evidence)
    discovery = normalize_job_document(discovery_doc, dfrags, _scope())[0]
    official = normalize_job_document(official_doc, ofrags, _scope())[0]
    official = official.model_copy(update={"source_type":"employer_official"})
    # Re-label the official artifact metadata for this focused authority test.
    artifact = evidence.get_artifact(official.raw_artifact_ids[0])
    with evidence._connect() as conn:
        updated = artifact.model_copy(update={"metadata":{**artifact.metadata,"channel":"employer_official"}})
        conn.execute("UPDATE artifacts SET payload_json=? WHERE artifact_id=?", (updated.model_dump_json(), artifact.artifact_id))
    claims = extract_recruitment_claims(discovery, owner_id="u", repository=evidence, subject_id="job:j1")
    claims += extract_recruitment_claims(official, owner_id="u", repository=evidence, subject_id="job:o1")
    link = JobIdentityLink(job_cluster_id="c", official_job_posting_id=official.job_posting_id, status="confirmed", match_confidence=.95,
                           match_signals={"company":"exact","role_title":"exact","location":"exact","recruitment_cycle":"exact"},
                           supporting_fragment_ids=dfrags[0].fragment_id and [dfrags[0].fragment_id, ofrags[0].fragment_id])
    resolutions = resolve_fields(link, claims, repository=evidence)
    degree = next(item for item in resolutions if item.predicate == "qualification.degree")
    salary = next(item for item in resolutions if item.predicate == "salary.platform_display")
    assert degree.authority == "primary" and degree.conflicting_claim_ids
    assert degree.freshness == "current"
    assert salary.resolution_status == "third_party_only"


def test_role_family_prevalence_has_real_denominator_and_sample_guard(tmp_path):
    _, repository, _ = _runtime(tmp_path)
    projector = RoleProfileProjector(repository)
    # Direct snapshots keep this unit test focused on deterministic aggregation.
    from campus_job_agent.schemas import JobInstanceRoleProfile, ProfileSnapshot, Qualification, RoleRequirement
    snapshots = []
    for index, company in enumerate(["A", "B", "C"], start=1):
        requirements = [RoleRequirement(requirement_id=f"r{index}", raw_label="Python", capability_id="cap:python", confidence=.9,
                                        supporting_claim_ids=[f"c{index}"])] if index <= 2 else []
        bonus_items = [RoleRequirement(requirement_id="bonus-a", category="bonus_capability", raw_label="LangGraph",
                                       importance="bonus", obligation="preferred", confidence=.8,
                                       supporting_claim_ids=["bonus-claim-a"])] if index == 1 else []
        responsibilities = [RoleRequirement(requirement_id=f"resp-{index}", category="responsibility", raw_label="构建 Agent 系统",
                                            importance="context", confidence=.8,
                                            supporting_claim_ids=[f"resp-claim-{index}"])]
        qualifications = [Qualification(qualification_id=f"q-{index}", qualification_type="degree", value="本科",
                                        confidence=.9, supporting_claim_ids=[f"q-claim-{index}"])]
        profile = JobInstanceRoleProfile(role_profile_id=f"role-{index}", job_cluster_id=f"cluster-{index}", role_title="AI Agent",
                                         role_family="ai_agent_engineering", company=company, locations=["成都"],
                                         qualifications=qualifications, responsibilities=responsibilities,
                                         requirements=requirements, bonus_items=bonus_items,
                                         freshness={"retrieved_at":"2026-07-20T00:00:00+00:00"}, confidence=.9)
        snapshots.append(ProfileSnapshot(subject_id=f"role_instance:cluster-{index}", profile_type="role", version=1,
                                         schema_version="v0.5", profile_data=profile.model_dump(mode="json")))
    family = projector.aggregate_role_family(_scope(), snapshots).profile_data
    aggregate = family["core_requirements"][0]
    assert aggregate["supporting_job_instance_count"] == 2
    assert aggregate["eligible_job_instance_count"] == 3
    assert aggregate["supporting_company_count"] == 2
    assert aggregate["prevalence"] == pytest.approx(2/3)
    assert aggregate["prevalence_band"] == "common"
    assert family["hard_qualifications"][0]["prevalence_band"] == "common"
    assert family["common_responsibilities"][0]["supporting_job_instance_count"] == 3
    assert family["bonus_items"][0]["prevalence_band"] == "frequent"
    assert family["company_specific_variations"][0]["companies"] == ["A"]


def test_role_family_insufficient_sample_never_says_common(tmp_path):
    _, repository, _ = _runtime(tmp_path)
    projector = RoleProfileProjector(repository)
    from campus_job_agent.schemas import JobInstanceRoleProfile, ProfileSnapshot, RoleRequirement
    profile = JobInstanceRoleProfile(role_profile_id="r", job_cluster_id="c", role_title="AI", role_family="ai_agent_engineering",
                                     company="A", requirements=[RoleRequirement(raw_label="Python", supporting_claim_ids=["claim"])] )
    snapshot = ProfileSnapshot(subject_id="role_instance:c", profile_type="role", version=1, schema_version="v0.5", profile_data=profile.model_dump(mode="json"))
    family = projector.aggregate_role_family(_scope(), [snapshot]).profile_data
    assert family["sample"]["sample_status"] == "insufficient_jobs"
    assert family["observed_requirements"][0]["prevalence_band"] == "insufficient_sample"
