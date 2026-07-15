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
