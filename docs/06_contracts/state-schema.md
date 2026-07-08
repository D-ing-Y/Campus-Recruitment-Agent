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
