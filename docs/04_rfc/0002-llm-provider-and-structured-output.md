# RFC-0002: LLM Provider 与结构化输出

状态：Draft
日期：2026-07-08
关联需求：`docs/03_requirements/v0.2-llm-provider.md`

## 1. 背景

v0.1 已经实现基于 LangGraph 的最小 Agent Runtime，但 `parse_goal` 仍是规则解析，无法处理更自然、更复杂的用户目标。v0.2 需要引入 LLM API 调用能力，并建立结构化输出、校验、重试、缓存和可观测能力。

本 RFC 定义 v0.2 的实现边界：

- 定义 LLM Provider 抽象。
- 定义 OpenAI-compatible provider。
- 定义 mock provider。
- 定义 `SearchGoal` 结构化输出 schema。
- 定义 prompt、JSON 解析、Pydantic 校验和重试流程。
- 定义 LLM 调用缓存。
- 定义 LLM 调用 trace 和本地输出。
- 定义 `parse_goal` 节点接入方式。

## 2. 目标

v0.2 的目标是把模型能力作为 Runtime 的可复用基础设施接入项目，但只在 `parse_goal` 节点落地验证。

必须满足：

- 工作流主链路仍保持 v0.1 的 5 个节点。
- `parse_goal` 优先通过 LLM 生成结构化 `SearchGoal`。
- LLM 输出必须经过 JSON 解析和 Pydantic 校验。
- 非法 JSON 或 schema 校验失败时最多执行 1 次结构化修复重试。
- 相同输入、相同 provider、相同 model、相同 prompt/schema version 可以命中本地缓存。
- 所有 LLM 调用记录非敏感摘要，支持调试和 eval。
- 单元测试、集成测试和 eval 默认使用 mock provider。

## 3. 非目标

本 RFC 不设计：

- 真实招聘网站工具。
- 多 provider 智能路由。
- streaming。
- function calling / tool calling。
- embedding provider。
- RAG / Memory。
- 长期会话记忆。
- LangGraph checkpoint / interrupt。
- LangChain Agent 或 OpenAI Agents SDK 作为 Runtime 核心。
- 生产级限流、预算控制和队列调度。

## 4. 总体设计

v0.2 不改变 LangGraph 拓扑。

```text
START
  -> parse_goal
  -> plan_tasks
  -> run_mock_tool
  -> verify_result
  -> write_report
  -> END
```

`parse_goal` 内部从规则解析升级为结构化 LLM 解析：

```text
user_input
  -> load LLM config
  -> build goal_parser_v1 prompt
  -> compute cache key
  -> read cache
  -> provider.generate() when cache miss
  -> parse JSON
  -> validate SearchGoal
  -> retry once on JSON/schema failure
  -> write parsed_goal
  -> write llm call records
```

核心分层：

```text
agent/planner.py
  -> llm/structured.py
      -> prompts/goal_parser.py
      -> llm/cache.py
      -> llm/provider.py
          -> llm/mock.py
          -> llm/openai_compatible.py
      -> schemas/goal.py
      -> schemas/llm.py
```

分层原则：

- Provider 层只负责模型请求和原始响应，不理解 `SearchGoal`。
- Structured output 层负责 JSON 解析、schema 校验、重试和缓存协调。
- Agent 节点只接收结构化结果和调用摘要，不直接处理 HTTP 细节。
- `parsed_goal` 状态键继续保留，值改为 `SearchGoal.model_dump()`，兼容 v0.1 后续节点。

## 5. 模块划分

建议新增文件：

```text
src/campus_job_agent/llm/__init__.py
src/campus_job_agent/llm/base.py
src/campus_job_agent/llm/cache.py
src/campus_job_agent/llm/config.py
src/campus_job_agent/llm/mock.py
src/campus_job_agent/llm/openai_compatible.py
src/campus_job_agent/llm/structured.py
src/campus_job_agent/prompts/__init__.py
src/campus_job_agent/prompts/goal_parser.py
src/campus_job_agent/schemas/llm.py
src/campus_job_agent/schemas/goal.py
```

需要更新文件：

