"""Deterministic final authority for candidate-profile routing."""

from __future__ import annotations

from campus_job_agent.schemas import BudgetState, CounterState, SufficiencyAssessment


class CandidateRoutePolicy:
    """Validate evaluator suggestions against evidence availability and budgets."""

    def decide(
        self,
        *,
        assessment: SufficiencyAssessment,
        budgets: BudgetState,
        counters: CounterState,
        pending_artifact_ids: list[str],
        skipped_gap_ids: list[str],
        asked_question_keys: list[str],
        has_fatal_error: bool = False,
    ) -> str:
        if has_fatal_error:
            return "fail"
        if (
            counters.profile_rounds >= budgets.max_profile_rounds
            or counters.llm_calls >= budgets.max_llm_calls
            or counters.tool_calls >= budgets.max_tool_calls
        ):
            return "finalize_with_unknowns"
        open_gaps = [
            gap
            for gap in assessment.information_gaps
            if gap.status == "open" and gap.gap_id not in skipped_gap_ids
        ]
        if pending_artifact_ids:
            return "read_more"
        if assessment.blocking_conflict_ids:
            return (
                "ask_user"
                if any(gap.answerability > 0 for gap in open_gaps)
                else "finalize_with_unknowns"
            )
        suggested = assessment.recommended_action
        if suggested == "complete":
            return "complete" if assessment.is_sufficient and not open_gaps else "finalize_with_unknowns"
        if suggested == "read_more":
            return "read_more" if pending_artifact_ids else _fallback(open_gaps)
        if suggested == "ask_user":
            answerable = [
                gap
                for gap in open_gaps
                if gap.answerability > 0
                and gap.preferred_action == "ask_user"
            ]
            return "ask_user" if answerable else _fallback(open_gaps)
        if suggested == "request_more_materials":
            return "request_more_materials" if open_gaps else "complete"
        if suggested in {"finalize_with_unknowns", "fail"}:
            return suggested
        return _fallback(open_gaps)


def _fallback(open_gaps: list) -> str:
    if not open_gaps:
        return "complete"
    if any(
        gap.answerability > 0 and gap.preferred_action == "ask_user"
        for gap in open_gaps
    ):
        return "ask_user"
    if any(gap.preferred_action == "request_more_materials" for gap in open_gaps):
        return "request_more_materials"
    return "finalize_with_unknowns"
