import json
from pathlib import Path

from campus_job_agent.schemas import SearchScope, SourceQuery
from campus_job_agent.sources import FixtureExperienceAdapter, FixtureOfficialAdapter, FixtureRecruitmentAdapter, SQLiteRoleRepository
from campus_job_agent.sources.processing import extract_archived_document, link_job_identity, normalize_experience_document, normalize_job_document
from campus_job_agent.storage import LocalBlobStore, SQLiteRepository


FIXTURES = Path(__file__).parents[1] / "fixtures" / "v05"


def test_jd_official_not_found_preserves_zhaopin_record_and_no_confirmed_link(tmp_path):
    db = tmp_path / "db.sqlite"; blob = LocalBlobStore(tmp_path / "blob"); evidence = SQLiteRepository(db); role = SQLiteRoleRepository(db)
    zhaopin_payload = json.loads((FIXTURES / "zhaopin_jd_ai_agent.json").read_text(encoding="utf-8"))
    adapter = FixtureRecruitmentAdapter(source_id="fixture_zhaopin", fixture_pages={"first":[zhaopin_payload]}, blob_store=blob,
                                        evidence_repository=evidence, role_repository=role, owner_id="u")
    query = SourceQuery(channel="recruitment_discovery", source_id="fixture_zhaopin", keywords=["AI Agent"], role_family="ai_agent_engineering",
                        graduation_year="2027", recruitment_type="autumn_campus")
    document = adapter.collect(query).documents[0]
    _, fragments = extract_archived_document(document, blob_store=blob, repository=evidence)
    scope = SearchScope(target_role_queries=["AI Agent"], target_role_family="ai_agent_engineering", graduation_year="2027", recruitment_type="autumn_campus")
    job = normalize_job_document(document, fragments, scope)[0]
    from campus_job_agent.sources.processing import deduplicate_jobs
    cluster = deduplicate_jobs([job])[0][0]
    link = link_job_identity(cluster, job, [], verification_status="official_not_found")
    assert job.company == "京东科技" and job.status == "included"
    assert link.status == "official_not_found"
    assert link.official_job_posting_id is None


def test_nowcoder_fixture_has_only_experience_signals_and_no_invented_counts(tmp_path):
    db = tmp_path / "db.sqlite"; blob = LocalBlobStore(tmp_path / "blob"); evidence = SQLiteRepository(db); role = SQLiteRoleRepository(db)
    payload = json.loads((FIXTURES / "nowcoder_jd_agent_interview.json").read_text(encoding="utf-8"))
    adapter = FixtureExperienceAdapter(source_id="fixture_nowcoder", fixture_pages={"first":[payload]}, blob_store=blob,
                                       evidence_repository=evidence, role_repository=role, owner_id="u")
    query = SourceQuery(channel="experience", source_id="fixture_nowcoder", keywords=["京东 AI Agent 面经"], role_family="ai_agent_engineering",
                        graduation_year="2027", recruitment_type="autumn_campus")
    document = adapter.collect(query).documents[0]
    _, fragments = extract_archived_document(document, blob_store=blob, repository=evidence)
    record = normalize_experience_document(document, fragments, "ai_agent_engineering")[0]
    assert record.scope_level == "company_role"
    assert record.signals.interview and record.signals.project_preference
    assert "like" not in record.model_dump_json().lower()
