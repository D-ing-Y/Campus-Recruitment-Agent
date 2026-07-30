# RFC-0009：规范化 Model、Tool 与 MCP 接入边界

状态：Implemented  
日期：2026-07-30  
关联需求：`docs/03_requirements/v0.7.1-wp1.1-langchain-integration-hardening.md`

## 1. 背景

项目已采用 LangGraph 编排，但模型和工具接入仍是早期自研层：手写 OpenAI-compatible HTTP、
JSON Mode 后置解析、`name + run(dict)` ToolRegistry，且没有 MCP adapter。该形状保留了透明度，
但重复实现了 LangChain 已提供的 provider message、structured output、tool schema 和 MCP 转换能力。

本次重构不改变业务 Workflow，而是把 LangChain 放在基础设施 adapter 内，避免厂商 SDK 和 Agent
消息对象渗透进 State、Evidence 或领域服务。

## 2. 方案

### 2.1 ModelGateway

保留项目 `LLMProvider` 隔离接口，新增两类实现：

- `MockLLMProvider`：离线确定性测试；
- `LangChainChatProvider`：持有 LangChain `BaseChatModel`，统一普通调用与结构化调用。

工厂按 profile integration 装配：

| integration | LangChain adapter | 默认能力 |
| --- | --- | --- |
| mock | 项目 mock | fixture-defined |
| deepseek | `ChatDeepSeek` | JSON Mode、V3 Tool Calling；非原生 JSON Schema |
| openai_compatible | `ChatOpenAI(base_url=...)` | 由显式 profile/capability 决定 |

不根据“OpenAI-compatible”名称推断原生 Schema。custom provider 的原生能力必须由 preset、已知
provider profile 或显式管理员配置给出，并通过 capability smoke 验证。

### 2.2 StructuredOutputGateway

Gateway 接收 Pydantic output model、版本化 messages、provider 与 policy。策略选择必须确定性：

1. `provider_native_json_schema`：`with_structured_output(..., method="json_schema")`；
2. `tool_calling`：`with_structured_output(..., method="function_calling")`；
3. `json_mode`：普通调用后 `json.loads + model_validate`；
4. 不支持则抛出 typed error。

`auto` 只按已声明 capability 选择，不通过异常试探扩大权限。实现可在声明支持但调用失败时回退，
但必须记录 fallback reason，且不能把 `provider_native` 改写成成功的 `json_mode` 而不留痕。

缓存保存规范化 parsed JSON 与安全 raw output；cache key 增加 strategy fingerprint。

### 2.3 ToolCatalog 与 LangChain adapter

`ToolRegistry` 增加 `ToolSpec`：

```text
name, description, args_schema, exposure,
side_effect, requires_confirmation, source
```

现有工具注册保持兼容，未提供 spec 时自动成为 `internal_only`，不能导出给模型。显式 spec 可转换为
LangChain `StructuredTool`；adapter 调用原 `registry.run()`，从而保留项目 `ToolResult`、错误分类和
观测边界。

### 2.4 MCP adapter

使用 `langchain-mcp-adapters` 的 `MultiServerMCPClient` 加载 MCP Tool。项目新增 `MCPServerConfig`
和 `MCPToolCatalog`：

- 验证 transport 与 server/tool allowlist；
- 加载后返回 LangChain `BaseTool`，不注册成内部写 Tool；
- 检测与本地 Tool 重名；
- 保留 server id/source metadata；
- 外部结果进入领域前必须经业务 adapter/raw evidence。

本轮只提供客户端和 fixture 验收，不新增 MCP 配置 UI，不把项目本身发布为公共 MCP Server。

## 3. 接口影响

- `LLMConfig`：新增 integration、structured-output policy/capability；
- `LLMResponse`/`LLMCallRecord`：新增 strategy 与标准 tool/message metadata；
- `LLMProvider`：增加可选的 structured invocation protocol，旧普通生成兼容；
- `ToolResult`：保持业务 shape；新增 `ToolSpec` 与 LangChain adapter；
- RuntimeFactory：通过统一 model factory 装配，不直接选择手写 HTTP provider；
- Model Profile：旧记录兼容推导，新记录显式保存 integration/policy；
- MCP：新增独立配置 schema 和 catalog，不进入 Graph State。

## 4. 失败语义

| 场景 | error_type | retryable |
| --- | --- | --- |
| capability 不支持 | `unsupported_capability` | false |
| provider/tool schema 拒绝 | `schema_validation_error` | false/按 provider 明示 |
| tool arguments 无效 | `tool_input_error` | false，可由 Agent 有限修复 |
| MCP server 不可达 | `external_dependency` | true |
| MCP server/tool 未授权 | `authorization_required` | false |
| MCP tool 运行失败 | `tool_retryable_error` 或 `tool_fatal_error` | 显式 |
| structured output 业务校验失败 | `schema_validation_error` | 按 retry policy |

## 5. 安全与可观测性

- secret 只在 provider/MCP transport 建连边界解析；
- schema、tool description、server id 可记录，headers/API key/完整私人输入不可记录；
- 每次 structured call 记录 requested/effective strategy、fallback、模型 integration；
- 每次 model-visible tool 调用记录 call id、tool/source、status、duration、artifact refs；
- 外部 Tool/MCP 内容不具有系统指令权限，Prompt Injection 不得改变 allowlist 或 route policy。

## 6. 测试与验收

- adapter contract：mock chat model 验证 message/usage/error 映射；
- strategy matrix：native/tool/json/unsupported；
- DeepSeek profile：V3 选择 tool calling，reasoner 拒绝；
- compatibility：旧 SQLite profile 自动推导 integration；
- ToolSpec：默认不可见、显式可见、schema validation、重名和高风险门；
- MCP：stdio fixture 加载/调用、未授权拒绝、server 不可达、secret redaction；
- Candidate：同一 fixture/真实简历产生等价业务结果，receipt/projection 不回退；
- 全量回归与 `git diff --check`。

## 7. 风险

- LangChain provider integration API 会随版本变化，因此使用受控版本范围并只在 adapter 层依赖；
- 不同 provider 的 `with_structured_output` 参数存在差异，capability profile 不能仅靠类名推断；
- MCP 扩大外部输入面，默认 deny 和明确 allowlist 比“自动加载所有工具”更重要；
- 若直接用 `create_agent` 替换业务 Graph，会失去当前证据/策略边界，因此本 RFC 明确禁止大爆炸迁移。