```text
src/campus_job_agent/agent/state.py
src/campus_job_agent/agent/planner.py
src/campus_job_agent/agent/report_writer.py
src/campus_job_agent/schemas/__init__.py
apps/cli/main.py
configs/local.example.yaml
.env.example
pyproject.toml
```

职责边界：

| 模块 | 职责 |
| --- | --- |
| `llm/base.py` | 定义 provider protocol 和请求/响应对象 |
| `llm/config.py` | 从环境变量和本地配置构造 LLM 配置 |
| `llm/mock.py` | 测试和本地 smoke test 的确定性 provider |
| `llm/openai_compatible.py` | OpenAI-compatible Chat Completions 调用 |
| `llm/cache.py` | 本地 JSON 文件缓存 |
| `llm/structured.py` | JSON 解析、schema 校验、重试、缓存协调 |
| `prompts/goal_parser.py` | 目标解析 prompt 名称、版本和消息构造 |
| `schemas/llm.py` | LLM 调用记录、配置和错误类型 schema |
| `schemas/goal.py` | `SearchGoal` 和 v0.1 兼容字段 |
| `agent/planner.py` | `parse_goal` 接入结构化 LLM 解析 |
| `agent/report_writer.py` | 输出 `llm_calls.json` 和报告摘要 |

## 6. Schema 设计

### 6.1 `SearchGoal`

v0.2 新增 `SearchGoal`，并保留 v0.1 的 `ParsedGoal` 兼容字段。

```python
class SearchGoal(BaseModel):
    role_query: str
    city: str
    graduation_year: str
    recruitment_type: Literal[
        "autumn_campus",
        "spring_campus",
        "internship",
        "unknown",
    ] = "unknown"
    keywords: list[str] = []
    raw_text: str
    companies: list[str] = []
    industries: list[str] = []
    locations: list[str] = []
    constraints: list[str] = []
    confidence: float | None = None
    warnings: list[str] = []
```

字段规则：

- `role_query`、`city`、`graduation_year`、`raw_text` 必填。
- 无法识别的 string 字段填 `"unknown"`。
- 无法识别的 list 字段填空数组。
- `confidence` 允许为 `null`。
- `raw_text` 必须等于原始用户输入。

`ParsedGoal` 暂不删除。`parse_goal` 节点输出：

```python
{
    "parsed_goal": search_goal.model_dump(),
    "llm_calls": [call_record.model_dump()],
}
```

`plan_tasks` 继续读取：

- `parsed_goal["role_query"]`
- `parsed_goal["city"]`
- `parsed_goal["graduation_year"]`

### 6.2 `LLMRequest`

```python
class LLMRequest(BaseModel):
    messages: list[dict[str, str]]
    model: str
    temperature: float = 0.0
    response_format: dict[str, str] | None = {"type": "json_object"}
    timeout_seconds: float = 30.0
```

### 6.3 `LLMResponse`

```python
class LLMResponse(BaseModel):
    text: str
    provider: str
    model: str
    usage: dict[str, Any] | None = None
    raw_metadata: dict[str, Any] = {}
```

### 6.4 `LLMCallRecord`

```python
class LLMCallRecord(BaseModel):
    provider: str
    model: str
    prompt_name: str
    prompt_version: str
    schema_version: str
    cache_key: str
    cache_hit: bool
    retry_count: int
    duration_ms: int
    status: Literal["success", "failed"]
    error_type: str | None = None
    error: str | None = None
    usage: dict[str, Any] | None = None
```

`LLMCallRecord` 不保存 API key、Authorization header、完整环境变量或敏感请求头。

### 6.5 `AgentState`

v0.2 在 `AgentState` 中新增可选字段：

```python
class AgentState(TypedDict, total=False):
    ...
    llm_calls: list[dict]
```

如果实现阶段发现 LangGraph 状态合并需要 reducer，`llm_calls` 的追加策略应与 `trace` 保持一致。

## 7. Provider 设计

### 7.1 Provider Protocol

```python
class LLMProvider(Protocol):
    name: str

    def generate(self, request: LLMRequest) -> LLMResponse:
        ...
```

