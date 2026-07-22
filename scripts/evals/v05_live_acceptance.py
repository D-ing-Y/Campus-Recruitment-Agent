"""Low-frequency, opt-in v0.5 live acceptance runner.

The runner never serializes credential values. Raw responses are archived through the
same SourceAdapter boundary used by the RoleProfile Graph before a sanitized summary
is written to the run directory.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from campus_job_agent.schemas import OfficialVerificationPlan, SourceQuery
from campus_job_agent.sources import (
    LocalCredentialStore,
    NowcoderExperienceAdapter,
    OfficialCareersAdapter,
    SQLiteRoleRepository,
    SourceAdapterRegistry,
    ZhaopinJobsAdapter,
)
from campus_job_agent.storage import LocalBlobStore, SQLiteRepository
from campus_job_agent.tools import build_role_profile_registry


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", default=_default_run_id())
    parser.add_argument("--output-root", type=Path, default=Path("data/runs"))
    parser.add_argument(
        "--credential-root", type=Path, default=Path("data/cache/credentials")
    )
    parser.add_argument(
        "--case", action="append", choices=("zhaopin", "nowcoder", "official"),
        help="run only the selected case; may be repeated (default: all)",
    )
    args = parser.parse_args()
    selected = set(args.case or ("zhaopin", "nowcoder", "official"))

    run_dir = args.output_root / args.run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    database = run_dir / "live.sqlite3"
    evidence = SQLiteRepository(database)
    role = SQLiteRoleRepository(database)
    blobs = LocalBlobStore(run_dir / "raw")
    credentials = LocalCredentialStore(args.credential_root)
    adapters = SourceAdapterRegistry()
    common = {
        "blob_store": blobs,
        "evidence_repository": evidence,
        "role_repository": role,
        "owner_id": "v05-live-acceptance",
        "live_enabled": True,
        "credential_resolver": credentials.resolve,
        "max_retries": 0,
        "rate_limit_per_minute": 6,
        "timeout_seconds": 20.0,
    }
    adapters.register(ZhaopinJobsAdapter(**common))
    adapters.register(NowcoderExperienceAdapter(**common))
    adapters.register(OfficialCareersAdapter(**common))
    registry = build_role_profile_registry(
        blob_store=blobs,
        evidence_repository=evidence,
        profile_repository=evidence,
        role_repository=role,
        adapters=adapters,
        credential_store=credentials,
    )

    cases = [
        (
            "source.discover_jobs",
            SourceQuery(
                query_id=f"{args.run_id}:zhaopin",
                channel="recruitment_discovery",
                source_id="zhaopin_jobs",
                keywords=["AI Agent"],
                location="成都",
                role_family="ai_agent_engineering",
                graduation_year="2027",
                recruitment_type="autumn_campus",
                page_size=10,
            ),
            None,
        ),
        (
            "source.collect_experience",
            SourceQuery(
                query_id=f"{args.run_id}:nowcoder",
                channel="experience",
                source_id="nowcoder_experience",
                keywords=["京东", "AI Agent", "面经"],
                role_family="ai_agent_engineering",
                graduation_year="2027",
                recruitment_type="autumn_campus",
                page_size=10,
            ),
            "local-secret://nowcoder_experience/default",
        ),
    ]
    results: list[dict[str, Any]] = []
    for case_name, (tool_name, query, credential_ref) in zip(("zhaopin", "nowcoder"), cases):
        if case_name not in selected:
            continue
        result = registry.run(
            tool_name,
            {
                "run_id": args.run_id,
                "query": query.model_dump(mode="json"),
                "credential_ref": credential_ref,
            },
        )
        results.append(_sanitize_result(result.model_dump(mode="json")))

    official_plan = OfficialVerificationPlan(
        verification_plan_id=f"{args.run_id}:official",
        job_cluster_id=f"{args.run_id}:official-smoke-only",
        canonical_company="字节跳动",
        candidate_role_title="AI搜索Agent算法工程师-Seed大模型人才校招",
        candidate_location="北京",
        candidate_recruitment_cycle="2027_autumn_campus",
        candidate_application_ids=["7622891560695793973"],
        official_domain_candidates=["jobs.bytedance.com"],
        official_entry_url_candidates=[
            "https://jobs.bytedance.com/campus/position/7622891560695793973/detail"
        ],
        allowed_domains=["jobs.bytedance.com"],
        max_pages=1,
        max_depth=0,
        created_reason="official_adapter_live_smoke_only_not_identity_confirmation",
    )
    if "official" in selected:
        official_result = registry.run(
            "source.verify_official_career",
            {
                "run_id": args.run_id,
                "source_id": "official_careers",
                "plan": official_plan.model_dump(mode="json"),
            },
        )
        results.append(_sanitize_result(official_result.model_dump(mode="json")))

    summary = {
        "run_id": args.run_id,
        "executed_at": datetime.now(UTC).isoformat(),
        "mode": "opt_in_live_read_only",
        "results": results,
    }
    output = run_dir / "source_live_smoke_summary.json"
    output.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"run_id: {args.run_id}")
    print(f"summary_path: {output}")
    for item in results:
        print(
            f"{item['source_id']}: tool_status={item['tool_status']} "
            f"batch_status={item['batch_status']} received={item['received_count']} "
            f"archived={item['archived_count']}"
        )
    return 0 if all(item["passed"] for item in results) else 1


def _sanitize_result(payload: dict[str, Any]) -> dict[str, Any]:
    record = payload.get("records", [{}])[0] if payload.get("records") else {}
    batch = record.get("batch", {})
    receipt = record.get("receipt", {})
    documents = batch.get("documents", [])
    archived_count = int(receipt.get("archived_count", 0))
    received_count = int(receipt.get("received_count", 0))
    batch_status = str(batch.get("status", payload.get("metadata", {}).get("error_type")))
    return {
        "source_id": batch.get("source_id"),
        "tool_status": payload.get("status"),
        "batch_status": batch_status,
        "error_type": payload.get("metadata", {}).get("error_type"),
        "retryable": bool(payload.get("metadata", {}).get("retryable")),
        "needs_user_action": bool(payload.get("metadata", {}).get("needs_user_action")),
        "received_count": received_count,
        "archived_count": archived_count,
        "raw_before_parse": received_count > 0 and archived_count == received_count,
        "http_statuses": [item.get("http_status") for item in documents],
        "access_statuses": [item.get("access_status") for item in documents],
        "public_source_urls": receipt.get("public_source_urls", []),
        "artifact_ids": receipt.get("artifact_ids", []),
        "auth_used": bool(receipt.get("auth_used")),
        "receipt_status": receipt.get("status"),
        "receipt_warnings": receipt.get("warnings", []),
        "passed": payload.get("status") == "success"
        and batch_status in {"success", "empty"}
        and received_count > 0
        and archived_count == received_count,
    }


def _default_run_id() -> str:
    return "v05-live-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


if __name__ == "__main__":
    raise SystemExit(main())
