# Source Collection Contract

状态：v0.5 Design Accepted / Pending Implementation
日期：2026-07-18

本契约定义岗位招聘来源与社区经验来源的查询、采集、原始归档、归一化、去重和运行记录。两类来源共享 transport/归档基础设施，但不得共享业务输出 schema。

## 1. Source Channel 与 Authority

`channel`：

```text
recruitment
experience
```

`source_type`：

```text
employer_official
recruitment_platform
community_experience
fixture
manual_import
```

`authority`：

```text
primary
allowed
signal_only
forbidden
```

authority 是字段级策略，不是来源的单一总分。同一来源可对不同 predicate 有不同权限。

## 2. SearchScope

```json
{
  "scope_id": "scope-1",
  "schema_version": "v0.5",
  "career_intent_snapshot_id": "intent-snapshot-1",
  "target_role_queries": ["AI Agent工程师", "LLM应用开发"],
  "target_role_family": "ai_agent_engineering",
  "locations": ["成都"],
  "graduation_year": "2027",
  "recruitment_type": "autumn_campus",
  "industries": [],
  "companies": [],
  "hard_constraints": [],
  "preferred_languages": ["zh-CN"],
  "created_at": "2026-07-18T00:00:00+08:00"
}
```

约束：

- SearchScope 来自 CareerIntent 或显式输入。
- target role、毕业年份和招聘类型不能为空或必须显式 unknown。
- 每个 SearchScope 只对应一个 canonical target role family；多个方向使用独立 Graph run。
- CandidateProfile capability 不进入 SearchScope。
- SearchScope 在单次 run 中不可由 LLM 自行扩大。

## 3. SourceCapabilities

```json
{
  "source_id": "zhaopin_jobs",
  "channel": "recruitment",
  "source_type": "recruitment_platform",
  "adapter_version": "zhaopin_jobs_v1",
  "supports_keyword": true,
  "supports_location": true,
  "supports_company": false,
  "supports_pagination": true,
  "requires_auth": false,
  "live_enabled": false,
  "rate_limit_per_minute": 6
}
```

capability 由 adapter 声明，QueryPlanner 不得生成 source 不支持的过滤条件。

## 4. SourceQuery 与 RoleQueryPlan

```json
{
  "query_id": "query-1",
  "schema_version": "v0.5",
  "channel": "recruitment",
  "source_id": "zhaopin_jobs",
  "keywords": ["AI Agent", "LLM应用"],
  "location": "成都",
  "company": null,
  "role_family": "ai_agent_engineering",
  "graduation_year": "2027",
  "recruitment_type": "autumn_campus",
  "cursor": null,
  "page_size": 20,
  "parent_query_id": null,
  "change_reason": "initial_scope",
  "fingerprint": "sha256"
}
```

`change_reason`：

```text
initial_scope
pagination
synonym_expansion
low_relevance
low_recall
authority_gap
source_fallback
```

fingerprint 由规范化 channel/source/keywords/location/company/role family/year/type/cursor 计算。

```json
{
  "plan_id": "plan-1",
  "schema_version": "v0.5",
  "scope_id": "scope-1",
  "queries": [],
  "coverage_gap_ids": [],
  "planner": {
    "provider": "mock",
    "model": "deterministic-role-query-v1"
  },
  "prompt_version": "role_query_planner_v1",
  "created_at": "2026-07-18T00:00:00+08:00"
}
```

## 5. SourceDocument

SourceAdapter 成功返回的每个 document 必须已经归档：

```json
{
  "source_document_id": "source-doc-1",
  "schema_version": "v0.5",
  "source_id": "zhaopin_jobs",
  "channel": "recruitment",
  "query_id": "query-1",
  "source_url": "https://example.com/job/1",
  "document_kind": "job_detail",
  "http_status": 200,
  "published_at": null,
  "retrieved_at": "2026-07-18T00:00:00+08:00",
  "raw_artifact_id": "artifact-1",
  "content_hash": "sha256",
  "content_type": "text/html",
  "access_status": "success",
  "warnings": []
}
```

`document_kind`：

```text
search_page
job_detail
employer_job_detail
experience_search
experience_post
imported_snapshot
```

`access_status`：

```text
success
empty
authentication_required
rate_limited
source_changed
robots_disallowed
failed
```

`success` 必须有有效 raw_artifact_id。登录页、验证码页和错误模板不得标为 success。

## 6. SourceCollectionBatch

```json
{
  "batch_id": "batch-1",
  "schema_version": "v0.5",
  "source_id": "zhaopin_jobs",
  "channel": "recruitment",
  "query_id": "query-1",
  "cursor": null,
  "next_cursor": "page-2",
  "documents": [],
  "status": "success",
  "error_type": null,
  "retryable": false,
  "needs_user_action": false,
  "idempotency_key": "sha256",
  "started_at": "2026-07-18T00:00:00+08:00",
  "completed_at": "2026-07-18T00:00:01+08:00"
}
```

batch 幂等键包含 source/query fingerprint/cursor/adapter version。相同 batch 重放复用第一次归档结果。

## 7. SourceRunReceipt

```json
{
  "source_run_id": "source-run-1",
  "schema_version": "v0.5",
  "run_id": "role-run-1",
  "source_id": "zhaopin_jobs",
  "channel": "recruitment",
  "adapter_version": "zhaopin_jobs_v1",
  "query_ids": ["query-1"],
  "received_count": 10,
  "archived_count": 10,
  "normalized_count": 8,
  "deduplicated_count": 7,
  "artifact_ids": ["artifact-1"],
  "public_source_urls": ["https://example.com/job/1"],
  "auth_used": false,
  "status": "completed",
  "warnings": [],
  "started_at": "2026-07-18T00:00:00+08:00",
  "completed_at": "2026-07-18T00:00:03+08:00"
}
```

