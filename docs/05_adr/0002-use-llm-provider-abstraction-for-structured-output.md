# ADR-0002: 使用 LLM Provider 抽象支撑结构化输出

## 状态

Accepted

## 日期

2026-07-08

## 背景

v0.2 需要在 v0.1 Mini Agent Runtime 的基础上接入 LLM API，并将 `parse_goal` 从规则解析升级为 LLM JSON 解析。该能力后续会被岗位字段抽取、经验帖总结、技能聚类、最小能力包生成等模块复用。

如果每个节点直接调用具体模型 API，会带来几个问题：

- provider 切换成本高，后续从 OpenAI-compatible API 切换到 DeepSeek、Qwen、OpenRouter 或其他兼容平台时需要改多个节点。
- JSON 解析、Pydantic 校验、重试、缓存和 trace 容易散落在业务节点中。
- 测试会依赖真实 API key、网络和模型输出稳定性。
- API key、请求 header、原始输出等敏感或高噪声信息更容易泄漏进 state、trace、cache 或 report。

因此 v0.2 需要先把模型调用变成 Runtime 的基础设施，而不是把 LLM 调用逻辑写死在单个节点里。

## 决策

v0.2 使用 `LLMProvider` 抽象作为模型调用边界。

初始实现包含两个 provider：

- `mock`
- `openai_compatible`

其中：

- `mock` provider 用于单元测试、集成测试、eval 和无 API key 的本地 smoke test。
- `openai_compatible` provider 使用 HTTP Chat Completions 兼容接口调用模型。

Provider 层只负责：

- 接收标准化 `LLMRequest`。
- 调用模型或返回 mock 响应。
- 返回标准化 `LLMResponse`。
- 提供非敏感 metadata，例如 provider、model、usage。

Provider 层不负责：

- 读取或写入 `AgentState`。
- 解析业务 JSON。
- 执行 Pydantic 业务 schema 校验。
- 写 cache。
- 写 report。
- 调用工具。

结构化输出能力放在 provider 之上的独立层中：

```text
Agent Node
  -> Structured Output Layer
  -> LLM Cache
  -> LLMProvider
  -> concrete provider
```

该层负责：

- 构造版本化 prompt。
- 生成 cache key。
- 读取和写入本地 LLM cache。
- 解析 JSON。
- 使用 Pydantic 校验 `SearchGoal`。
- 对 JSON 解析失败和 schema 校验失败执行有限重试。
- 生成 `LLMCallRecord`。

v0.2 只在 `parse_goal` 节点落地使用该能力。后续节点需要 LLM 时，复用同一 provider 和结构化输出层。

## 备选方案

### 方案 A：在 `parse_goal` 中直接调用具体 LLM API

优点：

- 初始实现最短。
- 文件和抽象更少。
- 适合一次性 demo。

缺点：

- API 调用、prompt、JSON 解析、校验、重试和缓存会混在业务节点中。
- 后续多个节点需要 LLM 时会产生重复代码。
- provider 切换需要改业务节点。
- 测试不容易隔离真实模型调用。

结论：不采用。

### 方案 B：直接引入 LangChain model abstraction

优点：

- 已有成熟模型接口。
- 后续可复用 LangChain structured output 等能力。
- 生态集成较多。

缺点：

- v0.2 当前只需要最小 provider 抽象，引入 LangChain 会增加依赖和概念层。
- 本项目希望先显式理解 provider、cache、trace、schema 校验的边界。
- LangChain 的封装可能隐藏部分请求和响应细节，不利于当前阶段学习和调试。

结论：暂不采用。后续可在 v1.x 复盘是否引入 LangChain 组件。

### 方案 C：直接使用 OpenAI Agents SDK 或其他 Agent SDK

优点：

- 提供更完整的 agent、tool、guardrail、trace 能力。
- 对 OpenAI API 的支持直接。

缺点：

- 本项目 Runtime 核心已经选择 LangGraph。
- v0.2 需要 provider 可切换，不应过早绑定某一平台 SDK。
- Agent SDK 的抽象层级高于当前需求，会掩盖结构化输出、缓存和 trace 的基础实现。

结论：不采用为 v0.2 LLM 层基础。

### 方案 D：只支持一个具体厂商 SDK

优点：

- 对单一厂商的兼容性更好。
- SDK 可能内置重试、错误类型和 response parsing。

缺点：

- 与本项目后续支持 DeepSeek、Qwen、OpenRouter 等 OpenAI-compatible API 的目标不一致。
- SDK 对象模型会渗透到业务代码。
- 更换 provider 时迁移成本高。

结论：不采用。

### 方案 E：自定义 `LLMProvider` 抽象 + OpenAI-compatible HTTP 实现

优点：

- provider 边界清晰。
- 兼容多个 OpenAI-compatible 平台。
- 便于控制 JSON-only prompt、Pydantic 校验、重试、缓存和 trace。
- mock provider 可保证测试稳定。
- 不把具体厂商 SDK 对象暴露给 Agent 节点。

缺点：

- 需要自行维护 HTTP 请求、错误映射和响应解析。
- 初始代码比直接调用 API 稍多。
- 对不同兼容平台的细节差异需要逐步适配。

结论：采用。

## 影响

### 正向影响

- LLM 调用成为可复用基础设施，后续岗位抽取、经验帖总结和能力包生成可以复用。
- Agent 节点不直接依赖具体 provider。
- 测试可以使用 mock provider，不依赖真实 API key 和网络。
- 结构化输出逻辑集中，便于统一 JSON 解析、Pydantic 校验、重试和缓存。
- trace 和 `llm_calls.json` 可以统一记录 provider、model、cache hit、retry count、duration 和错误摘要。
- API key 不需要进入 state、trace、cache 或 report。

### 成本

- 需要维护 `llm/`、`prompts/` 和 `schemas/llm.py` 等模块。
- 需要为 provider error、JSON parse error、schema validation error 和 cache error 建立清晰错误类型。
- OpenAI-compatible 平台之间可能存在响应字段差异，需要在 provider 层逐步兼容。
- 本地 cache 需要处理损坏文件、版本失效和敏感信息隔离。

### 约束

- Provider 层不得依赖 `AgentState`。
- Provider 层不得做业务 schema 校验。
- JSON 解析、Pydantic 校验、重试和缓存必须在 structured output 层完成。
- v0.2 默认使用 mock provider，真实 provider 需要显式配置。
- fallback 到 v0.1 规则解析默认关闭。
- API key、Authorization header 和完整环境变量不得写入 state、trace、cache、report 或测试快照。
- cache key 必须包含 provider、model、prompt version、schema version 和 messages hash。
- v0.2 只在 `parse_goal` 节点接入 LLM，其他节点暂不改为 LLM 驱动。

## 后续复盘点

在 v0.3 或 v1.0 后复盘该决策：

- `LLMProvider` 抽象是否足够支持岗位抽取和经验帖总结。
- 是否需要引入 provider 级 request retry、rate limit 和预算控制。
- 是否需要从 JSON 文件 cache 迁移到 SQLite。
- 是否需要引入 LangChain structured output 或 provider SDK。
- `SearchGoal` 是否应继续与 `ParsedGoal` 并存，还是替换旧 schema。
- `LLMCallRecord` 是否需要纳入统一 observability / eval report。
