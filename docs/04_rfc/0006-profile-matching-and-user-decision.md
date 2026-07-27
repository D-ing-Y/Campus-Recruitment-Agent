# RFC-0006: 可解释双画像匹配与用户决策 Graph

状态：Implemented / Accepted
日期：2026-07-22
关联需求：`docs/03_requirements/v0.6-profile-matching-and-user-decision.md`
关联 ADR：`docs/05_adr/0006-separate-eligibility-coverage-and-preference.md`

## 1. 背景

CandidateProfile、CareerIntent 和 RoleProfile 已是相互独立的版本化对象。v0.6 的任务不是
把它们拼进 prompt 后让模型“打分”，而是建立一个可重放的比较投影：代码逐项判断资格、
能力证据与偏好，LLM 只把确定性结果解释为用户可理解的决策信息。

比较结果会因任一输入 snapshot 变化而变化，所以它本身也必须版本化。用户反馈还可能属于
三个不同事实域：候选人证据、求职意图或岗位事实。matching Graph 需要给出正确回退指令，
但 v0.6 不提前承担 v1.0 Parent Graph 的跨 subgraph 编排职责。

## 2. 决策摘要

- 采用 `qualification → capability evidence → preference → uncertainty` 四段确定性流水线。
- 以 job instance 为比较和决策单位，role family 只作上下文。
- 用 `CoverageBreakdown` 展示覆盖的分子、分母和 unknown，不输出综合成功率。
- 采用不可变 `GapAssessment` + `ComparisonSet` + `TargetDecision`。
- 采用稳定字典序分层比较，不采用加权总分。
- 采用 `review_comparison` interrupt；所有输入纠正先进入所属画像流程。
- matching Graph 通过 `RebuildDirective` 与 v0.4/v0.5 解耦。

## 3. Graph

```text
START
  ↓
initialize_matching_run
  ↓
load_and_validate_snapshots
  ├─ invalid owner/schema → fail
  ├─ stale/expired role → build_refresh_directive
  └─ valid
  ↓
evaluate_hard_qualifications
  ↓
align_capability_requirements
  ↓
compute_deterministic_coverage
  ↓
evaluate_preferences_and_uncertainty
  ↓
build_gap_assessments
  ↓
build_comparison_set
  ↓
explain_comparison
  ↓
route_matching_next_action
  ├─ review_user → plan_decision_interaction
  │                 ↓
  │               interrupt_for_decision
  │                 ↓ resume
  │               validate_and_archive_decision
  │                 ↓
  │               route_user_decision
  │                 ├─ select/defer/reject → persist_target_decisions → finalize
  │                 ├─ revise_candidate → build_candidate_directive → finalize_reroute
  │                 ├─ revise_intent → persist_intent_snapshot → assess_intent_impact
  │                 │      ├─ same search scope → rematch loop
  │                 │      └─ changed scope → build_role_research_directive → finalize_reroute
  │                 ├─ refresh_role → build_role_refresh_directive → finalize_reroute
  │                 └─ cancel → finalize
  ├─ complete_with_unknowns → finalize
  ├─ complete → finalize
  └─ fail → finalize
```

节点不得把任意 LLM 输出直接作为 edge name。所有路由均由枚举、状态和预算确定。

## 4. 数据模型

### 4.1 MatchingInputSet

```json
{
  "input_set_id": "matching-input-1",
  "schema_version": "v0.6",
  "user_id": "user-1",
  "candidate_profile_snapshot_id": "candidate-snapshot-4",
  "career_intent_snapshot_id": "intent-snapshot-2",
  "job_instance_profile_snapshot_ids": ["role-job-10", "role-job-11"],
  "role_family_profile_snapshot_ids": ["role-family-3"],
  "candidate_policy_version": "candidate_v0.4",
  "role_policy_version": "role_v0.5",
  "matching_policy_version": "matching_v1",
  "canonical_input_hash": "sha256:..."
}
```

hash 覆盖有序 snapshot ID、snapshot canonical hash 和所有 policy version。

CareerIntent 输入必须是 v0.6 structured constraint，或已显式迁移并由用户确认的 legacy snapshot。
其 discovery 字段通过 v0.5 SearchScope projector 生成 fingerprint；matching 不复制 scope 算法。

### 4.2 QualificationAssessment

