# RFC-0004: 有状态候选人画像 Graph

状态：Implemented
日期：2026-07-17
实现验收：2026-07-18，68 项测试通过
关联需求：`docs/03_requirements/v0.4-candidate-profile-graph.md`
关联 ADR：`docs/05_adr/0004-use-stateful-candidate-profile-subgraph.md`

## 1. 背景

v0.3 已建立统一证据层，但当前 Evidence Pipeline 仍是独立模块。候选人材料的类型、完整度和可信度不固定，无法用单条线性流程预先决定需要读取哪些材料、询问哪些问题以及何时停止。

v0.4 将候选人画像实现为固定业务边界内的动态状态机：人定义安全边界、节点集合和停止条件，LLM 在边界内评价未知项并建议下一动作，确定性代码验证路由和预算。

## 2. 目标

- 把 v0.3 Evidence Pipeline 接入真实 LangGraph subgraph。
- 支持材料增量进入、画像充分性评价、定向提问和用户纠正。
- 使用 conditional edge、loop、checkpoint、interrupt/resume。
- 保证所有画像事实可回溯，所有循环可终止，所有恢复写入幂等。
- 保留 v0.1/v0.2 Graph 作为回归基线。

## 3. 非目标

- 岗位画像、匹配、学习计划和反馈闭环。
- OCR、复杂 PDF 版式、完整代码仓库分析。
- RAG、分布式存储、远程任务队列和 Multi-Agent。
- 外部 MCP 是未来可选 Tool adapter，不是 v0.4 完成条件。

## 4. 总体方案

```text
START
  ↓
initialize_profile_run
  ↓
ingest_pending_materials
  ↓
extract_and_validate_claims
  ↓
project_candidate_profile
  ↓
assess_profile_sufficiency
  ↓
route_next_action
  ├─ read_more ───────────────→ ingest_pending_materials
  ├─ ask_user ────────────────→ plan_human_interaction
  ├─ request_more_materials ──→ plan_human_interaction
  ├─ finalize_with_unknowns ──→ finalize_profile
  ├─ complete ────────────────→ finalize_profile
  └─ fail ────────────────────→ finalize_profile

plan_human_interaction
  ↓
interrupt_for_user
  ↓ resume
archive_human_input
  ├─ answer/correction → project_candidate_profile
  ├─ files             → ingest_pending_materials
  ├─ skip              → assess_profile_sufficiency
  └─ cancel            → finalize_profile
```

`CareerIntent` 由独立 repository/schema 保存。v0.4 可以在初始化或 resume 时更新它，但它不进入候选人能力充分性判定，也不修改 Candidate Claim。

## 5. 状态边界

`CandidateProfileGraphState` 只保存引用和决策：

```text
run_id, thread_id, user_id, candidate_id
input_paths, pending_artifact_ids, active_artifact_ids, processed_artifact_ids
fragment_ids, claim_ids, candidate_profile_snapshot_id
sufficiency_assessment, information_gaps
question_plan, pending_interaction, resume_input
next_action, status
budgets, counters
llm_calls, tool_results, trace, errors
```

完整 Artifact、Fragment、Claim 和 ProfileSnapshot 通过 repository 读取。`resume_input`
只允许短暂存在：`archive_human_input` 成功后必须清空，只保留新 evidence ID 和非敏感摘要。

Reducer：

- `trace`、`errors`、`llm_calls`、`tool_results`：append。
- evidence ID 集合：稳定去重并保持首次出现顺序。
- `sufficiency_assessment`、`question_plan`、`pending_interaction`、`next_action`：replace。
- `budgets`：只读配置；`counters`：由节点以确定性增量更新。
- `candidate_profile_snapshot_id`：replace，旧版本留在 ProfileRepository。

## 6. 节点职责

### 6.1 `initialize_profile_run`

- 校验身份、路径、预算和 `thread_id`。
- 读取最新 profile snapshot 和已存在证据引用。
- 将新路径加入待摄取集合。
- 不执行 LLM 调用。

### 6.2 `ingest_pending_materials`

