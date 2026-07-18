# Campus Job Agent 总体架构

版本：v0.4 实现基线
日期：2026-07-18

## 1. 项目定位

本项目是一个证据驱动的垂直求职 Agent：

> 原始材料进入统一证据层，Agent 持续构建候选人画像和岗位需求画像，结合独立的求职意图比较差距，在用户确认下完成岗位探索、准备计划和笔面试反馈更新。

系统不是招聘搜索引擎，也不是单次简历分析工具。它是一个长期、可恢复、可追溯的状态估计与决策系统。

## 2. 核心设计原则

### 2.1 证据是事实源，画像是派生状态

- PDF、网页、项目文件、用户回答和面试反馈以不可变 Artifact 保存。
- LLM 只能从已提供证据提取 Claim；事实性 Claim 必须引用 Fragment。
- CandidateProfile、RoleProfile、GapAssessment 和 LearningPlan 均可从证据重建。
- prompt、模型或解析器升级后，不需要重新采集即可重放派生流程。

### 2.2 两个画像与一个意图模型

- `CandidateProfile`：用户当前的能力、教育、经历和可证明证据。
- `RoleProfile`：具体岗位或岗位族的资格、能力、加分项和招聘筛选信号。
- `CareerIntent`：岗位、城市、薪资、行业、风险和硬性/可协商约束。

求职偏好变化不应被误写为能力变化；能力变化、新证据到达和模型修正也必须区分。

### 2.3 确定性骨架与自主性岛屿

代码负责：

- schema、状态更新、权限、预算、评分公式、去重、版本和安全边界；
- 硬性条件判断、数据校验和停止上限。

LLM Agent 负责：

- 判断当前最重要的未知项；
- 选择继续读材料、提问、检索、换词、换源或停止；
- 从证据提取结构化 Claim；
- 识别证据冲突、提出画像假设和解释差距。

用户负责：

- 职业偏好、目标选择和高影响判断；
- 确认或纠正画像；
- 授权登录、Cookie/cURL 导入和外部高风险操作。

## 3. v0.1/v0.2 复用边界

### 3.1 v0.1 Mini Runtime

可复用：

- LangGraph `StateGraph` 入口；
- 节点只通过 State 传递数据的约束；
- ToolRegistry、Executor、trace helper、ReportWriter、CLI 和测试组织。

需要演进：

- 当前线性 Graph 是 smoke test，不是最终父图；
- 当前 `AgentState` 是最小状态，不是最终领域状态；
- 当前 Planner 固定生成 mock task，将改为按未知项选择动作；
- 当前 Verifier 只验证字段存在，将改为阶段完成条件和证据充分性评价。

### 3.2 v0.2 LLM 基础设施

可复用：

- Provider 抽象与 mock/OpenAI-compatible 实现；
- prompt/schema 版本；
- JSON/Pydantic、重试、缓存和 LLMCallRecord；
- 密钥不进入 state、trace、cache 和报告的安全约束。

需要演进：

- 将 `parse_search_goal_with_llm` 提炼为泛型 structured output 调用；
- `SearchGoal` 逐步迁移为 `CareerIntent`；
- 新增 Claim、Profile、Role、Gap 和 Evaluator 的版本化 prompt/schema。

结论：保留同一仓库和历史版本；v0.4 在 v0.3 证据基础上实现首个有状态业务 subgraph。

## 4. 总体分层

```text
CLI / Web / Desktop Codex
        ↓
Application Service / Task API
        ↓
LangGraph Parent Graph
        ↓
Candidate / Role / Matching / Preparation Subgraphs
        ↓
Agent Runtime + Middleware + Tool Registry
        ↓
Evidence Ingestion / Retrieval / Crawlers / Scoring Tools
        ↓
Evidence Store / Profile Store / Checkpoint Store / Vector Index
        ↓
LLM Provider / Embedding / Reranker
        ↓
Eval / Trace / Metrics / Audit
```