```json
{
  "assessment_item_id": "qualification-item-1",
  "qualification_id": "qualification-graduation-2027",
  "qualification_type": "graduation_year",
  "operator": "equals",
  "required_value": "2027",
  "candidate_value": "2027",
  "outcome": "passed",
  "reason_code": "exact_value_match",
  "candidate_claim_ids": ["claim-candidate-graduation"],
  "role_claim_ids": ["claim-role-graduation"]
}
```

`outcome`：`passed | failed | unknown | conflicted | not_applicable`。

### 4.3 RequirementAssessment

```json
{
  "assessment_item_id": "requirement-item-1",
  "requirement_id": "requirement-python",
  "capability_id": "cap:python",
  "mapping_type": "exact",
  "required_level": "intermediate",
  "candidate_level": "advanced",
  "outcome": "satisfied",
  "importance": "core",
  "obligation": "required",
  "base_weight": 1.0,
  "effective_weight": 1.5,
  "reason_code": "candidate_level_meets_requirement",
  "candidate_claim_ids": ["claim-python-project"],
  "role_claim_ids": ["claim-role-python"]
}
```

`outcome`：

```text
satisfied
insufficient
evidence_insufficient
unknown
unmapped
not_applicable
```

`mapping_type`：`exact | transfer | unmapped`。transfer 必须引用 ontology relation ID。

### 4.4 CoverageBreakdown

```json
{
  "dimension": "core_capability",
  "total_weight": 5.5,
  "eligible_weight": 4.0,
  "covered_weight": 3.0,
  "uncertain_weight": 1.5,
  "coverage": 0.75,
  "covered_item_ids": ["requirement-item-1"],
  "uncovered_item_ids": ["requirement-item-2"],
  "uncertain_item_ids": ["requirement-item-3"],
  "policy_version": "matching_weight_v1"
}
```

`coverage` 只在 `eligible_weight > 0` 时存在。客户端必须同时展示 uncertain weight/count。

### 4.5 PreferenceAssessment

```json
{
  "assessment_item_id": "preference-item-1",
  "preference_key": "location",
  "constraint_kind": "negotiable",
  "intent_value": ["成都"],
  "role_value": ["北京"],
  "outcome": "conflict",
  "reason_code": "no_location_overlap",
  "role_claim_ids": ["claim-role-location"]
}
```

`outcome`：`aligned | conflict | unknown | not_applicable`。

### 4.6 GapAssessment

一个 assessment 对应一个具体岗位。它包含：

- input snapshot refs 与 canonical hash；
- overall hard status 和 qualification items；
- requirement items 与 core/bonus coverage；
- preference items；
- 四类 GapItem；
- deterministic fact index；
- explanation refs；
- status：`current | stale | superseded`。

旧 v0.3 `coverage_score` 迁移为 v0.6 的 `core_coverage.coverage`；不能只复制旧字段，必须补齐
分子、分母、unknown 和 policy version。无法补齐的历史对象只读，不参与 v0.6 排序。

### 4.7 ComparisonSet

```json
{
  "comparison_set_id": "comparison-1",
  "schema_version": "v0.6",
  "input_set_id": "matching-input-1",
  "entries": [
    {
      "job_instance_profile_snapshot_id": "role-job-10",
      "gap_assessment_id": "gap-10",
      "recommended_tier": "review_first",
      "sort_key": [0, 0, -0.75, 1.5, "role-job-10"]
    }
  ],
  "status": "current",
  "generated_at": "2026-07-22T00:00:00+08:00"
}
```

序列化的 sort key 仅用于审计；客户端不得把它显示为单一分数。

### 4.8 TargetDecision

```json
{
  "decision_id": "decision-1",
  "schema_version": "v0.6",
  "user_id": "user-1",
  "comparison_set_id": "comparison-1",
  "job_instance_profile_snapshot_id": "role-job-10",
  "status": "selected",
  "reason_codes": ["evidence_coverage_acceptable"],
  "note": null,
  "supersedes_decision_id": null,
  "created_from_response_id": "response-1",
  "created_at": "2026-07-22T00:05:00+08:00"
}
```

`status`：`selected | deferred | rejected`。新决策 supersede 旧决策，不删除历史。

### 4.9 RebuildDirective

