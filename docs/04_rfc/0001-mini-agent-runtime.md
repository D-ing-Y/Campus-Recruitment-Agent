# RFC-0001: Mini Agent Runtime

状态：Accepted
日期：2026-07-07
关联需求：`docs/03_requirements/v0.1-mini-runtime.md`

## 1. 背景

v0.1 需要交付一个可运行、可测试、可扩展的 Mini Agent Runtime。该 Runtime 不接入真实招聘网站和 LLM API，只验证 Agent 项目的核心执行骨架：状态、图编排、工具调用、结果校验、trace 和报告输出。

## 2. 目标

本 RFC 设计 v0.1 的实现边界：

- 定义 `AgentState` 的实现方式。
- 定义 LangGraph 工作流节点和边。
- 定义 `ToolRegistry` 和 `mock_job_search` 的调用方式。
- 定义 trace 记录方式。
- 定义本地输出文件结构。
- 定义错误处理、测试和 eval 范围。

## 3. 非目标

本 RFC 不设计：

- 真实招聘网站工具。
- LLM Provider。
- Evidence Store 完整持久化。
- RAG / Memory。
- 多 Agent / Sub-Agent。
- Web UI。
- 云服务器部署。
- LangGraph checkpoint / interrupt。

## 4. 总体设计

v0.1 Runtime 采用单向 LangGraph 流程：

```text
START
  -> parse_goal
  -> plan_tasks
  -> run_mock_tool
  -> verify_result
  -> write_report
  -> END
```

节点只通过 `AgentState` 传递数据。节点不得通过全局变量共享运行时状态。

`run_mock_tool` 不直接调用工具函数，必须通过 `ToolRegistry` 按工具名调用。

## 5. 模块划分

建议实现文件：

```text
src/campus_job_agent/agent/state.py
src/campus_job_agent/agent/graph.py
src/campus_job_agent/agent/planner.py
src/campus_job_agent/agent/executor.py
src/campus_job_agent/agent/verifier.py
src/campus_job_agent/agent/report_writer.py
src/campus_job_agent/tools/base.py
src/campus_job_agent/tools/registry.py
src/campus_job_agent/tools/mock.py
src/campus_job_agent/schemas/goal.py
src/campus_job_agent/schemas/tool.py
src/campus_job_agent/schemas/trace.py
apps/cli/main.py
```

职责边界：

| 模块 | 职责 |
| --- | --- |
| `state.py` | 定义 `AgentState` 与运行初始化 |
| `graph.py` | 构建 LangGraph workflow |
| `planner.py` | 实现 goal parsing 和 plan generation |
| `executor.py` | 执行 plan 中的工具任务 |
| `verifier.py` | 校验状态和工具结果 |
| `report_writer.py` | 输出 state、trace 和 Markdown report |
| `tools/base.py` | 定义工具接口 |
| `tools/registry.py` | 注册和查找工具 |
| `tools/mock.py` | 实现 `mock_job_search` |
| `schemas/*` | 定义跨模块数据结构 |
| `apps/cli/main.py` | CLI 入口 |

## 6. AgentState 设计

v0.1 使用 `TypedDict` 作为 LangGraph 的状态类型，使用 Pydantic model 定义核心数据对象。

原因：

- LangGraph 与 `TypedDict` 状态配合直接。
- Pydantic 适合校验 `ParsedGoal`、`PlanTask`、`ToolResult`、`TraceEvent` 等结构。
- 避免把整个可变运行状态强行建成一个大型 Pydantic model。

`AgentState` 字段：

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

初始化要求：

- `run_id` 使用 UUID 或时间戳生成。
- `user_input` 来自 CLI。
- `trace`、`errors`、`plan`、`tool_results` 初始化为空列表。
- `output_dir` 默认为 `data/runs/<run_id>`。

## 7. Schema 设计

v0.1 需要定义以下 Pydantic schema：

### 7.1 `ParsedGoal`