## 5. 核心领域对象

### 5.1 EvidenceArtifact

表示不可变原始材料：

- 用户上传的简历、论文、项目说明和反馈；
- 招聘JD、企业官网和招聘平台页面；
- 牛客、小红书等社区经验帖；
- Graph 生成的最终报告不作为其自身事实来源。

最少字段：

```text
artifact_id, owner_id, source_type, content_type, source_url,
original_name, raw_uri, text_uri, content_hash,
published_at, retrieved_at, parser_name, parser_version, metadata
```

### 5.2 EvidenceFragment

表示可精确引用的片段：

```text
fragment_id, artifact_id, locator_type, locator,
text, text_hash, embedding_ref, metadata
```

`locator` 可以是页码、行号、DOM selector、JSONPath 或字符范围。

### 5.3 EvidenceClaim

表示结构化声明：

```text
claim_id, subject_id, predicate, value,
claim_type, evidence_fragment_ids, confidence,
extractor, prompt_version, schema_version,
status, created_at, supersedes_claim_id
```

`claim_type` 至少区分：

- `observed_fact`：原文明确表达；
- `user_reported`：用户自述；
- `model_inference`：模型推断；
- `feedback_signal`：练习、笔试或面试反馈。

### 5.4 CandidateProfile

包含：

- 教育与基本资格；
- 技能、熟练度和能力证据；
- 科研、项目、实习和责任边界；
- 可迁移能力；
- 未知项、矛盾项和画像置信度；
- snapshot/version 和 supporting claim IDs。

### 5.5 CareerIntent

包含：

- 岗位假设；
- 城市、薪资、行业、公司类型；
- 工作强度、稳定性、成长性等偏好；
- hard constraints 与 negotiable preferences；
- 用户确认状态和版本。

### 5.6 RoleProfile

分为 `job_instance` 和 `role_family`：

- 硬性资格；
- 工作职责与能力要求；
- 加分项；
- 笔试/面试筛选信号；
- 公司特异项；
- 市场分布和证据覆盖；
- 适用时间、地域和置信度。

### 5.7 GapAssessment

至少区分：

- capability gap；
- evidence gap；
- preference conflict；
- epistemic uncertainty。

匹配分数表示“岗位要求的证据覆盖度”，不表示 Offer 概率。

## 6. LangGraph 父图

```text
START
  ↓
intake_evidence
  ↓
candidate_profile_subgraph
  ├─ insufficient → read_more / ask_user / keep_unknown ─┐
  └─ sufficient ------------------------------------------┘
  ↓
role_profile_subgraph
  ├─ insufficient → search_more / change_source / stop ──┐
  └─ sufficient ------------------------------------------┘
  ↓
match_profiles
  ↓
human_decision_interrupt
  ├─ revise candidate evidence → candidate_profile_subgraph
  ├─ revise intent → role_profile_subgraph
  └─ select targets → preparation_subgraph
                          ↓
                     feedback_subgraph
                          ├─ update candidate
                          ├─ update role
                          └─ replan preparation
```

### 6.1 Parent State

State 只保存结构化状态和引用，不内嵌全部原文：

```text
run_id, thread_id, user_id, current_stage,
career_intent, candidate_profile_ref, role_profile_refs,
active_artifact_ids, active_claim_ids,
unresolved_questions, gap_assessments,
selected_target_ids, learning_plan_ref, feedback_event_ids,
next_action, budgets, checkpoints, errors, trace_refs
```

### 6.2 Subgraph 与 Sub-Agent

- Subgraph 是固定业务边界中的状态机，v0.4 起使用。
- Sub-Agent 是运行时动态创建的隔离工作单元，只有 v1.2 的评估证明有收益时使用。
- 多来源检索若任务列表已知，优先普通并行工具调用；子任务不可预先确定时才使用动态 worker。

### 6.3 v0.4 Candidate Profile Subgraph