- 通过 ToolRegistry 调用真实本地摄取/文本抽取工具。
- 先归档 Artifact，再提取文本和 Fragment。
- PDF 使用页码 locator；Markdown/TXT/README 使用行号或字符范围。
- 无文本 PDF 返回 `unsupported_input`，不伪造解析结果。
- 工具失败按 `retryable`、`needs_user_action`、`fatal` 分类。

### 6.3 `extract_and_validate_claims`

- 只加载授权 Fragment。
- 使用版本化 structured output 提取 Candidate Claim。
- 通过 ClaimValidator 校验引用、owner、类型和 schema。
- 有效 Claim 幂等写入；非法 Claim 进入错误摘要但不进入画像。

### 6.4 `project_candidate_profile`

- 从当前 subject 的 active Claim 重建 CandidateProfile。
- 不允许 LLM 直接修改数据库中的画像。
- Claim 集合发生语义变化时创建递增 snapshot。
- 未变化的重放复用现有 snapshot。

### 6.5 `assess_profile_sufficiency`

输入是 profile 摘要、Claim/证据覆盖摘要、未处理材料摘要和剩余预算，不是完整原文。

输出：

- 分维度结果；
- 高价值 `InformationGap`；
- 证据冲突；
- 建议 `next_action`；
- 可解释原因和 confidence。

v0.4 默认保留 deterministic mock evaluator，真实 provider 仍通过相同 schema。充分性表示画像是否可诚实用于后续职业探索，不表示候选人是否达到某个岗位要求。

### 6.6 `route_next_action`

路由采用“两段式决策”：

1. LLM 或 mock evaluator 提出枚举动作。
2. 确定性 policy 校验动作、数据可用性、用户选择和预算。

优先级：

1. fatal error → `fail`；
2. 达到硬预算 → `finalize_with_unknowns`；
3. 有与高价值缺口相关的未处理材料 → `read_more`；
4. 需要用户可直接回答的信息 → `ask_user`；
5. 需要可核验材料或解析失败 → `request_more_materials`；
6. 无阻塞缺口 → `complete`；
7. 继续获取信息价值低 → `finalize_with_unknowns`。

### 6.7 `plan_human_interaction`

- 每轮最多选择 `max_questions_per_interrupt` 个问题。
- 同一字段、同一含义的问题不得重复。
- 每个问题说明为什么问、影响哪个画像字段以及允许用户跳过。
- 需要文件时说明支持格式，不诱导用户提交无关敏感信息。

### 6.8 `interrupt_for_user`

- 使用 LangGraph `interrupt()` 返回 `HumanInteractionRequest`。
- request 必须有稳定 `request_id`，由 thread、轮次、动作和问题计划 hash 派生。
- 节点在 resume 时可能重新执行，因此 interrupt 前不得执行非幂等写操作。

### 6.9 `archive_human_input`

- 校验 `thread_id`、`request_id`、响应动作和 owner。
- 回答文本保存为 `conversation_response` Artifact 与 Fragment。
- 补充文件走标准 ingestion。
- 回答根据 QuestionPlan 的目标字段生成 `user_reported` Claim；纠正生成新 Claim 并设置 `supersedes_claim_id`。
- 使用 `response_id`/内容 hash 保证重复 resume 幂等。
- 成功后清空 `resume_input`；回答/纠正直接进入画像重建，补充文件进入标准摄取循环。

### 6.10 `finalize_profile`

- 保存最终状态、最新 snapshot 引用、remaining gaps 和完成原因。
- 允许 `completed_with_unknowns`，不把 unknown 当作系统错误。
- 输出报告只包含必要摘要和 evidence refs。

## 7. CandidateProfile 与充分性模型

CandidateProfile v0.4 继续保留 v0.3 的教育、能力、经历、可迁移能力、unknown 和 conflicts，并增加：

- 字段级 status/confidence/supporting claim；
- responsibility boundary；
- evidence coverage summary；
- profile completion reason；
- previous snapshot reference。

`InformationGap`：

```text
gap_id, target_path, category, description
importance, uncertainty, answerability, evidence_cost
information_value, preferred_action
related_claim_ids, related_artifact_ids
status
```

