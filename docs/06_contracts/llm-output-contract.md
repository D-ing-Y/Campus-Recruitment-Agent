# LLM Output Contract

LLM 输出必须遵循：

- 优先使用 JSON。
- 关键结构用 Pydantic 校验。
- 缺失字段显式置空或 `unknown`。
- 不允许 Markdown 包裹 JSON。
- 失败时允许有限重试。
- 所有 LLM 调用需要可追踪 provider、model、输入摘要、输出摘要和缓存状态。

