# Candidate Profile Contract

状态：v0.4 Design Accepted / Pending Implementation
日期：2026-07-17

本契约定义候选人画像 Graph 的领域对象。事实性字段必须能回溯到已持久化 Claim；未被证据支持的内容只能是显式 `unknown` 或带来源的 `model_inference`。

## 1. CandidateProfile v0.4

```json
{
  "candidate_id": "candidate:anonymous",
  "schema_version": "v0.4",
  "education": [],
  "capabilities": [],
  "experiences": [],
  "transferable_skills": [],
  "responsibility_boundaries": [],
  "unknowns": [],
  "conflicts": [],
  "evidence_coverage": {
    "supported_field_count": 0,
    "inferred_field_count": 0,
    "unknown_field_count": 0,
    "conflicted_field_count": 0
  },
  "supporting_claim_ids": [],
  "previous_snapshot_id": null,
  "completion_reason": null,
  "generated_at": "2026-07-17T00:00:00+08:00"
}
```

字段状态：

- `confirmed`：有有效 Claim 支撑，且不受未解决冲突影响。
- `inferred`：属于 `model_inference`，必须带 confidence 和 supporting claims。
- `unknown`：当前无法可靠确定。
- `conflicted`：存在未解决的有效冲突 Claim。

画像不是原始事实表，而是当前 Claim 集合的版本化投影。`completion_reason` 只能是：

- `sufficient`
- `low_information_value`
- `user_skipped`
- `budget_exhausted`
- `cancelled`
- `failed`

## 2. Candidate 字段对象

### 2.1 CapabilityAssessment

```json
{
  "capability_id": "cap:python",
  "raw_label": "Python",
  "level": "intermediate",
  "confidence": 0.82,
  "status": "inferred",
  "evidence_summary": "项目中实现了异步数据管线",
  "supporting_claim_ids": ["claim-1"]
}
```

能力等级仍使用：

```text
unknown / beginner / intermediate / advanced / expert
```

等级是基于证据的当前估计，不等于考试分数或岗位匹配结果。

### 2.2 EducationRecord

教育记录中的院校、学位、专业、毕业年份分别保存 supporting claim IDs。不得用一个教育 Claim 为未出现的字段提供支持。

### 2.3 ExperienceRecord

经历至少区分：

```text
research / project / internship / competition / other
```

职责、技术、产出和结果分别保存证据引用。项目标题存在不代表用户承担了全部项目职责。

### 2.4 ResponsibilityBoundary

```json
{
  "experience_id": "exp-1",
  "scope": "负责 LangGraph 工作流与评估设计",
  "status": "confirmed",
  "confidence": 0.9,
  "supporting_claim_ids": ["claim-2"]
}
```

## 3. CareerIntent 分离规则

`CareerIntent` 延续独立 schema 和 profile snapshot：

- 岗位、城市、薪资、行业、公司类型属于意图，不属于 CandidateProfile。
- CareerIntent 可来自用户表单或 `user_reported` Claim。
- CareerIntent 缺失不降低候选人能力画像充分性。
- 修改 CareerIntent 不 supersede Candidate Claim。

## 4. InformationGap

```json
{
  "gap_id": "gap:experience.exp-1.responsibility",
  "target_path": "experiences[exp-1].responsibilities",
  "category": "responsibility_boundary",
  "description": "无法确认用户在项目中的个人职责",
  "importance": 0.9,
  "uncertainty": 0.8,
  "answerability": 0.9,
  "evidence_cost": 0.1,
  "information_value": 0.548,
  "preferred_action": "ask_user",
  "related_claim_ids": ["claim-3"],
  "related_artifact_ids": ["artifact-1"],
  "status": "open"
}
```

枚举：

- `category`：`education`、`experience`、`capability`、`responsibility_boundary`、`evidence_quality`、`conflict`。
- `preferred_action`：`read_more`、`ask_user`、`request_more_materials`、`keep_unknown`。
- `status`：`open`、`resolved`、`skipped`、`expired`。

所有 0-1 数值必须经过 schema 校验。`information_value` 使用确定性公式计算，LLM 只提供组成项与理由。

## 5. SufficiencyAssessment

```json
{
  "assessment_id": "assessment-1",
  "schema_version": "v0.4",
  "candidate_id": "candidate:anonymous",
  "profile_snapshot_id": "snapshot-1",
  "is_sufficient": false,
  "dimension_results": {
    "education": "sufficient",
    "experience": "partial",
    "capability": "partial",
    "responsibility_boundary": "insufficient",
    "evidence_quality": "partial"
  },
  "information_gaps": [],
  "blocking_conflict_ids": [],
  "recommended_action": "ask_user",
  "reason": "项目职责是后续能力判断的高价值未知项",
  "confidence": 0.85,
  "evaluator": {
    "provider": "mock",
    "model": "deterministic-sufficiency-v1"
  },
  "prompt_version": "candidate_sufficiency_v1",
  "created_at": "2026-07-17T00:00:00+08:00"
}
```

`dimension_results` 值为 `sufficient`、`partial`、`insufficient` 或 `unknown`。

`recommended_action` 值为：

```text
read_more
ask_user
request_more_materials
finalize_with_unknowns
complete
fail
```

该建议必须再经过 Graph policy 校验。

## 6. QuestionPlan

```json
{
  "plan_id": "question-plan-1",
  "schema_version": "v0.4",
  "assessment_id": "assessment-1",
  "questions": [
    {
      "question_id": "question-1",
      "gap_id": "gap:experience.exp-1.responsibility",
      "target_path": "experiences[exp-1].responsibilities",
      "prompt": "在该项目中，你本人具体负责哪些模块？",
      "reason": "需要区分团队成果与你的个人贡献",
      "answer_type": "free_text",
      "required": false,
      "related_claim_ids": ["claim-3"]
    }
  ],
  "created_at": "2026-07-17T00:00:00+08:00"
}
```

约束：

- 单次问题数不得超过 Graph 配置。
- 问题必须绑定 open gap。
- 问题不得暗示用户虚构能力或成果。
- 用户始终可以跳过非安全必需问题。
- 已回答或语义重复的问题不得再次生成。

## 7. ProfileCorrection

```json
{
  "correction_id": "correction-1",
  "candidate_id": "candidate:anonymous",
  "target_path": "experiences[exp-1].responsibilities",
  "operation": "replace",
  "new_value": ["负责评估体系，不负责爬虫"],
  "reason": "原画像扩大了个人职责",
  "supersedes_claim_ids": ["claim-old"],
  "response_artifact_id": "artifact-response",
  "created_at": "2026-07-17T00:00:00+08:00"
}
```

`operation` 为 `add`、`replace`、`remove` 或 `mark_unknown`。Correction 本身不能直接修改 profile；系统必须将其转换为经过验证的新 Claim。

## 8. ProfileVersionDiff

```json
{
  "from_snapshot_id": "snapshot-1",
  "to_snapshot_id": "snapshot-2",
  "added_paths": [],
  "removed_paths": [],
  "changed_paths": [],
  "resolved_gap_ids": [],
  "new_conflicts": [],
  "resolved_conflicts": []
}
```

版本差异由确定性代码计算，不由 LLM 自由总结代替。

## 9. Snapshot 与幂等规则

- ProfileSnapshot version 对同一 `(subject_id, profile_type)` 单调递增。
- profile 数据、active supporting claim IDs 和 schema version 的 canonical hash 相同则复用已有 snapshot。
- 新 Claim、supersede、冲突状态变化或 schema 迁移可创建新 snapshot。
- 旧 snapshot 永不原地修改。