receipt 不得保存 Cookie、Authorization、完整 headers、cURL 或凭据路径内容。

## 8. NormalizedJobPosting

```json
{
  "job_posting_id": "job-1",
  "schema_version": "v0.5",
  "job_id": "platform-job-id",
  "company": "示例科技",
  "company_type": "unknown",
  "role_title": "AI Agent开发工程师",
  "role_family": "ai_agent_engineering",
  "city": "成都",
  "work_location_detail": null,
  "salary_min": null,
  "salary_max": null,
  "salary_unit": null,
  "salary_source": "unknown",
  "job_description": "...",
  "requirements_raw": "...",
  "requirements_normalized": [],
  "degree_requirement": "硕士",
  "major_requirement": "计算机相关",
  "graduation_year": "2027",
  "recruitment_type": "autumn_campus",
  "application_deadline": null,
  "application_url": "https://example.com/apply/1",
  "source_url": "https://example.com/job/1",
  "source_id": "zhaopin_jobs",
  "source_type": "recruitment_platform",
  "source_date": null,
  "retrieved_at": "2026-07-18T00:00:00+08:00",
  "confidence": 0.9,
  "status": "included",
  "exclusion_code": null,
  "raw_artifact_ids": ["artifact-1"],
  "supporting_fragment_ids": ["fragment-1"],
  "notes": []
}
```

`status`：

```text
included
deferred
excluded_hard_scope
expired
closed
unknown
```

要求：

- 不允许只因信息缺失使用 `excluded_hard_scope`。
- `source_url`、`retrieved_at` 和 raw artifact refs 必填。
- 原始 description/requirements 与 normalized fields 同时保留。
- 缺失值使用 null、unknown 或空数组。

## 9. ExperienceEvidenceRecord

```json
{
  "experience_record_id": "experience-1",
  "schema_version": "v0.5",
  "platform": "nowcoder",
  "query_id": "query-2",
  "content_type": "interview_post",
  "source_url": "https://example.com/discuss/1",
  "title": "示例公司 AI Agent 一面",
  "author_ref": "anonymous",
  "published_at": "2026-06-01T00:00:00+08:00",
  "retrieved_at": "2026-07-18T00:00:00+08:00",
  "company": "示例公司",
  "role_title": "AI Agent开发工程师",
  "role_family": "ai_agent_engineering",
  "city": "成都",
  "stage": "first_interview",
  "scope_level": "company_role",
  "signals": {
    "written_exam": [],
    "interview": [],
    "tech_stack": [],
    "project_preference": [],
    "salary": [],
    "work_context": []
  },
  "summary": "...",
  "evidence_quotes": [
    {
      "text": "重点追问项目中的检索评估方法",
      "fragment_id": "fragment-2"
    }
  ],
  "confidence": 0.7,
  "tags": [],
  "raw_artifact_id": "artifact-2",
  "supporting_fragment_ids": ["fragment-2"],
  "notes": []
}
```

`scope_level`：

```text
job_instance
company_role
role_family
company_only
unknown
```

约束：

- signal 每项必须能引用 Fragment。
- company-only/unknown 内容不能归到具体岗位。
- 作者显示名不是身份验证；不得把匿名经验视为官方声明。
- summary 不能替代 signals 和 evidence refs。

## 10. JobPostingCluster

```json
{
  "cluster_id": "cluster-1",
  "schema_version": "v0.5",
  "canonical_job_posting_id": "job-1",
  "member_job_posting_ids": ["job-1", "job-2"],
  "exact_key": "sha256",
  "merge_method": "exact_normalized_key",
  "confidence": 1.0,
  "conflicts": [],
  "source_ids": ["zhaopin_jobs", "fixture_official"]
}
```

`merge_method`：

```text
same_source_url
same_content_hash
exact_normalized_key
verified_fuzzy_candidate
not_merged
```

自动 merge 还必须有 canonical application ID、相同 URL/hash 或职责/要求内容签名支撑；
仅公司、标题和地点相同不能证明是同一岗位。LLM 只能提出 fuzzy candidate，不能写最终 cluster。

## 11. Source Authority Policy

最低规则：

- `role.active`、`application.url`、`application.deadline`：community forbidden。
- `qualification.*`、`responsibility.*`、`requirement.*`：community signal_only。
- `hiring_signal.written_exam/interview/project_preference`：community allowed。
- `salary.platform_display`：recruitment allowed；community 只能 anecdotal。
- `work_context`：community anecdotal，必须显示 scope/confidence。

authority violation 的 Claim 必须拒绝写入并进入 Eval。

## 12. CredentialRef

```json
{
  "credential_ref": "local-secret://nowcoder/default",
  "source_id": "nowcoder_experience",
  "credential_type": "imported_curl",
  "validated_at": "2026-07-18T00:00:00+08:00"
}
```

这只是引用契约。CredentialRef 不能被 EvidenceArtifact 保存，真实秘密值只能由本地 credential service 在 Tool 调用边界解析。

## 13. 归档与版本

- 原始 Artifact 不可变。
- extraction/parser/normalizer/adapter/prompt/schema 都必须有版本。
- raw hash 相同可复用 Artifact；新获取时间作为新的 SourceRun observation 保存。
- 网页更新产生新 raw hash 和新 Artifact，不覆盖旧版本。
- live raw 和 credential 默认进入 Git 忽略目录。
