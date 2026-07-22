"""Deterministic evidence coverage assessment for role profiles."""

from __future__ import annotations

import json

from campus_job_agent.llm import LLMCache, LLMProvider, parse_structured_output
from campus_job_agent.prompts.role_profile import ROLE_COVERAGE_PROMPT_VERSION, ROLE_COVERAGE_SYSTEM
from campus_job_agent.schemas import LLMConfig, RoleCoverageAssessment, RoleCoverageGap, SearchScope


class DeterministicRoleCoverageEvaluator:
    name = "deterministic"

    def evaluate(
        self,
        *,
        scope: SearchScope,
        family_profile: dict | None,
        job_count: int,
        company_count: int,
        experience_count: int,
        official_status_count: int,
        ambiguous_identity_count: int,
        has_next_cursor: bool,
        auth_source_id: str | None,
    ) -> RoleCoverageAssessment:
        gaps: list[RoleCoverageGap] = []
        if job_count < 3:
            gaps.append(_gap("job-count", "job_count", "有效去重岗位少于 3 个", "search_more" if has_next_cursor else "change_query", "recruitment_discovery"))
        if company_count < 2:
            gaps.append(_gap("company-diversity", "company_diversity", "岗位样本覆盖企业少于 2 家", "change_source", "recruitment_discovery"))
        if official_status_count < job_count:
            gaps.append(_gap("official-verification", "official_verification", "部分第三方岗位缺少明确官网核验状态", "verify_official", "employer_official"))
        if experience_count < 1:
            gaps.append(_gap("experience", "experience_signal", "尚未获得经验来源信号", "await_user_auth" if auth_source_id else "change_source", "experience"))
        if ambiguous_identity_count:
            gaps.append(_gap("identity", "identity_ambiguity", "存在官网岗位身份模糊", "verify_official", "employer_official"))
        sufficient = not gaps and bool(family_profile) and family_profile.get("sample", {}).get("sample_status") == "sufficient"
        action = "complete" if sufficient else gaps[0].preferred_action if gaps else "finalize_with_unknowns"
        if action == "keep_unknown": action = "finalize_with_unknowns"
        return RoleCoverageAssessment(
            scope_id=scope.scope_id, role_family_profile_snapshot_id=None,
            is_sufficient=sufficient,
            dimension_results={
                "recruitment_fields": "sufficient" if job_count else "insufficient",
                "job_sample": "sufficient" if job_count >= 3 else "partial" if job_count else "insufficient",
                "company_diversity": "sufficient" if company_count >= 2 else "insufficient",
                "source_authority": "sufficient" if official_status_count else "partial",
                "official_verification": "sufficient" if official_status_count >= job_count and job_count else "partial",
                "identity_links": "partial" if ambiguous_identity_count else "sufficient",
                "freshness": "sufficient" if job_count else "unknown",
                "experience_signals": "sufficient" if experience_count >= 2 else "partial" if experience_count else "insufficient",
                "conflicts": "partial" if ambiguous_identity_count else "sufficient",
            },
            coverage_gaps=gaps, recommended_action=action,
            reason="覆盖标准已满足" if sufficient else gaps[0].description if gaps else "无更多高价值来源",
            confidence=1.0,
        )


class LLMRoleCoverageEvaluator:
    name = "llm"

    def __init__(self, config: LLMConfig, provider: LLMProvider, cache: LLMCache) -> None:
        self.config, self.provider, self.cache = config, provider, cache

    def evaluate(self, **kwargs):
        scope = kwargs["scope"]
        payload = {key: value for key, value in kwargs.items() if key != "scope"}
        payload["scope"] = scope.model_dump(mode="json")
        messages = [{"role":"system","content":ROLE_COVERAGE_SYSTEM}, {"role":"user","content":json.dumps(payload, ensure_ascii=False, default=str)}]
        def retry(previous: str, error: str):
            return [messages[0], {"role":"user","content":messages[1]["content"] + f"\nPrevious output invalid: {error}. Return the complete JSON object again."}]
        assessment, calls = parse_structured_output(
            messages=messages, output_model=RoleCoverageAssessment, config=self.config, provider=self.provider, cache=self.cache,
            prompt_name="role_coverage", prompt_version=ROLE_COVERAGE_PROMPT_VERSION, schema_version="v0.5", retry_builder=retry,
        )
        if assessment.scope_id != scope.scope_id: raise ValueError("LLM coverage references another scope")
        return assessment, calls


def _gap(identifier: str, category: str, description: str, action: str, channel: str) -> RoleCoverageGap:
    return RoleCoverageGap(
        gap_id=f"role-gap:{identifier}", category=category, description=description,
        importance=0.8, uncertainty=0.9, retrievability=0.8, collection_cost=0.2,
        preferred_action=action, target_channel=channel,
    )
