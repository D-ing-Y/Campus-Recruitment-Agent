"""Recoverable WP3.1 RoleProfileGraph with detail-first Demand/Reputation projection."""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from campus_job_agent.schemas import (
    CommunityEvidenceCoverage, CommunityEvidenceDocument, CommunityEvidenceSegment,
    CommunityPostCandidate, CommunitySearchAttemptReceipt, CommunitySearchPlan,
    CompanyRoleGroup, JobDetailCandidate, JobPostingCluster, NormalizedJobPosting,
    OfficialEscalationReceipt, RoleDetailEvidenceReceipt, RoleFamilyMembership,
    RoleProfileGraphState, RoleSearchBudget, RoleSearchCounter, SearchScope,
    SourceBatch, SourceDetailRequest, SourceDocument, SourceQuery,
)
from campus_job_agent.sources.role_intelligence import (
    COMMUNITY_SOURCE_CASCADES,
    build_community_search_query,
)
from campus_job_agent.sources.repository import SQLiteRoleRepository
from campus_job_agent.storage.base import EvidenceRepository, ProfileRepository
from campus_job_agent.tools import ToolRegistry
from campus_job_agent.workflows.candidate_profile.graph import open_sqlite_checkpointer
from campus_job_agent.workflows.role_profile.planner import DeterministicRoleQueryPlanner


WORKFLOW_VERSION = "wp3.1.1"


class RoleProfileWorkflowError(RuntimeError):
    pass


class RoleProfileGraphRuntime:
    def __init__(
        self, *, registry: ToolRegistry, evidence_repository: EvidenceRepository,
        profile_repository: ProfileRepository, role_repository: SQLiteRoleRepository,
        checkpointer: Any, planner: Any | None = None, evaluator: Any | None = None,
        route_policy: Any | None = None,
    ) -> None:
        self.registry, self.role_repository = registry, role_repository
        self.app = build_role_profile_graph(
            registry=registry, evidence_repository=evidence_repository,
            profile_repository=profile_repository, role_repository=role_repository,
            checkpointer=checkpointer, planner=planner,
        )

    def invoke(self, state: RoleProfileGraphState) -> dict[str, Any]:
        thread_id = str(state.get("thread_id", "")).strip()
        if not thread_id:
            raise ValueError("thread_id is required")
        if state.get("workflow_version") != WORKFLOW_VERSION:
            raise RoleProfileWorkflowError("legacy_session_incompatible")
        try:
            return self.app.invoke(state, {"configurable": {"thread_id": thread_id}})
        except sqlite3.Error as exc:
            raise RoleProfileWorkflowError(f"checkpoint_error: {exc}") from exc

    def resume(self, *, thread_id: str, response: dict[str, Any]) -> dict[str, Any]:
        if str(response.get("thread_id", "")) != thread_id:
            raise ValueError("resume thread_id does not match response thread_id")
        current = self.app.get_state({"configurable": {"thread_id": thread_id}})
        values = dict(current.values or {})
        if values.get("workflow_version") != WORKFLOW_VERSION:
            raise RoleProfileWorkflowError("legacy_session_incompatible")
        if not values.get("pending_interaction"):
            return values
        return self.app.invoke(
            Command(resume=response), {"configurable": {"thread_id": thread_id}}
        )

    def get_state(self, thread_id: str) -> Any:
        return self.app.get_state({"configurable": {"thread_id": thread_id}})


def create_role_profile_state(
    *, thread_id: str, user_id: str, search_scope: SearchScope | dict[str, Any],
    enabled_source_ids: list[str], source_capabilities: dict[str, dict[str, Any]],
    run_id: str | None = None, budgets: RoleSearchBudget | dict[str, Any] | None = None,
    official_domains: dict[str, list[str]] | None = None,
    output_dir: str | None = None,
    credential_refs: dict[str, str] | None = None,
    user_requested_official_job_ids: list[str] | None = None,
) -> RoleProfileGraphState:
    scope = search_scope if isinstance(search_scope, SearchScope) else SearchScope.model_validate(search_scope)
    budget = budgets if isinstance(budgets, RoleSearchBudget) else RoleSearchBudget.model_validate(budgets or {})
    return {
        "workflow_version": WORKFLOW_VERSION,
        "run_id": run_id or str(uuid4()), "thread_id": thread_id, "user_id": user_id,
        "status": "initialized", "output_dir": output_dir,
        "career_intent_snapshot_id": scope.career_intent_snapshot_id,
        "search_scope": scope.model_dump(mode="json"), "query_plan": None,
        "pending_queries": [], "completed_query_ids": [], "query_history": [],
        "enabled_source_ids": list(enabled_source_ids), "skipped_source_ids": [],
        "source_capabilities": source_capabilities, "official_domains": official_domains or {},
        "next_cursors": {}, "pending_auth_source_id": None,
        "credential_refs": dict(credential_refs or {}), "source_batch_ids": [],
        "source_run_receipts": [], "raw_artifact_ids": [], "extraction_ids": [],
        "fragment_ids": [], "recruitment_search_document_ids": [],
        "recruitment_detail_candidate_ids": [], "recruitment_detail_request_ids": [],
        "recruitment_detail_document_ids": [], "normalized_job_ids": [],
        "role_family_membership_ids": [], "job_cluster_ids": [],
        "role_detail_evidence_receipt_ids": [], "eligible_job_cluster_ids": [],
        "company_role_group_ids": [], "community_search_plan_ids": [],
        "community_attempt_queue": [], "community_attempt_index": 0,
        "community_current_query": None, "community_current_group_id": None,
        "community_current_purpose": None, "community_current_source_id": None,
        "community_current_source_priority": None, "community_current_round": None,
        "community_current_search_document_ids": [],
        "community_current_candidate_ids": [], "community_current_detail_document_ids": [],
        "community_current_evidence_document_ids": [],
        "community_current_evidence_segment_ids": [],
        "community_attempt_receipt_ids": [], "community_coverage_ids": [],
        "community_accepted_document_ids_by_scope": {},
        "community_exhausted_source_ids_by_scope": {},
        "community_sufficient_scope_keys": [], "community_last_query_by_lane": {},
        "community_route": None,
        "community_skip_current_source": False,
        "community_query_group_map": {}, "community_query_intended_types": {},
        "community_search_document_ids": [], "community_post_candidate_ids": [],
        "community_detail_request_ids": [], "community_detail_document_ids": [],
        "community_evidence_document_ids": [], "community_evidence_segment_ids": [],
        "community_classification_receipt_ids": [],
        "official_escalation_receipt_ids": [],
        "user_requested_official_job_ids": list(user_requested_official_job_ids or []),
        "claim_ids": [], "job_demand_profile_ids": [],
        "role_family_demand_profile_id": None, "job_reputation_profile_ids": [],
        "company_reputation_profile_ids": [], "role_intelligence_bundle_id": None,
        "missing_sections": [], "next_action": None, "pending_interaction": None,
        "resume_input": None, "budgets": budget.model_dump(),
        "counters": RoleSearchCounter().model_dump(), "tool_results": [],
        "llm_calls": [], "trace": [], "errors": [], "recruitment_errors": [],
        "community_errors": [], "report": None,
        # Historical fields remain readable but WP3.1 never writes new legacy profiles.
        "experience_record_ids": [], "experience_scope_link_ids": [],
        "official_verification_plan_ids": [], "job_identity_link_ids": [],
        "field_resolution_ids": [], "official_status_by_cluster": {},
        "job_instance_profile_snapshot_ids": [], "role_family_profile_snapshot_id": None,
        "coverage_assessment": None, "coverage_gaps": [],
    }


