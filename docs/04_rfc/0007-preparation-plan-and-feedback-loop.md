# RFC-0007: 可解释准备计划与证据优先反馈闭环

状态：Accepted / Implemented
日期：2026-07-27
关联需求：`docs/03_requirements/v0.7-preparation-plan-and-feedback-loop.md`
关联 ADR：`docs/05_adr/0007-evidence-first-feedback-and-deterministic-preparation.md`

## 1. 背景

v0.6 已产生带证据的 GapAssessment、ComparisonSet 和用户 TargetDecision。准备阶段需要在多个
selected target、有限时间和岗位截止日期之间做真实取舍；反馈阶段则必须判断新信息属于任务进度、
候选人能力证据、具体岗位考察信号、岗位族候选信号还是求职意图。

“让 LLM 生成学习路线并在失败后重写画像”无法保证容量可行、引用完整、因果合理或版本可恢复。
本 RFC 将 planning 与 feedback 设计为两个固定状态机：前者使用确定性优先级/调度器，后者使用
raw-before-interpret 和高影响归因确认。两者通过不可变 snapshot、directive 和 resume refs 串联。

## 2. 决策摘要

- 采用 `PreparationPlanGraph` 和 `FeedbackGraph` 两个独立 subgraph。
- 计划以 selected TargetDecision 为起点，不为未选择目标生成通用清单。
- 采用 policy-driven priority band + stable ordering，不让 LLM 拍分。
- 采用 MinimumPreparationPackage，不追求所有 gap 100% 关闭。
- 采用 capacity/dependency/deadline-aware deterministic scheduler。
- 反馈先归档，再分离 Observation、Diagnosis、Attribution 和 Impact。
- 无明确评价的 rejection/outcome 不产生能力 diagnosis。
- 单次反馈不能直接改变 RoleFamilyProfile；只能形成 aggregation candidate。
- 跨 Candidate/Role/Intent/Matching 边界只输出 directive；resolved refs 恢复后重排计划。

## 3. PreparationPlanGraph

```text
START
  ↓
initialize_preparation_run
  ↓
load_and_validate_selected_targets
  ├─ no selected target → target_selection_required → finalize
  ├─ stale input → rematch_required → finalize_reroute
  └─ valid
  ↓
derive_preparation_objectives
  ↓
generate_activity_candidates
  ↓
validate_activity_candidates
  ↓
compute_priority_factors
  ↓
build_minimum_preparation_package
  ↓
schedule_activities
  ↓
project_learning_plan
  ↓
route_plan_next_action
  ├─ review_user → plan_review_interaction
  │                 ↓
  │               interrupt_for_plan_review
  │                 ↓ resume
  │               validate_plan_review
  │                 ├─ accept → finalize_plan
  │                 ├─ revise constraints → planning loop
  │                 ├─ exclude/revise activity → candidate validation loop
  │                 ├─ defer/cancel → finalize_plan
  │                 └─ invalid → fail
  ├─ complete/partial/blocked → finalize_plan
  └─ fail → finalize_plan
```

## 4. FeedbackGraph

```text
START
  ↓
initialize_feedback_run
  ↓
ingest_and_archive_feedback
  ├─ raw write failure → fail
  └─ archived
  ↓
extract_feedback_observations
  ↓
propose_feedback_diagnoses
  ↓
validate_feedback_attributions
  ↓
route_feedback_confirmation
  ├─ high impact → plan_attribution_interaction
  │                 ↓
  │               interrupt_for_attribution
  │                 ↓ resume
  │               validate_attribution_response
  └─ observation/progress only
  ↓
persist_feedback_claims_and_progress
  ↓
assess_feedback_impact
  ↓
build_feedback_directives
  ↓
route_feedback_next_action
  ├─ progress only → finalize_feedback
  ├─ rebuild required → await_external_rebuild → END(awaiting_rebuild)
  ├─ resumed resolution → validate_rebuild_resolution
  │                       → request rematch/replan → finalize_feedback
  ├─ unknown/cancel → finalize_feedback
  └─ fail → finalize_feedback
```

`awaiting_rebuild` 是合法终态。application service 执行既有 v0.4/v0.5/v0.6 操作后，使用原
directive ID 和 resolved refs 开启恢复 run。v1.0 才将这条 saga 内置到 Parent Graph。

## 5. Preparation 数据模型

### 5.1 PreparationInputSet

```json
{
  "input_set_id": "prep-input-1",
  "schema_version": "v0.7",
  "user_id": "user-1",
  "target_decision_ids": ["decision-job-1"],
  "candidate_profile_snapshot_id": "candidate-s4",
  "career_intent_snapshot_id": "intent-s2",
  "comparison_set_id": "comparison-3",
  "gap_assessment_ids": ["gap-job-1"],
  "job_instance_profile_snapshot_ids": ["role-job-1"],
  "role_family_profile_snapshot_ids": ["role-family-1"],
  "constraints_id": "prep-constraints-1",
  "planning_policy_version": "preparation_v1",
  "snapshot_hashes": {},
  "canonical_input_hash": "sha256:..."
}
```