```json
{
  "directive_id": "directive-1",
  "directive_type": "role_research_required",
  "originating_comparison_set_id": "comparison-1",
  "reason_codes": ["search_scope_changed"],
  "required_input_refs": ["intent-snapshot-3"],
  "affected_job_profile_ids": ["role-job-10"],
  "requested_scope": {"locations": ["上海"]},
  "status": "pending"
}
```

`directive_type`：

```text
candidate_profile_required
rematch_required
role_research_required
role_refresh_required
```

## 5. 确定性比较策略

### 5.1 Hard qualification

每种 qualification type 通过已注册 comparator 处理：

```python
compare(operator, required_value, candidate_value) -> outcome + reason_code
```

首版支持 equals、in、contains_any、contains_all、gte、lte 和明确日期/年份窗口。
字符串归一化器可处理受控别名，但不得用 LLM 常识替代 comparator。

总体状态：

```text
any failed                         → failed
else any unknown/conflicted        → unknown
else at least one applicable item  → passed
else                               → unknown
```

### 5.2 Capability matching

处理顺序：

1. canonical capability ID exact；
2. ontology 中显式 transfer edge；
3. raw label 只产生 unmapped；
4. candidate status/level/evidence quality 判定；
5. 生成 outcome 和 GapType 候选。

首版等级顺序为：`beginner < intermediate < advanced < expert`；unknown 不进入顺序。

GapType 规则：

```text
confirmed candidate level < required level → capability_gap
candidate claims capability but support/level is insufficient → evidence_gap
candidate data missing/conflicted or capability unmapped → epistemic_uncertainty
intent/role disagreement → preference_conflict
```

### 5.3 Weight policy

权重表是版本化配置，例如：

| importance / obligation | base weight |
| --- | ---: |
| core + required | 1.5 |
| core + preferred/unknown | 1.0 |
| bonus + preferred | 0.5 |
| context/mentioned | 不进入能力 coverage |

来源 authority 可决定 requirement 是否 assessable，但不由 LLM 动态增减权重。实际数值在实现前写入
配置与 golden tests；改动必须提升 policy version。

### 5.4 避免 unknown 虚高

coverage 分母排除 unknown 是为了避免把未知当失败，但报告必须一起展示：

- `coverage`；
- `eligible_weight / total_weight`；
- `uncertain_weight / total_weight`；
- satisfied、insufficient、evidence insufficient、unknown 数量。

当 uncertainty 超过配置阈值时，推荐层必须是 `needs_clarification`，即使已知项 coverage 很高。

### 5.5 Stable ordering

将语义映射成 tuple：

```text
hard_rank: passed=0, unknown=1, failed=2
blocking_preference_conflict_count: ascending
coverage_sort: -(core coverage), null after numeric
uncertainty_weight: ascending
job_profile_id: ascending
```

排序只帮助用户先审阅，不代表系统替用户做最终决定。

## 6. Intent 变化与回退

`IntentImpactAnalyzer` 对旧/新 CareerIntent 的规范化字段做 diff：

v0.6 将偏好表达为 `key/operator/value/kind/affects_search_scope/status` 的结构化 constraint。
旧字符串偏好只可解析为待确认项，不能在未确认时产生明确 preference conflict。

### 6.1 只 rematch

- salary preference 权重/上下限变化，但岗位已具有可比较薪资；
- 工作方式、成长性、稳定性等 negotiable preference 变化；
- hard/negotiable 标签变化但不改变岗位发现范围；
- 用户对已有目标的决策说明变化。

### 6.2 role research required

- target role family/keywords 变化；
- locations 作为检索 hard scope 变化；
- graduation year、recruitment type 变化；
- industries、companies、company types 作为 SearchScope inclusion/exclusion 变化；
- 其他会使 v0.5 已有候选集合不再代表目标市场的变化。

Analyzer 必须输出 changed fields、scope hash before/after 和 reason code。相同 scope hash 不允许
创建 role research directive。

## 7. Human-in-the-loop

`review_comparison` request 展示：

- comparison ID 和输入 snapshot refs；
- 每个岗位的 hard status、coverage breakdown、主要 gap 和 unknown；
- 允许的 job IDs 与动作；
- “覆盖度不是 Offer 概率”声明。

resume 校验顺序：identity → request → snapshot/current status → allowed action → target IDs →
payload schema → idempotency。校验失败时 assessment/decision/intent repository 零写入。