```text
START
  → initialize_profile_run
  → ingest_pending_materials
  → extract_and_validate_claims
  → project_candidate_profile
  → assess_profile_sufficiency
  → route_next_action
      ├─ read_more → ingest_pending_materials
      ├─ ask_user / request_more_materials
      │      → plan_human_interaction
      │      → interrupt_for_user
      │      → archive_human_input
      │      → evidence/profile loop
      ├─ finalize_with_unknowns → finalize_profile
      ├─ complete → finalize_profile
      └─ fail → finalize_profile
```

动态决策采用“模型建议、代码裁决”：

- LLM 或 deterministic evaluator 识别高价值 Information Gap，并提出枚举动作。
- 确定性 policy 检查是否存在未处理材料、问题是否可回答、用户是否跳过、预算是否耗尽。
- Graph 只能在预定义节点和动作之间路由，LLM 不得生成任意工具名或突破预算。

执行状态与事实状态严格分离：

- LangGraph checkpointer 保存节点边界、pending interrupt、计数器和引用。
- Evidence Store 保存不可变材料、用户回答、Fragment 和 Claim。
- Profile Store 保存可从 Claim 重建的 CandidateProfile snapshot。
- resume 载荷先由 `archive_human_input` 证据化，随后才能更新画像。

v0.4 本地运行使用 SQLite checkpointer，测试可使用内存 checkpointer。相同
`thread_id` 用于恢复同一任务；中断前后的外部写入必须具备稳定幂等键。

实现状态：上述 subgraph 已位于 `src/campus_job_agent/workflows/candidate_profile/`；
真实本地 Tool 位于 `tools/candidate_profile.py`，Evidence/Profile metadata 与
LangGraph checkpoint 分库保存。文本型 PDF 使用 `pypdf`，持久化恢复使用官方
`langgraph-checkpoint-sqlite`。2026-07-18 的全量验收为 68/68 通过。

## 7. 统一证据管线

```text
collect/upload
  → archive raw artifact
  → extract text
  → split into fragments
  → structured claim extraction
  → claim validation
  → profile projection
  → profile sufficiency evaluation
```

规则：

- 招聘平台和企业官网是岗位存在、职责和硬性要求的主要证据。
- 社区来源用于笔面试、薪资、氛围和实践信号，必须保留较低或独立置信度。
- 原始 HTML/文本先归档再总结；解析和 prompt 变化不得强制重新采集。
- 未知值显式保存为 unknown/null/空数组，不允许模型补齐不存在的事实。

## 8. Hybrid RAG 架构

RAG 用于“围绕当前未知项找到最相关证据”，而不是给聊天界面增加一个向量库。

```text
query from graph state
  → query rewrite / decomposition
  → metadata filter
  → sparse/full-text retrieval
  → dense vector retrieval
  → fusion
  → rerank
  → context packing with citations
  → grounded structured output
```

核心场景：

- 从论文、项目和简历中检索支持某项能力的片段；
- 从同类JD中检索岗位族能力要求；
- 从面经中检索与目标公司/岗位/阶段匹配的问题；
- 在反馈后检索能解释新差距的历史证据。

必须保留的基线：

- 无检索；
- 仅关键词/FTS；
- 仅 dense；
- hybrid + reranker。

评估：Recall@K、MRR/NDCG、citation precision、groundedness、无答案正确率、延迟和成本。

## 9. 存储演进

### 9.1 本地阶段

- SQLite：artifact/fragment/claim/profile 元数据。
- 本地文件：raw、text、report 和 cache。
- 本地向量索引或 SQLite/轻量 vector adapter。

先验证领域契约和图闭环，避免存储系统掩盖业务错误。

### 9.2 分布式阶段

```text
PostgreSQL
  ├─ task / thread / claim / profile / version / transaction metadata
S3 or MinIO
  ├─ immutable raw artifacts / extracted text / reports
pgvector or vector database
  ├─ fragment embeddings and metadata index
Redis + task queue
  ├─ async jobs / rate limit / notification / short cache
LangGraph checkpointer
  └─ durable graph state
```