def build_role_profile_graph(
    *, registry: ToolRegistry, evidence_repository: EvidenceRepository,
    profile_repository: ProfileRepository, role_repository: SQLiteRoleRepository,
    checkpointer: Any, planner: Any | None = None, evaluator: Any | None = None,
    route_policy: Any | None = None,
):
    nodes = _RoleNodes(
        registry, evidence_repository, role_repository,
        planner or DeterministicRoleQueryPlanner(),
    )
    graph = StateGraph(RoleProfileGraphState)
    names = [
        "initialize_role_run", "plan_recruitment_searches",
        "collect_recruitment_searches", "discover_recruitment_detail_candidates",
        "fetch_recruitment_details", "normalize_recruitment_details",
        "classify_role_family_membership", "deduplicate_jobs",
        "assess_role_detail_evidence", "extract_recruitment_claims",
        "build_company_role_groups", "build_official_escalation_receipts",
        "plan_community_searches", "plan_next_community_attempt",
        "collect_community_searches",
        "discover_community_post_candidates", "fetch_community_details",
        "classify_community_details", "assess_community_coverage",
        "project_role_intelligence",
        "plan_source_auth", "interrupt_for_source_auth",
        "validate_source_authorization", "cancel_role_research",
        "finalize_role_intelligence",
    ]
    for name in names:
        graph.add_node(name, getattr(nodes, name))
    graph.add_edge(START, names[0])
    initial_chain = names[:13]
    for left, right in zip(initial_chain, initial_chain[1:]):
        graph.add_edge(left, right)
    graph.add_edge("plan_community_searches", "plan_next_community_attempt")
    graph.add_conditional_edges(
        "plan_next_community_attempt", _community_plan_route,
        {"collect": "collect_community_searches", "project": "project_role_intelligence"},
    )
    graph.add_conditional_edges(
        "collect_community_searches", _auth_route,
        {"auth": "plan_source_auth", "continue": "discover_community_post_candidates"},
    )
    graph.add_edge("discover_community_post_candidates", "fetch_community_details")
    graph.add_conditional_edges(
        "fetch_community_details", _auth_route,
        {"auth": "plan_source_auth", "continue": "classify_community_details"},
    )
    graph.add_edge("classify_community_details", "assess_community_coverage")
    graph.add_conditional_edges(
        "assess_community_coverage", _community_assessment_route,
        {"next": "plan_next_community_attempt", "project": "project_role_intelligence"},
    )
    graph.add_edge("project_role_intelligence", "finalize_role_intelligence")
    graph.add_edge("plan_source_auth", "interrupt_for_source_auth")
    graph.add_edge("interrupt_for_source_auth", "validate_source_authorization")
    graph.add_conditional_edges(
        "validate_source_authorization", lambda state: state.get("last_auth_action", "retry"),
        {
            "retry": "collect_community_searches",
            "continue": "assess_community_coverage",
            "cancel": "cancel_role_research",
        },
    )
    graph.add_edge("cancel_role_research", "finalize_role_intelligence")
    graph.add_edge("finalize_role_intelligence", END)
    return graph.compile(checkpointer=checkpointer)


def _auth_route(state: RoleProfileGraphState) -> str:
    return "auth" if state.get("pending_auth_source_id") else "continue"


def _community_plan_route(state: RoleProfileGraphState) -> str:
    return "collect" if state.get("community_route") == "collect" else "project"


def _community_assessment_route(state: RoleProfileGraphState) -> str:
    return "project" if state.get("community_route") == "project" else "next"


def _community_scope_key(group_id: str, purpose: str) -> str:
    return f"{group_id}|{purpose}"


def _merge_independent_community_documents(
    existing_ids: list[str], new_ids: list[str], *,
    role_repository: SQLiteRoleRepository, evidence_repository: EvidenceRepository,
) -> list[str]:
    """Dedupe by canonical URL, platform post ID, or normalized body hash."""

    candidates = role_repository.list("community_post_candidate", CommunityPostCandidate)

    def keys(document_id: str) -> set[str]:
        document = role_repository.get(document_id, CommunityEvidenceDocument)
        if document is None:
            return set()
        parsed = urlparse(document.detail_url)
        canonical = parsed._replace(query="", fragment="").geturl().rstrip("/")
        result = {f"url:{canonical}"} if canonical else set()
        for candidate in candidates:
            candidate_url = urlparse(candidate.detail_url)._replace(
                query="", fragment=""
            ).geturl().rstrip("/")
            if candidate_url == canonical and candidate.platform_post_id:
                result.add(f"post:{candidate.source_id}:{candidate.platform_post_id}")
        source_document = role_repository.get(document.source_document_id, SourceDocument)
        if source_document is not None and source_document.raw_artifact_id:
            fragments = evidence_repository.list_fragments(source_document.raw_artifact_id)
            body = next((
                item.text for item in fragments
                if item.metadata.get("parser_version") == "community_body_v1"
            ), "")
            if not body and fragments:
                body = max(fragments, key=lambda item: len(item.text)).text
            normalized = " ".join(body.split())
            if normalized:
                result.add(f"body:{hashlib.sha256(normalized.encode('utf-8')).hexdigest()}")
        return result

    accepted: list[str] = []
    seen: set[str] = set()
    for document_id in [*existing_ids, *new_ids]:
        document_keys = keys(document_id)
        if document_id in accepted or (document_keys and seen.intersection(document_keys)):
            continue
        accepted.append(document_id)
        seen.update(document_keys)
    return accepted