Provider 约束：

- 不读取或写入 `AgentState`。
- 不做 Pydantic 业务 schema 校验。
- 不写 cache。
- 不写 report。
- 不调用工具。
- 只返回原始模型文本和非敏感 metadata。

### 7.2 Mock Provider

`MockLLMProvider` 支持以下模式：

```text
valid_json
invalid_json_then_valid
schema_error_then_valid
always_invalid_json
provider_error
```

用途：

- 测试正常结构化解析。
- 测试 JSON 解析失败重试。
- 测试 schema 校验失败重试。
- 测试 provider error。
- 测试缓存命中时不调用 provider。

### 7.3 OpenAI-compatible Provider

`OpenAICompatibleProvider` 通过 Chat Completions 兼容接口调用模型。

配置：

```python
class LLMConfig(BaseModel):
    provider: Literal["mock", "openai_compatible"] = "mock"
    base_url: str | None = None
    api_key: str | None = None
    model: str = "mock-goal-parser"
    timeout_seconds: float = 30.0
    temperature: float = 0.0
    max_retries: int = 1
    cache_enabled: bool = True
    cache_dir: str = "data/cache/llm"
    fallback_to_rule_parser: bool = False
```

实现取舍：

- v0.2 使用 `httpx` 调用 OpenAI-compatible HTTP API。
- `httpx` 需要作为直接依赖写入 `pyproject.toml`。
- 不引入官方 provider SDK，避免 v0.2 过早绑定某一个 SDK 的对象模型。
- 请求路径默认为 `<base_url>/chat/completions`。
- 如果 `base_url` 已包含尾部 `/`，实现需避免拼接出双斜杠。
- 请求 header 使用 `Authorization: Bearer <api_key>`。
- 请求 body 使用 `model`、`messages`、`temperature`、`response_format`。

真实 provider 配置缺失时：

- `provider=openai_compatible` 且缺少 `api_key`、`base_url` 或 `model` 时，配置加载失败。
- 错误进入 CLI 可读输出，不进入缓存。

## 8. Prompt 设计

目标解析 prompt 固定为：

```text
prompt_name = "goal_parser"
prompt_version = "v1"
schema_version = "v0.2"
```

`prompts/goal_parser.py` 提供：

```python
def build_goal_parser_messages(user_input: str) -> list[dict[str, str]]:
    ...

def build_goal_parser_retry_messages(
    user_input: str,
    previous_output: str,
    error_summary: str,
) -> list[dict[str, str]]:
    ...
```

prompt 要求：

- system message 明确要求只输出 JSON。
- user message 包含原始用户目标。
- schema 字段说明写入 prompt。
- 未识别字段填 `"unknown"`、`[]` 或 `null`。
- 不要求模型补充用户没有提供的信息。
- 不输出 Markdown、解释、注释或代码块。

重试 prompt 要求：

- 包含上一次输出摘要。
- 包含错误类型和 schema 校验摘要。
- 要求重新输出完整 JSON，而不是 patch。

## 9. 结构化输出流程

新增 `parse_structured_output` 或等价服务函数：

```python
def parse_search_goal_with_llm(
    user_input: str,
    config: LLMConfig,
    provider: LLMProvider,
    cache: LLMCache,
) -> tuple[SearchGoal, list[LLMCallRecord]]:
    ...
```

流程：

1. 构造 `goal_parser_v1` messages。
2. 根据 provider、model、prompt version、schema version、messages hash 生成 cache key。
3. 如果启用缓存且命中，读取 cached raw output。
4. 如果未命中，调用 provider。
5. 对 raw output 执行 `json.loads`。
6. 使用 `SearchGoal.model_validate` 校验。
7. 成功则返回 `SearchGoal` 和 `LLMCallRecord`。
8. 如果发生 `json_parse_error` 或 `schema_validation_error`，构造 retry messages。
9. 最多重试 1 次。
10. 重试成功则返回结果，并记录 `retry_count=1`。
11. 重试失败则抛出结构化异常，由 `parse_goal` 节点写入 `errors`。

错误类型：

