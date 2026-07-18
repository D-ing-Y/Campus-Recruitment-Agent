"""Deterministic baseline and structured-LLM sufficiency evaluators."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Protocol

from campus_job_agent.llm import LLMCache, LLMProvider, parse_structured_output
from campus_job_agent.prompts import (
    SUFFICIENCY_PROMPT_NAME,
    SUFFICIENCY_PROMPT_VERSION,
    SUFFICIENCY_SCHEMA_VERSION,
    build_candidate_sufficiency_messages,
    build_candidate_sufficiency_retry_messages,
)
from campus_job_agent.schemas import (
    InformationGap,
    LLMCallRecord,
    LLMConfig,
    SufficiencyAssessment,
)


class SufficiencyEvaluator(Protocol):
    def evaluate(
        self,
        *,
        candidate_id: str,
        profile_snapshot_id: str | None,
        profile: dict[str, Any],
        active_artifact_ids: list[str],
        pending_artifact_ids: list[str],
        skipped_gap_ids: list[str],
        budgets: dict[str, Any],
        counters: dict[str, Any],
    ) -> tuple[SufficiencyAssessment, list[LLMCallRecord]]: ...


class DeterministicSufficiencyEvaluator:
    name = "deterministic"

    def evaluate(
        self,
        *,
        candidate_id: str,
        profile_snapshot_id: str | None,
        profile: dict[str, Any],
        active_artifact_ids: list[str],
        pending_artifact_ids: list[str],
        skipped_gap_ids: list[str],
        budgets: dict[str, Any],
        counters: dict[str, Any],
    ) -> tuple[SufficiencyAssessment, list[LLMCallRecord]]:
        dimensions: dict[str, str] = {
            "education": "sufficient" if profile.get("education") else "insufficient",
            "experience": "sufficient" if profile.get("experiences") else "insufficient",
            "capability": "sufficient"
            if profile.get("capabilities")
            else "insufficient",
            "responsibility_boundary": "sufficient"
            if profile.get("responsibility_boundaries")
            else "insufficient",
            "evidence_quality": "sufficient"
            if profile.get("supporting_claim_ids")
            else "insufficient",
        }
        gaps = _deterministic_gaps(profile, active_artifact_ids)
        for gap in gaps:
            if gap.gap_id in skipped_gap_ids:
                gap.status = "skipped"
        open_gaps = [gap for gap in gaps if gap.status == "open"]
        conflicts = [
            str(item.get("conflict_id"))
            for item in profile.get("conflicts", [])
            if item.get("conflict_id")
        ]
        if pending_artifact_ids:
            action = "read_more"
            reason = "Unprocessed submitted material may resolve current gaps."
        elif conflicts:
            action = "ask_user"
            reason = "The profile contains unresolved evidence conflicts."
        elif not open_gaps:
            if gaps:
                action = "finalize_with_unknowns"
                reason = "All remaining high-value gaps were skipped."
            else:
                action = "complete"
                reason = "All required profile dimensions have evidence."
        elif not profile.get("supporting_claim_ids"):
            action = "request_more_materials"
            reason = "No persisted candidate evidence is available yet."
        else:
            top = max(open_gaps, key=lambda item: item.information_value)
            action = (
                "ask_user"
                if top.preferred_action == "ask_user"
                else "request_more_materials"
                if top.preferred_action == "request_more_materials"
                else "read_more"
                if top.preferred_action == "read_more"
                else "finalize_with_unknowns"
            )
            reason = top.description
        assessment_id = _stable_id(
            "assessment",
            {
                "candidate_id": candidate_id,
                "snapshot_id": profile_snapshot_id,
                "claim_ids": profile.get("supporting_claim_ids", []),
                "skipped": sorted(skipped_gap_ids),
            },
        )
        return (
            SufficiencyAssessment(
                assessment_id=assessment_id,
                candidate_id=candidate_id,
                profile_snapshot_id=profile_snapshot_id,
                is_sufficient=not open_gaps and not conflicts and not gaps,
                dimension_results=dimensions,
                information_gaps=sorted(
                    gaps, key=lambda item: (-item.information_value, item.gap_id)
                ),
                blocking_conflict_ids=conflicts,
                recommended_action=action,
                reason=reason,
                confidence=1.0,
            ),
            [],
        )


class LLMSufficiencyEvaluator:
    name = "llm"

    def __init__(
        self, config: LLMConfig, provider: LLMProvider, cache: LLMCache
    ) -> None:
        self.config = config
        self.provider = provider
        self.cache = cache

    def evaluate(
        self,
        *,
        candidate_id: str,
        profile_snapshot_id: str | None,
        profile: dict[str, Any],
        active_artifact_ids: list[str],
        pending_artifact_ids: list[str],
        skipped_gap_ids: list[str],
        budgets: dict[str, Any],
        counters: dict[str, Any],
    ) -> tuple[SufficiencyAssessment, list[LLMCallRecord]]:
        assessment_id = _stable_id(
            "assessment",
            {
                "candidate_id": candidate_id,
                "snapshot_id": profile_snapshot_id,
                "claim_ids": profile.get("supporting_claim_ids", []),
                "skipped": sorted(skipped_gap_ids),
            },
        )
        payload = {
            "assessment_id": assessment_id,
            "candidate_id": candidate_id,
            "profile_snapshot_id": profile_snapshot_id,
            "profile": _profile_summary(profile),
            "active_artifact_ids": active_artifact_ids,
            "pending_artifact_ids": pending_artifact_ids,
            "skipped_gap_ids": skipped_gap_ids,
            "budgets": budgets,
            "counters": counters,
            "allowed_actions": [
                "read_more",
                "ask_user",
                "request_more_materials",
                "finalize_with_unknowns",
                "complete",
                "fail",
            ],
        }

        def retry(previous: str, error: str) -> list[dict[str, str]]:
            return build_candidate_sufficiency_retry_messages(
                payload, previous, error
            )

        remaining = int(budgets["max_llm_calls"]) - int(counters["llm_calls"])
        config = self.config.model_copy(
            update={
                "max_retries": min(
                    self.config.max_retries, max(0, remaining - 1)
                )
            }
        )
        assessment, calls = parse_structured_output(
            messages=build_candidate_sufficiency_messages(payload),
            output_model=SufficiencyAssessment,
            config=config,
            provider=self.provider,
            cache=self.cache,
            prompt_name=SUFFICIENCY_PROMPT_NAME,
            prompt_version=SUFFICIENCY_PROMPT_VERSION,
            schema_version=SUFFICIENCY_SCHEMA_VERSION,
            retry_builder=retry,
        )
        if assessment.candidate_id != candidate_id:
            raise ValueError("LLM assessment references another candidate")
        if assessment.profile_snapshot_id != profile_snapshot_id:
            raise ValueError("LLM assessment references another profile snapshot")
        allowed_claims = set(profile.get("supporting_claim_ids", []))
        allowed_artifacts = set(active_artifact_ids)
        for gap in assessment.information_gaps:
            if not set(gap.related_claim_ids) <= allowed_claims:
                raise ValueError("LLM gap contains out-of-scope claim references")
            if not set(gap.related_artifact_ids) <= allowed_artifacts:
                raise ValueError("LLM gap contains out-of-scope artifact references")
            if gap.gap_id in skipped_gap_ids:
                gap.status = "skipped"
        return assessment, calls


def _deterministic_gaps(
    profile: dict[str, Any], artifact_ids: list[str]
) -> list[InformationGap]:
    claim_ids = [str(value) for value in profile.get("supporting_claim_ids", [])]
    gaps: list[InformationGap] = []
    if not profile.get("education"):
        gaps.append(
            _gap(
                "gap:education",
                "education",
                "education",
                "Education background is not supported by current evidence.",
                0.65,
                0.85,
                0.8,
                0.15,
                "ask_user",
                claim_ids,
                artifact_ids,
            )
        )
    if not profile.get("experiences"):
        gaps.append(
            _gap(
                "gap:experience",
                "experiences",
                "experience",
                "No project, research, internship or competition experience is documented.",
                0.9,
                0.95,
                0.5,
                0.25,
                "request_more_materials",
                claim_ids,
                artifact_ids,
            )
        )
    if not profile.get("capabilities"):
        gaps.append(
            _gap(
                "gap:capability",
                "capabilities",
                "capability",
                "No capability has a persisted supporting claim.",
                0.8,
                0.9,
                0.65,
                0.2,
                "request_more_materials",
                claim_ids,
                artifact_ids,
            )
        )
    if profile.get("experiences") and not profile.get("responsibility_boundaries"):
        experience_id = str(profile["experiences"][0].get("experience_id", "project"))
        gaps.append(
            _gap(
                f"gap:experience.{experience_id}.responsibility",
                f"experiences[{experience_id}].responsibilities",
                "responsibility_boundary",
                "The candidate's personal responsibility is unclear relative to team output.",
                0.95,
                0.9,
                0.95,
                0.05,
                "ask_user",
                claim_ids,
                artifact_ids,
            )
        )
    for conflict in profile.get("conflicts", []):
        conflict_id = str(conflict.get("conflict_id", "unknown"))
        predicate = str(conflict.get("predicate", "conflicts"))
        gaps.append(
            _gap(
                f"gap:{conflict_id}",
                predicate,
                "conflict",
                "Persisted evidence contains conflicting values that require review.",
                1.0,
                1.0,
                0.8,
                0.1,
                "ask_user",
                [str(value) for value in conflict.get("claim_ids", [])],
                artifact_ids,
            )
        )
    return gaps


def _gap(
    gap_id: str,
    target_path: str,
    category: str,
    description: str,
    importance: float,
    uncertainty: float,
    answerability: float,
    cost: float,
    action: str,
    claim_ids: list[str],
    artifact_ids: list[str],
) -> InformationGap:
    return InformationGap(
        gap_id=gap_id,
        target_path=target_path,
        category=category,
        description=description,
        importance=importance,
        uncertainty=uncertainty,
        answerability=answerability,
        evidence_cost=cost,
        preferred_action=action,
        related_claim_ids=claim_ids,
        related_artifact_ids=artifact_ids,
    )


def _profile_summary(profile: dict[str, Any]) -> dict[str, Any]:
    return {
        key: profile.get(key, [])
        for key in [
            "education",
            "capabilities",
            "experiences",
            "responsibility_boundaries",
            "unknowns",
            "conflicts",
            "evidence_coverage",
            "supporting_claim_ids",
        ]
    }


def _stable_id(prefix: str, payload: dict[str, Any]) -> str:
    canonical = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    )
    return f"{prefix}-{hashlib.sha256(canonical.encode('utf-8')).hexdigest()[:20]}"