```python
class ParsedGoal(BaseModel):
    role_query: str
    city: str
    graduation_year: str
    raw_text: str
```

无法解析的字段填入 `"unknown"`。

### 7.2 `PlanTask`

```python
class PlanTask(BaseModel):
    task_id: str
    tool_name: str
    args: dict[str, Any]
    reason: str
```

### 7.3 `ToolResult`

```python
class ToolResult(BaseModel):
    tool_name: str
    status: Literal["success", "failed"]
    records: list[dict[str, Any]]
    evidence_ids: list[str]
    error: str | None = None
    metadata: dict[str, Any] = {}
```

### 7.4 `TraceEvent`

```python
class TraceEvent(BaseModel):
    node: str
    status: Literal["success", "failed"]
    started_at: str
    ended_at: str
    duration_ms: int
    input_summary: dict[str, Any]
    output_summary: dict[str, Any]
    error: str | None = None
```

### 7.5 `VerificationResult`

```python
class VerificationResult(BaseModel):
    passed: bool
    checks: dict[str, bool]
    messages: list[str]
```

## 8. LangGraph 节点设计

每个节点函数接收 `AgentState`，返回局部状态更新。

### 8.1 `parse_goal`

职责：

- 从 `user_input` 中提取 `role_query`、`city`、`graduation_year`。
- v0.1 使用规则解析，不调用 LLM。

规则：

- 若文本包含 `成都`，`city = "成都"`，否则 `unknown`。
- 若文本包含 `AI Agent`，`role_query = "AI Agent"`；若包含 `智能体`，`role_query = "智能体"`；否则 `unknown`。
- 若文本包含 `2027`，`graduation_year = "2027"`，否则 `unknown`。

输出：

- `parsed_goal`
- 追加 `trace`

### 8.2 `plan_tasks`

职责：

- 根据 `parsed_goal` 生成工具调用计划。

规则：

- 固定生成一个任务，调用 `mock_job_search`。
- task args 来自 `parsed_goal`。

输出：

- `plan`
- 追加 `trace`

### 8.3 `run_mock_tool`

职责：

- 遍历 `plan`。
- 使用 `ToolRegistry` 按 `tool_name` 调用工具。
- 收集 `ToolResult`。

规则：

- 工具不存在时返回 failed `ToolResult`。
- 单个工具失败不阻止流程进入 `verify_result`。

输出：

- `tool_results`
- `errors`
- 追加 `trace`

### 8.4 `verify_result`

职责：

- 校验 `parsed_goal`、`plan`、`tool_results`。

检查项：

- `role_query_present`
- `plan_non_empty`
- `tool_results_non_empty`
- `tool_result_fields_valid`

输出：

- `verification`
- `errors`
- 追加 `trace`

### 8.5 `write_report`

职责：

- 创建输出目录。
- 写入 `state.json`。
- 写入 `trace.json`。
- 写入 Markdown report。

输出：

- `report_path`
- 追加 `trace`

## 9. ToolRegistry 设计

`ToolRegistry` 提供：

```python
class ToolRegistry:
    def register(self, tool: Tool) -> None: ...
    def get(self, tool_name: str) -> Tool: ...
    def run(self, tool_name: str, args: dict[str, Any]) -> ToolResult: ...
```

`Tool` 协议：

```python
class Tool(Protocol):
    name: str

    def run(self, args: dict[str, Any]) -> ToolResult:
        ...
```

v0.1 默认注册：

```text
mock_job_search
```

## 10. Mock Tool 设计

`mock_job_search` 固定返回一组 mock records。

输入字段：

- `role_query`
- `city`
- `graduation_year`

输出至少 2 条岗位记录：

- 一条匹配成都 AI Agent。
- 一条包含部分噪声，用于后续验证报告摘要不要求完美筛选。

v0.1 不做真实过滤，只保留结构化结果。

## 11. Trace 设计

每个节点通过统一 helper 追加 trace。

建议 helper：

