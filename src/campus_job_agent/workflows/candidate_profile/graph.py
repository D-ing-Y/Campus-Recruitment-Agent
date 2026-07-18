"""Stateful, interruptible v0.4 candidate-profile LangGraph subgraph."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import AbstractContextManager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from campus_job_agent.schemas import (
    BudgetState,
    CandidateProfileGraphState,
    CounterState,
    HumanInteractionRequest,
    HumanInteractionResponse,
    InformationGap,
    QuestionPlan,
    RequestedMaterial,
    SufficiencyAssessment,
    ToolResult,
)
from campus_job_agent.storage.base import EvidenceRepository, ProfileRepository
from campus_job_agent.tools import ToolRegistry
from campus_job_agent.tools.candidate_profile import canonical_response_payload
from campus_job_agent.workflows.candidate_profile.evaluator import (
    DeterministicSufficiencyEvaluator,
    SufficiencyEvaluator,
)
from campus_job_agent.workflows.candidate_profile.planner import (
    DeterministicQuestionPlanner,
    question_key,
)
from campus_job_agent.workflows.candidate_profile.policy import CandidateRoutePolicy


class CandidateProfileWorkflowError(RuntimeError):
    """A validated workflow boundary rejected an operation."""


class CandidateProfileGraphRuntime:
    """Small runtime facade that enforces the thread ID on invoke and resume."""

    def __init__(
        self,
        *,
        registry: ToolRegistry,
        evidence_repository: EvidenceRepository,
        profile_repository: ProfileRepository,
        evaluator: SufficiencyEvaluator | None = None,
        question_planner: Any | None = None,
        route_policy: CandidateRoutePolicy | None = None,
        checkpointer: Any,
    ) -> None:
        self.registry = registry
        self.evidence_repository = evidence_repository
        self.profile_repository = profile_repository
        self.evaluator = evaluator or DeterministicSufficiencyEvaluator()
        self.question_planner = question_planner or DeterministicQuestionPlanner()
        self.route_policy = route_policy or CandidateRoutePolicy()
        self.app = build_candidate_profile_graph(
            registry=registry,
            evidence_repository=evidence_repository,
            profile_repository=profile_repository,
            evaluator=self.evaluator,
            question_planner=self.question_planner,
            route_policy=self.route_policy,
            checkpointer=checkpointer,
        )

    def invoke(self, state: CandidateProfileGraphState) -> dict[str, Any]:
        thread_id = str(state.get("thread_id", "")).strip()
        if not thread_id:
            raise ValueError("thread_id is required")
        try:
            return self.app.invoke(
                state, {"configurable": {"thread_id": thread_id}}
            )
        except sqlite3.Error as exc:
            raise CandidateProfileWorkflowError(
                f"checkpoint_error: durable state could not be saved: {exc}"
            ) from exc

    def resume(
        self, *, thread_id: str, response: HumanInteractionResponse | dict[str, Any]
    ) -> dict[str, Any]:
        payload = (
            response.model_dump(mode="json")
            if isinstance(response, HumanInteractionResponse)
            else response
        )
        if str(payload.get("thread_id", "")) != thread_id:
            raise ValueError("resume thread_id does not match response thread_id")
        validated_response = HumanInteractionResponse.model_validate(payload)
        try:
            current = self.app.get_state(
                {"configurable": {"thread_id": thread_id}}
            )
        except sqlite3.Error as exc:
            raise CandidateProfileWorkflowError(
                f"checkpoint_error: durable state could not be loaded: {exc}"
            ) from exc
        values = dict(current.values or {})
        if not values.get("pending_interaction"):
            receipt = self.evidence_repository.get_response_receipt(
                validated_response.response_id
            )
            if receipt is None:
                raise CandidateProfileWorkflowError(
                    "no pending interaction exists for this thread"
                )
            payload_hash = hashlib.sha256(
                canonical_response_payload(validated_response)
            ).hexdigest()
            if receipt.get("payload_hash") != payload_hash:
                raise CandidateProfileWorkflowError(
                    "idempotency_conflict: response_id has a different payload"
                )
            return values
        try:
            return self.app.invoke(
                Command(resume=payload),
                {"configurable": {"thread_id": thread_id}},
            )
        except sqlite3.Error as exc:
            raise CandidateProfileWorkflowError(
                f"checkpoint_error: durable state could not be saved: {exc}"
            ) from exc

    def get_state(self, thread_id: str) -> Any:
        return self.app.get_state({"configurable": {"thread_id": thread_id}})


def open_sqlite_checkpointer(
    database_path: str | Path,
) -> AbstractContextManager[SqliteSaver]:
    """Open the official LangGraph SQLite saver as a managed resource."""

    path = Path(database_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    return SqliteSaver.from_conn_string(str(path))


def create_candidate_profile_state(
    *,
    thread_id: str,
    user_id: str,
    candidate_id: str,
    input_paths: list[str] | None = None,
    run_id: str | None = None,
    budgets: BudgetState | dict[str, Any] | None = None,
    pending_artifact_ids: list[str] | None = None,
    allowed_path_roots: list[str] | None = None,
) -> CandidateProfileGraphState:
    budget = (
        budgets
        if isinstance(budgets, BudgetState)
        else BudgetState.model_validate(budgets or {})
    )
    submitted_paths = list(input_paths or [])
    authorized_roots = list(allowed_path_roots or [])
    if not authorized_roots:
        authorized_roots = _stable_unique(
            [
                str(Path(value).resolve().parent)
                for value in submitted_paths
            ]
            or [str(Path.cwd().resolve())]
        )
    return {
        "run_id": run_id or str(uuid4()),
        "thread_id": thread_id,
        "user_id": user_id,
        "candidate_id": candidate_id,
        "status": "initialized",
        "allowed_path_roots": authorized_roots,
        "input_paths": submitted_paths,
        "pending_artifact_ids": list(pending_artifact_ids or []),
        "active_artifact_ids": [],
        "processed_artifact_ids": [],
        "fragment_ids": [],
        "processed_fragment_ids": [],
        "claim_ids": [],
        "unsupported_artifact_ids": [],
        "candidate_profile_snapshot_id": None,
        "sufficiency_assessment": None,
        "information_gaps": [],
        "question_plan": None,
        "next_action": None,
        "pending_interaction": None,
        "resume_input": None,
        "processed_response_ids": [],
        "skipped_gap_ids": [],
        "asked_question_keys": [],
        "last_human_action": None,
        "budgets": budget.model_dump(),
        "counters": CounterState().model_dump(),
        "tool_results": [],
        "llm_calls": [],
        "trace": [],
        "errors": [],
        "report": None,
    }


def build_candidate_profile_graph(
    *,
    registry: ToolRegistry,
    evidence_repository: EvidenceRepository,
    profile_repository: ProfileRepository,
    evaluator: SufficiencyEvaluator | None = None,
    question_planner: Any | None = None,
    route_policy: CandidateRoutePolicy | None = None,
    checkpointer: Any,
):
    handlers = _CandidateProfileNodes(
        registry=registry,
        evidence_repository=evidence_repository,
        profile_repository=profile_repository,
        evaluator=evaluator or DeterministicSufficiencyEvaluator(),
        question_planner=question_planner or DeterministicQuestionPlanner(),
        route_policy=route_policy or CandidateRoutePolicy(),
    )
    graph = StateGraph(CandidateProfileGraphState)
    graph.add_node("initialize_profile_run", handlers.initialize_profile_run)
    graph.add_node("ingest_pending_materials", handlers.ingest_pending_materials)
    graph.add_node(
        "extract_and_validate_claims", handlers.extract_and_validate_claims
    )
    graph.add_node("project_candidate_profile", handlers.project_candidate_profile)
    graph.add_node(
        "assess_profile_sufficiency", handlers.assess_profile_sufficiency
    )
    graph.add_node("route_next_action", handlers.route_next_action)
    graph.add_node("plan_human_interaction", handlers.plan_human_interaction)
    graph.add_node("interrupt_for_user", handlers.interrupt_for_user)
    graph.add_node("archive_human_input", handlers.archive_human_input)
    graph.add_node("finalize_profile", handlers.finalize_profile)

    graph.add_edge(START, "initialize_profile_run")
    graph.add_edge("initialize_profile_run", "ingest_pending_materials")
    graph.add_edge("ingest_pending_materials", "extract_and_validate_claims")
    graph.add_edge("extract_and_validate_claims", "project_candidate_profile")
    graph.add_edge("project_candidate_profile", "assess_profile_sufficiency")
    graph.add_edge("assess_profile_sufficiency", "route_next_action")
    graph.add_conditional_edges(
        "route_next_action",
        lambda state: state["next_action"],
        {
            "read_more": "ingest_pending_materials",
            "ask_user": "plan_human_interaction",
            "request_more_materials": "plan_human_interaction",
            "finalize_with_unknowns": "finalize_profile",
            "complete": "finalize_profile",
            "fail": "finalize_profile",
        },
    )
    graph.add_edge("plan_human_interaction", "interrupt_for_user")
    graph.add_edge("interrupt_for_user", "archive_human_input")
    graph.add_conditional_edges(
        "archive_human_input",
        _archive_route,
        {
            "project": "project_candidate_profile",
            "ingest": "ingest_pending_materials",
            "assess": "assess_profile_sufficiency",
            "finalize": "finalize_profile",
        },
    )
    graph.add_edge("finalize_profile", END)
    return graph.compile(checkpointer=checkpointer)


class _CandidateProfileNodes:
    def __init__(
        self,
        *,
        registry: ToolRegistry,
        evidence_repository: EvidenceRepository,
        profile_repository: ProfileRepository,
        evaluator: SufficiencyEvaluator,
        question_planner: Any,
        route_policy: CandidateRoutePolicy,
    ) -> None:
        self.registry = registry
        self.evidence_repository = evidence_repository
        self.profile_repository = profile_repository
        self.evaluator = evaluator
        self.question_planner = question_planner
        self.route_policy = route_policy
        self.fallback_evaluator = DeterministicSufficiencyEvaluator()
        self.fallback_planner = DeterministicQuestionPlanner()

    def initialize_profile_run(
        self, state: CandidateProfileGraphState, config: RunnableConfig
    ) -> dict[str, Any]:
        required = ["run_id", "thread_id", "user_id", "candidate_id"]
        missing = [name for name in required if not str(state.get(name, "")).strip()]
        if missing:
            raise CandidateProfileWorkflowError(
                f"missing required state fields: {', '.join(missing)}"
            )
        configured_thread = str(
            config.get("configurable", {}).get("thread_id", "")
        )
        if configured_thread != state["thread_id"]:
            raise CandidateProfileWorkflowError(
                "configurable.thread_id must equal state.thread_id"
            )
        budgets = BudgetState.model_validate(state.get("budgets", {}))
        counters = CounterState.model_validate(state.get("counters", {}))
        roots = [str(value) for value in state.get("allowed_path_roots", [])]
        if not roots:
            raise CandidateProfileWorkflowError(
                "at least one allowed_path_root is required"
            )
        for path in state.get("input_paths", []):
            if not _path_is_allowed(path, roots):
                raise CandidateProfileWorkflowError(
                    "input path is outside allowed_path_roots"
                )
        latest = self.profile_repository.get_latest_profile(
            state["candidate_id"], "candidate"
        )
        return {
            "status": "running",
            "budgets": budgets.model_dump(),
            "counters": counters.model_dump(),
            "candidate_profile_snapshot_id": (
                state.get("candidate_profile_snapshot_id")
                or (latest.snapshot_id if latest else None)
            ),
            "trace": [_trace("initialize_profile_run", state, counters)],
        }

    def ingest_pending_materials(
        self, state: CandidateProfileGraphState
    ) -> dict[str, Any]:
        budgets = BudgetState.model_validate(state["budgets"])
        counters = CounterState.model_validate(state["counters"])
        tool_results: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        active_ids: list[str] = []
        pending_ids: list[str] = []
        processed_ids: list[str] = []
        unsupported_ids: list[str] = []
        fragment_ids: list[str] = []

        for path in state.get("input_paths", []):
            result, counters = self._call_tool(
                "candidate.ingest_material",
                {
                    "path": path,
                    "owner_id": state["user_id"],
                    "candidate_id": state["candidate_id"],
                },
                counters,
                budgets,
            )
            tool_results.append(result.model_dump(mode="json"))
            if result.status == "failed":
                errors.append(_tool_error("ingest_pending_materials", result))
                if result.metadata.get("error_type") == "budget_exhausted":
                    break
                continue
            artifact_id = str(result.records[0]["artifact_id"])
            active_ids.append(artifact_id)
            pending_ids.append(artifact_id)

        known_pending = [
            value
            for value in [
                *state.get("pending_artifact_ids", []),
                *pending_ids,
            ]
            if value not in state.get("processed_artifact_ids", [])
        ]
        for artifact_id in _stable_unique(known_pending):
            artifact = self.evidence_repository.get_artifact(artifact_id)
            if artifact is None:
                errors.append(
                    {
                        "node": "ingest_pending_materials",
                        "error_type": "validation_error",
                        "message": f"unknown pending artifact: {artifact_id}",
                        "fatal": False,
                    }
                )
                processed_ids.append(artifact_id)
                continue
            active_ids.append(artifact_id)
            extractor_name = (
                "evidence.extract_pdf_text"
                if Path(artifact.original_name).suffix.lower() == ".pdf"
                else "evidence.extract_plain_text"
            )
            extracted, counters = self._call_tool(
                extractor_name,
                {"artifact_id": artifact_id, "owner_id": state["user_id"]},
                counters,
                budgets,
            )
            tool_results.append(extracted.model_dump(mode="json"))
            if extracted.status == "failed":
                errors.append(_tool_error("ingest_pending_materials", extracted))
                processed_ids.append(artifact_id)
                if extracted.metadata.get("error_type") == "unsupported_input":
                    unsupported_ids.append(artifact_id)
                if extracted.metadata.get("error_type") == "budget_exhausted":
                    break
                continue
            fragments, counters = self._call_tool(
                "evidence.create_fragments",
                {"artifact_id": artifact_id, "owner_id": state["user_id"]},
                counters,
                budgets,
            )
            tool_results.append(fragments.model_dump(mode="json"))
            processed_ids.append(artifact_id)
            if fragments.status == "failed":
                errors.append(_tool_error("ingest_pending_materials", fragments))
                if fragments.metadata.get("error_type") == "budget_exhausted":
                    break
                continue
            fragment_ids.extend(fragments.evidence_ids)
        return {
            "input_paths": [],
            "active_artifact_ids": active_ids,
            "pending_artifact_ids": pending_ids,
            "processed_artifact_ids": processed_ids,
            "unsupported_artifact_ids": unsupported_ids,
            "fragment_ids": fragment_ids,
            "counters": counters.model_dump(),
            "tool_results": tool_results,
            "errors": errors,
            "trace": [_trace("ingest_pending_materials", state, counters)],
        }

    def extract_and_validate_claims(
        self, state: CandidateProfileGraphState
    ) -> dict[str, Any]:
        budgets = BudgetState.model_validate(state["budgets"])
        counters = CounterState.model_validate(state["counters"])
        new_fragments = [
            value
            for value in state.get("fragment_ids", [])
            if value not in state.get("processed_fragment_ids", [])
        ]
        if not new_fragments:
            return {
                "trace": [_trace("extract_and_validate_claims", state, counters)]
            }
        if counters.llm_calls >= budgets.max_llm_calls:
            return {
                "errors": [_budget_error("extract_and_validate_claims", "max_llm_calls")],
                "trace": [_trace("extract_and_validate_claims", state, counters)],
            }
        result, counters = self._call_tool(
            "evidence.extract_candidate_claims",
            {
                "subject_id": state["candidate_id"],
                "owner_id": state["user_id"],
                "fragment_ids": new_fragments,
                "remaining_llm_calls": (
                    budgets.max_llm_calls - counters.llm_calls
                ),
            },
            counters,
            budgets,
        )
        llm_calls = _llm_records_from_tool(result)
        llm_attempts = sum(1 + int(item.get("retry_count", 0)) for item in llm_calls)
        counters = counters.model_copy(
            update={"llm_calls": counters.llm_calls + llm_attempts}
        )
        update: dict[str, Any] = {
            "counters": counters.model_dump(),
            "tool_results": [result.model_dump(mode="json")],
            "llm_calls": llm_calls,
            "trace": [_trace("extract_and_validate_claims", state, counters)],
        }
        if result.status == "failed":
            update["errors"] = [_tool_error("extract_and_validate_claims", result)]
            return update
        update["claim_ids"] = result.evidence_ids
        update["processed_fragment_ids"] = new_fragments
        return update

    def project_candidate_profile(
        self, state: CandidateProfileGraphState
    ) -> dict[str, Any]:
        if any(bool(item.get("fatal")) for item in state.get("errors", [])):
            return {
                "trace": [
                    _trace(
                        "project_candidate_profile",
                        state,
                        CounterState.model_validate(state["counters"]),
                    )
                ]
            }
        budgets = BudgetState.model_validate(state["budgets"])
        counters = CounterState.model_validate(state["counters"])
        result, counters = self._call_tool(
            "profile.project_candidate",
            {"candidate_id": state["candidate_id"]},
            counters,
            budgets,
        )
        rounds = counters.profile_rounds + 1
        counters = counters.model_copy(update={"profile_rounds": rounds})
        update: dict[str, Any] = {
            "counters": counters.model_dump(),
            "tool_results": [result.model_dump(mode="json")],
            "trace": [_trace("project_candidate_profile", state, counters)],
        }
        if result.status == "failed":
            update["errors"] = [_tool_error("project_candidate_profile", result)]
        else:
            update["candidate_profile_snapshot_id"] = result.records[0][
                "snapshot_id"
            ]
            update["claim_ids"] = result.records[0]["supporting_claim_ids"]
        return update

    def assess_profile_sufficiency(
        self, state: CandidateProfileGraphState
    ) -> dict[str, Any]:
        budgets = BudgetState.model_validate(state["budgets"])
        counters = CounterState.model_validate(state["counters"])
        snapshot_id = state.get("candidate_profile_snapshot_id")
        snapshot = (
            self.profile_repository.get_profile(snapshot_id) if snapshot_id else None
        )
        profile = snapshot.profile_data if snapshot else {}
        pending = _unprocessed_artifacts(state)
        errors: list[dict[str, Any]] = []
        try:
            if (
                getattr(self.evaluator, "name", "") == "llm"
                and counters.llm_calls >= budgets.max_llm_calls
            ):
                raise RuntimeError("max_llm_calls exhausted before sufficiency evaluation")
            assessment, calls = self.evaluator.evaluate(
                candidate_id=state["candidate_id"],
                profile_snapshot_id=snapshot_id,
                profile=profile,
                active_artifact_ids=state.get("active_artifact_ids", []),
                pending_artifact_ids=pending,
                skipped_gap_ids=state.get("skipped_gap_ids", []),
                budgets=budgets.model_dump(),
                counters=counters.model_dump(),
            )
            attempts = sum(1 + item.retry_count for item in calls)
            counters = counters.model_copy(
                update={"llm_calls": counters.llm_calls + attempts}
            )
        except Exception as exc:
            failed_calls = list(getattr(exc, "call_records", []))
            failed_attempts = sum(1 + item.retry_count for item in failed_calls)
            counters = counters.model_copy(
                update={"llm_calls": counters.llm_calls + failed_attempts}
            )
            assessment, calls = self.fallback_evaluator.evaluate(
                candidate_id=state["candidate_id"],
                profile_snapshot_id=snapshot_id,
                profile=profile,
                active_artifact_ids=state.get("active_artifact_ids", []),
                pending_artifact_ids=pending,
                skipped_gap_ids=state.get("skipped_gap_ids", []),
                budgets=budgets.model_dump(),
                counters=counters.model_dump(),
            )
            errors.append(
                {
                    "node": "assess_profile_sufficiency",
                    "error_type": "llm_output_error",
                    "message": str(exc),
                    "fatal": False,
                    "fallback": "deterministic",
                }
            )
            calls = [*failed_calls, *calls]
        return {
            "sufficiency_assessment": assessment.model_dump(mode="json"),
            "information_gaps": [
                item.model_dump(mode="json") for item in assessment.information_gaps
            ],
            "llm_calls": [item.model_dump(mode="json") for item in calls],
            "counters": counters.model_dump(),
            "errors": errors,
            "trace": [_trace("assess_profile_sufficiency", state, counters)],
        }

    def route_next_action(
        self, state: CandidateProfileGraphState
    ) -> dict[str, Any]:
        assessment = SufficiencyAssessment.model_validate(
            state["sufficiency_assessment"]
        )
        action = self.route_policy.decide(
            assessment=assessment,
            budgets=BudgetState.model_validate(state["budgets"]),
            counters=CounterState.model_validate(state["counters"]),
            pending_artifact_ids=_unprocessed_artifacts(state),
            skipped_gap_ids=state.get("skipped_gap_ids", []),
            asked_question_keys=state.get("asked_question_keys", []),
            has_fatal_error=any(
                bool(item.get("fatal")) for item in state.get("errors", [])
            ),
        )
        return {
            "next_action": action,
            "trace": [
                {
                    **_trace(
                        "route_next_action",
                        state,
                        CounterState.model_validate(state["counters"]),
                    ),
                    "route": action,
                    "reason": assessment.reason,
                }
            ],
        }

    def plan_human_interaction(
        self, state: CandidateProfileGraphState
    ) -> dict[str, Any]:
        budgets = BudgetState.model_validate(state["budgets"])
        counters = CounterState.model_validate(state["counters"])
        assessment = SufficiencyAssessment.model_validate(
            state["sufficiency_assessment"]
        )
        errors: list[dict[str, Any]] = []
        calls: list[Any] = []
        if state["next_action"] == "ask_user":
            try:
                if (
                    getattr(self.question_planner, "name", "") == "llm"
                    and counters.llm_calls >= budgets.max_llm_calls
                ):
                    raise RuntimeError(
                        "max_llm_calls exhausted before question planning"
                    )
                plan, calls = self.question_planner.plan(
                    assessment,
                    max_questions=budgets.max_questions_per_interrupt,
                    asked_question_keys=state.get("asked_question_keys", []),
                    skipped_gap_ids=state.get("skipped_gap_ids", []),
                    remaining_llm_calls=(
                        budgets.max_llm_calls - counters.llm_calls
                    ),
                )
                attempts = sum(1 + item.retry_count for item in calls)
                counters = counters.model_copy(
                    update={"llm_calls": counters.llm_calls + attempts}
                )
            except Exception as exc:
                failed_calls = list(getattr(exc, "call_records", []))
                failed_attempts = sum(1 + item.retry_count for item in failed_calls)
                counters = counters.model_copy(
                    update={"llm_calls": counters.llm_calls + failed_attempts}
                )
                plan, calls = self.fallback_planner.plan(
                    assessment,
                    max_questions=budgets.max_questions_per_interrupt,
                    asked_question_keys=state.get("asked_question_keys", []),
                    skipped_gap_ids=state.get("skipped_gap_ids", []),
                    remaining_llm_calls=None,
                )
                errors.append(
                    {
                        "node": "plan_human_interaction",
                        "error_type": "llm_output_error",
                        "message": str(exc),
                        "fatal": False,
                        "fallback": "deterministic",
                    }
                )
                calls = [*failed_calls, *calls]
            if not plan.questions:
                # The policy should normally prevent this; a safe material request
                # avoids an unanswerable empty question interrupt.
                interaction_type = "provide_materials"
                allowed_actions = ["upload", "skip", "cancel"]
            else:
                interaction_type = "answer_questions"
                allowed_actions = ["answer", "correct", "skip", "cancel"]
        else:
            plan = QuestionPlan(
                plan_id=_stable_hash(
                    "material-plan",
                    [assessment.assessment_id, state.get("information_gaps", [])],
                ),
                assessment_id=assessment.assessment_id,
                questions=[],
            )
            interaction_type = "provide_materials"
            allowed_actions = ["upload", "skip", "cancel"]
        gaps = [
            gap
            for gap in assessment.information_gaps
            if gap.status == "open"
            and gap.gap_id not in state.get("skipped_gap_ids", [])
        ]
        requested_materials = (
            [
                RequestedMaterial(
                    material_id=f"material-{hashlib.sha256(gap.gap_id.encode()).hexdigest()[:12]}",
                    gap_id=gap.gap_id,
                    description=(
                        "请补充可验证该字段的 Markdown、TXT、README 或文本型 PDF。"
                    ),
                    accepted_content_types=[
                        "text/markdown",
                        "text/plain",
                        "application/pdf",
                    ],
                    required=False,
                    reason=gap.description,
                )
                for gap in gaps[: budgets.max_questions_per_interrupt]
            ]
            if interaction_type == "provide_materials"
            else []
        )
        interaction_round = counters.interaction_rounds + 1
        request_id = _stable_hash(
            "hir",
            [
                state["thread_id"],
                interaction_round,
                interaction_type,
                [gap.gap_id for gap in gaps],
                plan.model_dump(mode="json"),
            ],
        )
        request = HumanInteractionRequest(
            request_id=request_id,
            thread_id=state["thread_id"],
            run_id=state["run_id"],
            user_id=state["user_id"],
            interaction_type=interaction_type,
            reason=assessment.reason,
            questions=plan.questions,
            requested_materials=requested_materials,
            profile_snapshot_id=state.get("candidate_profile_snapshot_id"),
            target_paths=_stable_unique(
                [
                    *[item.target_path for item in plan.questions],
                    *[gap.target_path for gap in gaps],
                ]
            ),
            related_artifact_ids=_stable_unique(
                value for gap in gaps for value in gap.related_artifact_ids
            ),
            related_claim_ids=_stable_unique(
                value for gap in gaps for value in gap.related_claim_ids
            ),
            allowed_actions=allowed_actions,
        )
        counters = counters.model_copy(
            update={"interaction_rounds": interaction_round}
        )
        return {
            "question_plan": plan.model_dump(mode="json"),
            "pending_interaction": request.model_dump(mode="json"),
            "asked_question_keys": [
                question_key(item.target_path) for item in plan.questions
            ],
            "counters": counters.model_dump(),
            "llm_calls": [item.model_dump(mode="json") for item in calls],
            "errors": errors,
            "status": "interrupted",
            "trace": [_trace("plan_human_interaction", state, counters, request_id)],
        }

    def interrupt_for_user(
        self, state: CandidateProfileGraphState
    ) -> dict[str, Any]:
        request = HumanInteractionRequest.model_validate(
            state["pending_interaction"]
        )
        response_payload = interrupt(request.model_dump(mode="json"))
        return {
            "resume_input": response_payload,
            "status": "running",
            "trace": [
                _trace(
                    "interrupt_for_user",
                    state,
                    CounterState.model_validate(state["counters"]),
                    request.request_id,
                )
            ],
        }

    def archive_human_input(
        self, state: CandidateProfileGraphState
    ) -> dict[str, Any]:
        request = HumanInteractionRequest.model_validate(
            state["pending_interaction"]
        )
        response = HumanInteractionResponse.model_validate(state["resume_input"])
        # Fail before invoking a mutating tool so an invalid resume cannot write
        # to the Evidence Store or advance the checkpoint.
        if response.request_id != request.request_id:
            raise CandidateProfileWorkflowError(
                "request_id does not match the pending interaction"
            )
        if response.thread_id != request.thread_id:
            raise CandidateProfileWorkflowError(
                "thread_id does not match the pending interaction"
            )
        if response.user_id != request.user_id:
            raise CandidateProfileWorkflowError(
                "user_id does not match the pending interaction"
            )
        if response.action not in request.allowed_actions:
            raise CandidateProfileWorkflowError(
                "response action is not allowed for this interaction"
            )
        budgets = BudgetState.model_validate(state["budgets"])
        counters = CounterState.model_validate(state["counters"])
        result, counters = self._call_tool(
            "evidence.archive_user_response",
            {
                "request": request.model_dump(mode="json"),
                "response": response.model_dump(mode="json"),
                "candidate_id": state["candidate_id"],
                "allowed_path_roots": state.get("allowed_path_roots", []),
            },
            counters,
            budgets,
        )
        if result.status == "failed":
            raise CandidateProfileWorkflowError(
                f"{result.metadata.get('error_type')}: {result.error}"
            )
        receipt = result.records[0]
        skipped = (
            [
                gap["gap_id"]
                for gap in state.get("information_gaps", [])
                if gap.get("status") == "open"
            ]
            if response.action == "skip"
            else []
        )
        unknown_correction_targets = {
            item.target_path
            for item in response.corrections
            if item.operation in {"remove", "mark_unknown"}
        }
        skipped.extend(
            str(gap["gap_id"])
            for gap in state.get("information_gaps", [])
            if gap.get("target_path") in unknown_correction_targets
        )
        status = "cancelled" if response.action == "cancel" else "running"
        return {
            "resume_input": None,
            "pending_interaction": None,
            "processed_response_ids": [response.response_id],
            "active_artifact_ids": [receipt["artifact_id"]],
            "fragment_ids": receipt.get("fragment_ids", []),
            "processed_fragment_ids": receipt.get("fragment_ids", []),
            "claim_ids": receipt.get("claim_ids", []),
            "input_paths": response.file_paths if response.action == "upload" else [],
            "skipped_gap_ids": skipped,
            "last_human_action": response.action,
            "status": status,
            "counters": counters.model_dump(),
            "tool_results": [result.model_dump(mode="json")],
            "trace": [
                _trace(
                    "archive_human_input",
                    state,
                    counters,
                    request.request_id,
                    response.response_id,
                )
            ],
        }

    def finalize_profile(
        self, state: CandidateProfileGraphState
    ) -> dict[str, Any]:
        budgets = BudgetState.model_validate(state["budgets"])
        counters = CounterState.model_validate(state["counters"])
        action = state.get("next_action")
        if state.get("last_human_action") == "cancel":
            status = "cancelled"
            reason = "cancelled"
        elif action == "fail":
            status = "failed"
            reason = "failed"
        elif action == "complete":
            status = "completed"
            reason = "sufficient"
        else:
            status = "completed_with_unknowns"
            if state.get("last_human_action") == "skip":
                reason = "user_skipped"
            elif (
                counters.profile_rounds >= budgets.max_profile_rounds
                or counters.llm_calls >= budgets.max_llm_calls
                or counters.tool_calls >= budgets.max_tool_calls
            ):
                reason = "budget_exhausted"
            else:
                reason = "low_information_value"
        unknowns = [
            str(item.get("target_path"))
            for item in state.get("information_gaps", [])
            if item.get("status") in {"open", "skipped"}
        ]
        tool_results: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        snapshot_id = state.get("candidate_profile_snapshot_id")
        if counters.tool_calls < budgets.max_tool_calls and status != "failed":
            result, counters = self._call_tool(
                "profile.project_candidate",
                {
                    "candidate_id": state["candidate_id"],
                    "completion_reason": reason,
                    "unknowns": unknowns,
                },
                counters,
                budgets,
            )
            tool_results.append(result.model_dump(mode="json"))
            if result.status == "success":
                snapshot_id = result.records[0]["snapshot_id"]
            else:
                errors.append(_tool_error("finalize_profile", result))
        latest = (
            self.profile_repository.get_profile(snapshot_id)
            if snapshot_id
            else None
        )
        profile_data = latest.profile_data if latest else {}
        return {
            "status": status,
            "candidate_profile_snapshot_id": snapshot_id,
            "counters": counters.model_dump(),
            "tool_results": tool_results,
            "errors": errors,
            "report": {
                "run_id": state["run_id"],
                "thread_id": state["thread_id"],
                "candidate_id": state["candidate_id"],
                "status": status,
                "completion_reason": reason,
                "snapshot_id": snapshot_id,
                "profile_version": latest.version if latest else None,
                "evidence_coverage": profile_data.get("evidence_coverage", {}),
                "unknowns": profile_data.get("unknowns") or unknowns,
                "conflict_ids": [
                    item.get("conflict_id")
                    for item in profile_data.get("conflicts", [])
                    if item.get("conflict_id")
                ],
                "remaining_gap_ids": [
                    item.get("gap_id")
                    for item in state.get("information_gaps", [])
                    if item.get("status") in {"open", "skipped"}
                ],
                "supporting_claim_count": len(
                    latest.supporting_claim_ids if latest else []
                ),
            },
            "trace": [_trace("finalize_profile", state, counters)],
        }

    def _call_tool(
        self,
        tool_name: str,
        args: dict[str, Any],
        counters: CounterState,
        budgets: BudgetState,
    ) -> tuple[ToolResult, CounterState]:
        if counters.tool_calls >= budgets.max_tool_calls:
            return (
                ToolResult(
                    tool_name=tool_name,
                    status="failed",
                    records=[],
                    evidence_ids=[],
                    error="max_tool_calls exhausted",
                    metadata={
                        "error_type": "budget_exhausted",
                        "retryable": False,
                        "needs_user_action": False,
                    },
                ),
                counters,
            )
        result = self.registry.run(tool_name, args)
        return (
            result,
            counters.model_copy(
                update={"tool_calls": counters.tool_calls + 1}
            ),
        )


def _archive_route(state: CandidateProfileGraphState) -> str:
    action = state.get("last_human_action")
    if action in {"answer", "correct"}:
        return "project"
    if action == "upload":
        return "ingest"
    if action == "skip":
        return "assess"
    return "finalize"


def _unprocessed_artifacts(state: CandidateProfileGraphState) -> list[str]:
    processed = set(state.get("processed_artifact_ids", []))
    return [
        value
        for value in state.get("pending_artifact_ids", [])
        if value not in processed
    ]


def _llm_records_from_tool(result: ToolResult) -> list[dict[str, Any]]:
    if not result.records:
        return []
    return [
        dict(value) for value in result.records[0].get("llm_calls", [])
    ]


def _tool_error(node: str, result: ToolResult) -> dict[str, Any]:
    error_type = result.metadata.get("error_type", "tool_retryable_error")
    return {
        "node": node,
        "tool_name": result.tool_name,
        "error_type": error_type,
        "message": result.error or f"{result.tool_name} failed",
        "retryable": bool(result.metadata.get("retryable")),
        "needs_user_action": bool(result.metadata.get("needs_user_action")),
        "fatal": error_type in {"storage_error", "checkpoint_error"},
    }


def _budget_error(node: str, budget: str) -> dict[str, Any]:
    return {
        "node": node,
        "error_type": "budget_exhausted",
        "message": f"{budget} exhausted",
        "fatal": False,
    }


def _trace(
    node: str,
    state: CandidateProfileGraphState,
    counters: CounterState,
    request_id: str | None = None,
    response_id: str | None = None,
) -> dict[str, Any]:
    timestamp = datetime.now(UTC).isoformat()
    return {
        "node": node,
        "status": "success",
        "started_at": timestamp,
        "ended_at": timestamp,
        "duration_ms": 0,
        "run_id": state.get("run_id"),
        "thread_id": state.get("thread_id"),
        "snapshot_id": state.get("candidate_profile_snapshot_id"),
        "artifact_count": len(state.get("active_artifact_ids", [])),
        "claim_count": len(state.get("claim_ids", [])),
        "counters": counters.model_dump(),
        "request_id": request_id,
        "response_id": response_id,
    }


def _stable_hash(prefix: str, values: Any) -> str:
    canonical = json.dumps(
        values, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    )
    return f"{prefix}-{hashlib.sha256(canonical.encode()).hexdigest()[:24]}"


def _stable_unique(values: Any) -> list[Any]:
    result: list[Any] = []
    for value in values:
        if value not in result:
            result.append(value)
    return result


def _path_is_allowed(path: str, roots: list[str]) -> bool:
    candidate = Path(path).resolve()
    for value in roots:
        root = Path(value).resolve()
        if candidate == root or root in candidate.parents:
            return True
    return False
