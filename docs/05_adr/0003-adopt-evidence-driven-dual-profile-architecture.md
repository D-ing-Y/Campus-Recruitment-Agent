# ADR-0003: 采用证据驱动的双画像架构并在现有 Runtime 上演进

## 状态

Accepted

## 日期

2026-07-15

## 背景

v0.1 已完成 LangGraph Mini Runtime，v0.2 已完成 LLM Provider 与结构化输出。原 roadmap 以岗位检索、经验帖采集和最小能力包为主，默认用户已经知道目标岗位，且没有把用户材料、岗位材料、画像和反馈统一到同一证据模型中。

新的产品理解是：求职 Agent 的核心不是自动搜索，而是根据真实材料持续估计候选人当前能力和岗位真实需求，比较两者差异，并根据用户选择与笔面试反馈反复更新。

项目还承担 Agent 岗位面试项目的学习目标，需要真实实现 LangGraph 高级能力、RAG、分布式存储和必要的 Multi-Agent，而不是停留在概念或简单封装。

## 决策

### 1. 采用证据驱动的双画像架构

- 原始文件、网页和反馈是事实源。
- `EvidenceArtifact`、`EvidenceFragment` 和 `EvidenceClaim` 构成统一证据层。
- `CandidateProfile` 和 `RoleProfile` 是由 Claim 构建的版本化派生视图。
- `CareerIntent` 与能力画像分离。
- `GapAssessment` 至少区分能力、证据、偏好和认知不确定性。

### 2. 保留当前仓库和 v0.1/v0.2 基础设施

- 不新建项目，不重写 Git 历史。
- v0.1 的 StateGraph、ToolRegistry、trace、report、CLI 和测试骨架继续复用。
- v0.2 的 Provider、structured output、Pydantic、重试、缓存和 LLM trace 继续复用。
- 线性 Graph、扁平 State、固定 Planner 和 `SearchGoal` 属于可替换的早期业务实现。

### 3. 前沿技术按真实职责分阶段引入

- LangGraph：从 v0.4 起使用 subgraph、conditional edge、loop、checkpoint、interrupt 和恢复。
- Hybrid RAG：从 v0.8 起服务于证据检索、画像补全和带引用生成，并与无检索、稀疏、dense 基线比较。
- 分布式存储：v1.1 引入 PostgreSQL、S3/MinIO、向量存储和异步队列，验证幂等、恢复和并发，而不是把单机实现称为分布式。
- Multi-Agent：只有单 Agent eval 证明存在动态并行、上下文污染或长任务瓶颈时引入。

### 4. Eval 为跨版本能力

从 v0.3 开始持续评估证据追溯、unsupported claim、画像完整度、Graph 路由、RAG 检索和系统恢复，不再把 Eval 推迟到独立后期版本。

## 备选方案

### 方案 A：继续原“岗位检索优先”roadmap

优点：短期更快接入爬虫并生成岗位表。

缺点：缺少候选人证据模型，无法可靠完成画像比较和反馈更新；后续容易在错误画像上生成学习计划。

结论：不采用。

### 方案 B：重开新项目

优点：目录和命名可以一次按新架构设计。

缺点：v0.1/v0.2 的 Runtime、Provider、测试和安全边界仍然需要重新实现；方向变化发生在业务模型而非技术栈。

结论：不采用。

### 方案 C：直接以聊天记录构建画像，不建立 Claim/Provenance

优点：实现简单、输出快。

缺点：无法审计、重建、纠错和评估；一次错误会污染后续匹配和计划。

结论：不采用。

### 方案 D：立即引入 RAG、分布式数据库和 Multi-Agent

优点：技术栈完整，展示效果强。

缺点：领域契约尚未稳定，复杂基础设施会掩盖数据模型和业务闭环问题，也无法证明技术收益。

结论：不采用。按 roadmap 分阶段引入并保留基线。

## 影响

### 收益

- 项目核心对象统一，画像、匹配、学习计划和反馈共享同一证据基础。
- v0.1/v0.2 投入得到保留。
- LangGraph、RAG、分布式存储和 Multi-Agent 都有可解释的业务职责。
- 项目能够形成有深度的面试叙事和可演示产物。

### 成本

- v0.3 需要先投入领域契约和 Evidence Store，真实岗位检索会后移。
- 状态、Prompt、Eval 和存储接口需要版本管理。
- 个人材料带来隐私、删除和访问控制要求。

### 约束

- 原始 Artifact 不可被派生输出覆盖。
- 事实性 Claim 必须引用 Fragment；推断必须标记。
- 已完成版本文档保留，变化通过新 RFC/ADR 记录。
- 新技术必须有简单基线和量化验收。
