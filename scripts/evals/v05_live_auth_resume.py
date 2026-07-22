"""Validate a real v0.5 auth interrupt/resume without serializing credentials."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from campus_job_agent.schemas import SearchScope
from campus_job_agent.sources import (
    LocalCredentialStore,
    NowcoderExperienceAdapter,
    SQLiteRoleRepository,
    SourceAdapterRegistry,
)
from campus_job_agent.storage import LocalBlobStore, SQLiteRepository
from campus_job_agent.tools import build_role_profile_registry
from campus_job_agent.workflows.candidate_profile import open_sqlite_checkpointer
from campus_job_agent.workflows.role_profile import RoleProfileGraphRuntime, create_role_profile_state


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", default="v05-live-auth-resume-20260721")
    args = parser.parse_args()
    run_id = args.run_id
    root = Path("data/runs") / run_id
    root.mkdir(parents=True, exist_ok=True)
    database = root / "role.sqlite3"
    evidence = SQLiteRepository(database)
    role = SQLiteRoleRepository(database)
    blobs = LocalBlobStore(root / "raw")
    credentials = LocalCredentialStore("data/cache/credentials")
    adapters = SourceAdapterRegistry()
    adapters.register(NowcoderExperienceAdapter(
        blob_store=blobs,
        evidence_repository=evidence,
        role_repository=role,
        owner_id="v05-live-acceptance",
        live_enabled=True,
        credential_resolver=credentials.resolve,
        max_retries=0,
        rate_limit_per_minute=6,
        timeout_seconds=20.0,
    ))
    registry = build_role_profile_registry(
        blob_store=blobs,
        evidence_repository=evidence,
        profile_repository=evidence,
        role_repository=role,
        adapters=adapters,
        credential_store=credentials,
    )
    scope = SearchScope(
        scope_id=f"{run_id}:scope",
        target_role_queries=["京东 AI Agent 面经"],
        target_role_family="ai_agent_engineering",
        graduation_year="2027",
        recruitment_type="autumn_campus",
    )
    state = create_role_profile_state(
        thread_id=f"{run_id}:thread",
        run_id=run_id,
        user_id="v05-live-acceptance",
        search_scope=scope,
        enabled_source_ids=["nowcoder_experience"],
        source_capabilities=adapters.capabilities(),
        budgets={
            "max_query_rounds": 2,
            "max_queries": 2,
            "max_source_switches": 0,
            "max_official_verifications": 0,
            "max_documents": 2,
            "max_llm_calls": 0,
            "max_tool_calls": 20,
        },
        output_dir=str(root / "handoff"),
    )
    with open_sqlite_checkpointer(root / "checkpoints.sqlite3") as checkpointer:
        runtime = RoleProfileGraphRuntime(
            registry=registry,
            evidence_repository=evidence,
            profile_repository=evidence,
            role_repository=role,
            checkpointer=checkpointer,
        )
        interrupted = runtime.invoke(state)
        requests = interrupted.get("__interrupt__", [])
        if len(requests) != 1:
            diagnostic = {
                "status": interrupted.get("status"),
                "next_action": interrupted.get("next_action"),
                "pending_auth_source_id": interrupted.get("pending_auth_source_id"),
                "pending_interaction": bool(interrupted.get("pending_interaction")),
                "interrupt_count": len(requests),
                "error_types": [item.get("error_type") for item in interrupted.get("errors", [])],
            }
            raise RuntimeError("expected exactly one source authorization interrupt: " + json.dumps(diagnostic))
        request = requests[0].value
        if request.get("source_id") != "nowcoder_experience":
            raise RuntimeError("authorization interrupt selected an unexpected source")
        completed = runtime.resume(
            thread_id=state["thread_id"],
            response={
                "request_id": request["request_id"],
                "thread_id": request["thread_id"],
                "user_id": request["user_id"],
                "source_id": request["source_id"],
                "action": "authorized",
                "credential_ref": "local-secret://nowcoder_experience/default",
            },
        )
    receipts = completed.get("source_run_receipts", [])
    completed_receipts = [item for item in receipts if item.get("status") == "completed"]
    summary = {
        "run_id": run_id,
        "interrupt_source_id": request["source_id"],
        "resume_action": "authorized",
        "credential_ref": "local-secret://nowcoder_experience/default",
        "final_status": completed.get("status"),
        "authorized_ref_present": completed.get("credential_refs", {}).get("nowcoder_experience")
        == "local-secret://nowcoder_experience/default",
        "completed_receipt_count": len(completed_receipts),
        "raw_artifact_count": len(completed.get("raw_artifact_ids", [])),
        "secret_material_serialized": any(
            marker in json.dumps(completed, ensure_ascii=False).casefold()
            for marker in ("cookie:", "authorization:", "bearer ", "curl ")
        ),
        "errors": [
            {key: item.get(key) for key in ("node", "error_type", "fatal", "retryable")}
            for item in completed.get("errors", [])
        ],
    }
    output = root / "auth_resume_summary.json"
    output.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["authorized_ref_present"] and completed_receipts and not summary["secret_material_serialized"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
