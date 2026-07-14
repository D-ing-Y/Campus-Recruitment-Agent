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