class _RoleNodes:
    def __init__(
        self, registry: ToolRegistry, evidence_repository: EvidenceRepository,
        role_repository: SQLiteRoleRepository, planner: Any,
    ) -> None:
        self.registry, self.evidence_repository = registry, evidence_repository
        self.role_repository, self.planner = role_repository, planner
        self.fallback_planner = DeterministicRoleQueryPlanner()

    def initialize_role_run(self, state: RoleProfileGraphState, config: RunnableConfig) -> dict[str, Any]:
        if state.get("workflow_version") != WORKFLOW_VERSION:
            raise RoleProfileWorkflowError("legacy_session_incompatible")
        missing = [key for key in ("run_id", "thread_id", "user_id", "search_scope") if not state.get(key)]
        if missing:
            raise RoleProfileWorkflowError(f"missing required state fields: {', '.join(missing)}")
        if config.get("configurable", {}).get("thread_id") != state["thread_id"]:
            raise RoleProfileWorkflowError("configurable.thread_id must equal state.thread_id")
        SearchScope.model_validate(state["search_scope"])
        if not set(state.get("enabled_source_ids", [])).issubset(state.get("source_capabilities", {})):
            raise RoleProfileWorkflowError("enabled source lacks declared capabilities")
        counters = RoleSearchCounter.model_validate(state.get("counters", {}))
        return {"status": "running", "trace": [_trace("initialize_role_run", counters)]}

    def plan_recruitment_searches(self, state: RoleProfileGraphState) -> dict[str, Any]:
        scope = SearchScope.model_validate(state["search_scope"])
        capabilities = {
            key: value for key, value in state["source_capabilities"].items()
            if key in state["enabled_source_ids"]
            and value.get("channel") == "recruitment_discovery"
            and key not in state.get("skipped_source_ids", [])
        }
        errors: list[dict[str, Any]] = []
        try:
            plan = self.planner.plan(
                scope, capabilities, completed_fingerprints=set(), next_cursors={}
            )
        except Exception as exc:
            plan = self.fallback_planner.plan(
                scope, capabilities, completed_fingerprints=set(), next_cursors={}
            )
            errors.append({
                "node": "plan_recruitment_searches",
                "error_type": "llm_output_error",
                "message": str(exc),
                "fatal": False,
                "retryable": False,
                "fallback": "deterministic",
            })
        queries = plan.queries[:RoleSearchBudget.model_validate(state["budgets"]).max_queries]
        counters = RoleSearchCounter.model_validate(state["counters"]).model_copy(
            update={"query_rounds": 1}
        )
        return {
            "query_plan": plan.model_dump(mode="json"),
            "pending_queries": [item.model_dump(mode="json") for item in queries],
            "counters": counters.model_dump(),
            "errors": errors,
            "trace": [_trace("plan_recruitment_searches", counters, planned=len(queries))],
        }

    def collect_recruitment_searches(self, state: RoleProfileGraphState) -> dict[str, Any]:
        return self._collect_queries(
            state, list(state.get("pending_queries", [])),
            tool_name="source.discover_jobs", phase="recruitment_search",
        )

    def discover_recruitment_detail_candidates(self, state: RoleProfileGraphState) -> dict[str, Any]:
        counters = RoleSearchCounter.model_validate(state["counters"])
        search_ids: list[str] = []
        detail_ids: list[str] = []
        fragments: list[str] = []
        results: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        for document_id in state.get("recruitment_search_document_ids", []):
            document = self.role_repository.get(document_id, SourceDocument)
            if document is None:
                continue
            if document.document_kind == "job_detail":
                detail_ids.append(document_id)
            elif document.document_kind == "search_page":
                search_ids.append(document_id)
                extracted = self.registry.run(
                    "source.extract_document",
                    {"document": document.model_dump(mode="json")},
                )
                counters = counters.model_copy(
                    update={"tool_calls": counters.tool_calls + 1}
                )
                results.append(_safe_tool_result(extracted))
                if extracted.status == "success":
                    fragments.extend(extracted.evidence_ids)
                else:
                    errors.append(
                        _tool_error("discover_recruitment_detail_candidates", extracted)
                    )
        result = self.registry.run(
            "source.discover_job_detail_candidates", {"source_document_ids": search_ids}
        )
        counters = counters.model_copy(update={"tool_calls": counters.tool_calls + 1})
        results.append(_safe_tool_result(result))
        if result.status != "success":
            errors.append(_tool_error("discover_recruitment_detail_candidates", result))
        return {
            "recruitment_detail_candidate_ids": result.evidence_ids if result.status == "success" else [],
            "recruitment_detail_document_ids": detail_ids,
            "fragment_ids": fragments,
            "counters": counters.model_dump(), "tool_results": results,
            "recruitment_errors": errors,
            "trace": [_trace("discover_recruitment_detail_candidates", counters, candidates=len(result.evidence_ids), preclassified_details=len(detail_ids))],
        }

    def fetch_recruitment_details(self, state: RoleProfileGraphState) -> dict[str, Any]:
        budget = RoleSearchBudget.model_validate(state["budgets"])
        return self._fetch_details(
            state, candidate_kind="job", candidate_ids=state.get("recruitment_detail_candidate_ids", []),
            limit=budget.max_recruitment_detail_documents,
        )

    def normalize_recruitment_details(self, state: RoleProfileGraphState) -> dict[str, Any]:
        counters = RoleSearchCounter.model_validate(state["counters"])
        job_ids: list[str] = []
        fragments: list[str] = []
        extractions: list[str] = []
        results: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        for document_id in state.get("recruitment_detail_document_ids", []):
            document = self.role_repository.get(document_id, SourceDocument)
            if document is None or document.document_kind != "job_detail":
                continue
            extracted = self.registry.run("source.extract_document", {"document": document.model_dump(mode="json")})
            counters = counters.model_copy(update={"tool_calls": counters.tool_calls + 1})
            results.append(_safe_tool_result(extracted))
            if extracted.status != "success":
                errors.append(_tool_error("normalize_recruitment_details", extracted))
                continue
            extractions.append(str(document.raw_artifact_id)); fragments.extend(extracted.evidence_ids)
            normalized = self.registry.run("source.normalize_job_posting", {
                "document": document.model_dump(mode="json"), "fragment_ids": extracted.evidence_ids,
                "search_scope": state["search_scope"],
            })
            counters = counters.model_copy(update={"tool_calls": counters.tool_calls + 1})
            results.append(_safe_tool_result(normalized))
            if normalized.status == "success":
                job_ids.extend(normalized.evidence_ids)
            else:
                errors.append(_tool_error("normalize_recruitment_details", normalized))
        return {
            "normalized_job_ids": job_ids, "fragment_ids": fragments,
            "extraction_ids": extractions, "counters": counters.model_dump(),
            "tool_results": results, "recruitment_errors": errors,
            "trace": [_trace("normalize_recruitment_details", counters, jobs=len(job_ids))],
        }

    def classify_role_family_membership(self, state: RoleProfileGraphState) -> dict[str, Any]:
        return self._simple_tool(
            state, "source.classify_role_family",
            {"search_scope": state["search_scope"], "job_ids": state.get("normalized_job_ids", [])},
            "role_family_membership_ids", "classify_role_family_membership",
        )

    def deduplicate_jobs(self, state: RoleProfileGraphState) -> dict[str, Any]:
        memberships = [
            self.role_repository.get(value, RoleFamilyMembership)
            for value in state.get("role_family_membership_ids", [])
        ]
        accepted = {item.job_posting_id for item in memberships if item and item.status == "accepted"}
        result = self.registry.run(
            "source.deduplicate_jobs", {"job_ids": sorted(accepted)}
        )
        counters = RoleSearchCounter.model_validate(state["counters"]).model_copy(
            update={"tool_calls": RoleSearchCounter.model_validate(state["counters"]).tool_calls + 1}
        )
        clusters = result.records[0]["clusters"] if result.status == "success" and result.records else []
        return {
            "job_cluster_ids": [item["cluster_id"] for item in clusters],
            "counters": counters.model_dump(), "tool_results": [_safe_tool_result(result)],
            "recruitment_errors": [] if result.status == "success" else [_tool_error("deduplicate_jobs", result)],
            "trace": [_trace("deduplicate_jobs", counters, clusters=len(clusters))],
        }

    def assess_role_detail_evidence(self, state: RoleProfileGraphState) -> dict[str, Any]:
        counters = RoleSearchCounter.model_validate(state["counters"])
        receipt_ids: list[str] = []
        eligible: list[str] = []
        results: list[dict[str, Any]] = []
        for cluster_id in state.get("job_cluster_ids", []):
            result = self.registry.run("source.assess_role_detail_evidence", {
                "scope_id": SearchScope.model_validate(state["search_scope"]).scope_id,
                "cluster_id": cluster_id, "identity_link_ids": [],
            })
            counters = counters.model_copy(update={"tool_calls": counters.tool_calls + 1})
            results.append(_safe_tool_result(result))
            if result.status != "success":
                continue
            receipt_ids.extend(result.evidence_ids)
            receipt = self.role_repository.get(result.evidence_ids[0], RoleDetailEvidenceReceipt)
            if receipt and receipt.status == "eligible":
                eligible.append(cluster_id)
        return {
            "role_detail_evidence_receipt_ids": receipt_ids,
            "eligible_job_cluster_ids": eligible,
            "counters": counters.model_dump(), "tool_results": results,
            "trace": [_trace("assess_role_detail_evidence", counters, eligible=len(eligible))],
        }

    def extract_recruitment_claims(self, state: RoleProfileGraphState) -> dict[str, Any]:
        job_ids = {
            job_id
            for cluster_id in state.get("eligible_job_cluster_ids", [])
            if (cluster := self.role_repository.get(cluster_id, JobPostingCluster)) is not None
            for job_id in cluster.member_job_posting_ids
        }
        return self._simple_tool(
            state, "evidence.extract_role_claims",
            {"owner_id": state["user_id"], "job_ids": sorted(job_ids)},
            "claim_ids", "extract_recruitment_claims",
        )

    def build_company_role_groups(self, state: RoleProfileGraphState) -> dict[str, Any]:
        return self._simple_tool(
            state, "role.build_company_role_groups",
            {"search_scope": state["search_scope"], "cluster_ids": state.get("eligible_job_cluster_ids", [])},
            "company_role_group_ids", "build_company_role_groups",
        )

    def build_official_escalation_receipts(self, state: RoleProfileGraphState) -> dict[str, Any]:
        result = self.registry.run("role.build_official_escalation_receipts", {
            "cluster_ids": state.get("eligible_job_cluster_ids", []),
            "user_requested_job_ids": state.get("user_requested_official_job_ids", []),
            "company_domains": state.get("official_domains", {}),
        })
        current = RoleSearchCounter.model_validate(state["counters"])
        counters = current.model_copy(update={"tool_calls": current.tool_calls + 1})
        return {
            "official_escalation_receipt_ids": result.evidence_ids if result.status == "success" else [],
            "official_verification_plan_ids": list(result.metadata.get("official_verification_plan_ids", [])),
            "counters": counters.model_dump(), "tool_results": [_safe_tool_result(result)],
            "errors": [] if result.status == "success" else [_tool_error("build_official_escalation_receipts", result)],
            "trace": [_trace(
                "build_official_escalation_receipts", counters,
                mandatory_official_verification_count=int(result.metadata.get("mandatory_official_verification_count", 0)),
                conditional_official_escalation_count=int(result.metadata.get("conditional_official_escalation_count", 0)),
            )],
        }

    def plan_community_searches(self, state: RoleProfileGraphState) -> dict[str, Any]:
        experience_sources = {
            key for key, value in state.get("source_capabilities", {}).items()
            if key in state.get("enabled_source_ids", [])
            and key not in state.get("skipped_source_ids", [])
            and value.get("channel") == "experience"
        }
        counters = RoleSearchCounter.model_validate(state["counters"])
        if not experience_sources:
            return {
                "pending_queries": [], "community_attempt_queue": [],
                "community_route": "project",
                "trace": [_trace("plan_community_searches", counters, planned=0)],
            }
        budget = RoleSearchBudget.model_validate(state["budgets"])
        queue: list[dict[str, Any]] = []
        plan_ids: list[str] = []
        fixture_sources = sorted(item for item in experience_sources if item.startswith("fixture"))
        for group_id in list(state.get("company_role_group_ids", []))[:budget.max_community_groups]:
            group = self.role_repository.get(str(group_id), CompanyRoleGroup)
            if group is None or group.status != "active":
                continue
            plan = CommunitySearchPlan(
                plan_id=f"community-plan-wp311:{group.group_id}",
                company_role_group_id=group.group_id, queries=[],
            )
            self.role_repository.save(
                "community_search_plan", plan,
                idempotency_key=f"community-search-plan:{plan.plan_id}",
            )
            plan_ids.append(plan.plan_id)
            for purpose in ("interview_experience", "employment_experience"):
                ordered = [
                    source_id for source_id in COMMUNITY_SOURCE_CASCADES[purpose]
                    if source_id in experience_sources
                ]
                if not ordered:
                    ordered = fixture_sources[:budget.max_community_sources_per_purpose]
                for source_index, source_id in enumerate(
                    ordered[:budget.max_community_sources_per_purpose], start=1
                ):
                    canonical = COMMUNITY_SOURCE_CASCADES[purpose]
                    priority = canonical.index(source_id) + 1 if source_id in canonical else source_index
                    for round_index in range(1, budget.max_community_rounds_per_source + 1):
                        queue.append({
                            "company_role_group_id": group.group_id,
                            "evidence_purpose": purpose, "source_id": source_id,
                            "source_priority": priority, "round_index": round_index,
                        })
        return {
            "community_search_plan_ids": plan_ids,
            "community_attempt_queue": queue, "community_attempt_index": 0,
            "pending_queries": [], "community_route": "next",
            "trace": [_trace("plan_community_searches", counters, planned=len(queue))],
        }

    def plan_next_community_attempt(self, state: RoleProfileGraphState) -> dict[str, Any]:
        counters = RoleSearchCounter.model_validate(state["counters"])
        budget = RoleSearchBudget.model_validate(state["budgets"])
        if (
            counters.community_searches >= budget.max_queries
            or counters.tool_calls >= budget.max_tool_calls
            or counters.documents >= budget.max_documents
        ):
            return {
                "pending_queries": [], "community_current_query": None,
                "community_route": "project",
                "trace": [_trace("plan_next_community_attempt", counters, reason="budget_exhausted")],
            }
        queue = list(state.get("community_attempt_queue", []))
        index = int(state.get("community_attempt_index", 0))
        sufficient = set(state.get("community_sufficient_scope_keys", []))
        exhausted = state.get("community_exhausted_source_ids_by_scope", {})
        skipped = set(state.get("skipped_source_ids", []))
        descriptor = None
        while index < len(queue):
            candidate = queue[index]
            index += 1
            scope_key = _community_scope_key(
                str(candidate["company_role_group_id"]), str(candidate["evidence_purpose"])
            )
            if scope_key in sufficient:
                continue
            source_id = str(candidate["source_id"])
            if source_id in skipped or source_id in set(exhausted.get(scope_key, [])):
                continue
            descriptor = candidate
            break
        if descriptor is None:
            return {
                "pending_queries": [], "community_current_query": None,
                "community_attempt_index": index, "community_route": "project",
                "trace": [_trace("plan_next_community_attempt", counters, reason="attempts_exhausted")],
            }
        group = self.role_repository.get(
            str(descriptor["company_role_group_id"]), CompanyRoleGroup
        )
        if group is None:
            raise RoleProfileWorkflowError("community attempt references missing group")
        lane = "|".join((
            group.group_id, str(descriptor["evidence_purpose"]), str(descriptor["source_id"]),
        ))
        last_by_lane = dict(state.get("community_last_query_by_lane", {}))
        planned = build_community_search_query(
            group, evidence_purpose=str(descriptor["evidence_purpose"]),
            source_id=str(descriptor["source_id"]),
            round_index=int(descriptor["round_index"]),
            source_priority=int(descriptor["source_priority"]),
            detail_budget=budget.max_community_detail_documents_per_query,
            parent_query_id=last_by_lane.get(lane),
        )
        attempt_plan = CommunitySearchPlan(
            plan_id=f"community-attempt-plan:{planned.query_id}",
            company_role_group_id=group.group_id, queries=[planned], status="running",
        )
        self.role_repository.save(
            "community_search_plan", attempt_plan,
            idempotency_key=f"community-search-plan:{attempt_plan.plan_id}",
        )
        last_by_lane[lane] = planned.query_id
        scope = SearchScope.model_validate(state["search_scope"])
        query = SourceQuery(
            query_id=planned.query_id, channel="experience", source_id=str(planned.source_id),
            keywords=[planned.query_text], company=group.company_display_name,
            role_family=group.role_family_id, graduation_year=scope.graduation_year,
            recruitment_type=scope.recruitment_type, page_size=planned.detail_budget,
            parent_query_id=planned.parent_query_id,
            change_reason="initial_scope" if planned.round_index == 1 else "low_recall",
        )
        query_group_map = dict(state.get("community_query_group_map", {}))
        query_group_map[query.query_id] = group.group_id
        intended = dict(state.get("community_query_intended_types", {}))
        intended[query.query_id] = list(planned.intended_document_types)
        return {
            "community_search_plan_ids": [attempt_plan.plan_id],
            "community_attempt_index": index,
            "community_current_query": query.model_dump(mode="json"),
            "community_current_group_id": group.group_id,
            "community_current_purpose": planned.evidence_purpose,
            "community_current_source_id": planned.source_id,
            "community_current_source_priority": planned.source_priority,
            "community_current_round": planned.round_index,
            "community_current_search_document_ids": [],
            "community_current_candidate_ids": [], "community_current_detail_document_ids": [],
            "community_current_evidence_document_ids": [],
            "community_current_evidence_segment_ids": [],
            "community_query_group_map": query_group_map,
            "community_query_intended_types": intended,
            "community_last_query_by_lane": last_by_lane,
            "pending_queries": [query.model_dump(mode="json")],
            "community_skip_current_source": False, "community_route": "collect",
            "trace": [_trace(
                "plan_next_community_attempt", counters, query_id=query.query_id,
                purpose=planned.evidence_purpose, source=planned.source_id,
                round=planned.round_index,
            )],
        }

    def collect_community_searches(self, state: RoleProfileGraphState) -> dict[str, Any]:
        queries = list(state.get("pending_queries", []))
        if not queries and state.get("community_current_query"):
            queries = [dict(state["community_current_query"])]
        update = self._collect_queries(
            state, queries,
            tool_name="source.collect_experience", phase="community_search",
        )
        update["community_current_search_document_ids"] = list(
            update.get("community_search_document_ids", [])
        )
        return update

    def discover_community_post_candidates(self, state: RoleProfileGraphState) -> dict[str, Any]:
        counters = RoleSearchCounter.model_validate(state["counters"])
        search_ids: list[str] = []
        details: list[str] = []
        for document_id in state.get("community_current_search_document_ids", []):
            document = self.role_repository.get(document_id, SourceDocument)
            if document is None:
                continue
            if document.document_kind == "experience_post":
                details.append(document_id)
            elif document.document_kind == "experience_search":
                search_ids.append(document_id)
        extracted_ids: list[str] = []
        results: list[dict[str, Any]] = []
        for document_id in search_ids:
            document = self.role_repository.get(document_id, SourceDocument)
            if document is None:
                continue
            result = self.registry.run("source.extract_document", {"document": document.model_dump(mode="json")})
            counters = counters.model_copy(update={"tool_calls": counters.tool_calls + 1})
            results.append(_safe_tool_result(result)); extracted_ids.extend(result.evidence_ids)
        discovered = self.registry.run("source.discover_community_post_candidates", {
            "source_document_ids": search_ids,
            "intended_by_query": state.get("community_query_intended_types", {}),
            "group_by_query": state.get("community_query_group_map", {}),
        })
        counters = counters.model_copy(update={"tool_calls": counters.tool_calls + 1})
        results.append(_safe_tool_result(discovered))
        return {
            "community_post_candidate_ids": discovered.evidence_ids if discovered.status == "success" else [],
            "community_current_candidate_ids": discovered.evidence_ids if discovered.status == "success" else [],
            "community_detail_document_ids": details,
            "community_current_detail_document_ids": details,
            "fragment_ids": extracted_ids, "counters": counters.model_dump(),
            "tool_results": results,
            "community_errors": [] if discovered.status == "success" else [_tool_error("discover_community_post_candidates", discovered)],
            "trace": [_trace("discover_community_post_candidates", counters, candidates=len(discovered.evidence_ids), preclassified_details=len(details))],
        }

    def fetch_community_details(self, state: RoleProfileGraphState) -> dict[str, Any]:
        budget = RoleSearchBudget.model_validate(state["budgets"])
        update = self._fetch_details(
            state, candidate_kind="community",
            candidate_ids=state.get("community_current_candidate_ids", []),
            limit=budget.max_community_detail_documents_per_query,
            include_existing=False,
        )
        update["community_current_detail_document_ids"] = list(
            update.get("community_detail_document_ids", [])
        )
        return update

    def classify_community_details(self, state: RoleProfileGraphState) -> dict[str, Any]:
        counters = RoleSearchCounter.model_validate(state["counters"])
        results: list[dict[str, Any]] = []
        fragments: list[str] = []
        current_detail_ids = list(state.get("community_current_detail_document_ids", []))
        for document_id in current_detail_ids:
            document = self.role_repository.get(document_id, SourceDocument)
            if document is None:
                continue
            extracted = self.registry.run("source.extract_document", {"document": document.model_dump(mode="json")})
            counters = counters.model_copy(update={"tool_calls": counters.tool_calls + 1})
            results.append(_safe_tool_result(extracted)); fragments.extend(extracted.evidence_ids)
        classified = self.registry.run("role.classify_community_documents", {
            "source_document_ids": current_detail_ids,
            "group_by_query": state.get("community_query_group_map", {}),
        })
        counters = counters.model_copy(update={"tool_calls": counters.tool_calls + 1})
        results.append(_safe_tool_result(classified))
        update: dict[str, Any] = {
            "fragment_ids": fragments, "counters": counters.model_dump(),
            "tool_results": results,
            "trace": [_trace("classify_community_details", counters)],
        }
        if classified.status == "success" and classified.records:
            record = classified.records[0]
            update.update({
                "community_evidence_document_ids": [item["document_id"] for item in record["documents"]],
                "community_evidence_segment_ids": [item["segment_id"] for item in record["segments"]],
                "community_current_evidence_document_ids": [item["document_id"] for item in record["documents"]],
                "community_current_evidence_segment_ids": [item["segment_id"] for item in record["segments"]],
                "community_classification_receipt_ids": [item["receipt_id"] for item in record["receipts"]],
                "llm_calls": record.get("llm_calls", []),
            })
        elif current_detail_ids:
            update["community_errors"] = [_tool_error("classify_community_details", classified)]
        return update

    def assess_community_coverage(self, state: RoleProfileGraphState) -> dict[str, Any]:
        counters = RoleSearchCounter.model_validate(state["counters"])
        group_id = str(state.get("community_current_group_id") or "")
        purpose = str(state.get("community_current_purpose") or "")
        source_id = str(state.get("community_current_source_id") or "")
        round_index = int(state.get("community_current_round") or 1)
        query_raw = state.get("community_current_query") or {}
        query_id = str(query_raw.get("query_id") or "")
        if not all((group_id, purpose, source_id, query_id)):
            return {
                "community_route": "project",
                "trace": [_trace("assess_community_coverage", counters, reason="missing_attempt")],
            }
        scope_key = _community_scope_key(group_id, purpose)
        accepted_current: list[str] = []
        current_segment_ids = set(state.get("community_current_evidence_segment_ids", []))
        for document_id in state.get("community_current_evidence_document_ids", []):
            document = self.role_repository.get(str(document_id), CommunityEvidenceDocument)
            if document is None:
                continue
            segments = [
                item for item in self.role_repository.list(
                    "community_evidence_segment", CommunityEvidenceSegment
                )
                if item.document_id == document.document_id
                and item.segment_id in current_segment_ids
                and item.validation_status == "accepted"
            ]
            allowed = (
                any(item.usage == "demand_assessment" for item in segments)
                if purpose == "interview_experience"
                else any(item.usage in {"reputation_job", "reputation_company"} for item in segments)
            )
            if allowed:
                accepted_current.append(document.document_id)
        accepted_map = {
            key: list(value)
            for key, value in state.get("community_accepted_document_ids_by_scope", {}).items()
        }
        accepted_map[scope_key] = _merge_independent_community_documents(
            accepted_map.get(scope_key, []), accepted_current,
            role_repository=self.role_repository,
            evidence_repository=self.evidence_repository,
        )
        accepted = accepted_map[scope_key]
        budget = RoleSearchBudget.model_validate(state["budgets"])
        sufficient = len(accepted) >= budget.community_target_documents_per_purpose
        history = next((
            item for item in reversed(state.get("query_history", []))
            if str(item.get("query_id")) == query_id
        ), {})
        error_type = str(history.get("error_type") or "")
        blocked = bool(state.get("community_skip_current_source")) or error_type in {
            "authentication_required", "risk_controlled", "adapter_required",
            "policy_blocked", "robots_disallowed",
        }
        attempt_status = (
            "blocked" if blocked else "failed" if error_type
            else "empty" if not state.get("community_current_detail_document_ids")
            else "completed"
        )
        attempt = CommunitySearchAttemptReceipt(
            attempt_id=f"community-attempt:{query_id}", company_role_group_id=group_id,
            query_id=query_id, source_id=source_id, evidence_purpose=purpose,
            round_index=round_index,
            relaxation_level={1: "exact_role", 2: "role_family", 3: "company_only"}[round_index],
            status=attempt_status,
            discovered_candidate_ids=list(state.get("community_current_candidate_ids", [])),
            detail_document_ids=list(state.get("community_current_detail_document_ids", [])),
            accepted_document_ids=accepted_current,
            reason_codes=[error_type] if error_type else ([] if accepted_current else ["no_accepted_detail"]),
        )
        self.role_repository.save(
            "community_search_attempt_receipt", attempt,
            idempotency_key=f"community-search-attempt:{attempt.attempt_id}",
        )
        exhausted = {
            key: list(value)
            for key, value in state.get("community_exhausted_source_ids_by_scope", {}).items()
        }
        exhausted_scope = set(exhausted.get(scope_key, []))
        if blocked or round_index >= budget.max_community_rounds_per_source:
            exhausted_scope.add(source_id)
        exhausted[scope_key] = sorted(exhausted_scope)
        status = "sufficient" if sufficient else "blocked" if blocked else "insufficient"
        next_action = (
            "next_purpose" if sufficient else "switch_source"
            if source_id in exhausted_scope else "next_round"
        )
        coverage = CommunityEvidenceCoverage(
            coverage_id=f"community-coverage:{query_id}",
            company_role_group_id=group_id, evidence_purpose=purpose,
            target_document_count=budget.community_target_documents_per_purpose,
            accepted_document_ids=accepted,
            independent_document_count=len(accepted),
            attempted_query_ids=[
                item.query_id for item in self.role_repository.list(
                    "community_search_attempt_receipt", CommunitySearchAttemptReceipt
                ) if item.company_role_group_id == group_id and item.evidence_purpose == purpose
            ],
            exhausted_source_ids=sorted(exhausted_scope), status=status,
            next_action=next_action,
            reason_codes=[] if sufficient else ["community_sample_insufficient"],
        )
        self.role_repository.save(
            "community_evidence_coverage", coverage,
            idempotency_key=f"community-evidence-coverage:{coverage.coverage_id}",
        )
        return {
            "community_attempt_receipt_ids": [attempt.attempt_id],
            "community_coverage_ids": [coverage.coverage_id],
            "community_accepted_document_ids_by_scope": accepted_map,
            "community_exhausted_source_ids_by_scope": exhausted,
            "community_sufficient_scope_keys": [scope_key] if sufficient else [],
            "community_route": "next", "community_skip_current_source": False,
            "trace": [_trace(
                "assess_community_coverage", counters, purpose=purpose,
                independent_documents=len(accepted), sufficient=sufficient,
                next_action=next_action,
            )],
        }

    def project_role_intelligence(self, state: RoleProfileGraphState) -> dict[str, Any]:
        detail_raw_refs = sorted({
            str(document.raw_artifact_id)
            for document_id in [
                *state.get("recruitment_detail_document_ids", []),
                *state.get("community_detail_document_ids", []),
            ]
            if (
                document := self.role_repository.get(document_id, SourceDocument)
            ) is not None and document.raw_artifact_id
        })
        result = self.registry.run("profile.project_role_intelligence", {
            "search_scope": state["search_scope"],
            "eligible_cluster_ids": state.get("eligible_job_cluster_ids", []),
            "claim_ids": state.get("claim_ids", []),
            "detail_receipt_ids": state.get("role_detail_evidence_receipt_ids", []),
            "official_escalation_receipt_ids": state.get("official_escalation_receipt_ids", []),
            "segment_ids": state.get("community_evidence_segment_ids", []),
            "raw_evidence_refs": detail_raw_refs,
            "source_receipt_ids": [item.get("source_run_id") for item in state.get("source_run_receipts", []) if item.get("source_run_id")],
        })
        counters = RoleSearchCounter.model_validate(state["counters"]).model_copy(
            update={"tool_calls": RoleSearchCounter.model_validate(state["counters"]).tool_calls + 1}
        )
        update: dict[str, Any] = {
            "counters": counters.model_dump(), "tool_results": [_safe_tool_result(result)],
            "trace": [_trace("project_role_intelligence", counters)],
        }
        if result.status == "success" and result.records:
            record = result.records[0]
            update.update(record)
            update["next_action"] = "complete" if not record.get("missing_sections") else "finalize_with_unknowns"
        else:
            update.update({"next_action": "fail", "errors": [_tool_error("project_role_intelligence", result)]})
        return update

    def plan_source_auth(self, state: RoleProfileGraphState) -> dict[str, Any]:
        source_id = state.get("pending_auth_source_id")
        if not source_id:
            raise RoleProfileWorkflowError("await_user_auth requires pending source")
        capability = state.get("source_capabilities", {}).get(source_id, {})
        external = capability.get("authorization_mode") == "external_session"
        request = {
            "request_id": f"request-role-auth-{state['thread_id']}-{source_id}",
            "thread_id": state["thread_id"], "run_id": state["run_id"],
            "user_id": state["user_id"], "interaction_type": "authorize_source",
            "source_id": source_id,
            "authorization_mode": capability.get("authorization_mode", "credential_ref"),
            "login_entry": (
                "请在 MediaCrawler 使用的真实 Chrome/CDP 会话中正常登录小红书"
                if external else "请在真实 Chrome 中正常登录该来源"
            ),
            "import_instruction": (
                "保持 Sidecar 和 Chrome 会话可用后选择 authorized；不要提交 Cookie"
                if external else "运行 campus-agent auth import-chrome --source nowcoder，只返回 credential_ref"
            ),
            "allowed_actions": ["authorized", "skip_source", "cancel"],
        }
        return {"pending_interaction": request, "status": "interrupted", "trace": [_trace("plan_source_auth", RoleSearchCounter.model_validate(state["counters"]), source=source_id)]}

    def interrupt_for_source_auth(self, state: RoleProfileGraphState) -> dict[str, Any]:
        return {"resume_input": interrupt(state["pending_interaction"])}

    def validate_source_authorization(self, state: RoleProfileGraphState) -> dict[str, Any]:
        request, response = state.get("pending_interaction") or {}, state.get("resume_input") or {}
        for key in ("request_id", "thread_id", "user_id", "source_id"):
            if str(response.get(key, "")) != str(request.get(key, "")):
                raise RoleProfileWorkflowError(f"authorization {key} mismatch")
        action = response.get("action")
        if action not in request.get("allowed_actions", []):
            raise RoleProfileWorkflowError("authorization action is not allowed")
        source_id = str(request["source_id"])
        refs = dict(state.get("credential_refs", {}))
        skipped: list[str] = []
        pending_queries: list[dict[str, Any]] = []
        skip_current = False
        if action == "authorized":
            capability = state.get("source_capabilities", {}).get(source_id, {})
            external = capability.get("authorization_mode") == "external_session"
            ref = str(response.get("credential_ref", ""))
            result = self.registry.run(
                "source.validate_external_session" if external else "source.validate_credential_ref",
                {"source_id": source_id, "credential_ref": ref},
            )
            if result.status != "success":
                raise RoleProfileWorkflowError(
                    "external_session_invalid" if external else "credential_invalid"
                )
            if ref:
                refs[source_id] = ref
            if state.get("community_current_query"):
                pending_queries = [dict(state["community_current_query"])]
            route = "retry"
        elif action == "skip_source":
            skipped = [source_id]; route = "continue"; skip_current = True
        else:
            route = "cancel"
        return {
            "credential_refs": refs, "skipped_source_ids": skipped,
            "pending_auth_source_id": None, "pending_interaction": None,
            "resume_input": None, "status": "running", "last_auth_action": route,
            "pending_queries": pending_queries,
            "community_skip_current_source": skip_current,
            "tool_results": [],
            "trace": [_trace("validate_source_authorization", RoleSearchCounter.model_validate(state["counters"]), action=action)],
        }

    def cancel_role_research(self, state: RoleProfileGraphState) -> dict[str, Any]:
        return {
            "status": "cancelled",
            "next_action": "cancel",
            "pending_interaction": None,
            "resume_input": None,
            "trace": [
                _trace(
                    "cancel_role_research",
                    RoleSearchCounter.model_validate(state["counters"]),
                )
            ],
        }

    def finalize_role_intelligence(self, state: RoleProfileGraphState) -> dict[str, Any]:
        action = state.get("next_action")
        status = (
            "cancelled" if action == "cancel"
            else "failed" if action == "fail"
            else "completed" if action == "complete"
            else "completed_with_unknowns"
        )
        report = {
            "status": status, "completion_reason": action,
            "role_intelligence_bundle_id": state.get("role_intelligence_bundle_id"),
            "job_demand_profile_count": len(state.get("job_demand_profile_ids", [])),
            "job_reputation_profile_count": len(state.get("job_reputation_profile_ids", [])),
            "company_reputation_profile_count": len(state.get("company_reputation_profile_ids", [])),
            "community_segment_count": len(state.get("community_evidence_segment_ids", [])),
            "missing_sections": state.get("missing_sections", []),
            "recruitment_error_count": len(state.get("recruitment_errors", [])),
            "community_error_count": len(state.get("community_errors", [])),
        }
        if state.get("output_dir"):
            _export_run(Path(str(state["output_dir"])), state, self.role_repository, report)
        return {
            "status": status, "report": report, "pending_interaction": None,
            "resume_input": None, "tool_results": [],
            "trace": [_trace("finalize_role_intelligence", RoleSearchCounter.model_validate(state["counters"]), status=status)],
        }

    def _collect_queries(
        self, state: RoleProfileGraphState, queries: list[dict[str, Any]],
        *, tool_name: str, phase: str,
    ) -> dict[str, Any]:
        counters = RoleSearchCounter.model_validate(state["counters"])
        documents: list[str] = []
        artifacts: list[str] = []
        batches: list[str] = []
        receipts: list[dict[str, Any]] = []
        history: list[dict[str, Any]] = []
        results: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        pending_auth = None
        for raw in queries:
            query = SourceQuery.model_validate(raw)
            result = self.registry.run(tool_name, {
                "query": raw, "run_id": state["run_id"],
                "credential_ref": state.get("credential_refs", {}).get(query.source_id),
            })
            updates = {"queries": counters.queries + 1, "tool_calls": counters.tool_calls + 1}
            updates["recruitment_searches" if phase == "recruitment_search" else "community_searches"] = (
                getattr(counters, "recruitment_searches" if phase == "recruitment_search" else "community_searches") + 1
            )
            counters = counters.model_copy(update=updates); results.append(_safe_tool_result(result))
            if not result.records:
                errors.append(_tool_error(f"collect_{phase}", result))
                if result.metadata.get("needs_user_action"):
                    pending_auth = query.source_id
                history.append({**raw, "status": "failed", "error_type": result.metadata.get("error_type")})
                continue
            batch = SourceBatch.model_validate(result.records[0]["batch"])
            batches.append(batch.batch_id); receipts.append(result.records[0]["receipt"])
            for document in batch.documents:
                self.role_repository.save("source_document", document, idempotency_key=f"source-document:{document.source_document_id}")
                documents.append(document.source_document_id)
                if document.raw_artifact_id:
                    artifacts.append(str(document.raw_artifact_id))
            if result.status != "success":
                errors.append(_tool_error(f"collect_{phase}", result))
                if result.metadata.get("needs_user_action"):
                    pending_auth = query.source_id
            history.append({**raw, "status": "completed" if result.status == "success" else "failed", "error_type": result.metadata.get("error_type")})
        key = "recruitment_search_document_ids" if phase == "recruitment_search" else "community_search_document_ids"
        error_key = "recruitment_errors" if phase == "recruitment_search" else "community_errors"
        counters = counters.model_copy(update={"documents": counters.documents + len(documents)})
        return {
            key: documents, "pending_queries": [], "pending_auth_source_id": pending_auth,
            "raw_artifact_ids": artifacts, "source_batch_ids": batches,
            "source_run_receipts": receipts, "query_history": history,
            "counters": counters.model_dump(), "tool_results": results,
            error_key: errors, "trace": [_trace(f"collect_{phase}", counters, documents=len(documents))],
        }

    def _fetch_details(
        self, state: RoleProfileGraphState, *, candidate_kind: str,
        candidate_ids: list[str], limit: int, include_existing: bool = True,
    ) -> dict[str, Any]:
        counters = RoleSearchCounter.model_validate(state["counters"])
        requests: list[str] = []
        documents: list[str] = []
        artifacts: list[str] = []
        receipts: list[dict[str, Any]] = []
        results: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        pending_auth = None
        model = JobDetailCandidate if candidate_kind == "job" else CommunityPostCandidate
        kind = "job_detail" if candidate_kind == "job" else "experience_post"
        channel = "recruitment_discovery" if candidate_kind == "job" else "experience"
        existing_detail_ids = state.get("recruitment_detail_document_ids" if candidate_kind == "job" else "community_detail_document_ids", [])
        if include_existing:
            documents.extend(existing_detail_ids)
        for candidate_id in candidate_ids[:max(0, limit - len(documents))]:
            candidate = self.role_repository.get(str(candidate_id), model)
            if candidate is None:
                continue
            request = SourceDetailRequest(
                source_id=candidate.source_id, channel=channel, query_id=candidate.query_id,
                candidate_id=candidate.candidate_id, parent_document_id=candidate.search_document_id,
                detail_url=candidate.detail_url, expected_document_kind=kind,
                external_locator_ref=getattr(candidate, "external_locator_ref", None),
            )
            result = self.registry.run("source.fetch_detail", {
                "request": request.model_dump(mode="json"), "run_id": state["run_id"],
                "credential_ref": state.get("credential_refs", {}).get(candidate.source_id),
            })
            field = "recruitment_details" if candidate_kind == "job" else "community_details"
            counters = counters.model_copy(update={
                "tool_calls": counters.tool_calls + 1,
                field: getattr(counters, field) + 1,
            })
            results.append(_safe_tool_result(result)); requests.append(request.detail_request_id)
            if result.records:
                batch = SourceBatch.model_validate(result.records[0]["batch"])
                receipts.append(result.records[0]["receipt"])
                for document in batch.documents:
                    documents.append(document.source_document_id)
                    if document.raw_artifact_id:
                        artifacts.append(str(document.raw_artifact_id))
            if result.status != "success":
                errors.append(_tool_error(f"fetch_{candidate_kind}_details", result))
                if result.metadata.get("needs_user_action"):
                    pending_auth = candidate.source_id
        prefix = "recruitment" if candidate_kind == "job" else "community"
        counters = counters.model_copy(update={"documents": counters.documents + len(documents)})
        return {
            f"{prefix}_detail_request_ids": requests,
            f"{prefix}_detail_document_ids": documents,
            "raw_artifact_ids": artifacts, "source_run_receipts": receipts,
            "pending_auth_source_id": pending_auth, "counters": counters.model_dump(),
            "tool_results": results, f"{prefix}_errors": errors,
            "trace": [_trace(f"fetch_{prefix}_details", counters, documents=len(documents))],
        }

    def _simple_tool(
        self, state: RoleProfileGraphState, tool_name: str, args: dict[str, Any],
        output_key: str, node: str,
    ) -> dict[str, Any]:
        result = self.registry.run(tool_name, args)
        current = RoleSearchCounter.model_validate(state["counters"])
        counters = current.model_copy(update={"tool_calls": current.tool_calls + 1})
        return {
            output_key: result.evidence_ids if result.status == "success" else [],
            "counters": counters.model_dump(), "tool_results": [_safe_tool_result(result)],
            "errors": [] if result.status == "success" else [_tool_error(node, result)],
            "trace": [_trace(node, counters, count=len(result.evidence_ids))],
        }


