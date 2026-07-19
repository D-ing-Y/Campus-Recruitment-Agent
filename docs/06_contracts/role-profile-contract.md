# Role Profile Contract

状态：v0.5 Design Accepted / Pending Implementation
日期：2026-07-19

本契约定义具体岗位画像、岗位族画像、招聘要求、经验信号和覆盖度评价。所有事实字段必须引用已验证 Claim；所有岗位族统计必须保留样本与分母。

## 1. Role Requirement

```json
{
  "requirement_id": "requirement-1",
  "category": "core_capability",
  "capability_id": "cap:python",
  "raw_label": "熟悉 Python",
  "required_level": "unknown",
  "importance": "core",
  "obligation": "required",
  "scope": "job_instance",
  "confidence": 0.9,
  "authority": "allowed",
  "supporting_claim_ids": ["claim-1"]
}
```

`category`：

```text
hard_qualification
core_capability
bonus_capability
responsibility
work_context
other
```

`importance`：

```text
hard
core
bonus
context
```

`obligation`：

```text
required
preferred
mentioned
unknown
```

硬性资格由原文“必须/限/要求”等语义和字段类型决定，不由岗位族出现频率决定。

## 2. HiringSignal

```json
{
  "signal_id": "signal-1",
  "signal_type": "interview",
  "stage": "first_interview",
  "scope_level": "company_role",
  "summary": "重点追问 RAG 评估与项目职责",
  "occurrence_count": 1,
  "independent_source_count": 1,
  "frequency_label": "observed_signal",
  "confidence": 0.7,
  "freshness": "current_window",
  "supporting_claim_ids": ["claim-2"]
}
```

`signal_type`：

```text
written_exam
interview
project_preference
tech_stack
salary
work_context
other
```

单一社区帖子只能形成 `observed_signal`，不能称为常见或必考。

## 3. Qualification

```json
{
  "qualification_id": "qualification-1",
  "qualification_type": "graduation_year",
  "operator": "equals",
  "value": "2027",
  "importance": "hard",
  "status": "confirmed",
  "confidence": 0.95,
  "supporting_claim_ids": ["claim-3"]
}
```

`qualification_type` 至少包括：

```text
degree
major
graduation_year
recruitment_eligibility
language
location
other
```

## 4. JobInstanceRoleProfile

```json
{
  "role_profile_id": "role-instance-1",
  "schema_version": "v0.5",
  "profile_scope": "job_instance",
  "job_cluster_id": "cluster-1",
  "role_title": "AI Agent开发工程师",
  "role_family": "ai_agent_engineering",
  "company": "示例科技",
  "locations": ["成都"],
  "recruitment_type": "autumn_campus",
  "graduation_year": "2027",
  "source_status": "included",
  "application_url": "https://example.com/apply/1",
  "application_deadline": null,
  "qualifications": [],
  "responsibilities": [],
  "requirements": [],
  "bonus_items": [],
  "hiring_signals": [],
  "work_context": [],
  "company_specific_items": [],
  "unknowns": [],
  "conflicts": [],
  "evidence_coverage": {},
  "source_refs": [],
  "supporting_claim_ids": [],
  "freshness": {
    "status": "current",
    "valid_as_of": "2026-07-18T00:00:00+08:00",
    "published_at": null,
    "retrieved_at": "2026-07-18T00:00:00+08:00"
  },
  "confidence": 0.88,
  "previous_snapshot_id": null,
  "generated_at": "2026-07-18T00:00:00+08:00"
}
```

约束：

- 一个 job cluster 对应一个当前 job instance profile。
- source refs 保留 cluster 的全部招聘/官方来源。
- community hiring signals 可挂载，但不改变 qualifications/requirements 的 authority。
- application deadline 不存在时为 unknown，不得推断岗位仍开放。
- closed/expired 通过新 snapshot 表达，旧 snapshot 保留。

## 5. FamilyRequirementAggregate

```json
{
  "aggregate_id": "family-requirement-1",
  "category": "core_capability",
  "capability_id": "cap:python",
  "raw_labels": ["Python", "熟悉Python开发"],
  "importance_distribution": {
    "hard": 0,
    "core": 3,
    "bonus": 1
  },
  "supporting_job_instance_count": 4,
  "eligible_job_instance_count": 5,
  "supporting_company_count": 3,
  "eligible_company_count": 3,
  "prevalence": 0.8,
  "company_coverage": 1.0,
  "prevalence_band": "common",
  "scope_notes": [],
  "supporting_claim_ids": ["claim-1", "claim-4"]
}
```

`prevalence_band`：

```text
common
frequent
observed
insufficient_sample
```

prevalence、company coverage 和 distribution 必须由代码计算。

## 6. RoleFamilyProfile

