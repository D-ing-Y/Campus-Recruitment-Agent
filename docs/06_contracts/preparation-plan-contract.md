# Preparation Plan Contract

状态：v0.7 Implemented / Accepted
日期：2026-07-27

本契约定义 selected target 到可执行准备计划的不可变对象。优先级和 package status 是计划策略，
不是 Offer、面试通过或能力认证概率。

## 1. PreparationInputSet

```json
{
  "input_set_id": "prep-input-1",
  "schema_version": "v0.7",
  "user_id": "user-1",
  "target_decision_ids": ["decision-1"],
  "candidate_profile_snapshot_id": "candidate-s4",
  "career_intent_snapshot_id": "intent-s2",
  "comparison_set_id": "comparison-3",
  "gap_assessment_ids": ["gap-job-1"],
  "job_instance_profile_snapshot_ids": ["role-job-1"],
  "role_family_profile_snapshot_ids": [],
  "constraints_id": "prep-constraints-1",
  "planning_policy_version": "preparation_v1",
  "snapshot_hashes": {},
  "canonical_input_hash": "sha256:...",
  "created_at": "2026-07-27T00:00:00+08:00"
}
```

不变量：

- target decisions 均为当前 user 的 `selected`。
- gap/job/comparison refs 与 decision 一致。
- 至少一个 selected target。
- snapshot、decision、constraints 或 policy 变化生成新 input set。

## 2. PreparationConstraints

```json
{
  "constraints_id": "prep-constraints-1",
  "schema_version": "v0.7",
  "timezone": "Asia/Shanghai",
  "horizon_start": "2026-07-27",
  "horizon_end": "2026-08-31",
  "weekly_hours": 15,
  "daily_max_hours": 4,
  "unavailable_dates": [],
  "preferred_activity_types": [],
  "excluded_activity_types": [],
  "session_minutes": 60,
  "confirmed": true,
  "created_from_response_id": null
}
```

- horizon_end 不早于 start。
- hours 非负；session 必须在允许范围并不大于 daily max。
- timezone 必须可解析。
- preferred/excluded 不得同时包含同一类型。
- 默认值也必须写入对象并进入 hash。

## 3. PreparationObjective

```json
{
  "objective_id": "objective-1",
  "objective_type": "strengthen_evidence",
  "title": "补强 Python 项目证据",
  "target_job_profile_ids": ["role-job-1"],
  "gap_ids": ["gap-python-evidence"],
  "requirement_assessment_ids": ["requirement-python"],
  "qualification_assessment_ids": [],
  "hiring_signal_ids": [],
  "supporting_claim_ids": ["candidate-python", "role-python"],
  "addressability": "addressable",
  "reason_codes": ["selected_target_core_evidence_gap"]
}
```

`objective_type`：

```text
resolve_uncertainty
strengthen_evidence
develop_capability
prepare_application_asset
written_exam_practice
interview_practice
portfolio_revision
target_review
```

`addressability`：`addressable | partially_addressable | unaddressable | unknown`。

objective 必须至少绑定 gap/requirement/qualification/signal/application asset 之一；target review 可绑定
unaddressable hard blocker。

## 4. PreparationActivity

```json
{
  "activity_id": "activity-1",
  "schema_version": "v0.7",
  "activity_type": "strengthen_evidence",
  "objective_ids": ["objective-1"],
  "title": "整理一个可验证的 Python 项目案例",
  "description": "从已有项目中整理个人职责、实现、评估和结果。",
  "expected_outputs": ["带引用的项目案例说明"],
  "completion_criteria": ["职责、实现和结果均有证据支撑"],
  "verification_method": "evidence_ingestion_required",
  "estimated_hours": 3,
  "splittable": true,
  "minimum_session_minutes": 60,
  "deadline": "2026-08-05",
  "dependencies": [],
  "target_job_profile_ids": ["role-job-1"],
  "gap_ids": ["gap-python-evidence"],
  "hiring_signal_ids": [],
  "supporting_claim_ids": ["candidate-python", "role-python"],
  "generation_source": "deterministic_template",
  "status": "proposed"
}
```

`generation_source`：`deterministic_template | llm_candidate | user_requested`。

`status`：`proposed | scheduled | active | completed | skipped | blocked | deferred | cancelled`。

约束：

- 所有 objective/target/gap/signal/claim refs 必须存在且属于 input set。
- estimated_hours > 0；splittable 活动的 minimum session 必须合法。
- dependency 只引用同一 plan candidate set，且最终必须是 DAG。
- 外部 URL 只能引用输入证据或 approved resource registry；v0.7 不允许模型自由生成链接。
- completion 只更新活动状态；能力变化需要新证据和画像重建。

## 5. PriorityFactors

