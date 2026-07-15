# Testing Strategy

## 测试分层

- 单元测试：schema、normalizer、repository、planner、tool registry、validator、scorer。
- 集成测试：一个 subgraph 或完整 workflow 的状态、存储和恢复。
- Contract 测试：local/remote storage、tool、LLM provider 和 retriever 实现遵守同一接口。
- Eval 测试：证据追溯、画像、检索、匹配、路由和推荐质量。
- Failure 测试：模型错误、工具失败、重复消息、worker 崩溃、存储部分失败和恢复。
- 回归测试：每个新版本必须继续运行 v0.1/v0.2 smoke test。

## 已完成基础

- v0.1：AgentState 初始化、ToolRegistry、线性 Graph、trace 和 report。
- v0.2：LLM config、provider、structured output、重试、cache 和 LLM eval。

## v0.3 测试重点

- Artifact hash 去重和不可变性。
- Fragment locator 可回溯。
- Claim schema 与 evidence 引用存在性。
- 无证据事实 Claim 被拒绝。
- Blob 写入与 SQLite metadata 的失败清理。
- Profile snapshot supporting claim 引用完整。
- 匿名 fixture 端到端 Artifact→Fragment→Claim。

## 后续专项评估

- LangGraph：conditional route accuracy、loop count、interrupt/resume、checkpoint recovery。
- RAG：Recall@K、NDCG/MRR、citation precision、groundedness、无答案正确率、延迟和成本。
- 分布式系统：幂等、并发冲突、消息重投、对象/元数据部分失败、服务重启恢复。
- Multi-Agent：与单 Agent 比较质量、延迟、token 成本、上下文污染和失败率。

## 数据规则

- 默认测试只使用匿名、合成或明确可提交的 fixture。
- 真实简历、Cookie 和个人材料不得进入 Git 或公开测试快照。
- 招聘和社区网页 fixture 必须保留来源时间并遵守使用边界。
