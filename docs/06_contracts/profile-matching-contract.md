# Profile Matching Contract

状态：v0.6 Implemented / Accepted
日期：2026-07-22

本契约定义候选人画像、求职意图与具体岗位画像之间的确定性比较对象。覆盖度表示“当前证据对
岗位能力要求的覆盖”，不是 Offer、录取或面试通过概率。

## 1. MatchingInputSet

```json
{
  "input_set_id": "matching-input-1",
  "schema_version": "v0.6",
  "user_id": "user-1",
  "candidate_profile_snapshot_id": "candidate-snapshot-1",
  "career_intent_snapshot_id": "intent-snapshot-1",
  "job_instance_profile_snapshot_ids": ["role-job-1"],
  "role_family_profile_snapshot_ids": ["role-family-1"],
  "candidate_policy_version": "candidate_v0.4",
  "role_policy_version": "role_v0.5",
  "matching_policy_version": "matching_v1",
  "canonical_input_hash": "sha256:...",
  "created_at": "2026-07-22T00:00:00+08:00"
}
```

约束：

- 必须有且只有一个 candidate 和 intent snapshot。
- 至少一个 job instance snapshot；family snapshot 可为空。
- 所有 snapshot 必须属于同一 owner/user。
- job instance ID 有序去重后进入 canonical hash。
- 输入和 policy version 在 run 中不可静默替换。

## 2. QualificationAssessment

```json
{
  "assessment_item_id": "qualification-item-1",
  "qualification_id": "qualification-1",
  "qualification_type": "graduation_year",
  "operator": "equals",
  "required_value": "2027",
  "candidate_value": "2027",
  "outcome": "passed",
  "reason_code": "exact_value_match",
  "candidate_claim_ids": ["candidate-claim-1"],
  "role_claim_ids": ["role-claim-1"],
  "comparator_version": "qualification_v1"
}
```

`outcome`：

```text
passed
failed
unknown
conflicted
not_applicable
```

规则：

- `failed` 必须有双方 Claim 和可复现 comparator 结果。
- 任一侧缺值、冲突或 operator 不支持时不得输出 failed。
- `not_applicable` 只能由显式 scope/operator 规则产生。

## 3. RequirementAssessment

```json
{
  "assessment_item_id": "requirement-item-1",
  "requirement_id": "requirement-1",
  "capability_id": "cap:python",
  "raw_label": "Python",
  "mapping_type": "exact",
  "ontology_relation_id": null,
  "required_level": "intermediate",
  "candidate_level": "advanced",
  "outcome": "satisfied",
  "importance": "core",
  "obligation": "required",
  "base_weight": 1.0,
  "effective_weight": 1.5,
  "reason_code": "candidate_level_meets_requirement",
  "candidate_claim_ids": ["candidate-claim-2"],
  "role_claim_ids": ["role-claim-2"],
  "policy_version": "matching_weight_v1"
}
```

`mapping_type`：`exact | transfer | unmapped`。

`outcome`：

```text
satisfied
insufficient
evidence_insufficient
unknown
unmapped
not_applicable
```

规则：

- `transfer` 必须有版本化 ontology relation ID。
- `insufficient` 需要候选人已确认等级/状态与岗位要求可比较。
- 候选人声称具备但 Claim、责任边界、成果或等级不足时使用 `evidence_insufficient`。
- 缺失、冲突、过期或未知等级使用 `unknown`。
- unmapped raw label 保留，不由 LLM 自动创建 ontology relation。

