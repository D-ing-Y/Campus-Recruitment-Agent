# Human Interaction Contract

状态：v0.4-v0.7 Implemented / Accepted
日期：2026-07-22

本契约统一 LangGraph `interrupt()` 与 resume 的结构化载荷。v0.4 用于候选人画像提问、补充材料和可选画像复核，后续版本可复用。

## 1. HumanInteractionRequest

```json
{
  "request_id": "hir-<stable-hash>",
  "schema_version": "v0.4",
  "thread_id": "thread-1",
  "run_id": "run-1",
  "user_id": "user-1",
  "interaction_type": "answer_questions",
  "reason": "需要确认项目中的个人职责",
  "questions": [],
  "requested_materials": [],
  "profile_snapshot_id": "snapshot-1",
  "target_paths": ["experiences[exp-1].responsibilities"],
  "related_artifact_ids": ["artifact-1"],
  "related_claim_ids": ["claim-1"],
  "allowed_actions": ["answer", "skip", "cancel"],
  "expires_at": null,
  "created_at": "2026-07-17T00:00:00+08:00"
}
```

`interaction_type`：

- `answer_questions`
- `provide_materials`
- `review_profile`

`request_id` 必须由稳定输入派生，至少包含 thread、interaction round、类型、目标 gap 和问题计划 hash。相同节点重放必须得到相同 request ID。

## 2. RequestedMaterial

```json
{
  "material_id": "material-request-1",
  "gap_id": "gap-1",
  "description": "请补充包含个人职责说明的项目 README 或说明文档",
  "accepted_content_types": ["text/markdown", "text/plain", "application/pdf"],
  "required": false,
  "reason": "现有扫描 PDF 无法提取文字"
}
```

请求不得诱导用户提交身份证号、账号密钥、Cookie 或与画像无关的敏感材料。

## 3. HumanInteractionResponse

```json
{
  "response_id": "response-1",
  "schema_version": "v0.4",
  "request_id": "hir-<stable-hash>",
  "thread_id": "thread-1",
  "user_id": "user-1",
  "action": "answer",
  "answers": [
    {
      "question_id": "question-1",
      "text": "我负责 LangGraph 工作流和评估，未负责爬虫。",
      "declined": false
    }
  ],
  "file_paths": [],
  "corrections": [],
  "confirmation": null,
  "submitted_at": "2026-07-17T00:05:00+08:00"
}
```

`action`：

- `answer`
- `upload`
- `correct`
- `confirm`
- `skip`
- `cancel`

载荷规则：

- `answer` 至少包含一个 answer。
- `upload` 至少包含一个允许访问的本地路径。
- `correct` 至少包含一个 `ProfileCorrection`。
- `confirm` 只对 request 中展示的 snapshot 生效。
- `skip` 可包含被跳过 question/material ID。
- `cancel` 终止本次画像收集，不删除已有证据。

## 4. Resume 校验

恢复前必须校验：

- `thread_id`、`request_id` 和 `user_id` 与 pending request 一致。
- response action 在 `allowed_actions` 中。
- question ID、material request ID 和 correction target 属于当前 request。
- request 未过期或已由 policy 允许恢复。
- 本地文件路径在调用方授权范围内。

实现中授权范围由 Graph 初始化时的 `allowed_path_roots` 固定；resume 只能提交该
范围内且实际存在的文件，不能通过响应扩大授权根目录。

校验失败时不得写 Evidence Store，也不得推进 Graph。

## 5. 回答证据化

用户回答不能只保存在 State。处理顺序：

```text
HumanInteractionResponse
  → canonical response hash
  → EvidenceArtifact(content_type=conversation_response)
  → EvidenceFragment(locator_type=json_pointer)
  → EvidenceClaim(claim_type=user_reported)
  → ClaimValidator
  → CandidateProfile projection
```

Artifact metadata 可保存 `request_id`、`response_id` 和 question IDs，但不得保存密钥或无关敏感上下文。每个回答 Fragment 使用 JSON Pointer 或等效 locator 精确定位。

## 6. 文件与纠正