```text
provider_error
json_parse_error
schema_validation_error
cache_error
config_error
```

缓存错误处理：

- cache 读取失败时记录 `cache_error`，允许继续调用 provider。
- cache 写入失败时记录 `cache_error`，不应导致本次解析失败。
- cache 内容损坏时视为 cache miss，并记录错误摘要。

## 10. 缓存设计

v0.2 使用按 hash 分文件的 JSON cache。

路径：

```text
data/cache/llm/<cache_key>.json
```

cache key：

```text
sha256(canonical_json({
  "provider": provider,
  "model": model,
  "prompt_name": prompt_name,
  "prompt_version": prompt_version,
  "schema_version": schema_version,
  "messages": messages
}))
```

canonical JSON 规则：

- `ensure_ascii=False`
- `sort_keys=True`
- 紧凑分隔符

cache value：

```json
{
  "cache_key": "string",
  "created_at": "ISO-8601",
  "provider": "openai_compatible",
  "model": "example-model",
  "prompt_name": "goal_parser",
  "prompt_version": "v1",
  "schema_version": "v0.2",
  "raw_output": "{}",
  "parsed_json": {},
  "usage": null
}
```

缓存约束：

- API key 不进入 key 和 value。
- cache hit 时不调用 provider。
- retry prompt 使用新的 messages，因此生成新的 cache key。
- 禁用缓存时仍应生成 `LLMCallRecord.cache_hit=false`。

## 11. `parse_goal` 节点接入

v0.2 的 `parse_goal` 节点策略：

1. 加载 LLM config。
2. 构造 provider 和 cache。
3. 调用结构化解析服务。
4. 将 `SearchGoal.model_dump()` 写入 `parsed_goal`。
5. 将调用摘要追加到 `llm_calls`。
6. 将 LLM 摘要写入 trace output summary。

失败策略：

- `config_error`：节点失败。
- `provider_error`：节点失败，除非显式启用 fallback。
- `json_parse_error`：结构化重试；重试失败后节点失败，除非显式启用 fallback。
- `schema_validation_error`：结构化重试；重试失败后节点失败，除非显式启用 fallback。
- fallback 默认关闭。
- fallback 启用时调用 v0.1 规则解析，并在 `LLMCallRecord.error_type` 或 trace 中标记 fallback。

## 12. 文件输出设计

继续输出 v0.1 文件：

```text
data/runs/<run_id>/state.json
data/runs/<run_id>/trace.json
data/reports/<run_id>.md
```

新增：

```text
data/runs/<run_id>/llm_calls.json
data/cache/llm/<cache_key>.json
```

`state.json` 包含：

```json
{
  "parsed_goal": {},
  "llm_calls": []
}
```

`llm_calls.json` 包含：

```json
[
  {
    "provider": "mock",
    "model": "mock-goal-parser",
    "prompt_name": "goal_parser",
    "prompt_version": "v1",
    "schema_version": "v0.2",
    "cache_hit": false,
    "retry_count": 0,
    "duration_ms": 1,
    "status": "success",
    "error_type": null,
    "error": null,
    "usage": null
  }
]
```

Markdown report 新增或扩展内容：

```text
## LLM Calls

- provider
- model
- prompt version
- schema version
- cache hit
- retry count
- status
```

报告不得输出：

- API key。
- Authorization header。
- 完整请求 header。
- 完整环境变量。

## 13. CLI 与配置

v0.2 CLI 可以先不新增复杂参数，但需要支持通过环境变量配置 provider。

环境变量：

```text
CAMPUS_AGENT_LLM_PROVIDER=mock
OPENAI_API_KEY=
OPENAI_BASE_URL=
OPENAI_MODEL=
CAMPUS_AGENT_LLM_CACHE_ENABLED=true
CAMPUS_AGENT_LLM_FALLBACK_TO_RULE_PARSER=false
```

默认值：

- `CAMPUS_AGENT_LLM_PROVIDER=mock`
- `CAMPUS_AGENT_LLM_CACHE_ENABLED=true`
- `CAMPUS_AGENT_LLM_FALLBACK_TO_RULE_PARSER=false`

