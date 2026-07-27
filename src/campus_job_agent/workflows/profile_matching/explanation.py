"""Validated, read-only explanations over deterministic comparison facts."""

from __future__ import annotations

import json
import re
from typing import Any, Protocol

from campus_job_agent.schemas import (
    ComparisonSet,
    GapAssessment,
    JobMatchExplanation,
    MatchExplanation,
)
from campus_job_agent.schemas.matching import canonical_hash


ALLOWED_ACTIONS = {
    "review", "provide_candidate_evidence", "keep_unknown", "revise_candidate",
    "revise_intent", "refresh_role", "select_target", "defer_target", "reject_target",
}
FORBIDDEN_CLAIMS = (
    "offer概率", "录取概率", "面试通过率", "上岸概率", "成功率", "offer probability",
    "admission probability", "interview pass rate", "综合匹配率", "匹配率",
)


class ExplanationProvider(Protocol):
    def explain(self, payload: dict[str, Any]) -> tuple[MatchExplanation, list[Any]]: ...


class LLMMatchExplanationProvider:
    """Versioned structured-output adapter; it has no repository write access."""

    def __init__(self, *, config: Any, provider: Any, cache: Any) -> None:
        self.config, self.provider, self.cache = config, provider, cache

    def explain(self, payload: dict[str, Any]) -> tuple[MatchExplanation, list[Any]]:
        from campus_job_agent.llm.structured import parse_structured_output
        from campus_job_agent.prompts.profile_matching import (
            PROMPT_NAME, PROMPT_VERSION, SCHEMA_VERSION, build_match_explanation_messages,
        )

        request_payload = {
            **payload,
            "output_contract": {
                "explanation_id": "string",
                "schema_version": "v0.6",
                "comparison_set_id": payload["comparison_set_id"],
                "job_explanations": [{
                    "job_profile_id": "must be an entry job id", "summary": "string",
                    "fact_ids": ["existing fact id"], "claim_ids": [],
                    "suggested_actions": ["allowed action"],
                }],
                "warnings": ["coverage_is_not_offer_probability"],
                "prompt_version": "match_explanation_v1",
            },
        }

        def retry(previous: str, error: str) -> list[dict[str, str]]:
            retry_payload = {**request_payload, "previous_invalid_output": previous, "validation_error": error}
            return build_match_explanation_messages(retry_payload)

        return parse_structured_output(
            messages=build_match_explanation_messages(request_payload),
            output_model=MatchExplanation,
            config=self.config,
            provider=self.provider,
            cache=self.cache,
            prompt_name=PROMPT_NAME,
            prompt_version=PROMPT_VERSION,
            schema_version=SCHEMA_VERSION,
            retry_builder=retry,
        )


def deterministic_explanation(
    comparison: ComparisonSet,
    assessments: dict[str, GapAssessment],
) -> MatchExplanation:
    explanations: list[JobMatchExplanation] = []
    for entry in comparison.entries:
        assessment = assessments[entry.gap_assessment_id]
        core = assessment.core_coverage or {}
        summary = (
            f"硬性资格状态为{assessment.hard_constraint_status or 'unknown'}；"
            f"已知核心要求证据覆盖权重为{core.get('covered_weight', 0)}/"
            f"{core.get('eligible_weight', 0)}，未知权重为{core.get('uncertain_weight', 0)}。"
        )
        fact_ids = [
            f"fact:{assessment.assessment_id}:hard",
            f"fact:{assessment.assessment_id}:core",
        ]
        claim_ids = list(dict.fromkeys(assessment.supporting_claim_ids))
        actions = ["review"]
        if any(item.gap_type == "evidence_gap" for item in assessment.gaps):
            actions.append("provide_candidate_evidence")
        if any(item.gap_type == "epistemic_uncertainty" for item in assessment.gaps):
            actions.append("keep_unknown")
        explanations.append(
            JobMatchExplanation(
                job_profile_id=entry.job_instance_profile_snapshot_id,
                summary=summary,
                fact_ids=fact_ids,
                claim_ids=claim_ids,
                suggested_actions=actions,
            )
        )
    digest = canonical_hash("match-explanation", [comparison.comparison_set_id, [item.model_dump(mode="json") for item in explanations]])
    return MatchExplanation(
        explanation_id=f"explanation:{digest[7:31]}",
        comparison_set_id=comparison.comparison_set_id,
        job_explanations=explanations,
    )


def validate_explanation(
    explanation: MatchExplanation,
    *,
    comparison: ComparisonSet,
    assessments: dict[str, GapAssessment],
) -> None:
    if explanation.comparison_set_id != comparison.comparison_set_id:
        raise ValueError("invalid_fact_reference: comparison_set_id")
    fact_index = {
        key: value
        for assessment in assessments.values()
        for key, value in assessment.fact_index.items()
    }
    allowed_claims = {
        claim_id
        for assessment in assessments.values()
        for claim_id in assessment.supporting_claim_ids
    }
    allowed_jobs = {entry.job_instance_profile_snapshot_id for entry in comparison.entries}
    for item in explanation.job_explanations:
        if item.job_profile_id not in allowed_jobs:
            raise ValueError("invalid_fact_reference: job")
        if not set(item.fact_ids).issubset(fact_index):
            raise ValueError("invalid_fact_reference: fact")
        if not set(item.claim_ids).issubset(allowed_claims):
            raise ValueError("invalid_fact_reference: claim")
        if not set(item.suggested_actions).issubset(ALLOWED_ACTIONS):
            raise ValueError("invalid_fact_reference: action")
        lowered = item.summary.casefold()
        if any(term in lowered for term in FORBIDDEN_CLAIMS):
            raise ValueError("llm_fact_mutation: probability claim")
        supported_blob = json.dumps(
            [fact_index[fact_id] for fact_id in item.fact_ids],
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
        for number in re.findall(r"(?<![\w:])\d+(?:\.\d+)?", item.summary):
            normalized = str(float(number)).rstrip("0").rstrip(".") if "." in number else str(int(number))
            if number not in supported_blob and normalized not in supported_blob:
                raise ValueError(f"llm_fact_mutation: unsupported number {number}")
    if "coverage_is_not_offer_probability" not in explanation.warnings:
        raise ValueError("llm_fact_mutation: missing coverage warning")


def explain_with_fallback(
    comparison: ComparisonSet,
    assessments: dict[str, GapAssessment],
    provider: ExplanationProvider | None,
) -> tuple[MatchExplanation, list[Any], str | None]:
    fallback = deterministic_explanation(comparison, assessments)
    if provider is None:
        return fallback, [], None
    payload = {
        "comparison_set_id": comparison.comparison_set_id,
        "entries": [entry.model_dump(mode="json") for entry in comparison.entries],
        "fact_index": {key: value for assessment in assessments.values() for key, value in assessment.fact_index.items()},
        "allowed_actions": sorted(ALLOWED_ACTIONS),
        "warnings": ["coverage_is_not_offer_probability"],
    }
    try:
        output, calls = provider.explain(payload)
        validate_explanation(output, comparison=comparison, assessments=assessments)
        return output, calls, None
    except Exception as exc:
        return fallback, getattr(exc, "call_records", []), str(exc)


__all__ = [
    "ALLOWED_ACTIONS", "ExplanationProvider", "LLMMatchExplanationProvider", "deterministic_explanation",
    "explain_with_fallback", "validate_explanation",
]
