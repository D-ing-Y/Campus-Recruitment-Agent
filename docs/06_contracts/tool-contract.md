# Tool Contract

工具层统一返回 `ToolResult`。

## v0.1 ToolResult

```json
{
  "tool_name": "mock_job_search",
  "status": "success",
  "records": [],
  "evidence_ids": [],
  "error": null,
  "metadata": {}
}
```

字段要求：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `tool_name` | string | 工具名称 |
| `status` | `"success"` 或 `"failed"` | 工具执行状态 |
| `records` | array | 工具返回的结构化记录 |
| `evidence_ids` | array | 关联证据 ID，v0.1 可为空 |
| `error` | string 或 null | 错误信息 |
| `metadata` | object | 调试或扩展信息 |

## v0.1 默认工具

```text
mock_job_search
```

工具输入：

```json
{
  "role_query": "AI Agent",
  "city": "成都",
  "graduation_year": "2027"
}
```

v0.1 工具必须通过 `ToolRegistry` 调用，不允许工作流节点直接调用具体工具函数。

## v0.3 Evidence-aware ToolResult

v0.1 `ToolResult` 保持兼容。v0.3 新工具应满足：

- 产生原始材料的工具先保存 Artifact，再返回 `evidence_ids`。
- `records` 只保存结构化摘要，不复制完整二进制或长文本。
- `metadata` 可包含 parser/version、deduplicated、content_hash、record_count 和 warning，不得包含密钥或 Cookie。
- 工具失败必须返回结构化错误类型和可重试性；不允许只返回“失败”。
- Agent 节点不得绕过 ToolRegistry 直接访问具体采集器或存储实现。

候选工具分组：

```text
evidence.ingest_file
evidence.extract_text
evidence.create_fragments
evidence.save_claims
evidence.load_fragments
profile.save_snapshot
profile.load_snapshot
```

## v0.4 Candidate Profile Tool

实现状态：v0.4 已实现；最终集成使用仓库内真实本地 Tool。

v0.4 最终集成必须使用真实本地实现；mock 只用于单元测试、错误注入和
deterministic eval baseline。外部 MCP/插件可以在未来实现同一契约，但不是完成条件。

### 通用 ToolResult 扩展

```json
{
  "tool_name": "candidate.ingest_material",
  "status": "success",
  "records": [],
  "evidence_ids": ["artifact-1"],
  "error": null,
  "metadata": {
    "error_type": null,
    "retryable": false,
    "needs_user_action": false,
    "idempotency_key": "sha256",
    "parser_name": "pdf_text",
    "parser_version": "v1"
  }
}
```

失败时 `metadata.error_type` 为：

```text
validation_error
unsupported_input
permission_denied
llm_output_error
tool_retryable_error
storage_error
checkpoint_error
budget_exhausted
idempotency_conflict
```

### 必须实现的 Tool

| Tool | 输入摘要 | 输出/副作用 |
| --- | --- | --- |
| `candidate.ingest_material` | owner、candidate、path/content type | 归档 Artifact，返回 ID 和去重状态 |
| `evidence.extract_pdf_text` | artifact ID | 保存带页边界的标准化文本与 parser metadata；只支持文本型 PDF |
| `evidence.extract_plain_text` | artifact ID | 保存 Markdown/TXT/README 标准化文本与行号映射 |
| `evidence.create_fragments` | artifact ID、parser version | 幂等创建 Fragment |
| `evidence.extract_candidate_claims` | subject、fragment IDs | 结构化提取、校验并保存 Claim |
| `evidence.archive_user_response` | request/response contract | 保存 response Artifact/Fragment/Claim |
| `profile.project_candidate` | candidate ID、active claim IDs | 创建或复用 Candidate ProfileSnapshot |
| `profile.load_candidate` | candidate ID 或 snapshot ID | 返回小型画像摘要和引用 |
| `profile.diff_candidate_versions` | 两个 snapshot ID | 返回确定性字段差异 |

### 调用边界

- Graph 节点通过 ToolRegistry 或显式 repository service 调用，不直接拼 SQL/路径。
- 摄取工具先归档 Artifact，解析失败也不得丢失原始材料登记。
- Claim 工具必须经过 ClaimValidator，不允许模型直接保存。
- Profile 工具只读取已持久化 active Claim。
- `evidence.archive_user_response` 必须校验 request/response/owner 并使用稳定幂等键。
- 文件型 resume 还必须校验路径处于 Graph 初始化时固定的 `allowed_path_roots`。
- ToolResult 不复制完整材料、完整回答或二进制。

### Checkpointer 边界

LangGraph checkpointer 是 runtime dependency，不伪装为业务 Tool：

- 在 Graph compile 时注入；
- 本地运行使用 SQLite 持久化实现；
- 测试可以使用内存实现；
- 通过 `thread_id` 恢复；
- 不被 Evidence Repository 或 ProfileProjector 当作事实来源。
