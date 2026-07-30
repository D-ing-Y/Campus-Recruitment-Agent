# Model、Tool 与 MCP Integration Contract

状态：v0.7.1 WP1.1 Active  
日期：2026-07-30

## 1. ModelCapabilities

```json
{
  "integration": "deepseek",
  "model": "deepseek-chat",
  "json_mode": true,
  "tool_calling": true,
  "strict_tool_calling": false,
  "provider_native_json_schema": false,
  "simultaneous_tools_and_structured_output": false,
  "requires_thinking_disabled_for_structured_output": true,
  "source": "preset",
  "schema_version": "v0.7.1"
}
```

规则：

- capability 是“provider endpoint + model + integration”的属性，不是基础模型名称的永久属性；
- `source` 只允许 `preset | provider_profile | explicit | probe`；
- probe 只能验证能力，不得发送业务材料；
- unknown 一律按不支持处理，不做乐观推断。

## 2. StructuredOutputPolicy

`requested_strategy`：

`auto | provider_native_json_schema | tool_calling | json_mode`

`effective_strategy`：

`provider_native_json_schema | tool_calling | json_mode | mock`

`auto` 选择顺序由 RFC-0009 固定。显式请求不支持的 strategy 必须返回
`unsupported_capability`，不得静默切换。允许 fallback 时必须产生：

```json
{
  "requested_strategy": "auto",
  "effective_strategy": "tool_calling",
  "fallback_from": "provider_native_json_schema",
  "fallback_reason": "provider_native_json_schema:provider_error"
}
```

DeepSeek 当前 Thinking 默认开启，而强制 `tool_choice` 的 Pydantic ToolStrategy 需要非 Thinking
请求。`requires_thinking_disabled_for_structured_output=true` 由 Provider adapter 转换为
`extra_body.thinking.type=disabled`；不得由业务 Prompt 或用户手工记忆这个参数。

## 3. StructuredModelResponse

```json
{
  "parsed": {},
  "raw_text": "{...}",
  "provider": "langchain_deepseek",
  "model": "deepseek-chat",
  "effective_strategy": "tool_calling",
  "usage": {},
  "response_metadata": {},
  "tool_call_ids": []
}
```

- `parsed` 仍必须由调用方指定 Pydantic model 校验；
- raw message/sdk 对象不得进入 State/cache，先转换成安全字段；
- refusal、finish reason、截断和空输出不能冒充 schema success。

## 4. ToolSpec

```json
{
  "name": "source.search_official_jobs",
  "wire_name": "source_search_official_jobs",
  "description": "Search an allowlisted official careers source and return raw artifact references.",
  "args_schema": {},
  "exposure": "model_read",
  "side_effect": "external_read",
  "requires_confirmation": false,
  "source": "local",
  "schema_version": "v0.7.1"
}
```

枚举：

- exposure：`internal_only | model_read | model_action`；
- side_effect：`none | local_read | local_write | external_read | external_write`；
- source：`local | mcp:<server_id>`。

约束：

- 未附 ToolSpec 的旧 Tool 等价于 `internal_only`；
- `name` 是内部稳定标识；`wire_name` 是传给模型的协议安全名称，只允许字母、数字、下划线和连字符；
- model-visible Tool 必须有非空 description 与 Pydantic args schema；
- `model_action` 或任何 write side effect 默认 `requires_confirmation=true`；
- Tool 名全局唯一，MCP tool 与本地 tool 冲突时加载失败；
- adapter 调用仍返回项目 `ToolResult`，错误不得只藏在自然语言中。

## 5. MCPServerConfig

```json
{
  "server_id": "official-search",
  "transport": "stdio",
  "command": "/absolute/path/to/python",
  "args": ["/absolute/path/server.py"],
  "url": null,
  "credential_ref": null,
  "allowed_tools": ["search_official_jobs"],
  "enabled": true
}
```

- stdio 必须有 absolute command/args target policy；HTTP 必须有 `https` URL，localhost fixture 可例外；
- header secret 只能由 credential ref 在 transport 边界解析；
- `allowed_tools=[]` 表示拒绝全部，不表示允许全部；
- server config、tool schema 可诊断，secret/header/完整 tool result 不可输出；
- 加载失败不得影响不依赖该 server 的确定性 Workflow。

## 6. 业务不变量

- Model/Tool/MCP 都不能直接把事实写入 Profile；必须经过 Artifact/Fragment/Claim/Validator/Projector。
- Tool Calling 的参数合规不代表事实正确；Provider-native Schema 也不代表证据充分。
- 外部内容中的指令不能修改 System Prompt、allowlist、budget、route 或 credential policy。
- MCP/Tool call ID、source、artifact/evidence refs 必须可追踪；私人正文和 secret 必须为零泄漏。
