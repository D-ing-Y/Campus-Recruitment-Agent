# Feedback Event Contract

状态：v0.7 Implemented / Accepted
日期：2026-07-27

本契约定义练习、笔试、面试、申请结果和用户复盘的证据化、归因与影响路由。反馈结果不是自动
因果诊断；单次反馈不能直接改写岗位族画像。

## 1. FeedbackEvent

```json
{
  "feedback_event_id": "feedback-1",
  "schema_version": "v0.7",
  "user_id": "user-1",
  "feedback_type": "mock_interview",
  "source_kind": "evaluator_report",
  "occurred_at": "2026-08-03T10:00:00+08:00",
  "plan_id": "learning-plan-1",
  "activity_id": "activity-1",
  "target_job_profile_ids": ["role-job-1"],
  "stage": "technical_interview",
  "capability_id": "programming.python",
  "suggested_scope": "candidate_capability",
  "raw_artifact_ids": ["artifact-feedback-1"],
  "fragment_ids": ["fragment-feedback-1"],
  "canonical_event_hash": "sha256:...",
  "status": "archived",
  "created_at": "2026-08-03T10:05:00+08:00"
}
```

`feedback_type`：

```text
task_progress
practice_result
mock_interview
written_exam
interview
application_outcome
portfolio_review
user_reflection
other
```

`source_kind`：

```text
self_reported
evaluator_report
platform_result
official_result
system_measurement
imported_document
```

`status`：`received | archived | interpreted | awaiting_confirmation | processed |
completed_with_unknowns | cancelled | failed`。

规则：

- archived 前至少一个 raw Artifact。
- fragment 只能属于 event Artifact。
- related plan/activity/target refs 必须属于同 user。
- event hash 包含内容 hash、时间、类型、source、stage、capability/scope hints 和 refs。

## 2. FeedbackObservation

```json
{
  "observation_id": "observation-1",
  "schema_version": "v0.7",
  "feedback_event_id": "feedback-1",
  "observation_type": "evaluator_comment",
  "value": "回答未说明评估数据集构造。",
  "outcome": null,
  "source_kind": "evaluator_report",
  "authority": "evaluator_observed",
  "fragment_ids": ["fragment-feedback-1"],
  "confidence": 1.0,
  "extractor_version": "feedback_observation_v1"
}
```

`observation_type` 至少包括：

```text
task_status
score
question_asked
behavior_observed
evaluator_comment
platform_outcome
official_outcome
user_reflection
other
```

`authority`：

```text
self_reported
system_measured
evaluator_observed
platform_reported
official_reported
unknown
```

Observation 必须引用 Fragment。它不得包含未在原文中的原因或能力等级结论。

## 3. FeedbackDiagnosis

```json
{
  "diagnosis_id": "diagnosis-1",
  "schema_version": "v0.7",
  "feedback_event_id": "feedback-1",
  "observation_ids": ["observation-1"],
  "diagnosis_type": "candidate_evidence_gap",
  "subject_scope": "candidate_evidence",
  "capability_id": "cap:rag_evaluation",
  "target_job_profile_ids": ["role-job-1"],
  "summary": "当前回答证据未覆盖评估数据集构造。",
  "alternative_explanations": ["时间不足", "问题未要求展开"],
  "limitations": ["单次反馈不能确认整体能力等级"],
  "confidence": 0.7,
  "claim_type": "model_inference",
  "status": "proposed",
  "extractor_version": "feedback_diagnosis_v1"
}
```

`diagnosis_type`：

```text
candidate_capability_signal
candidate_evidence_gap
job_hiring_signal
company_role_signal
role_family_signal_candidate
intent_signal
plan_adjustment_signal
unknown
```

`subject_scope`：

```text
plan_task
candidate_capability
candidate_evidence
job_instance
company_role
role_family_candidate
career_intent
unknown
```

规则：

- diagnosis 至少引用一个 observation。
- 非 unknown diagnosis 必须有 alternative explanations 和 limitations。
- rejection/no offer/fail outcome 没有明确评价时禁止 diagnosis。
- diagnosis 默认 model_inference 或 user_reported，不得伪装 observed fact。

## 4. FeedbackAttribution

```json
{
  "attribution_id": "attribution-1",
  "schema_version": "v0.7",
  "feedback_event_id": "feedback-1",
  "observation_ids": ["observation-1"],
  "diagnosis_ids": ["diagnosis-1"],
  "subject_scope": "candidate_evidence",
  "subject_ref": "candidate-s4",
  "capability_id": "cap:rag_evaluation",
  "target_job_profile_ids": ["role-job-1"],
  "authority": "evaluator_observed",
  "requires_confirmation": true,
  "confirmation_status": "pending",
  "confirmed_by_response_id": null,
  "reason_codes": ["high_impact_candidate_attribution"]
}
```

`confirmation_status`：`not_required | pending | confirmed | relabeled | rejected | unknown`。

