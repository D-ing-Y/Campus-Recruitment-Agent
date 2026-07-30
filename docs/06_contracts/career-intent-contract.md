# CareerIntent Intake Contract

状态：v0.7.1 WP2 Active  
日期：2026-07-30

## 1. CareerIntentCandidate

```json
{
  "target_roles": [
    {"value": "Agent 开发", "evidence_fragment_ids": ["fragment-1"], "confidence": 0.95}
  ],
  "constraints": [
    {
      "key": "location",
      "values": ["成都"],
      "kind": "hard",
      "evidence_fragment_ids": ["fragment-1"],
      "confidence": 0.98
    }
  ],
  "unresolved_fields": ["recruitment_type"]
}
```

候选必须由 Pydantic 校验；fragment refs 必须属于本次 raw artifact。候选 ID、constraint ID、role family
和 `affects_search_scope` 均由代码生成，不信任模型提供的身份或策略字段。

## 2. Classification

| 用户表达 | canonical 结果 |
| --- | --- |
| 工作地点必须成都 | location=成都, hard, affects_search_scope=true |
| 2027 年毕业 | graduation_year=2027, hard, affects_search_scope=true |
| 校招 | recruitment_type=campus_unspecified, unresolved |
| 优先大型企业 | company_type=大型企业, negotiable |
| 优先互联网科技公司 | company_type=互联网科技公司, negotiable |

`campus_unspecified` 不得进入最终 SearchScope recruitment_type；确认时必须改为
`autumn_campus | spring_campus | unknown`。

## 3. CareerIntent v0.7.1

- `constraints` 是约束/偏好的 canonical source；必须全部 confirmed。
- `locations/graduation_year/recruitment_type/industries/companies/company_types` 是确定性投影。
- `raw_artifact_ids/source_fragment_ids` 保留来源；不使用 Candidate supporting_claim_ids 冒充意图来源。
- `confirmed=true` 是发布 snapshot 的前提。
- 相同 canonical payload + owner 复用 snapshot；不同 payload 创建 successor snapshot。

## 4. IntentReviewRequest/Response

Request 含 request/thread/run/user、candidate summary、validation issues、unresolved fields 和
`confirm|revise|cancel` allowlist。Response 以 response ID 幂等：

- confirm 不带 patch；
- revise 必须带 allowlisted patch，支持 target_roles、locations、graduation_year、
  recruitment_type、industries、companies 和 company_types；constraint kind 由确定性 policy 管理，
  不接受任意自由修改；
- raw response 归档为 evidence；同 response ID 不同 payload 拒绝。

## 5. SearchScopeProjection

SearchScope 通过 `intent_scope_v1` 确定性生成并保存。fingerprint 不含 snapshot ID；普通偏好变化不改变
scope fingerprint，hard scope 变化必须改变 fingerprint。

## 6. Handoff

```json
{
  "handoff_type": "role_research_required",
  "origin_object_refs": {"career_intent_snapshot_id": "intent-snapshot-1"},
  "required_input_refs": {
    "career_intent_snapshot_id": "intent-snapshot-1",
    "search_scope_id": "scope-1"
  },
  "status": "pending",
  "handler_version": "role_research_handoff_v1"
}
```

WP2 只创建 handoff，不消费、不访问外部岗位来源。

## 7. 不变量

- raw-before-interpret；模型失败时 raw artifact 仍存在。
- 非 confirmed intent 不产生 snapshot/scope/handoff。
- Tool Calling/Pydantic 成功不等于分类正确；领域 Validator 必须运行。
- Run artifact/State 不保存完整 raw intent，secret/private-content leak 为 0。
- duplicate confirmation write count 为 0。