```json
{
  "role_profile_id": "role-family-1",
  "schema_version": "v0.5",
  "profile_scope": "role_family",
  "role_title": "AI Agent / LLM应用开发",
  "role_family": "ai_agent_engineering",
  "market_scope": {
    "locations": ["成都"],
    "recruitment_type": "autumn_campus",
    "graduation_year": "2027",
    "industries": [],
    "companies": []
  },
  "sample": {
    "job_instance_count": 5,
    "distinct_company_count": 3,
    "distinct_location_count": 1,
    "experience_post_count": 4,
    "collection_window_start": "2026-07-01T00:00:00+08:00",
    "collection_window_end": "2026-07-18T00:00:00+08:00",
    "valid_as_of": "2026-07-18T00:00:00+08:00",
    "sample_status": "sufficient"
  },
  "hard_qualifications": [],
  "common_responsibilities": [],
  "core_requirements": [],
  "frequent_requirements": [],
  "observed_requirements": [],
  "bonus_items": [],
  "hiring_signals": [],
  "company_specific_variations": [],
  "location_specific_variations": [],
  "temporal_variations": [],
  "unknowns": [],
  "conflicts": [],
  "source_coverage": {},
  "supporting_job_instance_profile_ids": [],
  "supporting_claim_ids": [],
  "aggregation_policy_version": "role_family_aggregation_v1",
  "thresholds": {
    "common_min_prevalence": 0.6,
    "frequent_min_prevalence": 0.3,
    "min_job_instances": 3,
    "min_distinct_companies": 2,
    "common_min_supporting_job_instances": 2,
    "common_min_supporting_companies": 2
  },
  "confidence": 0.82,
  "previous_snapshot_id": null,
  "generated_at": "2026-07-18T00:00:00+08:00"
}
```

`sample_status`：

```text
sufficient
insufficient_jobs
insufficient_companies
stale
conflicted
unknown
```

约束：

- 不同 SearchScope/time window 的样本不能静默混合。
- company-specific requirement 保留为 variation。
- 没有足够公司/岗位样本时所有聚合 requirement 最多为 observed/insufficient_sample。
- `common` 还必须达到 supporting job/company 门槛，不能只依赖 prevalence 小样本。
- community signal 与招聘 requirement 分栏展示。

## 7. SourceRef 与 Freshness

```json
{
  "source_id": "boss_jobs",
  "source_type": "recruitment_platform",
  "source_url": "https://example.com/job/1",
  "raw_artifact_id": "artifact-1",
  "published_at": null,
  "retrieved_at": "2026-07-18T00:00:00+08:00",
  "authority": "allowed",
  "freshness": "current",
  "job_identity_link_id": "identity-link-1",
  "field_resolution_ids": ["resolution-1"],
  "supporting_claim_ids": ["claim-1"]
}
```

`freshness`：

```text
current
recent
historical
expired
unknown
```

时效标签由配置和时间字段计算，不由 LLM 自由决定。

## 8. RoleCoverageGap

```json
{
  "gap_id": "role-gap:company-diversity",
  "category": "company_diversity",
  "description": "当前岗位族样本只覆盖一家企业",
  "importance": 0.8,
  "uncertainty": 0.9,
  "retrievability": 0.8,
  "collection_cost": 0.2,
  "information_value": 0.376,
  "preferred_action": "change_source",
  "target_channel": "recruitment_discovery",
  "target_source_ids": [],
  "related_query_ids": ["query-1"],
  "status": "open"
}
```

`category`：

```text
job_count
company_diversity
field_completeness
source_authority
source_diversity
freshness
experience_signal
official_verification
identity_ambiguity
conflict
query_relevance
```

`preferred_action`：

```text
search_more
change_query
change_source
verify_official
await_user_auth
keep_unknown
```

information value 由确定性公式计算：

```text
importance × uncertainty × retrievability - collection_cost
```

## 9. RoleCoverageAssessment

```json
{
  "assessment_id": "role-assessment-1",
  "schema_version": "v0.5",
  "scope_id": "scope-1",
  "role_family_profile_snapshot_id": "snapshot-role-family-1",
  "is_sufficient": false,
  "dimension_results": {
    "recruitment_fields": "sufficient",
    "job_sample": "partial",
    "company_diversity": "insufficient",
    "source_authority": "sufficient",
    "official_verification": "partial",
    "identity_links": "partial",
    "freshness": "sufficient",
    "experience_signals": "partial",
    "conflicts": "sufficient"
  },
  "coverage_gaps": [],
  "recommended_action": "change_source",
  "reason": "岗位族样本只覆盖一家企业",
  "confidence": 0.85,
  "evaluator": {
    "provider": "mock",
    "model": "deterministic-role-coverage-v1"
  },
  "prompt_version": "role_coverage_v1",
  "created_at": "2026-07-18T00:00:00+08:00"
}
```

`recommended_action`：

```text
search_more
change_query
change_source
verify_official
await_user_auth
finalize_with_unknowns
complete
fail
```

最终 Graph 路由由 deterministic policy 裁决。

## 10. 聚合规则

```text
prevalence = unique supporting job clusters / eligible job clusters
company_coverage = unique supporting companies / eligible companies
signal_frequency = unique supporting experience records / eligible experience records
```

- cluster member source 数量不能增加 prevalence 分子。
- 同一经验帖转载不能增加 signal frequency。
- hard qualification 的 hard 属性不由频率决定。
- 统计分母为 0 时结果为 unknown，不得除零或填 0% 暗示不存在。

## 11. Snapshot 与兼容

- ProfileSnapshot `profile_type` 仍可使用 `role`，subject ID 区分
  `role_instance:<cluster_id>` 与 `role_family:<scope_hash>`。
- v0.3 RoleProfile 可读取但不参与 v0.5 aggregator，除非先显式迁移。
- canonical profile hash 相同则复用最新 snapshot。
- raw、Claim、job instance 和 family snapshot 均保留历史。
- v0.5 不生成 Candidate/Role match score。