输入 hash 包含 exact snapshot canonical hash、selected decision 状态、constraints 和 policy version。

### 5.2 PreparationConstraints

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
  "confirmed": true
}
```

所有默认值必须落盘。修改 constraints 创建新对象，不原地覆盖。

### 5.3 PreparationObjective

```json
{
  "objective_id": "objective-python-evidence",
  "objective_type": "strengthen_evidence",
  "title": "补强 Python 项目责任与结果证据",
  "target_job_profile_ids": ["role-job-1"],
  "gap_ids": ["gap-evidence-python"],
  "requirement_assessment_ids": ["requirement-python"],
  "hiring_signal_ids": [],
  "supporting_claim_ids": ["candidate-python", "role-python"],
  "addressability": "addressable",
  "reason_codes": ["selected_target_core_evidence_gap"]
}
```

`objective_type` 与 activity type 对齐，但 objective 可由多个 activity 完成。

`addressability`：`addressable | partially_addressable | unaddressable | unknown`。

### 5.4 PreparationActivity

```json
{
  "activity_id": "activity-python-readme",
  "activity_type": "strengthen_evidence",
  "objective_ids": ["objective-python-evidence"],
  "title": "整理一个可验证的 Python 项目案例",
  "description": "从现有项目材料中整理个人职责、实现、评估方法和结果。",
  "expected_outputs": ["一份带引用的项目案例说明"],
  "completion_criteria": ["职责、实现和结果均有已有材料或新提交材料支撑"],
  "verification_method": "evidence_ingestion_required",
  "estimated_hours": 3,
  "splittable": true,
  "minimum_session_minutes": 60,
  "deadline": "2026-08-05",
  "dependencies": [],
  "target_job_profile_ids": ["role-job-1"],
  "gap_ids": ["gap-evidence-python"],
  "hiring_signal_ids": [],
  "supporting_claim_ids": ["candidate-python", "role-python"],
  "generation_source": "deterministic_template",
  "status": "proposed"
}
```

`verification_method` 至少区分：

```text
self_report_only
artifact_required
evidence_ingestion_required
practice_result_required
evaluator_feedback_required
official_outcome_required
```

### 5.5 PriorityFactors

```json
{
  "activity_id": "activity-python-readme",
  "priority_band": "P1_core",
  "selected_target_count": 1,
  "role_importance_weight": 1.5,
  "hiring_signal_strength": 0,
  "transfer_target_count": 1,
  "deadline_urgency": 0.8,
  "improvability": "high",
  "estimated_effort_hours": 3,
  "reason_codes": ["core_evidence_gap", "before_application_deadline"],
  "policy_version": "preparation_priority_v1"
}
```

因素是可审计排序字段，不合成为“成功分”。

### 5.6 MinimumPreparationPackage

```json
{
  "package_id": "package-1",
  "status": "partial",
  "included_activity_ids": ["activity-python-readme"],
  "deferred_activity_ids": ["activity-bonus-framework"],
  "unaddressable_objective_ids": [],
  "target_summaries": {
    "role-job-1": {
      "addressable_hard_blockers_included": 0,
      "projected_core_coverage": 0.75,
      "coverage_target": 0.8,
      "required_application_assets_included": true,
      "practice_minimum_included": true
    }
  },
  "warnings": ["capacity_prevents_full_policy_package"],
  "policy_version": "minimum_package_v1"
}
```

`projected_core_coverage` 是“若活动产生预期证据后可重新评估的规划估计”，必须标为 projection，
不能直接写回 GapAssessment 或 CandidateProfile。

### 5.7 LearningPlan

```json
{
  "learning_plan_id": "learning-plan-1",
  "schema_version": "v0.7",
  "user_id": "user-1",
  "input_set_id": "prep-input-1",
  "package_id": "package-1",
  "activity_ids": ["activity-python-readme"],
  "schedule": [
    {
      "activity_id": "activity-python-readme",
      "start_at": "2026-07-28T19:00:00+08:00",
      "end_at": "2026-07-28T20:00:00+08:00",
      "session_index": 1
    }
  ],
  "status": "proposed",
  "previous_plan_id": null,
  "supersedes_plan_id": null,
  "canonical_hash": "sha256:...",
  "generated_at": "2026-07-27T00:00:00+08:00"
}
```

status：`proposed | accepted | active | completed | partial | blocked | deferred | stale | superseded | cancelled`。

## 6. Priority 与 Scheduling Policy

### 6.1 Priority band

```text
P0_blocker:
  current window 内可解决、且阻塞 selected target 申请/基本准备的事项

