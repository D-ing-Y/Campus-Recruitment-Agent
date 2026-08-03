# RFC-0013：Claim 生命周期与增量画像投影

状态：Accepted  
日期：2026-08-02

## 1. 方案

在 Evidence Store 与 CandidateProfileProjector 之间增加纯确定性的 Claim Resolution。它接收
`candidate_id + current_resume_evidence_id + all claims`，返回当前可投影 Claim 和安全的 resolution
summary。Projector 不再自行读取全部 active Claim。

## 2. Claim 来源与时间

新 Claim 保存 `origin_kind`、`origin_ref`、`effective_at` 和 `supersedes_claim_ids`。来源枚举为：

```text
resume_evidence / conversation_response / feedback_event / supplemental_document / legacy
```

旧 `supersedes_claim_id` 仅作兼容读取。Resume origin_ref 是 ResumeEvidenceSnapshot ID；conversation
和 supplemental 使用 Artifact/Response 证据引用；feedback 使用 FeedbackEvent ID。旧 Claim 缺失
effective_at 时按 created_at 处理。

## 3. 当前集合选择

1. 只处理 active Claim；
2. resume_evidence 只选择当前 Snapshot；
3. conversation/feedback/supplemental 跨 Resume 版本保留；
4. legacy human/feedback 保留；存在当前 Resume 时排除 legacy model extraction；
5. 有 active successor 的 ancestor 不参与投影；
6. 所有排除和选择保存 reason code，不保存 Claim 内容。

## 4. 语义解析

- capability 只按 canonical level 判断事实值，raw label/level 是说明元数据；
- experience kind 按 kind/context 判断；
- `YYYY` 与同年 `YYYY-MM` 是 refinement，选择更精确值；不同月份仍冲突；
- 字符串执行 NFKC 与空白归一；其余值使用 canonical JSON。

等价 Claim 共同提供 supporting IDs。代表值按显式人工修订、当前 Resume、用户回答、确认反馈、
补充材料的顺序选择；该顺序不能覆盖不同语义值。跨来源不同值进入现有 conflict/HITL。

## 5. Supersede 与快照

一次 correction 创建一个 successor，允许引用多个同 subject/predicate active predecessor；保存 successor
和更新 predecessor lifecycle 必须处于同一 SQLite 事务。CandidateProfile 保存
`evidence_basis_ids=[current_resume_evidence_id]`，旧 Snapshot 不修改。

## 6. CLI 恢复

failed session 下一步为 `session.resume`。恢复后根据 current refs 选择 `resume.import` 或
`candidate.build`；有 Candidate pending request 时仍为 `candidate.resume`。不新增命令。

## 7. 拒绝的方案

- 仅按最新 created_at 取值：会静默覆盖跨来源冲突；
- 只保留当前 Resume Claim：会丢失长期对话和反馈；
- 建通用时态知识图谱：超出单机 v0.7.1 的必要范围；
- 批量猜测 legacy 来源：可能制造不可审计的错误归属。