- task progress/outcome-only attribution 可不需确认。
- candidate/role/intent 高影响 attribution 默认需要确认。
- user confirmation 不改变 authority，只改变 attribution 是否被当前 workflow 接受。

## 5. Feedback Claim

accepted observation/diagnosis 使用现有 `EvidenceClaim`：

```json
{
  "subject_id": "candidate-1",
  "predicate": "feedback.candidate_evidence_gap",
  "value": {"capability_id": "cap:rag_evaluation", "diagnosis_id": "diagnosis-1"},
  "claim_type": "feedback_signal",
  "evidence_fragment_ids": ["fragment-feedback-1"],
  "confidence": 0.7,
  "prompt_version": "feedback_claim_v1",
  "schema_version": "v0.7"
}
```

边界：

- observation Claim 可保持原 source authority。
- diagnosis Claim 必须保留 inference/confirmation metadata。
- task completion 不创建 `candidate.capability_level` Claim。
- community/user feedback 不创建 official hard requirement Claim。
- old Claim 不被直接覆盖；纠正使用 supersedes。

## 6. FeedbackImpactAssessment

```json
{
  "impact_assessment_id": "feedback-impact-1",
  "schema_version": "v0.7",
  "feedback_event_id": "feedback-1",
  "accepted_attribution_ids": ["attribution-1"],
  "progress_updates": [],
  "candidate_rebuild_required": true,
  "role_instance_refresh_required": false,
  "role_family_aggregation_candidate": false,
  "intent_review_required": false,
  "rematch_required_after_rebuild": true,
  "replan_required": true,
  "reason_codes": ["new_candidate_evidence_may_change_gap"],
  "policy_version": "feedback_impact_v1"
}
```

Impact 由代码根据 accepted attribution 计算。LLM 不输出最终路由。

## 7. FeedbackDirective

```json
{
  "directive_id": "feedback-directive-1",
  "schema_version": "v0.7",
  "directive_type": "candidate_profile_rebuild_required",
  "originating_feedback_event_id": "feedback-1",
  "originating_plan_id": "learning-plan-1",
  "reason_codes": ["new_candidate_feedback_claim"],
  "required_input_refs": ["feedback-claim-1", "candidate-s4"],
  "affected_target_ids": ["role-job-1"],
  "status": "pending",
  "resolved_refs": [],
  "created_at": "2026-08-03T10:10:00+08:00"
}
```

`directive_type`：

```text
candidate_profile_rebuild_required
role_instance_refresh_required
role_family_aggregation_candidate
intent_review_required
rematch_required
replan_required
```

`status`：`pending | consumed | resolved | cancelled | failed`。

Resolution 规则：

- candidate directive 只接受旧 candidate snapshot 的合法后继。
- role instance directive 只接受 affected target 的新 role snapshot 或明确 no-change receipt。
- family candidate 只有 v0.5 聚合门槛通过后才能接受新 family snapshot；否则保存 candidate/no-change。
- rematch 只接受引用新输入的 current ComparisonSet/GapAssessment。
- replan 只接受新 LearningPlan，且旧 plan stale/superseded。

## 8. PlanProgressEvent

progress event 使用 Preparation Contract。以下输入只能更新 progress：

- 用户说“已完成/未完成”；
- 任务计时结束；
- activity checklist 完成但没有要求的能力证据。

需要 artifact/practice/evaluator evidence 的 activity 只有在对应 feedback 已归档后才能满足验证条件。

## 9. Attribution Review

Request：`interaction_type=confirm_feedback_attribution`，包含：

- event/observation/diagnosis/attribution IDs；
- 最小 evidence excerpt；
- source kind/authority；
- proposed scope/subject/capability；
- alternatives、limitations 和 expected impact；
- allowed relabel values。

Response action：

```text
confirm_attributions
relabel_scope
reject_diagnoses
mark_unknown
cancel
```

响应只能引用 request 中 IDs。校验失败时 Claim/Impact/Directive 零写入。

## 10. 因果与岗位族守卫

必须拒绝：

- `rejected/no_offer → capability_gap`，除非有独立明确评价 observation；
- `task_completed → capability_mastered`；
- `question_asked_once → role_family_frequent`；
- `user_reflection → evaluator_observed/official_reported`；
- `single feedback → hard qualification`；
- 未经确认的高影响 diagnosis 进入 active profile rebuild。

## 11. 幂等、隐私与日志

- event key 包含 owner/type/time/content hash/refs；重复导入复用 event。
- observation/diagnosis/attribution/impact/directive 均使用 canonical key。
- 同 response ID 不同 payload 返回 idempotency conflict。
- trace 只记录 ID、type、scope、authority、route、count 和错误摘要。
- 不复制完整面试记录、私人评价、文件正文或无关个人信息。
- 成功归档后 checkpoint 清除 feedback/resume 正文，只保留 Artifact/Fragment refs。
