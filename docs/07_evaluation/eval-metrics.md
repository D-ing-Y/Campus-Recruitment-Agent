# Eval Metrics

## Runtime 与结构化输出

- `schema_valid_rate`
- `retry_rate`
- `tool_success_rate`
- `node_success_rate`
- `interrupt_resume_rate`
- `checkpoint_recovery_rate`

## 证据与画像

- `evidence_trace_rate`
- `unsupported_claim_rate`
- `citation_precision`
- `fragment_locator_valid_rate`
- `extraction_completeness`
- `profile_correction_rate`
- `contradiction_rate`

## 检索与 RAG

- Recall@K
- MRR / NDCG
- `rerank_gain`
- `groundedness`
- `no_answer_accuracy`
- retrieval latency / token cost

## 匹配与计划

- `hard_constraint_accuracy`
- `gap_label_accuracy`
- `match_calibration`
- `recommendation_coverage`
- `plan_evidence_trace_rate`

## 存储与分布式执行

- `duplicate_rate`
- `idempotency_violation_count`
- `queue_delay`
- `worker_recovery_rate`
- `storage_partial_failure_recovery_rate`
- end-to-end latency / cost

## 版本要求

- v0.1/v0.2 保留现有 smoke/eval 指标。
- v0.3 起每个版本至少新增一组与业务目标直接相关的 eval。
- RAG、分布式存储和 Multi-Agent 必须与简单基线比较，不只报告“成功运行”。

## v0.4 Candidate Profile Graph 指标

| 指标 | 定义 | 文档验收目标 |
| --- | --- | ---: |
| `candidate_route_accuracy` | 与 gold next action 一致的 fixture 比例 | 100% L0/L1 固定集 |
| `high_value_gap_recall` | gold 高价值 gap 被识别的比例 | ≥ 90% |
| `question_actionability_rate` | 可由用户直接回答且绑定 open gap 的问题比例 | 100% |
| `redundant_question_rate` | 与已问/已答问题语义重复的比例 | 0% 固定集 |
| `interrupt_resume_success_rate` | 合法中断可用相同 thread 恢复并继续的比例 | 100% |
| `checkpoint_recovery_rate` | 重建 Graph 后从持久化 checkpoint 恢复的比例 | 100% 固定故障集 |
| `profile_evidence_coverage_rate` | 非 unknown 事实字段带有效 Claim 引用的比例 | 100% |
| `profile_correction_trace_rate` | 纠正可回溯到 response、新 Claim 和被替代 Claim 的比例 | 100% |
| `resume_idempotency_violation_count` | 重复 resume 造成的重复 Artifact/Claim/snapshot 数量 | 0 |
| `max_loop_termination_rate` | 达到硬预算后正确终止的案例比例 | 100% |

说明：

- 上述目标先用于可提交的 L0/L1 固定 fixture，不声称代表开放世界真实用户质量。
- deterministic evaluator 是回归基线；接入真实 LLM 后单独报告同一数据集结果、重试、延迟和 token 成本。
- `is_sufficient` 评价的是画像是否可诚实进入下一阶段，不评价 Offer 概率或岗位匹配度。