def _trace(node: str, counters: RoleSearchCounter, **extra: Any) -> dict[str, Any]:
    return {"node": node, "counters": counters.model_dump(), **extra}


def _safe_tool_result(result: Any) -> dict[str, Any]:
    """Keep checkpoint diagnostics small and free of extracted page content."""

    safe_metadata_keys = {
        "error_type", "retryable", "needs_user_action", "idempotency_key",
        "parser_name", "parser_version", "record_count", "verification_status",
        "mandatory_official_verification_count",
        "conditional_official_escalation_count", "official_verification_plan_ids",
    }
    metadata = {
        key: value
        for key, value in dict(getattr(result, "metadata", {}) or {}).items()
        if key in safe_metadata_keys
    }
    records = list(getattr(result, "records", []) or [])
    evidence_ids = [str(value) for value in getattr(result, "evidence_ids", [])]
    return {
        "tool_name": str(getattr(result, "tool_name", "unknown")),
        "status": str(getattr(result, "status", "failed")),
        "record_count": len(records),
        "evidence_ids": evidence_ids,
        "error_type": metadata.get("error_type"),
        "metadata": metadata,
    }


def _tool_error(node: str, result: Any) -> dict[str, Any]:
    error_type = str(result.metadata.get("error_type", "failed"))
    return {
        "node": node, "error_type": error_type,
        # Raw adapter errors can contain page content or credentials. The
        # repository receipts retain typed diagnostics; checkpoint state only
        # carries the bounded error category needed for routing.
        "message": error_type,
        "fatal": error_type in {"storage_error", "checkpoint_error"},
        "retryable": bool(result.metadata.get("retryable")),
    }


def _export_run(
    root: Path, state: RoleProfileGraphState,
    repository: SQLiteRoleRepository, report: dict[str, Any],
) -> None:
    import json
    root.mkdir(parents=True, exist_ok=True)
    safe = {
        "status": report["status"],
        "role_intelligence_bundle_id": report["role_intelligence_bundle_id"],
        "job_demand_profile_count": report["job_demand_profile_count"],
        "job_reputation_profile_count": report["job_reputation_profile_count"],
        "company_reputation_profile_count": report["company_reputation_profile_count"],
        "community_segment_count": report["community_segment_count"],
        "missing_sections": report["missing_sections"],
    }
    (root / "role_intelligence_report.json").write_text(
        json.dumps(safe, ensure_ascii=False, indent=2), encoding="utf-8"
    )


__all__ = [
    "RoleProfileGraphRuntime", "RoleProfileWorkflowError", "build_role_profile_graph",
    "create_role_profile_state", "open_sqlite_checkpointer",
]
