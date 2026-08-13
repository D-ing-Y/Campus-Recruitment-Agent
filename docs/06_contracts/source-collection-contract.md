# Source Collection Contract

状态：v0.5 Accepted / Amended for v0.7.1 WP3.2
日期：2026-07-20（2026-08-12 修订）

本契约定义第三方招聘发现、企业官网核验与社区经验来源的查询、采集、原始归档、
归一化、身份链接、字段消解、去重和运行记录。三类来源共享 transport/归档基础设施，
但不得共享业务输出 schema。

## WP3.1 修订说明

- 招聘平台的具体 `job_detail` 是 Demand Profile 默认充分证据；search page/card 仍只做 discovery。
- `employer_official` 保留为关键冲突、疑似过期、字段缺失或用户指定时的可选证据升级，不再是每个
  Role run 的强制步骤。
- `experience` channel 的旧统一输出只做兼容读取；新 run 必须先进入 community post detail，再按
  `role-demand-reputation-contract.md` 分类为 interview/employment typed segments。
- 下文“第三方 + 官网”处理链只在创建 OfficialVerificationPlan 时执行；没有升级计划时，招聘平台
  detail Claims 可直接在来源限制内进行 Demand projection。

## 1. Source Channel 与 Authority

`channel`：

```text
recruitment_discovery
employer_official
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

处理顺序不变量：

```text
第三方 raw/Claim + 官网 raw/Claim
  → JobIdentityLink
  → FieldResolution
  → ResolvedJobPosting view