## 4. CoverageBreakdown

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
  "excluded_item_ids": [],
  "policy_version": "matching_weight_v1"
}
```

不变量：

```text
total_weight = sum(all in-scope requirement effective weights)
eligible_weight = sum(satisfied + insufficient + evidence_insufficient weights)
covered_weight = sum(satisfied weights)
uncertain_weight = sum(unknown + unmapped weights)
coverage = covered_weight / eligible_weight, only if eligible_weight > 0
```

- `coverage` 范围为 0..1 或 null。
- `eligible_weight=0` 时必须为 null。
- unknown 不能作为 covered 或 uncovered，但必须计入 uncertain。
- core 与 bonus 使用两个独立 breakdown。

## 5. PreferenceAssessment

```json
{
  "assessment_item_id": "preference-item-1",
  "preference_key": "location",
  "constraint_kind": "negotiable",
  "intent_value": ["成都"],
  "role_value": ["北京"],
  "outcome": "conflict",
  "reason_code": "no_location_overlap",
  "intent_source_ref": "intent-snapshot-1#/locations",
  "role_claim_ids": ["role-location-claim"]
}
```

`constraint_kind`：`hard | negotiable`。

`outcome`：`aligned | conflict | unknown | not_applicable`。

偏好 assessment 不得进入 capability coverage。

## 6. GapItem

```json
{
  "gap_id": "gap-item-1",
  "gap_type": "evidence_gap",
  "capability_id": "cap:python",
  "summary": "存在 Python 经历，但当前证据不足以确认岗位要求的熟练度。",
  "severity": "medium",
  "reason_code": "candidate_level_unverified",
  "assessment_item_ids": ["requirement-item-2"],
  "candidate_claim_ids": ["candidate-claim-3"],
  "role_claim_ids": ["role-claim-3"],
  "allowed_actions": ["provide_candidate_evidence", "keep_unknown"],
  "confidence": 0.8
}
```

`gap_type`：

```text
capability_gap
evidence_gap
preference_conflict
epistemic_uncertainty
```

GapType、severity 和 allowed actions 来自 policy。summary 可由模板或 LLM 生成，但不能改变结构化事实。

## 7. GapAssessment

```json
{
  "assessment_id": "gap-assessment-1",
  "schema_version": "v0.6",
  "input_set_id": "matching-input-1",
  "candidate_profile_snapshot_id": "candidate-snapshot-1",
  "career_intent_snapshot_id": "intent-snapshot-1",
  "job_instance_profile_snapshot_id": "role-job-1",
  "role_family_profile_snapshot_ids": ["role-family-1"],
  "hard_constraint_status": "unknown",
  "qualification_assessments": [],
  "requirement_assessments": [],
  "core_coverage": {},
  "bonus_coverage": {},
  "preference_assessments": [],
  "gaps": [],
  "fact_index": {},
  "supporting_claim_ids": [],
  "matching_policy_version": "matching_v1",
  "status": "current",
  "supersedes_assessment_id": null,
  "generated_at": "2026-07-22T00:00:00+08:00"
}
```

`hard_constraint_status`：`passed | failed | unknown`。

`status`：`current | stale | superseded`。

约束：

- 每个 assessment 只对应一个 job instance。
- `supporting_claim_ids` 是逐项引用的 stable union，不替代逐项 refs。
- assessment 不保存 Offer probability 字段。
- 输入或 policy 变化时创建新对象，旧对象更新生命周期状态但不改比较内容。

## 8. ComparisonSet

```json
{
  "comparison_set_id": "comparison-1",
  "schema_version": "v0.6",
  "input_set_id": "matching-input-1",
  "entries": [
    {
      "job_instance_profile_snapshot_id": "role-job-1",
      "gap_assessment_id": "gap-assessment-1",
      "recommended_tier": "needs_clarification",
      "hard_rank": 1,
      "blocking_preference_conflict_count": 0,
      "core_coverage": 0.75,
      "uncertainty_weight": 1.5,
      "stable_tie_breaker": "role-job-1"
    }
  ],
  "ranking_policy_version": "matching_rank_v1",
  "status": "current",
  "supersedes_comparison_set_id": null,
  "generated_at": "2026-07-22T00:00:00+08:00"
}
```

`recommended_tier`：`review_first | needs_clarification | blocked`。

entries 必须按 RFC 的稳定字典序排列。排序字段不得合成为一个用户可见分数。

## 9. MatchExplanation

```json
{
  "explanation_id": "explanation-1",
  "schema_version": "v0.6",
  "comparison_set_id": "comparison-1",
  "job_explanations": [
    {
      "job_profile_id": "role-job-1",
      "summary": "硬性资格仍有一项未知；已知核心能力要求的证据覆盖为 3/4 权重。",
      "fact_ids": ["fact-hard-unknown", "fact-core-coverage"],
      "claim_ids": ["candidate-claim-2", "role-claim-2"],
      "suggested_actions": ["review", "provide_candidate_evidence"]
    }
  ],
  "warnings": ["coverage_is_not_offer_probability"],
  "prompt_version": "match_explanation_v1"
}
```

Validator 必须拒绝：

- fact index 中不存在的数字或事实；
- 未引用的事实性结论；
- 新 GapType、weight、status 或 ranking；
- Offer/面试/录取概率；
- allowed actions 外的动作。

## 10. 版本兼容

- v0.3 GapAssessment 为 legacy read-only。
- v0.6 实现不得把旧 `coverage_score` 无损等同为新 coverage；缺少 breakdown 时必须重新计算。
- 任何 comparator、weight、ontology 或 ranking 语义变化都提升 policy version。
