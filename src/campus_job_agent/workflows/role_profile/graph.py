"""Recoverable v0.5 RoleProfile LangGraph with source authorization interrupts."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any
from uuid import uuid4

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from campus_job_agent.schemas import (
    ExperienceEvidenceRecord, FieldResolution, JobIdentityLink, JobPostingCluster,
    NormalizedJobPosting, OfficialVerificationPlan, RoleCoverageAssessment,
    RoleProfileGraphState, RoleSearchBudget, RoleSearchCounter, SearchScope,
    SourceBatch, SourceDocument, SourceQuery,
)
from campus_job_agent.sources.repository import SQLiteRoleRepository
from campus_job_agent.storage.base import EvidenceRepository, ProfileRepository
from campus_job_agent.tools import ToolRegistry
from campus_job_agent.workflows.candidate_profile.graph import open_sqlite_checkpointer
from campus_job_agent.workflows.role_profile.evaluator import DeterministicRoleCoverageEvaluator
from campus_job_agent.workflows.role_profile.planner import DeterministicRoleQueryPlanner
from campus_job_agent.workflows.role_profile.policy import RoleRoutePolicy


class RoleProfileWorkflowError(RuntimeError):
    pass


class RoleProfileGraphRuntime:
    def __init__(self, *, registry: ToolRegistry, evidence_repository: EvidenceRepository,
                 profile_repository: ProfileRepository, role_repository: SQLiteRoleRepository,
                 checkpointer: Any, planner: Any | None = None, evaluator: Any | None = None,
                 route_policy: RoleRoutePolicy | None = None) -> None:
        self.registry, self.role_repository = registry, role_repository
        self.app = build_role_profile_graph(
            registry=registry, evidence_repository=evidence_repository,
            profile_repository=profile_repository, role_repository=role_repository,
            checkpointer=checkpointer, planner=planner, evaluator=evaluator,
            route_policy=route_policy,
        )

    def invoke(self, state: RoleProfileGraphState) -> dict[str, Any]:
        thread_id = str(state.get("thread_id", "")).strip()
        if not thread_id: raise ValueError("thread_id is required")
        try:
            return self.app.invoke(state, {"configurable": {"thread_id": thread_id}})
        except sqlite3.Error as exc:
            raise RoleProfileWorkflowError(f"checkpoint_error: {exc}") from exc

    def resume(self, *, thread_id: str, response: dict[str, Any]) -> dict[str, Any]:
        if str(response.get("thread_id", "")) != thread_id:
            raise ValueError("resume thread_id does not match response thread_id")
        current = self.app.get_state({"configurable": {"thread_id": thread_id}})
        values = dict(current.values or {})
        if not values.get("pending_interaction"):
            return values
        return self.app.invoke(Command(resume=response), {"configurable": {"thread_id": thread_id}})

    def get_state(self, thread_id: str) -> Any:
        return self.app.get_state({"configurable": {"thread_id": thread_id}})


def create_role_profile_state(*, thread_id: str, user_id: str,
                              search_scope: SearchScope | dict[str, Any],
                              enabled_source_ids: list[str], source_capabilities: dict[str, dict[str, Any]],
                              run_id: str | None = None, budgets: RoleSearchBudget | dict[str, Any] | None = None,
                              official_domains: dict[str, list[str]] | None = None,
                              output_dir: str | None = None) -> RoleProfileGraphState:
    scope = search_scope if isinstance(search_scope, SearchScope) else SearchScope.model_validate(search_scope)
    budget = budgets if isinstance(budgets, RoleSearchBudget) else RoleSearchBudget.model_validate(budgets or {})
    return {
        "run_id": run_id or str(uuid4()), "thread_id": thread_id, "user_id": user_id,
        "status": "initialized", "output_dir": output_dir, "career_intent_snapshot_id": scope.career_intent_snapshot_id,
        "search_scope": scope.model_dump(mode="json"), "query_plan": None, "pending_queries": [],
        "completed_query_ids": [], "query_history": [], "enabled_source_ids": list(enabled_source_ids),
        "skipped_source_ids": [], "source_capabilities": source_capabilities,
        "official_domains": official_domains or {}, "next_cursors": {},
        "pending_auth_source_id": None, "credential_refs": {}, "source_batch_ids": [],
        "source_run_receipts": [], "raw_artifact_ids": [], "extraction_ids": [], "fragment_ids": [],
        "normalized_job_ids": [], "experience_record_ids": [], "job_cluster_ids": [],
        "official_verification_plan_ids": [], "job_identity_link_ids": [], "field_resolution_ids": [],
        "official_status_by_cluster": {}, "claim_ids": [], "job_instance_profile_snapshot_ids": [],
        "role_family_profile_snapshot_id": None, "coverage_assessment": None, "coverage_gaps": [],
        "next_action": None, "pending_interaction": None, "resume_input": None,
        "budgets": budget.model_dump(), "counters": RoleSearchCounter().model_dump(),
        "tool_results": [], "llm_calls": [], "trace": [], "errors": [], "report": None,
    }


def build_role_profile_graph(*, registry: ToolRegistry, evidence_repository: EvidenceRepository,
                             profile_repository: ProfileRepository, role_repository: SQLiteRoleRepository,
                             checkpointer: Any, planner: Any | None = None, evaluator: Any | None = None,
                             route_policy: RoleRoutePolicy | None = None):
    nodes = _RoleNodes(registry, evidence_repository, profile_repository, role_repository,
                       planner or DeterministicRoleQueryPlanner(), evaluator or DeterministicRoleCoverageEvaluator(),
                       route_policy or RoleRoutePolicy())
    graph = StateGraph(RoleProfileGraphState)
    for name in [
        "initialize_role_run", "plan_role_queries", "collect_and_archive_sources",
        "extract_and_normalize_sources", "deduplicate_source_records",
        "plan_official_verification", "collect_and_archive_official_sources",
        "link_official_job_records", "resolve_job_field_conflicts",
        "extract_and_validate_role_claims", "project_job_instance_profiles",
        "aggregate_role_family_profile", "assess_role_coverage", "route_role_next_action",
        "plan_source_auth", "interrupt_for_source_auth", "validate_source_authorization",
        "finalize_role_profiles",
    ]: graph.add_node(name, getattr(nodes, name))
    chain = [
        "initialize_role_run", "plan_role_queries", "collect_and_archive_sources",
        "extract_and_normalize_sources", "deduplicate_source_records",
        "plan_official_verification", "collect_and_archive_official_sources",
        "link_official_job_records", "resolve_job_field_conflicts",
        "extract_and_validate_role_claims", "project_job_instance_profiles",
        "aggregate_role_family_profile", "assess_role_coverage", "route_role_next_action",
    ]
    graph.add_edge(START, chain[0])
    for left, right in zip(chain, chain[1:]): graph.add_edge(left, right)
    graph.add_conditional_edges("route_role_next_action", lambda state: state["next_action"], {
        "search_more": "plan_role_queries", "change_query": "plan_role_queries", "change_source": "plan_role_queries",
        "verify_official": "plan_official_verification", "await_user_auth": "plan_source_auth",
        "finalize_with_unknowns": "finalize_role_profiles", "complete": "finalize_role_profiles", "fail": "finalize_role_profiles",
    })
    graph.add_edge("plan_source_auth", "interrupt_for_source_auth")
    graph.add_edge("interrupt_for_source_auth", "validate_source_authorization")
    graph.add_conditional_edges("validate_source_authorization", lambda state: state.get("last_auth_action", "retry"), {
        "retry": "plan_role_queries", "skip": "plan_role_queries", "finalize": "finalize_role_profiles",
    })
    graph.add_edge("finalize_role_profiles", END)
    return graph.compile(checkpointer=checkpointer)


class _RoleNodes:
    def __init__(self, registry: ToolRegistry, evidence_repository: EvidenceRepository,
                 profile_repository: ProfileRepository, role_repository: SQLiteRoleRepository,
                 planner: Any, evaluator: Any, route_policy: RoleRoutePolicy) -> None:
        self.registry, self.evidence_repository, self.profile_repository = registry, evidence_repository, profile_repository
        self.role_repository, self.planner, self.evaluator, self.route_policy = role_repository, planner, evaluator, route_policy
        self.fallback_planner, self.fallback_evaluator = DeterministicRoleQueryPlanner(), DeterministicRoleCoverageEvaluator()

    def initialize_role_run(self, state: RoleProfileGraphState, config: RunnableConfig) -> dict[str, Any]:
        missing = [key for key in ["run_id", "thread_id", "user_id", "search_scope"] if not state.get(key)]
        if missing: raise RoleProfileWorkflowError(f"missing required state fields: {', '.join(missing)}")
        if config.get("configurable", {}).get("thread_id") != state["thread_id"]:
            raise RoleProfileWorkflowError("configurable.thread_id must equal state.thread_id")
        SearchScope.model_validate(state["search_scope"])
        budget = RoleSearchBudget.model_validate(state.get("budgets", {}))
        counters = RoleSearchCounter.model_validate(state.get("counters", {}))
        if not set(state.get("enabled_source_ids", [])).issubset(state.get("source_capabilities", {})):
            raise RoleProfileWorkflowError("enabled source lacks declared capabilities")
        return {"status": "running", "budgets": budget.model_dump(), "counters": counters.model_dump(),
                "trace": [_trace("initialize_role_run", counters)]}

    def plan_role_queries(self, state: RoleProfileGraphState) -> dict[str, Any]:
        scope = SearchScope.model_validate(state["search_scope"])
        counters = RoleSearchCounter.model_validate(state["counters"])
        budgets = RoleSearchBudget.model_validate(state["budgets"])
        if counters.query_rounds >= budgets.max_query_rounds:
            return {"pending_queries": [], "trace": [_trace("plan_role_queries", counters)]}
        capabilities = {key: value for key, value in state["source_capabilities"].items()
                        if key in state["enabled_source_ids"] and key not in state.get("skipped_source_ids", [])}
        completed = {str(item.get("fingerprint")) for item in state.get("query_history", []) if item.get("status") == "completed"}
        errors = []
        try:
            planned = self.planner.plan(scope, capabilities, completed_fingerprints=completed,
                                        next_cursors=state.get("next_cursors", {}), preferred_action=state.get("next_action"))
        except Exception as exc:
            planned = self.fallback_planner.plan(scope, capabilities, completed_fingerprints=completed,
                                                 next_cursors=state.get("next_cursors", {}), preferred_action=state.get("next_action"))
            errors.append({"node":"plan_role_queries","error_type":"llm_output_error","message":str(exc),"fatal":False,"fallback":"deterministic"})
        plan, calls = planned if isinstance(planned, tuple) else (planned, [])
        attempts = sum(1 + int(item.retry_count) for item in calls)
        counters = counters.model_copy(update={"llm_calls": counters.llm_calls + attempts})
        remaining = max(0, budgets.max_queries - counters.queries)
        queries = plan.queries[:remaining]
        counters = counters.model_copy(update={"query_rounds": counters.query_rounds + 1})
        return {"query_plan": plan.model_dump(mode="json"), "pending_queries": [item.model_dump(mode="json") for item in queries],
                "llm_calls": [item.model_dump(mode="json") for item in calls],
                "errors": errors,
                "counters": counters.model_dump(), "trace": [_trace("plan_role_queries", counters, planned=len(queries))]}

    def collect_and_archive_sources(self, state: RoleProfileGraphState) -> dict[str, Any]:
        counters = RoleSearchCounter.model_validate(state["counters"])
        budgets = RoleSearchBudget.model_validate(state["budgets"])
        batches, receipts, artifacts, history, completed, errors, tool_results = [], [], [], [], [], [], []
        next_cursors = dict(state.get("next_cursors", {})); pending_auth = None
        for raw in state.get("pending_queries", []):
            if counters.queries >= budgets.max_queries or counters.tool_calls >= budgets.max_tool_calls: break
            query = SourceQuery.model_validate(raw)
            tool_name = "source.collect_experience" if query.channel == "experience" else "source.discover_jobs"
            result = self.registry.run(tool_name, {"query": raw, "run_id": state["run_id"], "credential_ref": state.get("credential_refs", {}).get(query.source_id)})
            counters = counters.model_copy(update={"queries": counters.queries + 1, "tool_calls": counters.tool_calls + 1})
            tool_results.append(result.model_dump(mode="json"))
            if result.status == "failed":
                error_type = str(result.metadata.get("error_type", "failed"))
                if result.records:
                    failed_batch = SourceBatch.model_validate(result.records[0]["batch"])
                    receipts.append(result.records[0]["receipt"])
                    batches.append(failed_batch.batch_id)
                    artifacts.extend(result.evidence_ids)
                history.append({**raw, "status": "failed", "error_type": error_type})
                errors.append(_tool_error("collect_and_archive_sources", result))
                if result.metadata.get("needs_user_action"): pending_auth = query.source_id
                continue
            batch = SourceBatch.model_validate(result.records[0]["batch"])
            self.role_repository.save("source_batch", batch, idempotency_key=f"source-batch:{batch.idempotency_key}")
            for document in batch.documents:
                self.role_repository.save("source_document", document, idempotency_key=f"source-document:{document.source_document_id}")
            batches.append(batch.batch_id); receipts.append(result.records[0]["receipt"])
            artifacts.extend(result.evidence_ids); completed.append(query.query_id)
            history.append({**raw, "status": "completed", "next_cursor": batch.next_cursor})
            if batch.next_cursor: next_cursors[query.source_id] = batch.next_cursor
            else: next_cursors.pop(query.source_id, None)
            counters = counters.model_copy(update={"documents": counters.documents + len(batch.documents)})
        return {"pending_queries": [], "source_batch_ids": batches, "source_run_receipts": receipts,
                "raw_artifact_ids": artifacts, "completed_query_ids": completed, "query_history": history,
                "next_cursors": next_cursors, "pending_auth_source_id": pending_auth,
                "counters": counters.model_dump(), "tool_results": tool_results, "errors": errors,
                "trace": [_trace("collect_and_archive_sources", counters, documents=len(artifacts))]}

    def extract_and_normalize_sources(self, state: RoleProfileGraphState) -> dict[str, Any]:
        return self._extract_and_normalize(state, channels={"recruitment_discovery", "experience"}, node="extract_and_normalize_sources")

    def deduplicate_source_records(self, state: RoleProfileGraphState) -> dict[str, Any]:
        counters = RoleSearchCounter.model_validate(state["counters"])
        planned_channels = {item.get("channel") for item in (state.get("query_plan") or {}).get("queries", [])}
        if state.get("job_cluster_ids") and "recruitment_discovery" not in planned_channels:
            return {"trace": [_trace("deduplicate_source_records", counters, reused=True)]}
        jobs = [item for item in self.role_repository.list("normalized_job", NormalizedJobPosting) if item.source_type != "employer_official"]
        result = self.registry.run("source.deduplicate_jobs", {"job_ids": [item.job_posting_id for item in jobs]})
        counters = counters.model_copy(update={"tool_calls": counters.tool_calls + 1})
        if result.status == "failed": return {"errors": [_tool_error("deduplicate_source_records", result)], "tool_results": [result.model_dump(mode="json")], "counters": counters.model_dump()}
        clusters = result.records[0]["clusters"]
        return {"job_cluster_ids": [item["cluster_id"] for item in clusters], "tool_results": [result.model_dump(mode="json")],
                "counters": counters.model_dump(), "trace": [_trace("deduplicate_source_records", counters, clusters=len(clusters))]}

    def plan_official_verification(self, state: RoleProfileGraphState) -> dict[str, Any]:
        counters = RoleSearchCounter.model_validate(state["counters"]); budgets = RoleSearchBudget.model_validate(state["budgets"])
        existing = {item.job_cluster_id for item in self.role_repository.list("official_plan", OfficialVerificationPlan)}
        ids, results, errors = [], [], []
        for cluster_id in state.get("job_cluster_ids", []):
            if cluster_id in existing or counters.official_verifications >= budgets.max_official_verifications: continue
            result = self.registry.run("source.plan_official_verification", {"cluster_id": cluster_id, "company_domains": state.get("official_domains", {})})
            counters = counters.model_copy(update={"tool_calls": counters.tool_calls + 1, "official_verifications": counters.official_verifications + 1})
            results.append(result.model_dump(mode="json"))
            if result.status == "success": ids.extend(result.evidence_ids)
            else: errors.append(_tool_error("plan_official_verification", result))
        return {"official_verification_plan_ids": ids, "counters": counters.model_dump(), "tool_results": results,
                "errors": errors, "trace": [_trace("plan_official_verification", counters, plans=len(ids))]}

    def collect_and_archive_official_sources(self, state: RoleProfileGraphState) -> dict[str, Any]:
        counters = RoleSearchCounter.model_validate(state["counters"]); artifacts, receipts, results, errors = [], [], [], []
        status_by_cluster = dict(state.get("official_status_by_cluster", {}))
        for plan_id in state.get("official_verification_plan_ids", []):
            plan = self.role_repository.get(plan_id, OfficialVerificationPlan)
            if plan is None or plan.job_cluster_id in status_by_cluster: continue
            result = self.registry.run("source.verify_official_career", {"plan": plan.model_dump(mode="json"), "run_id": state["run_id"],
                                                                          "source_id": "official_careers", "credential_ref": state.get("credential_refs", {}).get("official_careers")})
            counters = counters.model_copy(update={"tool_calls": counters.tool_calls + 1})
            results.append(result.model_dump(mode="json"))
            if result.status == "failed":
                status = str(result.metadata.get("error_type", "official_unavailable")); status_by_cluster[plan.job_cluster_id] = status
                if result.records:
                    failed_batch = SourceBatch.model_validate(result.records[0]["batch"])
                    self.role_repository.save("source_batch", failed_batch, idempotency_key=f"source-batch:{failed_batch.idempotency_key}")
                    for document in failed_batch.documents:
                        self.role_repository.save("source_document", document, idempotency_key=f"source-document:{document.source_document_id}")
                    artifacts.extend(result.evidence_ids)
                    receipts.append(result.records[0]["receipt"])
                    counters = counters.model_copy(update={"documents": counters.documents + len(failed_batch.documents)})
                errors.append(_tool_error("collect_and_archive_official_sources", result)); continue
            batch = SourceBatch.model_validate(result.records[0]["batch"]); status_by_cluster[plan.job_cluster_id] = batch.status
            self.role_repository.save("source_batch", batch, idempotency_key=f"source-batch:{batch.idempotency_key}")
            for document in batch.documents: self.role_repository.save("source_document", document, idempotency_key=f"source-document:{document.source_document_id}")
            artifacts.extend(result.evidence_ids); receipts.append(result.records[0]["receipt"])
            counters = counters.model_copy(update={"documents": counters.documents + len(batch.documents)})
        base = {"raw_artifact_ids": artifacts, "source_run_receipts": receipts, "official_status_by_cluster": status_by_cluster,
                "counters": counters.model_dump(), "tool_results": results, "errors": errors,
                "trace": [_trace("collect_and_archive_official_sources", counters, documents=len(artifacts))]}
        normalized = self._extract_and_normalize({**state, **base}, channels={"employer_official"}, node="collect_and_archive_official_sources")
        for error in normalized.get("errors", []):
            error_type = error.get("error_type")
            query_id = str(error.get("query_id", ""))
            if error_type in {"adapter_required", "official_not_found", "official_unavailable", "source_changed"} and query_id.startswith("official:"):
                plan = self.role_repository.get(query_id.removeprefix("official:"), OfficialVerificationPlan)
                if plan is not None:
                    status_by_cluster[plan.job_cluster_id] = str(error_type)
        base["official_status_by_cluster"] = status_by_cluster
        for key in ["counters", "tool_results", "errors", "trace"]:
            if key in normalized and key in base and isinstance(base[key], list): base[key] = [*base[key], *normalized[key]]
            elif key in normalized: base[key] = normalized[key]
        for key in ["extraction_ids", "fragment_ids", "normalized_job_ids"]:
            if key in normalized: base[key] = normalized[key]
        return base

    def link_official_job_records(self, state: RoleProfileGraphState) -> dict[str, Any]:
        counters = RoleSearchCounter.model_validate(state["counters"]); ids, results, errors = [], [], []
        official_jobs = [item for item in self.role_repository.list("normalized_job", NormalizedJobPosting) if item.source_type == "employer_official"]
        existing_links = [self.role_repository.get(value, JobIdentityLink) for value in state.get("job_identity_link_ids", [])]
        linked_clusters = {item.job_cluster_id for item in existing_links if item is not None}
        for cluster_id in state.get("job_cluster_ids", []):
            if cluster_id in linked_clusters: continue
            cluster = self.role_repository.get(cluster_id, JobPostingCluster)
            if cluster is None: continue
            discovery = self.role_repository.get(cluster.canonical_job_posting_id, NormalizedJobPosting)
            candidates = [item.job_posting_id for item in official_jobs if discovery and item.company == discovery.company]
            result = self.registry.run("source.link_job_identity", {"cluster_id": cluster_id, "official_job_ids": candidates,
                                                                     "verification_status": state.get("official_status_by_cluster", {}).get(cluster_id)})
            counters = counters.model_copy(update={"tool_calls": counters.tool_calls + 1}); results.append(result.model_dump(mode="json"))
            if result.status == "success": ids.extend(result.evidence_ids)
            else: errors.append(_tool_error("link_official_job_records", result))
        return {"job_identity_link_ids": ids, "counters": counters.model_dump(), "tool_results": results,
                "errors": errors, "trace": [_trace("link_official_job_records", counters, links=len(ids))]}

    def resolve_job_field_conflicts(self, state: RoleProfileGraphState) -> dict[str, Any]:
        counters = RoleSearchCounter.model_validate(state["counters"])
        if state.get("field_resolution_ids"):
            return {"trace": [_trace("resolve_job_field_conflicts", counters, reused=True)]}
        all_jobs = self.role_repository.list("normalized_job", NormalizedJobPosting)
        claim_result = self.registry.run("evidence.extract_role_claims", {"owner_id": state["user_id"], "job_ids": [item.job_posting_id for item in all_jobs]})
        counters = counters.model_copy(update={"tool_calls": counters.tool_calls + 1}); claim_ids = list(claim_result.evidence_ids)
        resolution_ids, results, errors = [], [claim_result.model_dump(mode="json")], []
        if claim_result.status == "failed": errors.append(_tool_error("resolve_job_field_conflicts", claim_result))
        for link_id in state.get("job_identity_link_ids", []):
            link = self.role_repository.get(link_id, JobIdentityLink)
            if link is None: continue
            cluster = self.role_repository.get(link.job_cluster_id, JobPostingCluster)
            relevant_subjects = {f"job:{value}" for value in (cluster.member_job_posting_ids if cluster else [])}
            if link.official_job_posting_id: relevant_subjects.add(f"job:{link.official_job_posting_id}")
            relevant = [claim_id for claim_id in claim_ids if (claim := self.evidence_repository.get_claim(claim_id)) and claim.subject_id in relevant_subjects]
            result = self.registry.run("source.resolve_job_fields", {"identity_link_id": link_id, "claim_ids": relevant})
            counters = counters.model_copy(update={"tool_calls": counters.tool_calls + 1}); results.append(result.model_dump(mode="json"))
            if result.status == "success": resolution_ids.extend(result.evidence_ids)
            else: errors.append(_tool_error("resolve_job_field_conflicts", result))
        return {"claim_ids": claim_ids, "field_resolution_ids": resolution_ids, "counters": counters.model_dump(),
                "tool_results": results, "errors": errors, "trace": [_trace("resolve_job_field_conflicts", counters, resolutions=len(resolution_ids))]}

    def extract_and_validate_role_claims(self, state: RoleProfileGraphState) -> dict[str, Any]:
        counters = RoleSearchCounter.model_validate(state["counters"])
        result = self.registry.run("evidence.extract_role_claims", {"owner_id": state["user_id"], "experience_ids": state.get("experience_record_ids", []),
                                                                     "experience_subject_id": f"role_family:{SearchScope.model_validate(state['search_scope']).fingerprint()}"})
        counters = counters.model_copy(update={"tool_calls": counters.tool_calls + 1})
        update = {"counters": counters.model_dump(), "tool_results": [result.model_dump(mode="json")],
                  "trace": [_trace("extract_and_validate_role_claims", counters, claims=len(result.evidence_ids))]}
        if result.status == "success": update["claim_ids"] = result.evidence_ids
        else: update["errors"] = [_tool_error("extract_and_validate_role_claims", result)]
        return update

    def project_job_instance_profiles(self, state: RoleProfileGraphState) -> dict[str, Any]:
        counters = RoleSearchCounter.model_validate(state["counters"]); ids, results, errors = [], [], []
        for cluster_id in state.get("job_cluster_ids", []):
            result = self.registry.run("profile.project_job_instance", {"cluster_id": cluster_id, "claim_ids": state.get("claim_ids", []),
                                                                         "identity_link_ids": state.get("job_identity_link_ids", []),
                                                                         "field_resolution_ids": state.get("field_resolution_ids", [])})
            counters = counters.model_copy(update={"tool_calls": counters.tool_calls + 1}); results.append(result.model_dump(mode="json"))
            if result.status == "success": ids.extend(result.evidence_ids)
            else: errors.append(_tool_error("project_job_instance_profiles", result))
        return {"job_instance_profile_snapshot_ids": ids, "counters": counters.model_dump(), "tool_results": results,
                "errors": errors, "trace": [_trace("project_job_instance_profiles", counters, profiles=len(ids))]}

    def aggregate_role_family_profile(self, state: RoleProfileGraphState) -> dict[str, Any]:
        counters = RoleSearchCounter.model_validate(state["counters"])
        result = self.registry.run("profile.aggregate_role_family", {"search_scope": state["search_scope"],
                                                                       "snapshot_ids": state.get("job_instance_profile_snapshot_ids", [])})
        counters = counters.model_copy(update={"tool_calls": counters.tool_calls + 1})
        update = {"counters": counters.model_dump(), "tool_results": [result.model_dump(mode="json")],
                  "trace": [_trace("aggregate_role_family_profile", counters)]}
        if result.status == "success": update["role_family_profile_snapshot_id"] = result.evidence_ids[0]
        else: update["errors"] = [_tool_error("aggregate_role_family_profile", result)]
        return update

    def assess_role_coverage(self, state: RoleProfileGraphState) -> dict[str, Any]:
        scope = SearchScope.model_validate(state["search_scope"])
        snapshot = self.profile_repository.get_profile(state.get("role_family_profile_snapshot_id", "")) if state.get("role_family_profile_snapshot_id") else None
        jobs = [item for item in self.role_repository.list("normalized_job", NormalizedJobPosting) if item.source_type != "employer_official" and item.status != "excluded_hard_scope"]
        links = [self.role_repository.get(value, JobIdentityLink) for value in state.get("job_identity_link_ids", [])]
        eval_args = dict(scope=scope, family_profile=snapshot.profile_data if snapshot else None, job_count=len(state.get("job_cluster_ids", [])),
                         company_count=len({item.company for item in jobs}), experience_count=len(state.get("experience_record_ids", [])),
                         official_status_count=len(state.get("official_status_by_cluster", {})),
                         ambiguous_identity_count=sum(item is not None and item.status == "identity_ambiguous" for item in links),
                         has_next_cursor=bool(state.get("next_cursors")), auth_source_id=state.get("pending_auth_source_id"))
        errors = []
        try:
            evaluated = self.evaluator.evaluate(**eval_args)
        except Exception as exc:
            evaluated = self.fallback_evaluator.evaluate(**eval_args)
            errors.append({"node":"assess_role_coverage","error_type":"llm_output_error","message":str(exc),"fatal":False,"fallback":"deterministic"})
        assessment, calls = evaluated if isinstance(evaluated, tuple) else (evaluated, [])
        counters = RoleSearchCounter.model_validate(state["counters"])
        counters = counters.model_copy(update={"llm_calls": counters.llm_calls + sum(1 + int(item.retry_count) for item in calls)})
        assessment = assessment.model_copy(update={"role_family_profile_snapshot_id": state.get("role_family_profile_snapshot_id")})
        return {"coverage_assessment": assessment.model_dump(mode="json"),
                "coverage_gaps": [item.model_dump(mode="json") for item in assessment.coverage_gaps],
                "llm_calls": [item.model_dump(mode="json") for item in calls], "counters": counters.model_dump(),
                "errors": errors,
                "trace": [_trace("assess_role_coverage", counters)]}

    def route_role_next_action(self, state: RoleProfileGraphState) -> dict[str, Any]:
        assessment = RoleCoverageAssessment.model_validate(state["coverage_assessment"])
        action = self.route_policy.decide(
            assessment=assessment, budgets=RoleSearchBudget.model_validate(state["budgets"]), counters=RoleSearchCounter.model_validate(state["counters"]),
            has_fatal_error=any(item.get("fatal") for item in state.get("errors", [])), pending_auth_source_id=state.get("pending_auth_source_id"),
            has_official_plans=bool(state.get("official_verification_plan_ids")), has_next_cursor=bool(state.get("next_cursors")),
        )
        counters = RoleSearchCounter.model_validate(state["counters"])
        if action == "change_source": counters = counters.model_copy(update={"source_switches": counters.source_switches + 1})
        return {"next_action": action, "counters": counters.model_dump(), "trace": [_trace("route_role_next_action", counters, route=action, reason=assessment.reason)]}

    def plan_source_auth(self, state: RoleProfileGraphState) -> dict[str, Any]:
        source_id = state.get("pending_auth_source_id")
        if not source_id: raise RoleProfileWorkflowError("await_user_auth requires pending source")
        request = {"request_id": f"source-auth:{state['thread_id']}:{source_id}", "thread_id": state["thread_id"], "run_id": state["run_id"],
                   "user_id": state["user_id"], "interaction_type": "authorize_source", "source_id": source_id,
                   "login_entry": "请在真实 Chrome 中正常登录该来源", "import_instruction": "优先运行 campus-agent auth import-chrome --source <zhaopin|nowcoder>；Copy as cURL 仅作备用，且只返回 credential_ref",
                   "allowed_actions": ["authorized", "skip_source", "cancel"]}
        return {"pending_interaction": request, "status": "interrupted", "trace": [_trace("plan_source_auth", RoleSearchCounter.model_validate(state["counters"]), source=source_id)]}

    def interrupt_for_source_auth(self, state: RoleProfileGraphState) -> dict[str, Any]:
        response = interrupt(state["pending_interaction"])
        return {"resume_input": response}

    def validate_source_authorization(self, state: RoleProfileGraphState) -> dict[str, Any]:
        request, response = state.get("pending_interaction") or {}, state.get("resume_input") or {}
        for key in ["request_id", "thread_id", "user_id", "source_id"]:
            if str(response.get(key, "")) != str(request.get(key, "")): raise RoleProfileWorkflowError(f"authorization {key} mismatch")
        action = response.get("action")
        if action not in request.get("allowed_actions", []): raise RoleProfileWorkflowError("authorization action is not allowed")
        source_id = str(request["source_id"]); refs = dict(state.get("credential_refs", {})); skipped = []
        if action == "authorized":
            ref = str(response.get("credential_ref", ""))
            result = self.registry.run("source.validate_credential_ref", {"source_id": source_id, "credential_ref": ref})
            if result.status == "failed": raise RoleProfileWorkflowError("credential_invalid")
            refs[source_id] = ref; route = "retry"
        elif action == "skip_source": skipped = [source_id]; route = "skip"
        else: route = "finalize"
        return {"credential_refs": refs, "skipped_source_ids": skipped, "pending_auth_source_id": None,
                "pending_interaction": None, "resume_input": None, "status": "running", "last_auth_action": route,
                "trace": [_trace("validate_source_authorization", RoleSearchCounter.model_validate(state["counters"]), action=action)]}

    def finalize_role_profiles(self, state: RoleProfileGraphState) -> dict[str, Any]:
        action = state.get("next_action")
        status = "failed" if action == "fail" else "completed" if action == "complete" else "completed_with_unknowns"
        family = self.profile_repository.get_profile(state.get("role_family_profile_snapshot_id", "")) if state.get("role_family_profile_snapshot_id") else None
        report = {"status": status, "completion_reason": action, "search_scope": state.get("search_scope"),
                  "job_instance_profile_snapshot_ids": state.get("job_instance_profile_snapshot_ids", []),
                  "role_family_profile_snapshot_id": state.get("role_family_profile_snapshot_id"),
                  "sample": family.profile_data.get("sample") if family else None, "coverage": state.get("coverage_assessment"),
                  "source_receipt_count": len(state.get("source_run_receipts", [])), "remaining_gaps": state.get("coverage_gaps", [])}
        if state.get("output_dir"):
            _export_run(Path(str(state["output_dir"])), state, self.role_repository, report)
        return {"status": status, "report": report, "pending_interaction": None, "resume_input": None,
                "trace": [_trace("finalize_role_profiles", RoleSearchCounter.model_validate(state["counters"]), status=status)]}

    def _extract_and_normalize(self, state: dict[str, Any], *, channels: set[str], node: str) -> dict[str, Any]:
        counters = RoleSearchCounter.model_validate(state["counters"]); extraction_ids, fragment_ids, job_ids, experience_ids = [], [], [], []
        results, errors = [], []
        processed = set(state.get("extraction_ids", []))
        for document in self.role_repository.list("source_document", SourceDocument):
            if document.channel not in channels or document.raw_artifact_id in processed: continue
            extracted = self.registry.run("source.extract_document", {"document": document.model_dump(mode="json")})
            counters = counters.model_copy(update={"tool_calls": counters.tool_calls + 1}); results.append(extracted.model_dump(mode="json"))
            if extracted.status == "failed":
                error = _tool_error(node, extracted); error.update({"query_id": document.query_id, "source_document_id": document.source_document_id})
                errors.append(error); continue
            extraction_ids.append(str(document.raw_artifact_id)); fragments = extracted.evidence_ids; fragment_ids.extend(fragments)
            normalize_tool = "source.normalize_experience" if document.channel == "experience" else "source.normalize_job_posting"
            args = {"document": document.model_dump(mode="json"), "fragment_ids": fragments, "search_scope": state["search_scope"],
                    "role_family": SearchScope.model_validate(state["search_scope"]).target_role_family}
            normalized = self.registry.run(normalize_tool, args); counters = counters.model_copy(update={"tool_calls": counters.tool_calls + 1})
            results.append(normalized.model_dump(mode="json"))
            if normalized.status == "failed":
                error = _tool_error(node, normalized); error.update({"query_id": document.query_id, "source_document_id": document.source_document_id})
                errors.append(error); continue
            (experience_ids if document.channel == "experience" else job_ids).extend(normalized.evidence_ids)
        return {"extraction_ids": extraction_ids, "fragment_ids": fragment_ids, "normalized_job_ids": job_ids,
                "experience_record_ids": experience_ids, "counters": counters.model_dump(), "tool_results": results,
                "errors": errors, "trace": [_trace(node, counters, normalized=len(job_ids) + len(experience_ids))]}


def _trace(node: str, counters: RoleSearchCounter, **extra: Any) -> dict[str, Any]:
    return {"node": node, "counters": counters.model_dump(), **extra}


def _tool_error(node: str, result: Any) -> dict[str, Any]:
    error_type = str(result.metadata.get("error_type", "failed"))
    return {"node": node, "error_type": error_type, "message": result.error or error_type,
            "fatal": error_type in {"storage_error", "checkpoint_error"}, "retryable": bool(result.metadata.get("retryable"))}


def _export_run(root: Path, state: RoleProfileGraphState, repository: SQLiteRoleRepository, report: dict[str, Any]) -> None:
    import json
    root.mkdir(parents=True, exist_ok=True)
    (root / "search_scope.json").write_text(json.dumps(state.get("search_scope"), ensure_ascii=False, indent=2), encoding="utf-8")
    (root / "user_needs.md").write_text(
        "# 当前岗位搜索范围\n\n" + json.dumps(state.get("search_scope"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    collections = {
        "query_history.jsonl": state.get("query_history", []),
        "source_receipts.jsonl": state.get("source_run_receipts", []),
        "source_index.jsonl": [item.model_dump(mode="json") for item in repository.list("source_document", SourceDocument)],
        "jobs_normalized.jsonl": [item.model_dump(mode="json") for item in repository.list("normalized_job", NormalizedJobPosting)],
        "official_verifications.jsonl": [item.model_dump(mode="json") for item in repository.list("official_plan", OfficialVerificationPlan)],
        "job_identity_links.jsonl": [item.model_dump(mode="json") for item in repository.list("identity_link", JobIdentityLink)],
        "field_resolutions.jsonl": [item.model_dump(mode="json") for item in repository.list("field_resolution", FieldResolution)],
        "experience_normalized.jsonl": [item.model_dump(mode="json") for item in repository.list("experience", ExperienceEvidenceRecord)],
    }
    for name, rows in collections.items():
        (root / name).write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows), encoding="utf-8")
    (root / "role_profile_report.md").write_text(
        "# Role Profile Run Report\n\n```json\n" + json.dumps(report, ensure_ascii=False, indent=2) + "\n```\n",
        encoding="utf-8",
    )


__all__ = ["RoleProfileGraphRuntime", "build_role_profile_graph", "create_role_profile_state", "open_sqlite_checkpointer"]
