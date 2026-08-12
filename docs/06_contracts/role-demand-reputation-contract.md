# Role Demand and Reputation Contract

状态：WP3.1.2 Implemented / Offline Passed / Multi-platform L2 Partial
日期：2026-08-12

## 1. 适用范围

本 Contract 定义 WP3.1 新 run 的公司-岗位分组、社区证据分类、岗位需求画像、岗位/公司评价画像和统一读取包。
原始 Artifact、Extraction、Fragment、RoleTargetBinding、RoleFamilyMembership 与 detail receipt 继续复用。

## 2. CompanyRoleGroup

```text
CompanyRoleGroup
  group_id
  search_scope_id
  company_key
  company_display_name
  company_aliases[]
  company_search_term
  verified_company_aliases[]
  company_alias_policy_version
  role_family_id
  job_instance_ids[]
  exact_role_terms[]
  status: active | insufficient_identity | excluded
  created_at
```

Invariant：`job_instance_ids` 中每个岗位必须已经通过同一 SearchScope 的 family membership；
company identity 不足时不得生成 community query。`company_search_term` 必须等于法定显示名或
`verified_company_aliases` 成员；别名不得来自模型自由输出。

## 3. CommunitySearchPlan

```text
CommunitySearchPlan
  plan_id
  company_role_group_id
  queries[]:
    query_id
    evidence_purpose: interview_experience | employment_experience
    round_index: 1 | 2 | 3
    relaxation_level: exact_role | role_family | company_only
    parent_query_id?
    source_priority: 1 | 2
    source_id
    query_kind: company_exact_role | company_role_family |
                company_reputation | generic_family_interview
    query_text
    intended_document_types[]
    source_ids[]  # legacy read compatibility
    search_budget
    detail_budget
    expansion_reason
  status: planned | running | completed | partially_blocked | blocked
```

每个新 query 只绑定一个 source，具有独立预算和回执。`source_ids/query_kind` 只保留旧对象读取兼容。
round 1/2/3 分别使用完整岗位名、岗位族展示名、公司；company-only 结果不能自动绑定 job。

## 3.1 CommunitySearchAttemptReceipt

```text
CommunitySearchAttemptReceipt
  attempt_id
  company_role_group_id
  query_id
  source_id
  evidence_purpose
  round_index
  relaxation_level
  status: completed | empty | blocked | failed | budget_exhausted
  discovered_candidate_ids[]
  detail_document_ids[]
  accepted_document_ids[]
  reason_codes[]
  created_at
```

Attempt ID 由 group、purpose、source、round 和 query 内容生成；恢复或重复 response 不产生新对象。

## 3.2 CommunityEvidenceCoverage

```text
CommunityEvidenceCoverage
  coverage_id
  company_role_group_id
  evidence_purpose
  target_document_count: 2
  accepted_document_ids[]
  independent_document_count
  attempted_query_ids[]
  exhausted_source_ids[]
  status: sufficient | insufficient | blocked | budget_exhausted
  next_action: next_round | switch_source | next_purpose | next_group | complete | finalize_partial
  reason_codes[]
  assessed_at
```

独立详情按 canonical URL、平台帖子 ID、正文 hash 去重。Coverage 是确定性对象，不接受模型输出。
两篇即停止；未满两篇不得把搜索摘要、重复转载或模型常识计入覆盖率。

## 4. CommunityEvidenceDocument

```text
CommunityEvidenceDocument
  document_id
  artifact_id
  source_document_id
  source_id
  detail_url
  retrieved_at
  published_at?
  author_fingerprint?
  document_type: interview_experience | employment_experience | mixed | unknown
  company_key?
  role_family_id?
  job_instance_id?
  classification_receipt_id
```

search result/snippet 不能创建本对象；必须存在成功的社区 detail Raw Artifact。

## 4.1 CommunityDocumentClassificationReceipt