参考信息价值：

```text
information_value =
  importance × uncertainty × answerability
  - evidence_cost
```

该公式只用于稳定排序和基线。LLM 可补充理由，但不能突破动作枚举和预算。

## 8. Human-in-the-loop 协议

中断类型：

- `answer_questions`
- `provide_materials`
- `review_profile`

用户动作：

- `answer`
- `upload`
- `correct`
- `confirm`
- `skip`
- `cancel`

`review_profile` 只在高影响推断、冲突或调用方显式请求时使用，不强制每次运行额外中断。

用户回答证据化顺序：

```text
resume payload
  → validate request/owner
  → immutable Artifact
  → Fragment
  → user_reported Claim
  → ClaimValidator
  → ProfileSnapshot
```

checkpoint 只保存工作流状态，不替代上述证据链。

## 9. Checkpoint 与恢复

- 本地 CLI/集成运行使用 LangGraph SQLite checkpointer。
- 单元测试可使用 `InMemorySaver`。
- checkpointer 在 Graph compile 时注入。
- 每次 invoke/resume 使用 `configurable.thread_id`。
- Evidence Repository 与 checkpoint DB 分离，前者是事实层，后者是执行快照。
- 中断前后的写操作都必须幂等，因为节点可能重放。
- 恢复测试必须覆盖进程重建 Graph 后继续相同 thread。

## 10. Tool 与存储接口

v0.4 真实 Tool：

```text
candidate.ingest_material
evidence.extract_pdf_text
evidence.extract_plain_text
evidence.create_fragments
evidence.extract_candidate_claims
evidence.archive_user_response
profile.project_candidate
profile.load_candidate
profile.diff_candidate_versions
```

Tool 通过现有 `ToolRegistry` 调用 repository/blob store。外部 MCP 或插件未来可以实现相同 Tool contract，但不允许其返回的摘要绕过原始证据归档。

## 11. 错误与安全回退

错误类型：

- `validation_error`
- `unsupported_input`
- `permission_denied`
- `llm_output_error`
- `tool_retryable_error`
- `storage_error`
- `checkpoint_error`
- `budget_exhausted`

安全回退：

- LLM 路由非法：使用确定性 policy。
- 单个材料解析失败：保留错误并继续其他材料。
- 所有材料不可读：interrupt 请求支持格式或以 unknown 完成。
- checkpoint 失败：不得声称可恢复；返回 fatal error。
- 证据写入失败：不得更新 profile snapshot。

## 12. 测试与评估

### 单元测试

- schema、reducer、预算 policy、问题去重；
- 文本提取和 locator；
- route guard；
- request/response 校验；
- correction/supersedes。

### 集成测试

- 充分材料无中断完成；
- 不足材料 interrupt/resume 后完成；
- 补充文件后重新摄取；
- 用户 skip 后 unknown 完成；
- 纠正后新 snapshot 与 version diff；
- SQLite checkpoint 跨 Graph 实例恢复；
- 重复 resume 不重复写入；
- 最大循环终止。

### Eval

使用 L0/L1 固定 fixture，报告 requirements 中的 v0.4 指标，并保留 deterministic baseline 与 LLM provider 的可比较接口。

## 13. 迁移策略

- 不删除 `agent/graph.py`、`AgentState` 或 `SearchGoal`。
- 新 Graph 位于 `workflows/candidate_profile/`。
- v0.3 schema 继续可读取；v0.4 通过新 schema version 和 snapshot 版本演进。
- v0.4 先以独立 subgraph/CLI 或测试入口验收，v1.0 再接入完整 Parent Graph。

## 14. 风险

- 过度追问：通过信息价值、问题去重、用户 skip 和硬预算限制。
- LLM 误判充分性：保留 deterministic baseline、gold route eval 和可解释分维度结果。
- interrupt 重放导致重复写入：所有外部写入使用 request/response 幂等键。
- checkpoint 与证据层语义混淆：文档和代码明确拆分执行状态与事实状态。
- PDF 提取质量不稳：v0.4 限定文本型 PDF，失败显式请求其他格式，不假装支持 OCR。
