# Eval Metrics

## 初始指标

- `schema_valid_rate`：结构化输出合规率。
- `tool_success_rate`：工具调用成功率。
- `evidence_trace_rate`：结论可追溯证据比例。
- `extraction_completeness`：抽取字段完整率。
- `duplicate_rate`：重复记录比例。
- `recommendation_coverage`：推荐能力包覆盖岗位需求比例。

## v0.1 最小 eval

- graph 是否完成。
- trace 是否包含全部关键节点。
- mock tool 是否被调用。
- report 是否生成。

