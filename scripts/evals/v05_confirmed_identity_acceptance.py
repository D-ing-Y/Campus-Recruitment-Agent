"""Real Zhaopin candidate -> Meituan official identity acceptance for v0.5."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from campus_job_agent.schemas import OfficialVerificationPlan, SearchScope, SourceQuery
from campus_job_agent.sources import MeituanOfficialCareersAdapter, SQLiteRoleRepository, ZhaopinJobsAdapter
from campus_job_agent.sources.processing import (
    deduplicate_jobs, extract_archived_document, link_job_identity,
    normalize_job_document, parse_official_document,
)
from campus_job_agent.sources.role_pipeline import extract_recruitment_claims, resolve_fields
from campus_job_agent.storage import LocalBlobStore, SQLiteRepository


ZHAOPIN_COMPANY = "北京三快在线科技有限公司"
ROLE_TITLE = "AI Agent 产品经理"
ZHAOPIN_JOB_ID = "CC383625320J40873999709"
OFFICIAL_JOB_UNION_ID = "4613923553"
OFFICIAL_URL = (
    "https://zhaopin.meituan.com/web/position/detail"
    f"?jobUnionId={OFFICIAL_JOB_UNION_ID}&highlightType=social"
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", default="v05-live-confirmed-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"))
    parser.add_argument("--output-root", type=Path, default=Path("data/runs"))
    args = parser.parse_args()

    run_dir = args.output_root / args.run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    database = run_dir / "acceptance.sqlite3"
    evidence = SQLiteRepository(database)
    role = SQLiteRoleRepository(database)
    blobs = LocalBlobStore(run_dir / "raw")
    common = dict(
        blob_store=blobs, evidence_repository=evidence, role_repository=role,
        owner_id="v05-confirmed-identity-acceptance", live_enabled=True,
        max_retries=0, rate_limit_per_minute=6, timeout_seconds=25.0,
    )

    zhaopin = ZhaopinJobsAdapter(**common)
    query = SourceQuery(
        query_id=f"{args.run_id}:zhaopin-meituan", channel="recruitment_discovery",
        source_id="zhaopin_jobs", keywords=[ROLE_TITLE], company=ZHAOPIN_COMPANY,
        location="北京", role_family="ai_product_management",
        graduation_year="unknown", recruitment_type="social", page_size=20,
    )
    discovery_batch = zhaopin.collect(query)
    if discovery_batch.status != "success":
        return _write_failure(run_dir, args.run_id, "zhaopin_collection_failed", discovery_batch.status)

    scope = SearchScope(
        target_role_queries=[ROLE_TITLE], target_role_family="ai_product_management",
        locations=["北京"], graduation_year="unknown", recruitment_type="unknown",
    )
    candidates = []
    for document in discovery_batch.documents:
        _, fragments = extract_archived_document(document, blob_store=blobs, repository=evidence)
        candidates.extend(normalize_job_document(document, fragments, scope))
    discovery = next(
        (
            job for job in candidates
            if job.company == ZHAOPIN_COMPANY
            and job.role_title == ROLE_TITLE
            and job.job_id == ZHAOPIN_JOB_ID
        ),
        None,
    )
    if discovery is None:
        return _write_failure(run_dir, args.run_id, "zhaopin_candidate_not_found", f"candidates={len(candidates)}")

    cluster = deduplicate_jobs([discovery])[0][0]
    plan = OfficialVerificationPlan(
        verification_plan_id=f"{args.run_id}:meituan-official", job_cluster_id=cluster.cluster_id,
        canonical_company=discovery.company, candidate_role_title=discovery.role_title,
        candidate_location=discovery.city, candidate_recruitment_cycle="social",
        candidate_application_ids=[str(discovery.job_id or "")],
        official_domain_candidates=["zhaopin.meituan.com"],
        official_entry_url_candidates=[OFFICIAL_URL], allowed_domains=["zhaopin.meituan.com"],
        max_pages=1, max_depth=0, created_reason="confirm_zhaopin_candidate_on_meituan_official_careers",
    )
    official_adapter = MeituanOfficialCareersAdapter(**common)
    official_batch = official_adapter.verify(plan)
    if official_batch.status != "success":
        return _write_failure(run_dir, args.run_id, "official_collection_failed", official_batch.status)

    official_document = official_batch.documents[0]
    _, official_fragments = extract_archived_document(official_document, blob_store=blobs, repository=evidence)
    official_jobs, method, spec = parse_official_document(
        official_document, official_fragments, scope,
        registered_adapter=lambda text: _parse_meituan_job(text, plan.canonical_company, OFFICIAL_URL),
    )
    if len(official_jobs) != 1 or spec is not None:
        return _write_failure(run_dir, args.run_id, "official_parse_failed", method)
    official = official_jobs[0]

    role.save("normalized_job_posting", discovery)
    role.save("normalized_job_posting", official)
    role.save("job_posting_cluster", cluster)
    link = link_job_identity(cluster, discovery, [official])
    role.save("job_identity_link", link)

    subject_id = f"job_cluster:{cluster.cluster_id}"
    claims = extract_recruitment_claims(
        discovery, owner_id="v05-confirmed-identity-acceptance",
        repository=evidence, subject_id=subject_id,
    )
    claims += extract_recruitment_claims(
        official, owner_id="v05-confirmed-identity-acceptance",
        repository=evidence, subject_id=subject_id,
    )
    resolutions = resolve_fields(link, claims, repository=evidence)
    for resolution in resolutions:
        role.save("field_resolution", resolution)

    primary = [item for item in resolutions if item.authority == "primary"]
    resolved = [item for item in resolutions if item.resolution_status == "resolved"]
    passed = link.status == "confirmed" and bool(primary) and bool(resolved)
    summary = {
        "run_id": args.run_id,
        "executed_at": datetime.now(UTC).isoformat(),
        "mode": "opt_in_live_read_only",
        "passed": passed,
        "discovery": _job_summary(discovery),
        "official": _job_summary(official),
        "official_parse_method": method,
        "raw_before_parse": {
            "zhaopin_artifact_ids": discovery.raw_artifact_ids,
            "official_artifact_ids": official.raw_artifact_ids,
        },
        "identity_link": link.model_dump(mode="json"),
        "field_resolution_count": len(resolutions),
        "primary_resolution_count": len(primary),
        "resolved_conflict_count": len(resolved),
        "field_resolutions": [item.model_dump(mode="json") for item in resolutions],
    }
    output = run_dir / "confirmed_identity_acceptance_summary.json"
    output.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "run_id": args.run_id, "passed": passed, "identity_status": link.status,
        "match_signals": link.match_signals, "field_resolution_count": len(resolutions),
        "primary_resolution_count": len(primary), "resolved_conflict_count": len(resolved),
        "summary_path": str(output),
    }, ensure_ascii=False, indent=2))
    return 0 if passed else 1


def _parse_meituan_job(text: str, canonical_company: str, public_url: str) -> list[dict[str, Any]]:
    payload = json.loads(text)
    data = payload.get("data", {}) if isinstance(payload, dict) else {}
    if payload.get("status") != 1 or not data.get("jobUnionId"):
        return []
    cities = data.get("cityList", [])
    city = str(cities[0].get("name", "unknown")) if cities and isinstance(cities[0], dict) else "unknown"
    city = city.removesuffix("市")
    requirements = str(data.get("jobRequirement") or "")
    source_date = datetime.fromtimestamp(float(data["refreshTime"]) / 1000, UTC).isoformat() if data.get("refreshTime") else None
    return [{
        "job_id": str(data["jobUnionId"]), "company": canonical_company,
        "role_title": str(data.get("name") or "unknown"), "city": city,
        "job_description": str(data.get("jobDuty") or ""),
        "requirements_raw": requirements,
        "requirements_normalized": [line.strip() for line in requirements.splitlines() if line.strip()],
        "degree_requirement": "本科" if "本科" in requirements else None,
        "recruitment_type": "unknown", "source_date": source_date,
        "source_url": public_url, "application_url": public_url,
        "confidence": 0.98, "notes": ["meituan_official_public_job_api_v1"],
    }]


def _job_summary(job: Any) -> dict[str, Any]:
    return {
        "job_posting_id": job.job_posting_id, "job_id": job.job_id,
        "company": job.company, "role_title": job.role_title, "city": job.city,
        "source_id": job.source_id, "source_url": job.source_url,
        "retrieved_at": job.retrieved_at.isoformat(),
    }


def _write_failure(run_dir: Path, run_id: str, reason: str, detail: str) -> int:
    output = run_dir / "confirmed_identity_acceptance_summary.json"
    output.write_text(json.dumps({"run_id":run_id,"passed":False,"reason":reason,"detail":detail}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"run_id":run_id,"passed":False,"reason":reason,"detail":detail,"summary_path":str(output)}, ensure_ascii=False, indent=2))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
