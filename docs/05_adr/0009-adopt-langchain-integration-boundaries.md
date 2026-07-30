# ADR-0009：在接入边界采用 LangChain Model、Tool 与 MCP 抽象

## 状态

Accepted

## 日期

2026-07-30

## 背景

ADR-0001 选择 LangGraph 作为 Runtime，ADR-0002 在早期选择自研 `LLMProvider + HTTP` 以显式学习
调用、缓存、校验和观测。WP1 真实 DeepSeek 验收暴露了该早期决策的复盘条件已经满足：手写 JSON
协议无法充分利用原生 Schema/Tool Calling，ToolRegistry 也不能直接进入标准 Agent/MCP 生态。

## 决策

1. LangGraph 继续作为业务 Workflow Runtime。
2. Evidence、Validator、Projector、Policy、Repository 和 Human Gate 继续由项目控制。
3. LangChain 只进入基础设施接入边界：
   - provider-specific chat model integration；
   - `with_structured_output`/ProviderStrategy/ToolStrategy 等能力；
   - LangChain Tool schema/消息适配；
   - `langchain-mcp-adapters` MCP 客户端转换。
4. 保留项目协议作为 anti-corruption layer，业务代码不直接依赖具体厂商 SDK。
5. 采用渐进兼容迁移，不删除旧 Provider 配置、不一次性重写全部 Tool、不把整个项目改成单一 Agent loop。

本 ADR 更新 ADR-0002 中“暂不采用 LangChain model abstraction”的阶段性结论；ADR-0002 的 secret、
cache、trace、业务 schema 不进入 provider 层等约束继续有效。

## 备选方案

### A. 继续维护自研 HTTP/Tool/MCP

可完全控制细节，但会持续重复 provider 消息、Schema、Tool Calling、异步与 MCP adapter 工作；不采用。

### B. 全部改写为 LangChain `create_agent`

代码表面更短，但让模型工具循环侵入确定性业务图，削弱 Evidence/Validator/Policy；不采用。

### C. LangGraph + 边界型 LangChain adapter

复用生态同时保留领域控制、可测试性和迁移路径；采用。

### D. 所有内部能力发布为 MCP Server

跨进程统一但引入不必要 transport、授权和攻击面；只对需要跨应用复用的外部资源采用 MCP。

## 影响

### 收益

- 供应商切换不再要求复制 HTTP/消息/Tool Calling 代码；
- Structured Output 能按能力选原生 Schema 或 Tool Calling；
- 本地与 MCP Tool 可通过统一 LangChain Tool 接口进入有限 Agent 节点；
- 保留项目现有证据、重放、receipt 和安全边界。

### 成本

- 新增 `langchain`、provider integration 与 MCP adapter 依赖；
- 需要维护 capability profile 和第三方版本兼容测试；
- Tool 需要逐个补充模型可见 Schema，不能自动安全暴露。

### 约束

- 默认拒绝模型访问未显式暴露 Tool；
- MCP 输出永远视为外部不可信输入；
- Provider-native、Tool Calling、JSON Mode 必须在 receipt 中准确区分；
- LangChain 不能替代领域 Pydantic/Validator；
- 任何写操作、权限扩大或高风险 Tool 必须由确定性 policy/human gate 控制。