```

任何来源都必须先独立进入统一证据层，不能先合并网页字段再保存一份“最终证据”。

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
  "company_types": [],
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
- fingerprint 只覆盖规范化检索语义，不包含 `scope_id`、`created_at` 或
  `career_intent_snapshot_id`；因此同范围的新 CareerIntent snapshot 不会误触发重检索。

## 3. SourceCapabilities

```json
{
  "source_id": "zhaopin_jobs",
  "channel": "recruitment_discovery",
  "source_type": "recruitment_platform",
  "adapter_version": "zhaopin_jobs_v1",
  "supports_keyword": true,
  "supports_location": true,
  "supports_company": false,
  "supports_pagination": true,
  "supports_detail_fetch": true,
  "requires_auth": false,
  "authorization_mode": "none",
  "live_enabled": false,
  "rate_limit_per_minute": 6
}
```

capability 由 adapter 声明，QueryPlanner 不得生成 source 不支持的过滤条件。
`supports_detail_fetch=false` 的来源若收到详情请求，必须返回 `unsupported_input`，不得把搜索页
改标成详情页。

`authorization_mode` 取值为 `credential_ref | external_session | none`。`credential_ref` 来源只在
Graph 保存本地凭据引用；`external_session` 来源通过 health/auth status 验证外部浏览器会话，不向
Graph 返回 Cookie。历史对象缺少该字段时由 `requires_auth` 推导：true 为 credential_ref，false 为 none。

WP3.2.1 新 run 还应使用 additive `operation_authorization`：

```json
{
  "collect": ["credential_ref"],
  "fetch_detail": ["browser_profile_ref"]
}
```

允许的 mode 为 `credential_ref | browser_profile_ref | external_sidecar | none`。旧
`authorization_mode` 继续作为没有 operation map 时的兼容默认值。牛客使用 collect=credential_ref、
fetch_detail=browser_profile_ref；小红书两个操作均要求 browser_profile_ref 和 external_sidecar。

## 4. SourceQuery 与 RoleQueryPlan

```json
{
  "query_id": "query-1",
  "schema_version": "v0.5",
  "channel": "recruitment_discovery",
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

### 4.1 外部详情定位

`SourceDetailRequest` 可携带 `external_locator_ref`。该字段必须是不含 Cookie、token、签名参数和正文
的 opaque ref；详情 ID/传输参数只能在 Sidecar 本地候选缓存中解析。canonical URL 可用于跨轮去重，
但不得把 xsec_token 等敏感查询参数写入 checkpoint、日志、报告或 Git。

小红书来源成功响应仍必须先逐字节归档为 Raw Artifact，再生成 `experience_search` 或
`experience_post`。搜索响应不得生成 Segment。

真实 MediaCrawler HTTP adapter 必须使用固定版本实际提供的 `/api` 前缀和可下载文件格式。测试假
Sidecar 必须复刻真实路径、查询参数和响应结构。REST Client 只读取任务开始后新增或发生版本变化的内容
文件，并按本轮关键词/post ID 过滤，避免同日 append 文件把历史搜索结果混入当前 Raw。

公司品牌别名只属于 query discovery metadata。`company_key` 仍取自已通过详情门禁的招聘证据；
未经版本化 allowlist 核验的别名不得进入查询或详情 scope allowlist。

招聘搜索候选可携带 `location_hint`，应用可依据当前 `SearchScope.locations` 对详情抓取顺序做稳定
优先级调整。该提示只用于 discovery budget 排序，不能成为岗位事实或绕过详情证据门禁。

Sidecar 任务回到 `idle` 却没有产生本轮文件时，Bridge 必须读取有界的任务诊断并区分真实空结果与
子进程失败、登录要求、风控或网络超时；不得把浏览器启动/导航失败记为 `search_empty`。

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

## 4.1 Search Candidate 与 SourceDetailRequest

搜索页只允许生成发现候选，不允许生成画像事实：

```text
JobDetailCandidate
  candidate_id
  source_id / query_id
  search_document_id / search_artifact_id / supporting_fragment_id
  detail_url
  platform_job_id?
  company_hint? / role_title_hint?

CommunityPostCandidate
  candidate_id
  source_id / query_id
  search_document_id / search_artifact_id / supporting_fragment_id
  detail_url
  company_hint? / role_family_hint?
  intended_document_types[]
```

候选必须通过应用生成的 `SourceDetailRequest` 才能进入详情采集：

```json
{
  "detail_request_id": "detail-request:content-hash",
  "schema_version": "v0.7.1",
  "source_id": "zhaopin_jobs",
  "channel": "recruitment_discovery",
  "query_id": "query-1",
  "candidate_id": "candidate-1",
  "parent_document_id": "search-document-1",
  "detail_url": "https://example.com/job/1",
  "expected_document_kind": "job_detail",
  "idempotency_key": "sha256"
}
```

`detail_request_id` 与幂等键由 `source_id + channel + canonical detail URL + expected kind`
计算。query/candidate 只保留 discovery provenance，不能使同一详情页被重复归档。

来源适配器保留 `collect(query, credential_ref)`，并可独立实现
`fetch_detail(request, credential_ref)`；详情能力缺失必须显式返回 `unsupported_input`。

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
  "channel": "recruitment_discovery",
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
official_search
official_job_detail
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
official_not_found
official_unavailable
adapter_required
policy_blocked
failed
```

`success` 必须有有效 raw_artifact_id。登录页、验证码页和错误模板不得标为 success。

## 6. SourceCollectionBatch

```json
{
  "batch_id": "batch-1",
  "schema_version": "v0.5",
  "source_id": "zhaopin_jobs",
  "channel": "recruitment_discovery",
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
  "channel": "recruitment_discovery",
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
  "source_ids": ["zhaopin_jobs", "official_careers"]
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

## 11. OfficialVerificationPlan

```json
{
  "verification_plan_id": "official-plan-1",
  "schema_version": "v0.5",
  "job_cluster_id": "cluster-1",
  "canonical_company": "示例科技",
  "candidate_role_title": "AI Agent开发工程师",
  "candidate_location": "成都",
  "candidate_recruitment_cycle": "2027_autumn",
  "candidate_application_ids": [],
  "official_domain_candidates": ["careers.example.com"],
  "official_entry_url_candidates": ["https://careers.example.com/jobs"],
  "allowed_domains": ["careers.example.com"],
  "max_pages": 10,
  "max_depth": 2,
  "created_reason": "verify_third_party_candidate"
}
```

约束：

- 只在第三方岗位去重后创建，避免对重复记录重复核验。
- allowed domain、页面预算和 timeout 由确定性 policy 设置，LLM 不能扩大。
- 官网未确认时不能把第三方记录删除或标记为虚假。

## 12. JobIdentityLink

```json
{
  "job_identity_link_id": "identity-link-1",
  "schema_version": "v0.5",
  "job_cluster_id": "cluster-1",
  "official_job_posting_id": "official-job-1",
  "status": "confirmed",
  "match_confidence": 0.94,
  "match_signals": {
    "company": "exact",
    "role_title": "strong",
    "location": "exact",
    "recruitment_cycle": "exact",
    "application_id": "unknown",
    "responsibility_signature": "strong"
  },
  "supporting_fragment_ids": ["fragment-3", "fragment-4"],
  "created_at": "2026-07-19T00:00:00+08:00"
}
```

`status`：

```text
candidate
confirmed
rejected
identity_ambiguous
official_not_found
official_unavailable
```

仅公司、岗位标题和地点相似不能自动产生 `confirmed`。链接必须保存可重放的匹配信号与证据。

## 13. FieldResolution

```json
{
  "field_resolution_id": "resolution-1",
  "schema_version": "v0.5",
  "job_identity_link_id": "identity-link-1",
  "predicate": "qualification.degree",
  "chosen_claim_id": "claim-official-1",
  "conflicting_claim_ids": ["claim-platform-1"],
  "resolution_status": "resolved",
  "reason": "official_primary_and_newer",
  "authority": "primary",
  "freshness": "current",
  "resolved_at": "2026-07-19T00:00:00+08:00"
}
```

`resolution_status`：

```text
resolved
third_party_only
official_only
unresolved_conflict
identity_ambiguous
```

FieldResolution 是字段级派生状态，不覆盖或删除任何来源 Claim。官网缺少薪资等字段时，
第三方值可使用 `third_party_only` 保留。

## 14. OfficialSiteAdapterSpec

未知官网只能生成声明式 adapter 候选：

```json
{
  "spec_id": "official-spec-1",
  "schema_version": "v0.5",
  "allowed_domains": ["careers.example.com"],
  "entry_url_patterns": ["https://careers.example.com/jobs*"],
  "document_kind_rules": [],
  "selectors_or_jsonpaths": [],
  "pagination_rules": [],
  "stop_conditions": {
    "max_pages": 10,
    "max_depth": 2
  },
  "status": "candidate"
}
```

注册前必须通过 schema、域名白名单、离线 fixture replay、契约测试和人工批准。
live runtime 不得生成并执行 Python/JavaScript 爬虫。

## 15. Source Authority Policy

最低规则：

- employer official 对 `role.active`、`application.*`、`qualification.*`、
  `responsibility.*` 和 `location.*` 为 primary。
- recruitment platform 对上述字段为 allowed；与已确认官网冲突时生成 FieldResolution，
  不整条覆盖。
- `role.active`、`application.url`、`application.deadline`：community forbidden。
- `qualification.*`、`responsibility.*`、`requirement.*`：community signal_only。
- `hiring_signal.written_exam/interview/project_preference`：community allowed。
- `salary.platform_display`：recruitment allowed；community 只能 anecdotal。
- `work_context`：community anecdotal，必须显示 scope/confidence。

authority violation 的 Claim 必须拒绝写入并进入 Eval。

“官网未找到”本身不是 `role.closed` Claim；只有官网明确关闭、截止时间已过或新的官方材料
支持时，才能将岗位标为 closed/expired。

## 16. CredentialRef

```json
{
  "credential_ref": "local-secret://nowcoder_experience/default",
  "source_id": "nowcoder_experience",
  "credential_type": "api_key_ref",
  "validated_at": "2026-07-18T00:00:00+08:00"
}
```

这只是引用契约。CredentialRef 不能被 EvidenceArtifact 保存，真实秘密值只能由本地 credential service 在 Tool 调用边界解析。

## 16.1 BrowserProfileRef

```json
{
  "profile_ref": "local-browser-profile://nowcoder_experience/default",
  "source_id": "nowcoder_experience",
  "name": "default"
}
```

BrowserProfileRef 不是 CredentialRef，也不包含 Profile 路径或 CDP endpoint。真实 Profile 目录、浏览器
进程、Cookie 和 Storage State 只能由本地 BrowserProfileManager 解析。Graph State 只保存 ref。

## 17. 归档与版本

- 原始 Artifact 不可变。
- extraction/parser/normalizer/adapter/prompt/schema 都必须有版本。
- raw hash 相同可复用 Artifact；新获取时间作为新的 SourceRun observation 保存。
- 网页更新产生新 raw hash 和新 Artifact，不覆盖旧版本。
- live raw 和 credential 默认进入 Git 忽略目录。
