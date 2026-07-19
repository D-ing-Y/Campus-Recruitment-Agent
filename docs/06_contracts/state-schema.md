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
- v0.3 不接入 Graph；v0.4 使用下述独立 CandidateProfileGraphState，v1.0 再映射到 ParentState。

## v0.4 CandidateProfileGraphState

实现状态：v0.4 已实现。

关联：

- `docs/03_requirements/v0.4-candidate-profile-graph.md`
- `docs/04_rfc/0004-candidate-profile-graph.md`
- `docs/06_contracts/human-interaction-contract.md`

```python
class CandidateProfileGraphState(TypedDict, total=False):
    # identity and lifecycle
    run_id: str
    thread_id: str
    user_id: str
    candidate_id: str
    status: str
    allowed_path_roots: list[str]

    # submitted and persisted evidence references
    input_paths: list[str]
    pending_artifact_ids: list[str]
    active_artifact_ids: list[str]
    processed_artifact_ids: list[str]
    unsupported_artifact_ids: list[str]
    fragment_ids: list[str]
    processed_fragment_ids: list[str]
    claim_ids: list[str]

    # derived profile references and decisions
    candidate_profile_snapshot_id: str | None
    sufficiency_assessment: dict | None
    information_gaps: list[dict]
    question_plan: dict | None
    next_action: str | None

    # human-in-the-loop
    pending_interaction: dict | None
    resume_input: dict | None
    processed_response_ids: list[str]
    skipped_gap_ids: list[str]
    asked_question_keys: list[str]
    last_human_action: str | None

    # controls and observability
    budgets: dict
    counters: dict
    tool_results: list[dict]
    llm_calls: list[dict]
    trace: list[dict]
    errors: list[dict]
    report: dict | None
```

### 状态枚举

`status`：

```text
initialized
running
interrupted
completed
completed_with_unknowns
cancelled
failed
```

`next_action`：

```text
read_more
ask_user
request_more_materials
finalize_with_unknowns
complete
fail
```

### Reducer 规则

| 字段 | 合并方式 | 说明 |
| --- | --- | --- |
| `trace`、`errors`、`llm_calls`、`tool_results` | append | 节点只返回本次新增项，由 reducer 追加 |
| Artifact/Fragment/Claim/response ID 列表 | stable union | 去重并保持首次出现顺序 |
| `input_paths` | replace after consume | 摄取完成后清除已消费路径 |
| `sufficiency_assessment` | replace | 只保留最新评估；历史进入 trace/eval |
| `information_gaps` | replace | 每次基于最新 profile 重算 |
| `question_plan`、`pending_interaction` | replace/clear | 一个 thread 同时最多一个 pending request |
| `resume_input` | replace then clear | 证据化成功后必须清除正文 |
| `candidate_profile_snapshot_id` | replace | 旧版本保存在 ProfileRepository |
| `allowed_path_roots` | initialize once | 调用方授权的本地读取边界，resume 不得扩大 |
| `report` | replace | 仅保存最终画像版本、覆盖率、unknown/conflict 和引用计数摘要 |
| `budgets` | initialize once | 节点不得提高预算 |
| `counters` | deterministic increment | 只允许增加或由初始化恢复 |

### BudgetState

```json
{
  "max_profile_rounds": 3,
  "max_questions_per_interrupt": 3,
  "max_llm_calls": 12,
  "max_tool_calls": 30
}
```

### CounterState

```json
{
  "profile_rounds": 0,
  "interaction_rounds": 0,
  "llm_calls": 0,
  "tool_calls": 0
}
```

默认值是设计基线，可由配置降低；运行中的节点不得自行提高。任何硬预算耗尽后只能
`finalize_with_unknowns` 或 `fail`。

### Checkpoint 与恢复

- checkpointer 在 Graph compile 时注入，节点不得自行创建。
- invoke/resume 必须传 `configurable.thread_id`，且与 State 的 `thread_id` 一致。
- `pending_interaction` 保存 request 摘要；完整材料和长期事实仍保存在 Evidence Store。
- `resume_input` 仅用于恢复边界，`archive_human_input` 写入证据成功后必须清除。
- 相同 response 的重放通过 `processed_response_ids` 和 Evidence Store 幂等键去重。
- checkpoint 序列化失败必须使 run 失败，不得声称任务可恢复。