- `file_paths` 只作为摄取请求；文件必须先复制到不可变 BlobStore。
- 文件 hash 重复时复用已有 Artifact。
- Correction 必须引用 response Artifact/Fragment。
- 新纠正 Claim 使用 `supersedes_claim_id`；旧 Claim 保留历史。
- `remove` 和 `mark_unknown` 也必须生成可审计 Claim/事件，不能直接删字段。

## 7. Interrupt/Resume 语义

- `interrupt_for_user` 节点在调用 `interrupt()` 前不得做非幂等外部写入。
- resume 后该节点可能从头执行，所有 request 构建必须确定性。
- `archive_human_input` 是 resume 后唯一允许消费 response 并写入证据的节点。
- 写入成功后 State 清空 response 正文，只保留 response artifact ID 和摘要。
- Graph 以相同 `thread_id` 继续；新任务不得复用旧 thread ID。

## 8. 幂等

响应幂等键：

```text
sha256(
  thread_id
  + request_id
  + response_id
  + canonical_response_payload
  + schema_version
)
```

- 相同幂等键返回第一次处理结果。
- 同一 `response_id` 携带不同 payload 必须报 `idempotency_conflict`。
- 重复 resume 不重复创建 Artifact、Claim 或 ProfileSnapshot。
- 部分写入失败时不得标记 response 已消费。

## 9. 隐私与日志

- checkpoint 可暂存 resume payload，但成功归档后应清除正文。
- trace 只记录 request/response ID、动作、数量、状态和错误摘要。
- report 不展示完整用户回答，除非用户界面明确需要且数据仍处于本地授权范围。
- checkpoint DB、用户材料和响应 Artifact 默认位于 `data/` 并排除 Git。

## 10. 版本兼容

- 未识别的 schema version 必须拒绝或先迁移。
- 后续版本可增加 interaction type，但不得改变 v0.4 action 的既有语义。
- Parent Graph 复用本契约时，每次 request 仍必须声明 stage 和目标对象引用。

## 11. v0.5 Source Authorization

v0.5 增加 `authorize_source`，不改变 v0.4 既有语义。

### Request

```json
{
  "request_id": "hir-source-auth-1",
  "schema_version": "v0.5",
  "thread_id": "role-thread-1",
  "run_id": "role-run-1",
  "user_id": "user-1",
  "interaction_type": "authorize_source",
  "reason": "该经验来源需要用户正常登录",
  "source_authorization": {
    "source_id": "nowcoder_experience",
    "login_url": "https://www.nowcoder.com/",
    "credential_type": "imported_curl",
    "import_instruction": "在真实 Chrome 正常登录后，将 Copy as cURL 保存到本地秘密目录并执行导入命令",
    "expected_credential_ref_prefix": "local-secret://nowcoder/"
  },
  "allowed_actions": ["authorized", "skip_source", "cancel"],
  "created_at": "2026-07-18T00:00:00+08:00"
}
```

### Response

```json
{
  "response_id": "response-source-auth-1",
  "schema_version": "v0.5",
  "request_id": "hir-source-auth-1",
  "thread_id": "role-thread-1",
  "user_id": "user-1",
  "action": "authorized",
  "source_id": "nowcoder_experience",
  "credential_ref": "local-secret://nowcoder/default",
  "submitted_at": "2026-07-18T00:05:00+08:00"
}
```

新增 action：

```text
authorized
skip_source
```

约束：

- request/response 不得包含 Cookie、Authorization、headers 或 cURL 正文。
- credential import 在 Graph 外完成；resume 只传 ref。
- `authorized` 必须校验 ref 存在、source 匹配和调用权限。
- `skip_source` 将 source ID 写入 skipped set，不得重复中断请求。
- 错误 ref/request/thread/user/action 时 Evidence Store 零写入。
- Source authorization 不是 Evidence Claim，不进入 Candidate/Role Profile。

## 12. v0.6 Comparison Review

v0.6 增加 `review_comparison`，复用既有 identity、resume、checkpoint、幂等和隐私规则。

### Request

