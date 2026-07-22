"""Deterministic query planning baseline for the role graph."""

from __future__ import annotations

from uuid import NAMESPACE_URL, uuid5

from campus_job_agent.llm import LLMCache, LLMProvider, parse_structured_output
from campus_job_agent.prompts.role_profile import ROLE_QUERY_PROMPT_VERSION, ROLE_QUERY_SYSTEM
from campus_job_agent.schemas import LLMConfig, RoleQueryPlan, SearchScope, SourceCapabilities, SourceQuery


class DeterministicRoleQueryPlanner:
    name = "deterministic"

    def plan(
        self,
        scope: SearchScope,
        capabilities: dict[str, dict],
        *,
        completed_fingerprints: set[str],
        next_cursors: dict[str, str] | None = None,
        preferred_action: str | None = None,
    ) -> RoleQueryPlan:
        queries: list[SourceQuery] = []
        for source_id, raw in sorted(capabilities.items()):
            capability = SourceCapabilities.model_validate(raw)
            if not capability.live_enabled and not source_id.startswith("fixture_"):
                continue
            if capability.channel not in {"recruitment_discovery", "experience"}:
                continue
            cursor = (next_cursors or {}).get(source_id)
            reason = "pagination" if cursor else "source_fallback" if preferred_action == "change_source" else "synonym_expansion" if preferred_action == "change_query" else "initial_scope"
            keywords = list(scope.target_role_queries)
            if preferred_action == "change_query" and scope.target_role_family == "ai_agent_engineering":
                keywords = [*keywords, "LLM应用开发", "智能体开发"]
            query = SourceQuery(
                query_id=str(uuid5(NAMESPACE_URL, f"role-query:{scope.scope_id}:{source_id}:{cursor or reason}:{'|'.join(keywords)}")),
                channel=capability.channel, source_id=source_id, keywords=keywords,
                location=scope.locations[0] if scope.locations and capability.supports_location else None,
                company=scope.companies[0] if scope.companies and capability.supports_company else None,
                role_family=scope.target_role_family, graduation_year=scope.graduation_year,
                recruitment_type=scope.recruitment_type, cursor=cursor, change_reason=reason,
            )
            if query.fingerprint not in completed_fingerprints:
                queries.append(query)
        return RoleQueryPlan(
            plan_id=str(uuid5(NAMESPACE_URL, f"role-plan:{scope.scope_id}:{','.join(item.fingerprint for item in queries)}")),
            scope_id=scope.scope_id, queries=queries,
        )


class LLMRoleQueryPlanner:
    name = "llm"

    def __init__(self, config: LLMConfig, provider: LLMProvider, cache: LLMCache) -> None:
        self.config, self.provider, self.cache = config, provider, cache

    def plan(self, scope: SearchScope, capabilities: dict[str, dict], *, completed_fingerprints: set[str],
             next_cursors: dict[str, str] | None = None, preferred_action: str | None = None):
        payload = {"scope": scope.model_dump(mode="json"), "source_capabilities": capabilities,
                   "completed_fingerprints": sorted(completed_fingerprints), "next_cursors": next_cursors or {},
                   "preferred_action": preferred_action}
        messages = [{"role":"system","content":ROLE_QUERY_SYSTEM}, {"role":"user","content":__import__("json").dumps(payload, ensure_ascii=False)}]
        def retry(previous: str, error: str):
            return [messages[0], {"role":"user","content":messages[1]["content"] + f"\nPrevious output invalid: {error}. Return the complete JSON object again."}]
        plan, calls = parse_structured_output(
            messages=messages, output_model=RoleQueryPlan, config=self.config, provider=self.provider, cache=self.cache,
            prompt_name="role_query_planner", prompt_version=ROLE_QUERY_PROMPT_VERSION, schema_version="v0.5", retry_builder=retry,
        )
        if plan.scope_id != scope.scope_id: raise ValueError("LLM query plan references another scope")
        allowed = set(capabilities)
        for query in plan.queries:
            if query.source_id not in allowed or query.fingerprint in completed_fingerprints: raise ValueError("LLM query violates source or idempotency policy")
            if query.role_family != scope.target_role_family or query.graduation_year != scope.graduation_year or query.recruitment_type != scope.recruitment_type:
                raise ValueError("LLM query expands immutable hard scope")
        return plan, calls
