# State Schema

本文件用于维护 AgentState 的跨模块契约。

## v0.1 AgentState

关联：

- `docs/03_requirements/v0.1-mini-runtime.md`
- `docs/04_rfc/0001-mini-agent-runtime.md`

v0.1 使用 `TypedDict` 作为 LangGraph 状态类型，使用 Pydantic model 校验核心结构化对象。

```python
class AgentState(TypedDict, total=False):
    run_id: str
    user_input: str
    parsed_goal: dict
    plan: list[dict]
    tool_results: list[dict]
    verification: dict
    trace: list[dict]
    errors: list[dict]
    report_path: str | None
    output_dir: str
```

## v0.1 核心对象

- `ParsedGoal`
- `PlanTask`
- `ToolResult`
- `TraceEvent`
- `VerificationResult`

详细字段以 RFC-0001 为准。

## v0.2 AgentState 扩展

关联：

- `docs/03_requirements/v0.2-llm-provider.md`
- `docs/04_rfc/0002-llm-provider-and-structured-output.md`

v0.2 不改变 v0.1 主流程拓扑，只在 `parse_goal` 节点接入 LLM 结构化解析。

```python
class AgentState(TypedDict, total=False):
    run_id: str
    user_input: str
    parsed_goal: dict
    plan: list[dict]
    tool_results: list[dict]
    verification: dict
    trace: list[dict]
    llm_calls: list[dict]
    errors: list[dict]
    report_path: str | None
    output_dir: str
```

v0.2 中：

- `parsed_goal` 保存 `SearchGoal.model_dump()`。
- `parsed_goal` 继续包含 v0.1 需要的 `role_query`、`city`、`graduation_year`、`raw_text`，因此 `plan_tasks` 和 `mock_job_search` 保持兼容。
- `llm_calls` 保存非敏感 LLM 调用摘要。
- API key、Authorization header 和完整环境变量不得进入 state、trace、cache、report 或测试快照。

## v0.3 证据管线 State 扩展

v0.3 不删除 v0.2 字段。证据管线可以使用独立子状态或在测试入口中增加以下引用字段：

```python
class EvidencePipelineState(TypedDict, total=False):
    run_id: str
    owner_id: str
    input_paths: list[str]
    artifact_ids: list[str]
    fragment_ids: list[str]
    claim_ids: list[str]
    profile_snapshot_ids: list[str]
    llm_calls: list[dict]
    verification: dict
    trace: list[dict]
    errors: list[dict]
    budgets: dict
```

约束：

- State 保存 ID、摘要和决策，不保存二进制文件或完整长文本。
- Artifact、Fragment、Claim 和 Profile 的完整内容通过 repository 读取。
- list 字段的追加/覆盖策略必须显式定义，避免 LangGraph 合并歧义。
- v0.4 设计 ParentState 时再将候选人画像 subgraph 接入主图。

## v1.0 ParentState 目标形态

```python
class RecruitmentAgentState(TypedDict, total=False):
    run_id: str
    thread_id: str
    user_id: str
    current_stage: str
    career_intent: dict
    candidate_profile_ref: str | None
    role_profile_refs: list[str]
    active_artifact_ids: list[str]
    active_claim_ids: list[str]
    unresolved_questions: list[dict]
    gap_assessments: list[dict]
    selected_target_ids: list[str]
    learning_plan_ref: str | None
    feedback_event_ids: list[str]
    next_action: dict
    budgets: dict
    llm_calls: list[dict]
    trace: list[dict]
    errors: list[dict]
```