```json
{
  "request_id": "hir-comparison-1",
  "schema_version": "v0.6",
  "thread_id": "matching-thread-1",
  "run_id": "matching-run-1",
  "user_id": "user-1",
  "interaction_type": "review_comparison",
  "reason": "请审阅岗位比较结果并选择下一步",
  "comparison_set_id": "comparison-1",
  "input_snapshot_refs": {
    "candidate_profile_snapshot_id": "candidate-snapshot-1",
    "career_intent_snapshot_id": "intent-snapshot-1",
    "job_instance_profile_snapshot_ids": ["role-job-1"]
  },
  "target_summaries": [
    {
      "job_instance_profile_snapshot_id": "role-job-1",
      "gap_assessment_id": "gap-assessment-1",
      "recommended_tier": "needs_clarification"
    }
  ],
  "allowed_target_ids": ["role-job-1"],
  "allowed_actions": [
    "select_targets", "defer_targets", "reject_targets",
    "revise_candidate", "revise_intent", "refresh_role",
    "confirm_and_finish", "cancel"
  ],
  "warnings": ["coverage_is_not_offer_probability"],
  "created_at": "2026-07-22T00:00:00+08:00"
}
```

request 不内嵌完整 CandidateProfile、RoleProfile、用户材料或网页正文。展示层通过 owner 校验后的
repository 读取必要摘要。

### Response

```json
{
  "response_id": "response-comparison-1",
  "schema_version": "v0.6",
  "request_id": "hir-comparison-1",
  "thread_id": "matching-thread-1",
  "user_id": "user-1",
  "action": "select_targets",
  "target_decisions": [
    {
      "job_instance_profile_snapshot_id": "role-job-1",
      "status": "selected",
      "reason_codes": ["evidence_coverage_acceptable"],
      "note": null
    }
  ],
  "candidate_revision": null,
  "intent_revision": null,
  "role_refresh_target_ids": [],
  "submitted_at": "2026-07-22T00:05:00+08:00"
}
```

载荷规则：

- select/defer/reject 至少包含一个对应状态的 target decision。
- `revise_candidate` 只生成交给 v0.4 的 correction/补证请求，不直接修改画像字段。
- `revise_intent` 只允许 CareerIntent 字段 allowlist，并创建新 intent snapshot。
- `refresh_role` 的 target ID 必须属于当前 request。
- `confirm_and_finish` 不创建目标决策，只确认用户已完成本轮审阅。
- 任一输入 snapshot 已 stale 时拒绝 target decision，并返回新的审阅/刷新要求。

### 原子性与幂等

- target decision batch 必须全部校验后原子写入。
- intent revision、impact assessment 和 directive 必须在同一业务事务或可恢复 saga 边界内。
- 相同 response 重放不得重复创建 decision、intent snapshot 或 directive。
- 候选人 revision 的回答证据化继续遵守本契约第 5-8 节。

## 13. v0.7 Preparation Plan Review

新增 `review_preparation_plan`：

```json
{
  "request_id": "hir-plan-1",
  "schema_version": "v0.7",
  "thread_id": "prep-thread-1",
  "run_id": "prep-run-1",
  "user_id": "user-1",
  "interaction_type": "review_preparation_plan",
  "reason": "请确认准备计划的时间约束和活动安排",
  "input_set_id": "prep-input-1",
  "learning_plan_id": "learning-plan-1",
  "package_id": "package-1",
  "constraints_id": "prep-constraints-1",
  "allowed_activity_ids": ["activity-1"],
  "allowed_actions": [
    "accept_plan", "revise_constraints", "exclude_activities",
    "request_activity_revision", "defer_plan", "cancel"
  ],
  "warnings": ["priority_is_not_success_probability"],
  "created_at": "2026-07-27T00:00:00+08:00"
}
```

Response：

```json
{
  "response_id": "response-plan-1",
  "schema_version": "v0.7",
  "request_id": "hir-plan-1",
  "thread_id": "prep-thread-1",
  "user_id": "user-1",
  "action": "revise_constraints",
  "constraints_patch": {"weekly_hours": 10},
  "activity_ids": [],
  "activity_revision_requests": [],
  "submitted_at": "2026-07-27T00:05:00+08:00"
}
```

- constraints patch 使用 allowlist，创建新 constraints。
- exclude/revision 只能引用 request 中 activity。
- response 不能修改 priority factors、gap、role requirement 或 target decision。
- 相同 response 重放不得重复创建 constraints/plan。