必须实现和解释：

- local/remote repository abstraction；
- 事务边界与对象上传失败补偿；
- 幂等键、hash 去重和 exactly-once 的业务近似；
- 乐观并发或版本检查；
- worker 崩溃、消息重投和服务重启恢复；
- 数据删除、权限和个人材料隔离。

## 10. LLM、工具与评分边界

LLM：

- 证据 Claim 提取；
- 画像假设；
- 未知项优先级；
- 查询规划；
- 冲突解释和学习路线叙述。

确定性工具：

- 文档解析、归档、hash、去重；
- schema 校验、硬性条件、评分和版本；
- 数据库、对象存储和向量检索；
- 预算、重试和权限。

评分建议：

```text
coverage = Σ(requirement_weight × supported_level) / Σ(requirement_weight)
```

输出必须同时展示 hard constraint、coverage 区间、confidence、关键缺口和 supporting evidence。

## 11. Eval 与可观测性

跨版本指标：

| 维度 | 指标示例 |
| --- | --- |
| 结构化输出 | schema_valid_rate、retry_rate |
| 证据 | evidence_trace_rate、citation_precision、unsupported_claim_rate |
| 画像 | extraction_completeness、profile_correction_rate、contradiction_rate |
| 检索 | Recall@K、NDCG、rerank_gain、no-answer accuracy |
| Graph | node_success_rate、loop_count、interrupt_resume_rate、recovery_rate |
| 匹配 | hard_constraint_accuracy、gap_label_accuracy、calibration |
| 系统 | latency、token cost、queue delay、storage error rate、duplicate rate |

所有新技术都必须与简单基线比较，保留失败案例和决策解释。

## 12. 安全与隐私

- API key、Cookie、Authorization header 和真实个人材料不得进入 Git。
- 登录受限网站使用真实 Chrome 正常登录并由用户主动导入 Cookie/cURL，不绕过验证。
- 用户文件按 user/thread 隔离；服务器阶段使用对象前缀、数据库权限和审计日志。
- 外部写操作、高风险操作和不可逆操作必须 interrupt 确认。
- 原始证据、派生画像、embedding 和日志都必须支持按用户删除。

## 13. 目录演进建议

```text
src/campus_job_agent/
  agent/
    root_graph.py
    state.py
    runtime.py
  workflows/
    candidate_profile/
    role_profile/
    matching/
    preparation/
    feedback/
  schemas/
    evidence.py
    candidate.py
    intent.py
    role.py
    gap.py
    storage.py
  evidence/
    ingestion.py
    extractors.py
    repositories.py
  retrieval/
    sparse.py
    dense.py
    hybrid.py
    reranker.py
  storage/
    local/
    postgres/
    object_store/
    vector_store/
  tools/
  llm/
  evals/
```

旧 `agent/graph.py` 在新父图通过验收前保留，继续作为 v0.1/v0.2 smoke test。

## 14. 面试项目叙事

推荐表达：

> 我从零实现了一个证据驱动的求职垂类 Agent。系统用 LangGraph 管理长期状态、子图、人工介入和故障恢复；以不可变原始材料、Claim 和 Provenance 构建候选人/岗位双画像；用 Hybrid RAG 检索与当前未知项相关的证据；再通过可解释匹配和反馈闭环生成准备路线。系统从本地 SQLite/文件演进到 PostgreSQL、对象存储、向量索引和异步 worker，并用 eval 验证每项复杂度是否带来真实收益。

面试时应能够展示：

- 一条 Claim 如何回溯到 PDF/JD/面经片段；
- Graph 为什么回退、提问或停止；
- RAG 各基线的质量与成本对比；
- 分布式存储中的幂等、恢复和一致性取舍；
- Multi-Agent 相比单 Agent 是否真正改善结果。
