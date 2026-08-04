# Role Hierarchy and Evidence Gates Contract

状态：v0.7.1 Implemented / Offline Passed
日期：2026-08-03

## 1. RoleTargetBinding

```json
{
  "binding_id": "role-target:...",
  "target_role": "AI 应用开发",
  "role_family": "ai_agent_engineering",
  "mapping_policy_version": "role_family_mapping_v2"
}
```

同一 target role 只能有一个 primary family；一个 family 可以包含多个 target role。

## 2. RoleFamilyMembership

```json
{
  "membership_id": "role-membership:...",
  "scope_id": "scope-1",
  "job_posting_id": "job-1",
  "target_role_family": "backend_engineering",
  "primary_role_family": "backend_engineering",
  "secondary_role_tags": ["java"],
  "status": "accepted",
  "confidence": 0.95,
  "reason_codes": ["primary_family_matches_scope"],
  "supporting_fragment_ids": ["fragment-1"],
  "policy_version": "role_family_membership_v1"
}
```

`status`：`accepted | ambiguous | rejected`。只有 accepted 进入聚合分母。

## 3. RoleDetailEvidenceReceipt

```json
{
  "receipt_id": "role-detail:...",
  "scope_id": "scope-1",
  "job_cluster_id": "cluster-1",
  "status": "eligible",
  "detail_document_ids": ["source-doc-2"],
  "detail_artifact_ids": ["artifact-2"],
  "reason_codes": ["job_detail_archived"],
  "policy_version": "role_detail_gate_v1"
}
```

`status`：`eligible | missing | invalid`。eligible 必须至少引用一个成功详情 SourceDocument 和 Artifact。

## 4. ExperienceScopeLink

```json
{
  "experience_scope_link_id": "experience-link:...",
  "scope_id": "scope-1",
  "experience_record_id": "experience-1",
  "scope_level": "job_instance",
  "role_family": "ai_agent_engineering",
  "company": "示例公司",
  "job_cluster_id": "cluster-1",
  "status": "confirmed",
  "match_signals": {"company": "exact", "role_title": "exact", "family": "exact"},
  "supporting_fragment_ids": ["fragment-3"],
  "policy_version": "experience_scope_link_v1"
}
```

`status`：`confirmed | ambiguous | rejected`。job_instance confirmed 必须唯一指向一个 cluster。

## 5. 投影不变量

- RoleFamilyProfile supporting instances 全部具有 accepted membership 和 eligible detail receipt。
- JobInstanceRoleProfile hiring signal 全部具有 confirmed ExperienceScopeLink。
- Search document Artifact 不得出现在 detail_artifact_ids。
- 不同 SearchScope 的 receipt/link 不得跨 scope 复用。
- 所有新对象 ID、status、reason code 和 supporting refs 必须进入 run state/report，但不得包含网页全文。

## 6. 多 Scope 兼容边界

- `CareerIntent` 确认后以 `search_scope_ids`/`handoff_ids` 作为主输出。
- 仅有一个 family 时同时填充旧 `search_scope_id`/`handoff_id`；多 family 时 singular 字段为 null。
- `RebuildDirective` 以 `requested_scopes` 传递完整 scope set；仅有一个 scope 时保留
  `requested_scope` 兼容值。
- SearchScope set 的 impact hash 按所有 scope fingerprint 排序后计算，不得只比较第一个 family。
