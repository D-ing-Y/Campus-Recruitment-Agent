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

## v0.5 Role Profile Graph 指标

| 指标 | 定义 | 文档验收目标 |
| --- | --- | ---: |
| `role_route_accuracy` | Graph next action 与固定集 gold 一致的比例 | 100% |
| `raw_before_parse_rate` | 已解析 source document 中先存在 raw Artifact 的比例 | 100% |
| `normalized_job_schema_valid_rate` | 岗位归一化结果通过 v0.5 schema 的比例 | 100% |
| `job_dedup_precision` | 被合并 job pair 中 gold duplicate 的比例 | 100% 固定集 |
| `job_dedup_recall` | gold duplicate pair 被正确聚类的比例 | 100% 固定集 |
| `official_verification_coverage` | 进入画像的第三方 job cluster 中具有明确官网核验状态的比例 | 100% 固定集 |
| `job_identity_link_precision` | confirmed identity link 中确为同一岗位的比例 | 100% 固定集 |
| `field_resolution_accuracy` | 字段选择、状态和 reason 与 gold 一致的比例 | 100% 固定集 |
| `official_not_found_false_rejection_count` | 仅因官网未找到而错误删除/关闭第三方岗位的数量 | 0 |
| `hard_scope_exclusion_precision` | excluded_hard_scope 中确有证据违反 hard scope 的比例 | 100% |
| `role_claim_trace_rate` | 已接受 Role Claim 可回溯有效 Fragment 的比例 | 100% |
| `source_authority_violation_count` | 被接受但来源无权支持 predicate 的 Claim 数 | 0 |
| `role_family_prevalence_accuracy` | prevalence/分子/分母与 gold 完全一致的聚合项比例 | 100% |
| `experience_scope_accuracy` | experience scope_level 与 gold 一致的比例 | ≥ 95% |
| `freshness_label_accuracy` | freshness/expired 与 gold 一致的比例 | 100% 固定集 |
| `auth_interrupt_resume_success_rate` | 合法授权/跳过可恢复到正确分支的比例 | 100% |
| `source_run_idempotency_violation_count` | 重复 query/batch 导致的重复事实对象数量 | 0 |
| `credential_secret_leak_count` | State/trace/evidence/report/test snapshot 中秘密值命中数 | 0 |
| `runtime_generated_code_execution_count` | live run 中执行 LLM 生成采集代码的次数 | 0 |
| `source_project_gate_pass_rate` | 被采用上游项目完整通过 license/security/smoke 门禁的比例 | 100% |
| `live_source_smoke_pass_count` | 实际完成并产生 receipt 的目标 live adapter 数 | 3/3 |
| `max_search_loop_termination_rate` | 达到搜索硬预算后正确终止的比例 | 100% |

说明：

- 离线 L0/L1 指标和 live smoke 必须分栏报告。
- live smoke 只证明 adapter/归档/授权链路在验收时可运行，不代表长期稳定或完整覆盖。
- source 临时不可用时报告真实失败，不把 fixture 计入 `live_source_smoke_pass_count`。
- RoleCoverage 评价岗位画像证据，不评价候选人与岗位匹配程度。
