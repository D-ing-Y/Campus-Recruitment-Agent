"""Question planners with deterministic validation and fallback."""

from __future__ import annotations

import hashlib
import json
import re

from campus_job_agent.llm import LLMCache, LLMProvider, parse_structured_output
from campus_job_agent.prompts import (
    QUESTION_PROMPT_NAME,
    QUESTION_PROMPT_VERSION,
    QUESTION_SCHEMA_VERSION,
    build_question_planner_messages,
    build_question_planner_retry_messages,
)
from campus_job_agent.schemas import (
    InformationGap,
    LLMCallRecord,
    LLMConfig,
    QuestionItem,
    QuestionPlan,
    SufficiencyAssessment,
)


class DeterministicQuestionPlanner:
    name = "deterministic"

    def plan(
        self,
        assessment: SufficiencyAssessment,
        *,
        max_questions: int,
        asked_question_keys: list[str],
        skipped_gap_ids: list[str],
        remaining_llm_calls: int | None = None,
    ) -> tuple[QuestionPlan, list[LLMCallRecord]]:
        eligible = [
            gap
            for gap in assessment.information_gaps
            if gap.status == "open"
            and gap.preferred_action == "ask_user"
            and gap.answerability > 0
            and gap.gap_id not in skipped_gap_ids
            and question_key(gap.target_path) not in asked_question_keys
        ]
        eligible.sort(key=lambda item: (-item.information_value, item.gap_id))
        plan_id = _plan_id(assessment.assessment_id, eligible[:max_questions])
        questions = [
            QuestionItem(
                question_id=f"question-{hashlib.sha256((plan_id + gap.gap_id).encode()).hexdigest()[:16]}",
                gap_id=gap.gap_id,
                target_path=gap.target_path,
                prompt=_prompt_for_gap(gap),
                reason=gap.description,
                answer_type="free_text",
                required=False,
                related_claim_ids=gap.related_claim_ids,
            )
            for gap in eligible[:max_questions]
        ]
        return (
            QuestionPlan(
                plan_id=plan_id,
                assessment_id=assessment.assessment_id,
                questions=questions,
            ),
            [],
        )


class LLMQuestionPlanner:
    name = "llm"

    def __init__(
        self, config: LLMConfig, provider: LLMProvider, cache: LLMCache
    ) -> None:
        self.config = config
        self.provider = provider
        self.cache = cache

    def plan(
        self,
        assessment: SufficiencyAssessment,
        *,
        max_questions: int,
        asked_question_keys: list[str],
        skipped_gap_ids: list[str],
        remaining_llm_calls: int | None = None,
    ) -> tuple[QuestionPlan, list[LLMCallRecord]]:
        eligible = [
            gap
            for gap in assessment.information_gaps
            if gap.status == "open"
            and gap.gap_id not in skipped_gap_ids
            and question_key(gap.target_path) not in asked_question_keys
        ]
        plan_id = _plan_id(assessment.assessment_id, eligible)
        payload = {
            "plan_id": plan_id,
            "assessment_id": assessment.assessment_id,
            "information_gaps": [
                gap.model_dump(mode="json") for gap in eligible
            ],
            "max_questions": max_questions,
            "asked_question_keys": asked_question_keys,
            "skipped_gap_ids": skipped_gap_ids,
        }

        def retry(previous: str, error: str) -> list[dict[str, str]]:
            return build_question_planner_retry_messages(payload, previous, error)

        config = self.config
        if remaining_llm_calls is not None:
            config = self.config.model_copy(
                update={
                    "max_retries": min(
                        self.config.max_retries,
                        max(0, remaining_llm_calls - 1),
                    )
                }
            )
        plan, calls = parse_structured_output(
            messages=build_question_planner_messages(payload),
            output_model=QuestionPlan,
            config=config,
            provider=self.provider,
            cache=self.cache,
            prompt_name=QUESTION_PROMPT_NAME,
            prompt_version=QUESTION_PROMPT_VERSION,
            schema_version=QUESTION_SCHEMA_VERSION,
            retry_builder=retry,
        )
        _validate_plan(
            plan,
            assessment,
            max_questions=max_questions,
            asked_question_keys=asked_question_keys,
            skipped_gap_ids=skipped_gap_ids,
        )
        return plan, calls


def _validate_plan(
    plan: QuestionPlan,
    assessment: SufficiencyAssessment,
    *,
    max_questions: int,
    asked_question_keys: list[str],
    skipped_gap_ids: list[str],
) -> None:
    if plan.assessment_id != assessment.assessment_id:
        raise ValueError("question plan references another assessment")
    if len(plan.questions) > max_questions:
        raise ValueError("question plan exceeds max_questions_per_interrupt")
    gaps = {item.gap_id: item for item in assessment.information_gaps}
    seen: set[str] = set()
    for question in plan.questions:
        gap = gaps.get(question.gap_id)
        if gap is None or gap.status != "open":
            raise ValueError("question must bind an open information gap")
        if question.target_path != gap.target_path:
            raise ValueError("question target differs from its information gap")
        key = question_key(question.target_path)
        if key in asked_question_keys or key in seen:
            raise ValueError("question is redundant")
        if gap.gap_id in skipped_gap_ids:
            raise ValueError("question targets a skipped gap")
        seen.add(key)


def question_key(target_path: str) -> str:
    return re.sub(r"[^a-z0-9]+", ".", target_path.lower()).strip(".")


def _prompt_for_gap(gap: InformationGap) -> str:
    if gap.category == "responsibility_boundary":
        return (
            "在该经历中，你本人具体负责或实现了哪些部分？"
            "请区分个人贡献与团队整体成果。"
        )
    if gap.category == "education":
        return "请说明你的院校、专业、学位及预计毕业年份；不确定的部分可以跳过。"
    if gap.category == "conflict":
        return "现有材料对该字段表述不一致。请说明你认可的当前事实及需要纠正的旧表述。"
    return f"请补充与“{gap.description}”直接相关、可由你确认的信息；可以跳过。"


def _plan_id(assessment_id: str, gaps: list[InformationGap]) -> str:
    payload = json.dumps(
        [assessment_id, [gap.gap_id for gap in gaps]],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return f"question-plan-{hashlib.sha256(payload.encode()).hexdigest()[:20]}"