## v0.5 RoleProfileGraphState

实现状态：Design Accepted / Pending Implementation。

关联：

- `docs/03_requirements/v0.5-role-profile-graph.md`
- `docs/04_rfc/0005-role-profile-graph.md`
- `docs/06_contracts/source-collection-contract.md`
- `docs/06_contracts/role-profile-contract.md`

```python
class RoleProfileGraphState(TypedDict, total=False):
    # identity and lifecycle
    run_id: str
    thread_id: str
    user_id: str
    status: str

    # immutable scope
    career_intent_snapshot_id: str | None
    search_scope: dict

    # query and source execution
    query_plan: dict | None
    pending_queries: list[dict]
    completed_query_ids: list[str]
    query_history: list[dict]
    enabled_source_ids: list[str]
    skipped_source_ids: list[str]
    source_capabilities: dict[str, dict]
    pending_auth_source_id: str | None
    credential_refs: dict[str, str]
    source_batch_ids: list[str]
    source_run_receipts: list[dict]

    # evidence and normalized records
    raw_artifact_ids: list[str]
    extraction_ids: list[str]
    fragment_ids: list[str]
    normalized_job_ids: list[str]
    experience_record_ids: list[str]
    job_cluster_ids: list[str]
    claim_ids: list[str]

    # derived profiles and decisions
    job_instance_profile_snapshot_ids: list[str]
    role_family_profile_snapshot_id: str | None
    coverage_assessment: dict | None
    coverage_gaps: list[dict]
    next_action: str | None

    # human authorization
    pending_interaction: dict | None
    resume_input: dict | None

    # controls and observability
    budgets: dict
    counters: dict
    tool_results: list[dict]
    llm_calls: list[dict]
    trace: list[dict]
    errors: list[dict]
    report: dict | None
```

### 状态枚举

`status`：

```text
initialized
running
interrupted
completed
completed_with_unknowns
cancelled
failed
```

`next_action`：

```text
search_more
change_query
change_source
await_user_auth
finalize_with_unknowns
complete
fail
```

### Reducer

| 字段 | 合并方式 | 说明 |
| --- | --- | --- |
| `trace`、`errors`、`llm_calls`、`tool_results`、`source_run_receipts` | append | 保存每轮增量摘要 |
| Artifact/Extraction/Fragment/Record/Cluster/Claim/Snapshot ID | stable union | 去重并保持首次顺序 |
| `search_scope`、`budgets`、`enabled_source_ids` | initialize once | 节点不得扩大 scope、预算或来源权限 |
| `pending_queries` | replace after consume | 完成/失败的 query 写入 history |
| `completed_query_ids`、`skipped_source_ids` | stable union | 防止重复查询/授权 |
| `credential_refs` | merge by source ID | 只保存非敏感引用 |
| `query_plan`、`coverage_assessment`、`coverage_gaps`、`next_action` | replace | 只保留当前决策 |
| `pending_interaction`、`resume_input`、`pending_auth_source_id` | replace/clear | 同时最多等待一个来源授权 |
| `role_family_profile_snapshot_id`、`report` | replace | 历史版本留在 repository |
| `counters` | deterministic increment | 不能由 LLM 修改 |

### RoleSearchBudget

```json
{
  "max_query_rounds": 3,
  "max_queries": 12,
  "max_source_switches": 2,
  "max_documents": 60,
  "max_llm_calls": 20,
  "max_tool_calls": 50
}
```

### RoleSearchCounter

```json
{
  "query_rounds": 0,
  "queries": 0,
  "source_switches": 0,
  "documents": 0,
  "llm_calls": 0,
  "tool_calls": 0
}
```

预算耗尽后只能 `finalize_with_unknowns` 或 `fail`。

### 授权与 Checkpoint

- `credential_refs` 只保存 source→ref，不保存 Cookie、Authorization 或 cURL。
- `resume_input` 只允许 `authorized`、`skip_source`、`cancel` 及相关 ID。
- 用户在 Graph 外正常登录和导入凭据；Graph 恢复时只验证 ref。
- checkpointer 在 compile 时注入，Evidence/Source Repository 与 checkpoint 分库。
- source batch、query 和授权节点均需幂等，以支持 checkpoint 重放。

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