```text
CommunityDocumentClassificationReceipt
  receipt_id
  source_document_id
  artifact_id
  document_type
  accepted_segment_ids[]
  rejected_segment_count
  reason_codes[]
  provider / model / prompt_version
  created_at
```

LLM 只提供 document type、原文 quote 和 scope 建议；应用定位 quote、计算字符范围与 hash、
生成 Fragment/Segment ID 后，才创建本回执。不存在或不能唯一定位的 quote 计入 rejected，
不得保存为 accepted Segment。

## 5. CommunityEvidenceSegment

```text
CommunityEvidenceSegment
  segment_id
  document_id
  fragment_id
  quote_start
  quote_end
  quote_hash
  segment_type:
    written_exam | interview_process | interview_question |
    recruiter_feedback | project_preference |
    work_intensity | management | team_atmosphere | compensation |
    growth | stability | work_content | other_reputation | unknown
  usage:
    demand_assessment | reputation_job | reputation_company | excluded
  company_key?
  role_family_id?
  job_instance_id?
  scope_confidence
  classification_confidence
  validation_status: accepted | rejected | ambiguous
  reason_codes[]
```

Usage allowlist：

- written_exam、interview_process、interview_question、recruiter_feedback、project_preference
  只允许 `demand_assessment`；
- work_intensity、management、team_atmosphere、compensation、growth、stability、work_content、
  other_reputation 只允许 reputation；
- `reputation_job` 必须有经过验证的 job 或 company-role scope；
- `reputation_company` 必须有经过验证的 company scope；
- unknown、ambiguous 或缺少 quote 定位的 segment 必须 `excluded`。

`mixed` 文档只有在拆出至少两个各自带 quote 的 segment 后，才允许同时贡献 Demand 与 Reputation。

## 6. JobDemandProfile

```text
JobDemandProfile
  profile_id
  job_instance_id
  company_key
  role_family_id
  search_scope_id
  jd_requirements:
    responsibilities[]
    qualifications[]
    capabilities[]
    preferred_qualifications[]
    work_context[]
  assessment_signals[]:
    topic
    stage?
    observation: observed | frequent | insufficient_sample | disputed
    sample_count
    independent_source_count
    segment_ids[]
  source_document_ids[]
  official_escalation_receipt_id?
  published_at
```

`jd_requirements` 只能引用 permitted recruitment/official detail document；`assessment_signals` 只能引用
accepted demand_assessment segment。单篇面经不能发布为 frequent。

## 7. RoleFamilyDemandProfile

```text
RoleFamilyDemandProfile
  profile_id
  role_family_id
  search_scope_id
  member_job_profile_ids[]
  common_requirements[]
  differentiating_requirements[]
  assessment_signals[]
  denominator:
    accepted_job_count
    accepted_interview_document_count
  conflicts[]
  published_at
```

JD requirement prevalence 和 interview signal frequency 使用独立分母。未通过 family membership 的岗位不得参与。

## 8. ReputationDimension

```text
ReputationDimension
  dimension
  polarity: favorable | mixed | unfavorable | unknown
  sample_status: insufficient_sample | observed | sufficient | disputed
  sample_count
  independent_source_count
  role_distribution[]
  earliest_published_at?
  latest_published_at?
  supporting_segment_ids[]
  contradicting_segment_ids[]
  limited_summary
```

`limited_summary` 不能覆盖计数、scope、时间或冲突。Contract 不提供 `overall_score`。

## 9. JobReputationProfile 与 CompanyReputationProfile

```text
JobReputationProfile
  profile_id
  company_key
  role_family_id
  job_instance_ids[]
  dimensions[]: ReputationDimension
  source_document_ids[]
  published_at

CompanyReputationProfile
  profile_id
  company_key
  covered_role_families[]
  dimensions[]: ReputationDimension
  source_document_ids[]
  published_at
```

company-only segment 只能进入 CompanyReputationProfile。公司维度结论必须展示 role distribution，不能把
某一个岗位的体验无条件外推为全公司事实。

## 10. OfficialEscalationReceipt

