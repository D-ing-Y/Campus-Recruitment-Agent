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