```json
{
  "priority_factor_id": "priority-activity-1",
  "activity_id": "activity-1",
  "priority_band": "P1_core",
  "selected_target_count": 1,
  "role_importance_weight": 1.5,
  "hiring_signal_strength": 0,
  "transfer_target_count": 1,
  "deadline_urgency": 0.8,
  "improvability": "high",
  "estimated_effort_hours": 3,
  "sort_key": [1, -1, -1.5, 0, -1, -0.8, 0, 3, "activity-1"],
  "reason_codes": ["core_evidence_gap"],
  "policy_version": "preparation_priority_v1"
}
```

band：`P0_blocker | P1_core | P2_transferable | P3_bonus | P4_deferred`。

`improvability`：`high | medium | low | unaddressable | unknown`。

sort key 仅用于审计，不显示为单一分数。所有 factors 由确定性 policy 生成。

## 6. MinimumPreparationPackage

```json
{
  "package_id": "package-1",
  "schema_version": "v0.7",
  "status": "partial",
  "included_activity_ids": ["activity-1"],
  "deferred_activity_ids": ["activity-2"],
  "unaddressable_objective_ids": [],
  "target_summaries": {},
  "warnings": ["capacity_prevents_full_policy_package"],
  "policy_version": "minimum_package_v1"
}
```

status：`complete | partial | blocked | unknown`。

- status 描述 policy package，不代表候选人一定可投递或通过。
- deferred/unaddressable 必须有对应 reason。
- projected coverage 必须显式标 projection，不修改 v0.6 assessment。

## 7. ScheduledSession

```json
{
  "session_id": "session-1",
  "activity_id": "activity-1",
  "session_index": 1,
  "start_at": "2026-07-28T19:00:00+08:00",
  "end_at": "2026-07-28T20:00:00+08:00",
  "duration_minutes": 60
}
```

Scheduler 不变量：

- session 位于 horizon 和 activity deadline 内；
- 同时区、无重叠、非 unavailable date；
- daily/weekly duration 不超过 constraints；
- dependency 的所有 session 在 dependent activity 首 session 前完成；
- 总 session duration 与 scheduled activity estimated effort 一致或符合显式 rounding policy。

## 8. LearningPlan

```json
{
  "learning_plan_id": "learning-plan-1",
  "schema_version": "v0.7",
  "user_id": "user-1",
  "input_set_id": "prep-input-1",
  "constraints_id": "prep-constraints-1",
  "package_id": "package-1",
  "objective_ids": ["objective-1"],
  "activity_ids": ["activity-1"],
  "priority_factor_ids": ["priority-activity-1"],
  "schedule": [],
  "status": "proposed",
  "previous_plan_id": null,
  "supersedes_plan_id": null,
  "canonical_hash": "sha256:...",
  "generated_at": "2026-07-27T00:00:00+08:00"
}
```

status：

```text
proposed
accepted
active
completed
partial
blocked
deferred
stale
superseded
cancelled
```

输入 snapshot、constraints 或 planning policy 改变时旧 plan stale。发布新计划后旧计划 superseded；
历史 activity/progress 不删除。

## 9. PlanProgressEvent

```json
{
  "progress_event_id": "progress-1",
  "schema_version": "v0.7",
  "learning_plan_id": "learning-plan-1",
  "activity_id": "activity-1",
  "status": "completed",
  "progress_percent": 100,
  "feedback_event_id": "feedback-1",
  "evidence_artifact_ids": [],
  "occurred_at": "2026-07-29T12:00:00+08:00"
}
```

progress 只改变 plan/activity lifecycle。若 verification method 要求 evidence，缺少 Artifact 时可以记录
`completed_self_reported` 等 reason，但不能升级 CandidateProfile。

## 10. PlanReview Request/Response

Request：`interaction_type=review_preparation_plan`，固定引用 input/package/plan ID、允许活动、constraints
和 warnings。

Response action：

```text
accept_plan
revise_constraints
exclude_activities
request_activity_revision
defer_plan
cancel
```

- revise constraints 使用字段 allowlist 并创建新 constraints。
- exclude/revise 只引用 request 中 activity。
- response 不得修改 priority factors、GapAssessment 或 Role facts。
- 同 response 重放不重复创建 constraints/plan。

## 11. 版本与幂等

- 所有对象使用 canonical payload + input/policy refs 生成稳定 key。
- 相同 key 复用原对象；相同 ID 不同 payload 返回 idempotency conflict。
- plan 只有在所有引用对象、DAG、package 和 schedule 验证通过后才可发布。

## 12. WP3.1 输入边界

- Preparation 可以消费 Demand requirements 和 interview assessment signals；
- assessment signal 只能支持 written_exam/interview_practice 等准备目标，不能升级为 hard requirement；
- work intensity、management、team atmosphere、compensation、growth、stability 和 work content 等
  ReputationDimension 不得生成 PreparationObjective 或 Activity；
- 任何 reputation segment ID 进入 PreparationInputSet 时返回 `evidence_usage_violation`。