`configs/local.example.yaml` 与环境变量同时存在时：

- v0.2 优先读取环境变量。
- YAML config 作为后续扩展保留。
- 实现不得要求用户提交本地 config。

## 14. 依赖

`pyproject.toml` 需要新增：

```toml
dependencies = [
  "langgraph",
  "pydantic",
  "httpx",
]
```

不新增：

- LangChain。
- OpenAI Agents SDK。
- provider 官方 SDK。
- SQLite cache 依赖。

## 15. 测试设计

### 15.1 单元测试

新增测试文件建议：

```text
tests/unit/test_llm_config.py
tests/unit/test_llm_cache.py
tests/unit/test_llm_mock_provider.py
tests/unit/test_structured_output.py
tests/unit/test_search_goal_schema.py
```

必须覆盖：

- `SearchGoal` 合法输入校验成功。
- `SearchGoal` 缺必填字段校验失败。
- mock provider 返回合法 JSON。
- mock provider 返回非法 JSON 后重试成功。
- mock provider 返回 schema 错误后重试成功。
- mock provider 一直失败时抛出结构化错误。
- cache key 对 provider、model、prompt version、schema version、messages 敏感。
- cache hit 时 provider 不被调用。
- cache 损坏时视为 miss 并记录错误。
- openai provider 配置缺失时报 `config_error`。

### 15.2 集成测试

新增或更新：

```text
tests/integration/test_v02_llm_goal_parsing.py
```

必须覆盖：

- 使用 mock provider 跑通完整 graph。
- `parsed_goal` 包含 `recruitment_type` 和 `keywords`。
- `llm_calls.json` 被写入。
- `trace.json` 中 `parse_goal` output summary 包含 LLM 摘要。
- 第二次相同输入运行命中缓存。
- 启用 fallback 后，LLM 失败仍能生成 v0.1 兼容 `parsed_goal`。

### 15.3 Eval

新增：

```text
tests/evals/test_v02_llm_eval.py
```

规则检查：

- `llm_provider_configured`
- `structured_goal_valid`
- `json_parse_error_captured`
- `schema_validation_error_captured`
- `retry_attempted`
- `cache_hit_visible`
- `api_key_not_logged`

## 16. 接口影响

需要同步更新：

```text
docs/06_contracts/llm-output-contract.md
docs/06_contracts/state-schema.md
```

可能更新：

```text
docs/02_development/testing-strategy.md
docs/08_deployment/security-and-secrets.md
README.md
```

不改变：

```text
docs/06_contracts/tool-contract.md
docs/06_contracts/evidence-contract.md
```

## 17. 风险

| 风险 | 影响 | 缓解 |
| --- | --- | --- |
| 模型输出不稳定 | 解析失败或字段漂移 | JSON-only prompt、Pydantic 校验、一次修复重试 |
| Provider 兼容接口差异 | 不同平台响应字段略有不同 | provider 层只抽取 `text`、`usage` 和少量 metadata |
| 缓存污染 | 旧 prompt/schema 的结果被误用 | cache key 包含 prompt version 和 schema version |
| API key 泄漏 | 安全风险 | key 不进入 state、trace、cache、report |
| 过早抽象 provider | 实现复杂度增加 | v0.2 只实现 mock 和 openai_compatible |
| fallback 掩盖真实错误 | 测试误判成功 | fallback 默认关闭，发生时必须写 trace/report |

## 18. 验收

实现完成后必须满足：

- `python apps/cli/main.py run "成都 AI Agent 2027 秋招"` 在 mock provider 下可运行。
- `parsed_goal` 由 `SearchGoal` schema 校验生成。
- `plan_tasks`、`run_mock_tool`、`verify_result`、`write_report` 继续兼容。
- 生成 `state.json`、`trace.json`、`llm_calls.json` 和 Markdown report。
- 相同输入第二次运行可以命中 LLM cache。
- 非法 JSON 和 schema 错误可以触发一次重试。
- 重试失败时错误进入 `errors` 和 trace。
- API key 不出现在输出文件中。
- 单元测试通过。
- 集成测试通过。
- eval 规则检查通过。
