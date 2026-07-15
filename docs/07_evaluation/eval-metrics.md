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
