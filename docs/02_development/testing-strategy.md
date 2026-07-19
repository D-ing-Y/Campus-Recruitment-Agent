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

## v0.4 测试重点

### 单元测试

- CandidateProfile、SufficiencyAssessment、QuestionPlan 和 Human Interaction schema。
- State reducer 的 append、stable union、replace 和 clear 语义。
- 文本型 PDF 页码 locator，Markdown/TXT/README 行号或字符 locator。
- 信息价值计算、路由优先级、问题去重和硬预算。
- request/response、owner、action、correction 和 supersedes 校验。

### 集成测试

- 充分材料：无 interrupt 完成并生成证据化 profile。
- 信息不足：生成高价值问题，interrupt 后用相同 thread resume。
- 补充文件：resume 后重新摄取并构建新 snapshot。
- 用户纠正：新旧 Claim 可追溯，ProfileVersionDiff 正确。
- 用户 skip、材料 unsupported 和预算耗尽：显式 unknown 并终止。
- SQLite checkpointer：重建 Graph/进程边界后恢复。
- 重复 resume：不重复创建 Artifact、Claim 或 snapshot。
- LLM、工具、存储和 checkpoint 错误使用结构化安全回退。

### Contract 与回归

- 内存/SQLite checkpointer 遵守相同恢复语义。
- deterministic/LLM evaluator 遵守相同输出 schema。
- ToolRegistry 中真实本地工具与 mock 错误工具遵守统一 ToolResult。
- v0.1-v0.3 全量回归必须继续通过。

## v0.5 测试重点

### 单元测试

- SearchScope、SourceQuery、SourceDocument、NormalizedJobPosting 和 ExperienceEvidenceRecord。
- RoleProfileGraphState reducer、query fingerprint、budget/counter。
- recruitment/experience adapter contract 和 source capability。
- HTML/JSON/text extraction locator 与 raw-before-parse guard。
- hard scope、source authority 和 Claim validator。
- exact/fuzzy/cross-source job dedup 与转载经验去重。
- prevalence、company coverage、signal frequency、freshness 和 sample status。
- query planner、coverage evaluator、route policy 和授权 request/response redaction。

### 集成测试

- fixture recruitment raw→job record→Claim→job instance profile。
- fixture experience raw→signal Claim，且不能创建 hard requirement。
- 多岗位→去重 cluster→RoleFamilyProfile，计数和分母正确。
- pagination 选择 `search_more`，低相关选择 `change_query`，authority 缺口选择 `change_source`。
- auth required→interrupt→credential ref resume；skip 后不重复请求。
- SQLite checkpoint 跨 Graph 实例从 collection/auth 边界恢复。
- 重复 source batch/query 不重复创建 Artifact、Record、Claim 或 snapshot。
- source changed、rate limited、empty、parse/normalization/LLM/storage/checkpoint 错误安全回退。

### Live smoke

- live smoke 不进入默认 CI，必须由用户显式启用。
- `zhaopin_jobs` 与 `nowcoder_experience` 各执行一个小范围查询。
- 验证 raw Artifact、SourceRunReceipt、credential redaction 和限速。
- 如果来源临时不可用，记录真实失败并保持版本 Partial，不使用 fixture 冒充 live 成功。

### 回归

- v0.1-v0.4 全量测试必须继续通过；当前进入 v0.5 前基线为 68/68。

## 后续专项评估

- LangGraph：v0.4 起评估 conditional route accuracy、loop count、interrupt/resume、checkpoint recovery。
- RAG：Recall@K、NDCG/MRR、citation precision、groundedness、无答案正确率、延迟和成本。
- 分布式系统：幂等、并发冲突、消息重投、对象/元数据部分失败、服务重启恢复。
- Multi-Agent：与单 Agent 比较质量、延迟、token 成本、上下文污染和失败率。

## 数据规则

- 默认测试只使用匿名、合成或明确可提交的 fixture。
- 真实简历、Cookie 和个人材料不得进入 Git 或公开测试快照。
- 招聘和社区网页 fixture 必须保留来源时间并遵守使用边界。
- live raw、Cookie/cURL、credential store 和含个人登录状态的响应不得进入 Git。
- 受转载限制的网页只保存本地 raw；可提交 fixture 使用允许的最小快照、脱敏摘录或合成等价结构。
