# Target Decision Contract

状态：v0.6 Implemented / Accepted
日期：2026-07-22

本契约定义用户对比较结果的选择、CareerIntent 变更影响和跨 subgraph 重建指令。决策不是画像事实，
不得直接修改 CandidateProfile 或 RoleProfile。

## 1. CareerIntent v0.6

v0.3 `hard_constraints: list[str]` 和 `negotiable_preferences: list[str]` 继续兼容读取，但不能直接作为
确定性比较输入。v0.6 新建 intent snapshot 时使用结构化 constraint：

```json
{
  "constraint_id": "intent-constraint-1",
  "key": "work_mode",
  "operator": "in",
  "value": ["remote", "hybrid"],
  "kind": "negotiable",
  "affects_search_scope": false,
  "status": "confirmed",
  "source_ref": "response-2#/intent_revision/constraints/0"
}
```

`kind`：`hard | negotiable`。`status`：`confirmed | unknown | conflicted`。

首版可确定性比较的 key：

```text
location
salary
industry
company
company_type
work_mode
recruitment_type
graduation_year
other
```

CareerIntent v0.6 payload：

```json
{
  "user_id": "user-1",
  "schema_version": "v0.6",
  "target_roles": ["AI Agent开发工程师"],
  "target_role_families": ["ai_agent_engineering"],
  "locations": ["成都"],
  "graduation_year": "2027",
  "recruitment_type": "autumn_campus",
  "salary_min": null,
  "salary_max": null,
  "salary_unit": null,
  "industries": [],
  "companies": [],
  "company_types": [],
  "constraints": [],
  "confirmed": true,
  "supporting_claim_ids": [],
  "previous_snapshot_id": "intent-snapshot-1",
  "search_scope_policy_version": "intent_scope_v1",
  "updated_at": "2026-07-22T00:05:00+08:00"
}
```

约束：

- target role/family、location、graduation year、recruitment type、industry、company 和 company type
  是否进入 SearchScope，由显式字段与 `affects_search_scope` 决定，不从自然语言猜测。
- confirmed constraint 才能产生明确 aligned/conflict；unknown/conflicted 产生 uncertainty。
- legacy 字符串只能通过版本化 parser 生成“待确认 constraint”，用户确认后才能写新 snapshot。
- SearchScope fingerprint 由 v0.5 `SearchScope` projector 计算，matching 不维护第二套 hash 算法。

## 2. TargetDecision

```json
{
  "decision_id": "target-decision-1",
  "schema_version": "v0.6",
  "user_id": "user-1",
  "comparison_set_id": "comparison-1",
  "job_instance_profile_snapshot_id": "role-job-1",
  "status": "selected",
  "reason_codes": ["evidence_coverage_acceptable"],
  "note": null,
  "created_from_response_id": "response-1",
  "supersedes_decision_id": null,
  "created_at": "2026-07-22T00:05:00+08:00"
}
```

`status`：

```text
selected
deferred
rejected
```

约束：

- decision 只能引用 request 中当前 ComparisonSet 的 job instance。
- 一个 response 可对多个 job 形成 decision，但 batch 必须原子校验/写入。
- 同一 job 的新 decision 使用 `supersedes_decision_id`，旧记录保留。
- decision 不进入 EvidenceClaim，也不改变岗位或候选人事实。

## 3. IntentRevision

```json
{
  "revision_id": "intent-revision-1",
  "schema_version": "v0.6",
  "previous_intent_snapshot_id": "intent-snapshot-1",
  "requested_patch": {
    "negotiable_preferences": ["优先远程办公"]
  },
  "changed_paths": ["/negotiable_preferences"],
  "created_from_response_id": "response-2"
}
```

规则：

- patch 使用字段 allowlist，未知字段拒绝。
- 校验后创建新的 CareerIntent snapshot，不原地修改旧对象。
- 用户偏好不是 Candidate Claim。
- 若修改来自自由文本，必须先结构化展示并由当前 response 明确确认。

## 4. IntentImpactAssessment

```json
{
  "impact_assessment_id": "intent-impact-1",
  "previous_intent_snapshot_id": "intent-snapshot-1",
  "new_intent_snapshot_id": "intent-snapshot-2",
  "changed_paths": ["/negotiable_preferences"],
  "search_scope_hash_before": "sha256:old",
  "search_scope_hash_after": "sha256:old",
  "impact": "rematch_only",
  "reason_codes": ["negotiable_preference_changed"],
  "policy_version": "intent_impact_v1"
}
```

`impact`：

```text
rematch_only
role_research_required
no_effect
```

不变量：

- scope hash 相同时不能输出 `role_research_required`。
- target role、locations、graduation year、recruitment type 和作为 scope 的行业/公司范围变化，
  必须输出 `role_research_required`。
- impact 由确定性 diff/policy 生成，LLM 只能帮助解析待确认 patch。

## 5. RebuildDirective

```json
{
  "directive_id": "directive-1",
  "schema_version": "v0.6",
  "directive_type": "role_research_required",
  "originating_run_id": "matching-run-1",
  "originating_comparison_set_id": "comparison-1",
  "reason_codes": ["search_scope_changed"],
  "required_input_refs": ["intent-snapshot-2"],
  "affected_job_profile_ids": ["role-job-1"],
  "requested_scope": {"locations": ["上海"]},
  "status": "pending",
  "created_at": "2026-07-22T00:05:00+08:00"
}
```

`directive_type`：

```text
candidate_profile_required
rematch_required
role_research_required
role_refresh_required
```

`status`：`pending | consumed | cancelled | failed`。

规则：

- `candidate_profile_required` 只携带 correction request/evidence refs，不携带已投影字段修改。
- `rematch_required` 必须引用新 intent/candidate/role snapshot 中至少一个。
- `role_research_required` 必须包含新 CareerIntent 或 SearchScope ref。
- `role_refresh_required` 必须列出具体 job profile ID 和 freshness/identity reason。
- v0.6 只创建 directive；调用哪个 subgraph 由未来 Parent Graph 或 application service 决定。

## 6. 幂等与失效

幂等键：

```text
TargetDecision = request + response + job + status + canonical reasons
IntentRevision = previous intent + canonical patch + response
IntentImpact = old/new intent canonical hash + policy version
RebuildDirective = comparison + directive type + required input hash
```

- 相同幂等键复用原对象。
- 相同 response ID 携带不同 payload 返回 `idempotency_conflict`。
- 新输入 snapshot 到达后，引用旧输入的 ComparisonSet 标 stale；TargetDecision 保留其历史语境。

## 7. 状态所有权

| 对象 | 负责创建/修改的边界 |
| --- | --- |
| Candidate evidence/Claim/Profile | v0.4 candidate profile flow |
| CareerIntent snapshot | intent service / v0.6 confirmed revision |
| Role evidence/Profile | v0.5 role profile flow |
| GapAssessment/ComparisonSet | v0.6 matching flow |
| TargetDecision | v0.6 decision flow |
| LearningPlan | v0.7 preparation flow |

跨边界只能传 ID、已校验结构化请求和 RebuildDirective。

## 8. 隐私与日志

- note 可为空，默认不要求用户解释拒绝原因。
- trace 记录 decision ID、status、reason code，不复制完整 note。
- correction 自由文本必须交给 Human Interaction/Evidence 流程归档，不塞入 directive trace。
- intent/decision 不得包含 Cookie、API key 或平台登录信息。