```text
OfficialEscalationReceipt
  receipt_id
  job_instance_id
  required: false
  trigger: cross_platform_conflict | suspected_stale_or_closed |
           missing_critical_fields | user_priority_request
  status: not_requested | verified | unavailable | adapter_required | conflicting
  official_document_ids[]
  reason_codes[]
  created_at
```

充分的平台详情必须创建 `trigger=not_required,status=not_requested` 的可审计回执；存在 escalation
trigger 时才创建 OfficialVerificationPlan。官网不可用或未实现 adapter 不阻止已通过 recruitment
detail gate 的 Demand 发布，但必须保留对应状态。

## 11. RoleIntelligenceBundle

```text
RoleIntelligenceBundle
  bundle_id
  search_scope_id
  role_family_demand_profile_id
  job_demand_profile_ids[]
  job_reputation_profile_ids[]
  company_reputation_profile_ids[]
  raw_evidence_refs[]
  source_receipt_ids[]
  missing_sections[]
  evidence_cutoff
  created_at
```

Bundle 只是 typed references，不复制原文或合并成单一“总画像”。

## 12. Consumer Allowlist

- Matching/GapAssessment：JobDemandProfile、RoleFamilyDemandProfile 的 JD requirements；
- Preparation：JD requirements 与 assessment_signals；
- TargetDecision：Demand matching result 与 Reputation profiles；
- Role Q&A：Bundle 全部 typed refs，但输出必须标识 `JD fact | interview signal | subjective reputation`。

任何越界输入都返回 `evidence_usage_violation`，不得依赖 prompt 自律。

## 13. 发布 Invariants

1. search-only artifact 的投影数为 0；
2. 所有 Demand requirement 都可追溯到 recruitment/official detail Fragment；
3. 所有 assessment signal 都可追溯到 interview segment；
4. 所有 Reputation dimension 都可追溯到 employment segment；
5. interview -> Reputation 和 employment -> Demand 的泄漏数均为 0；
6. unknown/ambiguous segment 的投影数为 0；
7. 重复 detail、segment 或 response 不产生重复写入；
8. 旧 Snapshot 只读，新对象不得回写旧 `hiring_signals`。

## 14. CommunitySearchDiagnostic

```text
CommunitySearchDiagnostic
  diagnostic_id
  source_id
  query_id
  outcome: post_candidates_found | non_post_cards_only | search_empty |
           parser_changed | authentication_required | risk_controlled | failed
  raw_record_count
  post_candidate_count
  non_post_record_count
  parser_signature
  reason_codes[]
```

诊断由应用读取已归档 search Raw 后确定。`parser_changed` 必须有可识别的帖子结构证据；只有公司卡、
职位卡或零记录时分别使用 `non_post_cards_only/search_empty`。

## 15. MediaCrawler Bridge Protocol

- Base URL 默认 `http://127.0.0.1:8080/api`；只允许 localhost。
- 允许 `GET crawler/status`、`POST crawler/start`、`GET data/files`、
  `GET data/files/{path}?preview=false`。
- 请求只允许 `platform=xhs`、`crawler_type=search|detail`、关闭评论/子评论、空 creator IDs、
  `max_notes_count<=10`。
- Bridge 只处理本轮新增/变更、platform=xhs、item_type=contents 的 JSON 数据，并按关键词或目标 post ID
  过滤。传出对象删除 Cookie、xsec_token、签名、作者身份和上游文件路径。
- Graph 只接收 canonical URL 与 opaque candidate ref；详情 locator 仅存放于 Sidecar root 的 0600 cache。

## 16. DeepSeek Community Extraction

- `deepseek-v4-flash/pro` 使用标准 API、非思考模式 Tool Calling；不启用 beta strict endpoint。
- Tool payload 必须符合 `CommunityExtractionBatch`，缺失值显式为空，不允许补全、改写引文或产生内部 ID。
- 至多三次总尝试；所有尝试失败后才写最终模型失败回执。成功结果仍由应用执行 quote、scope 和 usage 校验。