`revise_candidate` 的自由文本只能作为待交给 v0.4 的用户输入或 correction request，不能在本节点
投影 CandidateProfile。`revise_intent` 则创建独立 CareerIntent snapshot；其来源是用户决策事件，
不是 Candidate Claim。

## 8. State 与 Reducer

`ProfileMatchingGraphState` 只保存 ID、确定性中间结果摘要和控制状态。完整对象由 repository 读取。

核心字段：

```text
run_id, thread_id, user_id, status
input_set_id, candidate_profile_snapshot_id, career_intent_snapshot_id
job_instance_profile_snapshot_ids, role_family_profile_snapshot_ids
qualification_assessment_ids, requirement_assessment_ids, preference_assessment_ids
gap_assessment_ids, comparison_set_id, explanation_ids
pending_interaction, resume_input, processed_response_ids
target_decision_ids, rebuild_directive_id
intent_impact_assessment, next_action
budgets, counters, llm_calls, tool_results, trace, errors, report
```

Reducer：

- assessment/explanation/decision ID：stable union；
- snapshot input 和 budget：initialize once，rematch 时通过新 round/input_set 替换；
- comparison/directive/impact/next action/pending interaction：replace/clear；
- trace/errors/LLM/tool：append；
- resume input：validate/archive 后 clear；
- processed response IDs：stable union。

## 9. LLM Explanation

模型接收只读 `DeterministicComparisonFacts`，输出：

```json
{
  "comparison_set_id": "comparison-1",
  "job_explanations": [
    {
      "job_profile_id": "role-job-10",
      "summary": "硬性资格已确认，已知核心要求覆盖 3/4 权重，另有一项证据不足。",
      "fact_ids": ["fact-hard-passed", "fact-core-coverage", "fact-evidence-gap"],
      "claim_ids": ["claim-role-python", "claim-python-project"],
      "suggested_actions": ["review", "provide_candidate_evidence"]
    }
  ],
  "warnings": ["coverage_is_not_offer_probability"]
}
```

Validator 要求所有数字能从 fact index 精确解析；所有 action 属于枚举；不得出现成功概率预测。
失败后使用模板：qualification summary + coverage numerator/denominator + top gaps + unknowns。

## 10. 幂等、版本与故障

- assessment key：input set + job snapshot + policy versions。
- comparison key：有序 assessment canonical hashes + ranking policy version。
- decision key：request + response + job + status。
- intent snapshot key：previous intent + canonical patch + response ID。
- directive key：comparison + directive type + required input hash。

部分持久化失败时不得发布 ComparisonSet。用户 decision batch 要么全部校验后事务写入，要么零写入。
LLM failure 不影响 deterministic assessment；repository/checkpoint/ownership failure 是 fatal。

## 11. 可观测与报告

每轮记录：

- 输入和 policy version；
- qualification outcome counts；
- coverage breakdown 与 uncertainty；
- gap counts by type/severity；
- stable ordering reason；
- explanation provider/cache/fallback；
- interrupt/resume/action；
- intent impact 或 rebuild directive；
- stale/supersede chain。

报告不得用“匹配率”单独展示 coverage；建议标题为“岗位要求证据覆盖”。

## 12. 安全边界

- repository 读取前校验 owner/user。
- 用户 response 只能引用 request 中列出的 target。
- LLM 不接收 Cookie、API key、完整简历或完整网页，只接收最小事实摘要。
- trace/report 不复制完整用户纠正文本。
- 网页或模型文本不能扩大 Graph action、预算、文件路径或工具权限。

## 13. 兼容与迁移

- v0.3 GapAssessment 继续可反序列化，但标为 legacy，不参与 v0.6 ComparisonSet。
- CandidateProfile v0.4、RoleProfile v0.5 是首版受支持输入。
- CareerIntent 需要增加 snapshot/version repository 语义；旧 inline object 首次使用时显式迁移。
- schema 变化采用新版本和迁移器，不回写历史 snapshot。

## 14. 实现阶段

1. schema/repository/migration；
2. deterministic policies 与 golden tests；
3. assessment/comparison projection；
4. StateGraph/checkpoint/report；
5. decision/intent impact/directive；
6. LLM explanation/validator/fallback；
7. fixtures、Eval、全量回归和可选 live smoke。