## 14. v0.7 Feedback Attribution Confirmation

新增 `confirm_feedback_attribution`。Request 固定引用 feedback event、observation、diagnosis 和
attribution ID，展示最小 evidence excerpt、source authority、alternative explanations、limitations
及预期影响。

allowed actions：

```text
confirm_attributions
relabel_scope
reject_diagnoses
mark_unknown
cancel
```

Response：

```json
{
  "response_id": "response-attribution-1",
  "schema_version": "v0.7",
  "request_id": "hir-attribution-1",
  "thread_id": "feedback-thread-1",
  "user_id": "user-1",
  "action": "relabel_scope",
  "attribution_ids": ["attribution-1"],
  "diagnosis_ids": ["diagnosis-1"],
  "scope_relabels": [
    {"attribution_id": "attribution-1", "subject_scope": "unknown", "subject_ref": null}
  ],
  "submitted_at": "2026-08-03T10:20:00+08:00"
}
```

- response 只能引用 request 中 IDs 和 allowed scope。
- 用户确认不改变原 source authority。
- 校验失败时 Claim、Impact、Directive 零写入。
- feedback 原文已在 Graph 输入阶段归档；checkpoint 成功处理后清除 response 正文。

## 15. v0.7.1 CareerIntent Review

WP2 新增独立的 `review_career_intent` 请求，用于确认模型从原始求职意向中提取的候选结构：

```json
{
  "request_id": "request-intent-<stable-hash>",
  "schema_version": "v0.7.1",
  "thread_id": "intent-thread-1",
  "run_id": "intent-run-1",
  "user_id": "user-1",
  "interaction_type": "review_career_intent",
  "draft_id": "intent-draft-1",
  "summary": {
    "target_roles": ["Agent 开发"],
    "target_role_families": ["ai_agent_engineering"],
    "constraints": []
  },
  "unresolved_fields": ["recruitment_type"],
  "validation_issues": [],
  "allowed_actions": ["confirm", "revise", "cancel"]
}
```

Response 仅允许 `confirm | revise | cancel`；`revise` 必须携带 allowlist patch，其他动作不得携带 patch。
若 draft 仍有 unresolved field 或 validation issue，`confirm` 不能发布 snapshot，Graph 必须再次
interrupt。响应正文先归档为 Evidence Artifact/Fragment，checkpoint 中处理后清除。

- `request_id` 由 thread、draft ID、revision 和当前问题集确定性生成。
- 相同 `response_id + canonical payload` 复用首次结果；不同 payload 报 `idempotency_conflict`。
- 只有完成人工确认才能将 constraint `status` 转为 `confirmed`。
- WP2 完成后创建 `role_research_required` Handoff，不在本 Graph 内调用 Role Graph。

## v0.7.1 WP1.3 Resume Review

Resume 审核使用独立 `ResumeReviewRequest/Response/Receipt`，不复用 Candidate 的 Agent 问答请求。
顺序固定为 BOSS 八区块；简单区块整块审核，非空列表逐记录审核并在最后一条后自动完成区块。动作仅允许
`confirm | correct | remove | retry | cancel`，没有 skip；空区块必须显式确认成 `confirmed_empty`。

`correct` 必须携带 JSON merge patch 和 PDF-source attestation，应用还会验证所有新增非空标量可在
当前 PDF Fragment 文本中找到。非法 identity/request/revision/patch 在消费 LangGraph interrupt 前
拒绝，保证失败输入不会污染 checkpoint。相同 response ID 的 canonical payload（排除提交时间）只写
一次；不同 payload 返回 `idempotency_conflict`。Draft CAS 与 Receipt 插入必须在同一 SQLite 事务。

## v0.7.1 WP1.3.2 Multi-Claim Correction

Candidate conflict correction 可以引用同一 subject/predicate 的多个 active Claim。一次 response 只创建
一个 successor Claim，并在同一事务内把全部 predecessor 标为 superseded；任一 identity、lineage 或
写入校验失败时 successor 和 lifecycle 更新全部回滚。跨来源不同值必须保留到用户完成该确认。