```python
def run_node_with_trace(
    node_name: str,
    state: AgentState,
    fn: Callable[[AgentState], dict[str, Any]],
) -> dict[str, Any]:
    ...
```

要求：

- 记录开始时间、结束时间、耗时。
- 捕获异常并写入 trace。
- 生成输入摘要和输出摘要。
- 异常继续抛出或转为 `errors`，由节点策略决定。

v0.1 可采用简单实现，不要求 decorator。

## 12. 错误处理设计

错误结构：

```python
class RuntimeErrorRecord(BaseModel):
    node: str
    message: str
    recoverable: bool = True
```

策略：

- `parse_goal` 失败：流程失败，CLI 返回错误。
- `plan_tasks` 失败：流程失败，CLI 返回错误。
- `run_mock_tool` 单个工具失败：记录错误，继续进入校验。
- `verify_result` 不通过：继续生成报告，但报告标记失败。
- `write_report` 失败：流程失败，CLI 返回错误。

## 13. 文件输出设计

每次运行输出：

```text
data/runs/<run_id>/state.json
data/runs/<run_id>/trace.json
data/reports/<run_id>.md
```

JSON 文件要求：

- UTF-8。
- `ensure_ascii=False`。
- 缩进 2 空格。

Markdown report 必须包含：

```text
# Mini Runtime Report

## User Goal
## Parsed Goal
## Plan
## Tool Results
## Verification
## Trace Summary
## Errors
```

## 14. CLI 设计

v0.1 使用最小 CLI：

```bash
python apps/cli/main.py run "成都 AI Agent 2027 秋招"
```

后续再通过 `pyproject.toml` 暴露 `campus-agent` console script。

CLI 输出：

```text
run_id: <run_id>
status: success|failed
report_path: data/reports/<run_id>.md
trace_path: data/runs/<run_id>/trace.json
```

## 15. 测试设计

### 15.1 单元测试

文件建议：

```text
tests/unit/test_goal_parsing.py
tests/unit/test_planner.py
tests/unit/test_tool_registry.py
tests/unit/test_verifier.py
```

必须覆盖：

- 目标解析成功。
- 目标解析未知字段。
- 计划生成包含 `mock_job_search`。
- 工具注册和调用成功。
- 未注册工具返回失败。
- verifier 成功和失败。

### 15.2 集成测试

文件建议：

```text
tests/integration/test_mini_runtime.py
```

必须覆盖：

- 完整 graph 运行成功。
- 输出文件存在。
- trace 包含 5 个核心节点。
- report 包含核心章节。

### 15.3 Eval

文件建议：

```text
tests/evals/test_v01_eval.py
```

规则检查：

- `graph_completed`
- `required_nodes_present`
- `mock_tool_called`
- `report_generated`
- `verification_passed`

## 16. 接口影响

需要同步更新：

- `docs/06_contracts/state-schema.md`
- `docs/06_contracts/tool-contract.md`

v0.1 不改变：

- `evidence-contract.md`
- `llm-output-contract.md`

## 17. 风险

| 风险 | 影响 | 缓解 |
| --- | --- | --- |
| 过早抽象 Runtime | 增加实现复杂度 | v0.1 只保留最小节点和 mock tool |
| TypedDict 与 Pydantic 混用边界不清 | 状态和 schema 容易重复 | TypedDict 只做 LangGraph 状态，Pydantic 只做结构化对象校验 |
| Trace 设计过重 | 拖慢 v0.1 实现 | v0.1 使用简单 JSON trace |
| CLI 入口不规范 | 后续迁移 console script 需要调整 | v0.1 先使用脚本入口，v0.2 后再标准化 |

## 18. 验收

实现完成后必须满足：

- `python apps/cli/main.py run "成都 AI Agent 2027 秋招"` 可运行。
- 生成 `state.json`、`trace.json` 和 Markdown report。
- trace 包含 `parse_goal`、`plan_tasks`、`run_mock_tool`、`verify_result`、`write_report`。
- 单元测试通过。
- 集成测试通过。
- eval 规则检查通过。
