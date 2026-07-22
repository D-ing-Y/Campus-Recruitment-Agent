"""Deterministic route and budget policy for v0.5."""

from campus_job_agent.schemas import RoleCoverageAssessment, RoleSearchBudget, RoleSearchCounter


class RoleRoutePolicy:
    def decide(
        self,
        *,
        assessment: RoleCoverageAssessment,
        budgets: RoleSearchBudget,
        counters: RoleSearchCounter,
        has_fatal_error: bool,
        pending_auth_source_id: str | None,
        has_official_plans: bool,
        has_next_cursor: bool,
    ) -> str:
        if has_fatal_error:
            return "fail"
        exhausted = (
            counters.query_rounds >= budgets.max_query_rounds
            or counters.queries >= budgets.max_queries
            or counters.documents >= budgets.max_documents
            or counters.tool_calls >= budgets.max_tool_calls
        )
        if exhausted:
            return "finalize_with_unknowns"
        if pending_auth_source_id:
            return "await_user_auth"
        if assessment.is_sufficient:
            return "complete"
        recommendation = assessment.recommended_action
        if recommendation == "verify_official" and not has_official_plans:
            return "finalize_with_unknowns"
        if recommendation == "search_more" and not has_next_cursor:
            return "change_query" if counters.query_rounds + 1 < budgets.max_query_rounds else "finalize_with_unknowns"
        if recommendation == "change_source" and counters.source_switches >= budgets.max_source_switches:
            return "finalize_with_unknowns"
        return recommendation