P1_core:
  selected target 的 core capability/evidence gap，或高 authority/frequent stage signal

P2_transferable:
  同时服务多个 selected target 的核心活动，或高价值 uncertainty resolution

P3_bonus:
  bonus capability、单次 observed signal 或低紧迫优化

P4_deferred:
  当前不可处理、容量外、依赖阻塞、低价值或用户排除
```

unaddressable hard blocker 进入 P4 + `target_review_required`，不因“hard”被错误排入学习任务。

### 6.2 Stable ordering

同 band tuple：

```text
(-selected_target_count,
 -role_importance_weight,
 -validated_hiring_signal_strength,
 -transfer_target_count,
 -deadline_urgency,
 -improvability_rank,
 estimated_effort_hours,
 activity_id)
```

所有映射、阈值和排序升级必须提升 policy version。

### 6.3 Scheduler

首版使用 deterministic greedy DAG scheduler：

1. 验证 dependency DAG；
2. 过滤不满足前置条件和明确 excluded 的活动；
3. 按 priority band/tuple 遍历；
4. 在截止日期前寻找满足 daily/weekly capacity 的最早 slot；
5. splittable activity 按 session 分配；
6. 无可行 slot 时 deferred，并保存 capacity/deadline reason；
7. canonical schedule hash 验证稳定性。

不引入通用优化求解器；若后续固定集证明 greedy 无法满足关键业务再写 ADR。

## 7. Feedback 数据模型

### 7.1 FeedbackEvent

```json
{
  "feedback_event_id": "feedback-1",
  "schema_version": "v0.7",
  "user_id": "user-1",
  "feedback_type": "mock_interview",
  "source_kind": "evaluator_report",
  "occurred_at": "2026-08-03T10:00:00+08:00",
  "plan_id": "learning-plan-1",
  "activity_id": "activity-interview-rag",
  "target_job_profile_ids": ["role-job-1"],
  "stage": "technical_interview",
  "raw_artifact_ids": ["artifact-feedback-1"],
  "fragment_ids": ["fragment-feedback-1"],
  "canonical_event_hash": "sha256:...",
  "status": "archived"
}
```

feedback type：`task_progress | practice_result | mock_interview | written_exam | interview |
application_outcome | portfolio_review | user_reflection | other`。

source kind：`self_reported | evaluator_report | platform_result | official_result |
system_measurement | imported_document`。

### 7.2 FeedbackObservation

```json
{
  "observation_id": "observation-1",
  "feedback_event_id": "feedback-1",
  "observation_type": "evaluator_comment",
  "value": "回答给出了 RAG 指标，但没有说明离线数据集构造。",
  "outcome": null,
  "source_kind": "evaluator_report",
  "authority": "evaluator_observed",
  "fragment_ids": ["fragment-feedback-1"],
  "confidence": 1.0
}
```

Observation 不包含“因此不具备 RAG 能力”等因果推断。

### 7.3 FeedbackDiagnosis

```json
{
  "diagnosis_id": "diagnosis-1",
  "observation_ids": ["observation-1"],
  "diagnosis_type": "candidate_evidence_gap",
  "subject_scope": "candidate_evidence",
  "capability_id": "cap:rag_evaluation",
  "target_job_profile_ids": ["role-job-1"],
  "summary": "当前回答证据未覆盖评估数据集构造。",
  "alternative_explanations": ["面试时间不足", "问题表述未要求展开"],
  "limitations": ["单次模拟面试不能确认整体能力等级"],
  "confidence": 0.7,
  "status": "proposed"
}
```

没有明确评价的 application/interview rejection 只能生成 outcome observation，diagnosis 列表为空。

### 7.4 FeedbackAttribution

```json
{
  "attribution_id": "attribution-1",
  "feedback_event_id": "feedback-1",
  "observation_ids": ["observation-1"],
  "diagnosis_ids": ["diagnosis-1"],
  "subject_scope": "candidate_evidence",
  "subject_ref": "candidate-s4",
  "authority": "evaluator_observed",
  "requires_confirmation": true,
  "confirmation_status": "pending",
  "reason_codes": ["high_impact_candidate_attribution"]
}
```

confirmation：`not_required | pending | confirmed | relabeled | rejected | unknown`。

### 7.5 FeedbackImpactAssessment

```json
{
  "impact_assessment_id": "feedback-impact-1",
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

### 7.6 FeedbackDirective

directive type：

```text
candidate_profile_rebuild_required
role_instance_refresh_required
role_family_aggregation_candidate
intent_review_required
rematch_required
replan_required
```

每个 directive 保存 originating event/plan、required refs、affected targets、reason codes、status 和
resolved refs。role family candidate 不能以 resolved 状态直接携带新 family snapshot，除非 v0.5
aggregation policy 已以足够样本完成重建。

## 8. Feedback Authority 与因果守卫

| 输入 | 可支持 | 不可直接支持 |
| --- | --- | --- |
| task completion self-report | plan progress | capability level upgrade |
| practice score/system measurement | 本次表现 observation | 长期能力或岗位要求 |
| evaluator explicit comment | candidate evidence/capability diagnosis candidate | Offer 因果、岗位族通用要求 |
| official written/interview outcome | 本次 outcome/stage | 无解释的能力缺口 |
| user reflection | user-reported hypothesis | evaluator/official fact |
| single interview question | job/company observed signal | role-family frequent/common signal |

Validator 必须检测 `rejected → capability gap`、`completed → mastered` 和
`single event → common role requirement` 等非法跃迁。

## 9. Human Interaction

### 9.1 Plan review

request 固定引用 plan/input/package version，展示活动、排期、deferred 和 blockers。response 只能：

- 接受计划；
- 修改 capacity/horizon/activity preference；
- 排除 request 中活动；
- 请求重写活动说明/拆分；
- 暂缓或取消。

不能通过 plan response 修改 GapAssessment、RoleProfile 或 priority facts。

### 9.2 Attribution confirmation

request 展示最小 observation excerpt、proposed diagnosis、scope、authority、alternatives 和 impact。
用户可 confirm、relabel、reject 或 mark unknown。用户确认只确认其认可的归因，不提升来源 authority。

## 10. State 与 Reducer

### 10.1 PreparationPlanGraphState

```text
run/thread/user/status
input_set_id, target_decision_ids, snapshot refs, constraints_id
objective_ids, activity_ids, priority_factor_ids, package_id, learning_plan_id
pending_interaction, resume_input, processed_response_ids
next_action, budgets, counters
llm_calls, tool_results, trace, errors, report
```

### 10.2 FeedbackGraphState

```text
run/thread/user/status, allowed_path_roots
feedback_event_id, raw_artifact_ids, fragment_ids
observation_ids, diagnosis_ids, attribution_ids, feedback_claim_ids
progress_event_ids, impact_assessment_id, directive_ids
pending_interaction, resume_input, processed_response_ids
resolved_snapshot_refs, next_action, budgets, counters
llm_calls, tool_results, trace, errors, report
```

Reducer 规则沿用 stable union、append、replace/clear、initialize-once。resume 正文成功归档后清除；
resolved refs 按 directive ID 合并且只允许一次有效 resolution。

## 11. LLM 边界

### 11.1 Activity candidate

输入 deterministic planning facts；输出 activity candidate。Validator 校验 refs、类型、依赖、
工时范围、完成定义和不存在外部虚构资源。PriorityFactors、package 和 schedule 不由模型输出。

### 11.2 Feedback extraction

模型从已归档 Fragment 输出 Observation candidate 和 Diagnosis candidate。Observation 必须逐条引用
Fragment；Diagnosis 必须引用 Observation、标明 inference、alternatives 和 limitations。

任何从 outcome 直接生成 capability cause、从 self-report 提升 authority、从单次事件生成 family
frequency 的输出均拒绝。失败后 deterministic baseline 至少保存 raw observation/outcome/unknown。

## 12. 幂等、事务与版本

- input set：snapshot/decision/constraints/policy canonical hash。
- activity：objective + type + canonical task spec + refs。
- plan：input + ordered activities + schedule + policy。
- feedback event：owner + type + occurred_at + content hash + related refs。
- observation/diagnosis：event + canonical payload + refs + extractor version。
- feedback Claim：existing EvidenceClaim idempotency key。
- directive：event + type + affected refs + policy。
- progress：event + activity + canonical progress payload。

Plan publish 必须在 objectives/activities/factors/package/schedule 全部持久化后才发布。Feedback Claim、
impact 和 directive 应使用事务或可恢复 saga；部分失败不得标 event processed。

## 13. 故障与恢复

- raw Artifact 写失败是 fatal，不能继续解析。
- LLM 失败回退 deterministic baseline。
- invalid/stale snapshot 返回 reroute，不生成新 plan。
- capacity 不足是 partial，不是系统错误。
- unsupported feedback format 以 unknown/request material 完成。
- checkpoint/repository/owner/idempotency conflict 是 fatal。
- external rebuild 未完成保持 awaiting_rebuild，不重复发 directive。

## 14. 实现阶段

1. preparation/feedback schema 与 repository；
2. deterministic priority/package/scheduler；
3. PreparationPlanGraph 与 plan review；
4. feedback ingestion/extraction/attribution/Claim；
5. FeedbackGraph/directives/resolution/replan saga；
6. LLM candidates/validators/fallback；
7. fixtures、Eval、回归和可选 smoke。
