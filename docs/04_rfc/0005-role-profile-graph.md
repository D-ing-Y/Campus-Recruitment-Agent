# RFC-0005: 证据驱动的岗位需求画像 Graph

状态：Accepted / Ready for Implementation（P0/P1 来源门禁完成）
日期：2026-07-19
关联需求：`docs/03_requirements/v0.5-role-profile-graph.md`
关联 ADR：`docs/05_adr/0005-separate-source-channels-and-role-profile-levels.md`

## 1. 背景

v0.4 已实现候选人画像 subgraph。v0.5 需要处理更动态的外部证据：岗位列表、岗位详情和社区经验帖会随时间变化，来源可能分页、限流、要求登录或改变页面结构，同一岗位也可能跨平台重复。

因此岗位画像不能只是“调用爬虫后让 LLM 总结”。Graph 必须保存检索意图、查询和来源运行记录，
保证 raw-before-parse，并将第三方岗位发现、企业官网核验和社区经验信号分开。
岗位身份链接和冲突消解发生在各来源分别进入证据层之后；Graph 在预算内自主决定继续搜索、
换词、换源、核验官网、等待授权或停止。

## 2. 目标

- 复用 v0.3 Evidence Store 与 v0.4 LangGraph/checkpoint 模式。
- 实现 recruitment discovery、employer official verification、experience 三类 SourceAdapter
  和统一运行外壳。
- 真实接入一个第三方招聘发现来源、一个企业官网核验链和一个经验来源，默认 CI 保持离线。
- 从归档网页构建 JobInstanceRoleProfile 和 RoleFamilyProfile。
- 实现查询规划、覆盖评价、换词/换源、授权 interrupt 和恢复。
- 让所有岗位事实、样本计数和停止原因可审计。

## 3. 非目标

- Candidate/Role 匹配、岗位推荐百分比和 Offer 概率。
- 学习计划、面试题生成、自动投递。
- Hybrid RAG、向量索引、分布式存储和 Multi-Agent。
- 全平台覆盖或绕过反爬。
- 运行时由 LLM 生成并执行新的爬虫代码。

## 4. 首版来源选择

2026-07-18 的设计核对：

| Adapter ID | Channel | 目标入口 | 用途 |
| --- | --- | --- | --- |
| `boss_jobs` | recruitment_discovery | `https://www.zhipin.com/` | 高召回岗位发现与第三方详情 |
| `official_careers` | employer_official | 企业官网/招聘站/受支持 ATS | 候选岗位身份和字段级核验 |
| `nowcoder_experience` | experience | `https://www.nowcoder.com/` | 校招笔经、面经和招聘过程信号 |

选择原则：

- 招聘平台用于未知公司范围的首版发现；企业官网用于每个保留岗位 cluster 的二次核验，
  但不承担全市场发现。
- 牛客经验内容与招聘岗位内容使用不同 adapter/schema。
- 当前页面可访问不代表长期稳定，adapter 必须以 `source_changed` 显式失败。
- live 请求参数从用户正常浏览产生的 Copy as cURL 中导入，本 RFC 不固定或逆向私有 API。
- 开源项目必须先进入可行性报告并通过 license、维护状态、凭据边界和最小 smoke；
  Graph 只依赖本项目的 adapter contract，不依赖上游项目的数据模型。

## 5. 总体方案

```text
START
  ↓
initialize_role_run
  ↓
plan_role_queries
  ↓
collect_and_archive_sources
  ↓
extract_and_normalize_sources
  ↓
deduplicate_source_records
  ↓
plan_official_verification
  ↓
collect_and_archive_official_sources
  ↓
link_official_job_records
  ↓
resolve_job_field_conflicts
  ↓
extract_and_validate_role_claims
  ↓
project_job_instance_profiles
  ↓
aggregate_role_family_profile
  ↓
assess_role_coverage
  ↓
route_role_next_action
  ├─ search_more ────────────────→ plan_role_queries
  ├─ change_query ───────────────→ plan_role_queries
  ├─ change_source ──────────────→ plan_role_queries
  ├─ verify_official ────────────→ plan_official_verification
  ├─ await_user_auth ────────────→ plan_source_auth
  ├─ finalize_with_unknowns ─────→ finalize_role_profiles
  ├─ complete ───────────────────→ finalize_role_profiles
  └─ fail ───────────────────────→ finalize_role_profiles

plan_source_auth
  → interrupt_for_source_auth
  → resume
  → validate_source_authorization
      ├─ authorized → collect_and_archive_sources
      ├─ skip_source → plan_role_queries
      └─ cancel → finalize_role_profiles
```

招聘和经验 query 可以在 `collect_and_archive_sources` 中做已知任务列表的有界并行调用，但不创建 Sub-Agent。

## 6. State

`RoleProfileGraphState`：

```text
run_id, thread_id, user_id, status
career_intent_snapshot_id, search_scope
query_plan, pending_queries, completed_query_ids, query_history
enabled_source_ids, skipped_source_ids, source_capabilities
pending_auth_source_id, credential_refs
source_batch_ids, source_run_receipts
raw_artifact_ids, extraction_ids
normalized_job_ids, experience_record_ids, job_cluster_ids
official_verification_plan_ids, job_identity_link_ids, field_resolution_ids
fragment_ids, claim_ids
job_instance_profile_snapshot_ids, role_family_profile_snapshot_id
coverage_assessment, coverage_gaps, next_action
pending_interaction, resume_input
budgets, counters
tool_results, llm_calls, trace, errors, report
```

完整 query record、SourceRun、Artifact、Normalized Record、Claim 和 ProfileSnapshot 通过 repository 读取；State 只保存 ID、当前决策和小型摘要。

Reducer：

- trace/errors/tool/LLM/source receipt：append；
- ID 集合：stable union；
- search scope 和 budget：initialize once；
- query plan、coverage、next action、pending interaction：replace；
- credential refs：按 source ID 合并，只保存引用；
- profile snapshot IDs：job instance stable union，role family replace；
- resume input：证据/授权校验后 clear。

## 7. 节点职责

### 7.1 `initialize_role_run`

- 校验 thread/user、CareerIntent/SearchScope 和预算。
- 加载已有 role snapshot、source capability 和历史 query fingerprint。
- SearchScope 在 run 中不可由节点扩大 hard scope；用户意图变化属于新 run 或 v0.6 回退。

### 7.2 `plan_role_queries`

输入：

- SearchScope；
- 当前覆盖缺口；
- 已完成/空结果/失败 query；
- source capability、授权状态与剩余预算。

输出 `RoleQueryPlan`。LLM 可建议岗位同义词、公司/地点组合和经验检索词；代码负责：

- 生成稳定 fingerprint；
- 去除重复 query；
- 检查 source/channel 支持；
- 限制 query/page 数；
- 禁止加入 SearchScope 外的 hard exclusion。

### 7.3 `collect_and_archive_sources`

- 通过 SourceAdapter/ToolRegistry 执行 pending query。
- 每个 HTTP/导入响应在 adapter 内先写 BlobStore 和 Artifact metadata，再返回 artifact ID。
- adapter 不得返回“仅解析结果而无 raw artifact”的 success。
- 空结果、登录页、验证码页、限流页和页面结构变化分别分类。
- 已知 query 列表可有界并行；每个 batch 独立幂等。

### 7.4 `extract_and_normalize_sources`

- 只读取已归档 Artifact。
- recruitment → `NormalizedJobPosting`。
- experience → `ExperienceEvidenceRecord`。
- HTML/JSON/text extractor 保存 DocumentExtraction 和 Fragment locator。
- LLM structured output 只看到授权 Fragment，输出严格 JSON。
- 未知字段显式保留，不能从标题或模型常识补齐。

### 7.5 `deduplicate_source_records`

招聘：

1. URL/content hash 去重；
2. normalized exact key 自动聚类；
3. fuzzy similarity 只产生 merge candidate；
4. 只有确定性证据满足 merge policy 才合并。

经验：

- URL/content hash 和规范化正文 hash 去重；
- 转载只保留一个独立统计单位，但保留全部 source refs。

`JobPostingCluster` 是统计单位，原始记录不删除。

### 7.6 `plan_official_verification`

- 为每个保留的第三方岗位 cluster 生成有界 `OfficialVerificationPlan`。
- 解析 canonical company、官网域名、招聘入口候选、允许域名、候选岗位和剩余预算。
- 优先使用候选岗位中的官网申请链接、公司已验证域名和已注册 ATS/site adapter。
- 大批量候选先去重再核验；所有第三方原始证据已经归档，未核验岗位保留
  `unverified`，不得静默丢弃。

### 7.7 `collect_and_archive_official_sources`

- 严格限制在 verification plan 的域名白名单、最大深度、最大页数、超时和速率内。
- 依次尝试 sitemap/招聘入口、JSON-LD `JobPosting`、已注册 ATS/site adapter、
  静态 HTML/DOM 提取；必要时才让 LLM 从已归档 Fragment 输出 strict JSON。
- 任意官方搜索页、详情页和接口响应仍遵守 raw-before-parse。
- 网页文本是非可信数据，不能修改工具权限、域名、预算或系统指令。
- 无法安全解析时返回 `adapter_required`，不生成猜测字段。

### 7.8 `link_official_job_records`

- 为第三方 cluster 和 0..N 个官网候选生成 `JobIdentityLink`。
- 匹配信号包括 canonical company、标题、地点、招聘周期、job/application ID、
  URL 和职责/资格内容签名。
- 仅公司、标题和地点相似只能生成 candidate 或 `identity_ambiguous`，不能确认。
- 官网未找到分为 `official_not_found`、`official_unavailable` 和
  `source_changed`，不自动否定第三方岗位。

### 7.9 `resolve_job_field_conflicts`

- 以 predicate 为单位比较第三方与官网 Claim。
- employer official 对 active/application/deadline/responsibility/qualification/location
  为 primary；第三方独有字段可保留为 `third_party_only`。
- 每次选择生成 `FieldResolution`，保存 chosen claim、冲突 claims、authority、
  freshness、identity link 和机器可读 reason。
- 不允许整条官网记录覆盖第三方记录；历史 Claim 和 resolution 均保留。

### 7.10 `extract_and_validate_role_claims`

- recruitment Fragment 提取岗位存在、资格、职责、能力、加分项、地点、申请和截止信息。
- experience Fragment 提取 written/interview/project/tech-stack signal。
- ClaimValidator 增加 predicate × source channel/authority 校验。
- community Claim 不允许写 `role.active`、`qualification.hard`、`application.deadline`。
- 规范化能力映射 Capability Ontology，unknown mapping 保留 raw label。

### 7.11 `project_job_instance_profiles`

- 每个去重 cluster 生成或复用一个 job instance snapshot。
- FieldResolution/authority policy 决定 confirmed 值，冲突值保留。
- profile 保存全部 source refs、freshness、status 和 company-specific items。
- 过期/关闭岗位不删除，只更新为新 snapshot。

### 7.12 `aggregate_role_family_profile`

聚合输入：

- 去重后的 job instance profiles；
- 去重且明确 scope 的 experience signals；
- 当前 SearchScope/time window。

确定性代码计算：

- sample size/denominator；
- distinct company/location；
- requirement prevalence；
- company coverage；
- signal post count/frequency；
- freshness and conflict counts。

LLM 只能生成带引用解释，不修改统计数据。样本不足时不输出 common/universal。

### 7.13 `assess_role_coverage`

维度：

- recruitment field completeness；
- active/recent job count；
- distinct company/location；
- authority coverage；
- temporal freshness；
- experience signal coverage；
- source/query diversity；
- unresolved conflicts。
- official verification coverage 与 identity ambiguity。

输出 `RoleCoverageAssessment` 和建议动作。该评估描述“当前证据能否诚实刻画这个岗位方向”，不表示岗位适合候选人。

### 7.14 `route_role_next_action`

两段式路由：

1. evaluator 建议枚举动作；
2. deterministic policy 校验预算、cursor、query history、source capability、授权和覆盖缺口。

优先级：

1. fatal/storage/checkpoint error → `fail`；
2. 硬预算耗尽 → `finalize_with_unknowns`；
3. 必需 source 等待正常登录 → `await_user_auth`；
4. 已有候选岗位但官网覆盖不足 → `verify_official`；
5. 当前 query 可继续分页 → `search_more`；
6. relevance/recall 缺口可通过同义词改善 → `change_query`；
7. authority/channel/source failure 缺口 → `change_source`；
8. 达到覆盖标准 → `complete`；
9. 边际价值低或无可用来源 → `finalize_with_unknowns`。

### 7.15 授权节点

`plan_source_auth` 生成 `authorize_source` request：

- source ID；
- 正常登录入口；
- 本地导入说明；
- 期望 credential ref；
- allowed actions。

`interrupt_for_source_auth` 调用 LangGraph `interrupt()`。用户在 Graph 外完成登录和导入，
resume 只传 `credential_ref`。`validate_source_authorization` 只验证 ref 存在、source 匹配和权限，
不得读取秘密值到 State。

### 7.16 `finalize_role_profiles`

- 保存完成状态、profile refs、coverage、source receipts 和 remaining gaps。
- 报告明确 sample size、denominator、freshness 和 completion reason。
- `completed_with_unknowns` 是合法结果。

## 8. SourceAdapter

```python
class RecruitmentDiscoveryAdapter(Protocol):
    source_id: str
    capabilities: SourceCapabilities

    def collect(
        self,
        query: SourceQuery,
        credential_ref: str | None = None,
    ) -> SourceCollectionBatch: ...


class ExperienceSourceAdapter(Protocol):
    source_id: str
    capabilities: SourceCapabilities

    def collect(
        self,
        query: SourceQuery,
        credential_ref: str | None = None,
    ) -> SourceCollectionBatch: ...


class OfficialCareerAdapter(Protocol):
    source_id: str
    capabilities: SourceCapabilities

    def verify(
        self,
        plan: OfficialVerificationPlan,
        credential_ref: str | None = None,
    ) -> SourceCollectionBatch: ...
```

成功 batch 中每个 document 必须已有 `raw_artifact_id`。adapter 可内部使用 HTTP client/curl_cffi
等正常请求方式，但认证素材只能从本地 credential service 获取，不能进入返回值。

FixtureAdapter 读取已保存 fixture 并走相同归档接口，不能绕过 Artifact。

未知官网只允许生成声明式 `OfficialSiteAdapterSpec` 候选：

```text
allowed_domains
entry_url_patterns
document_kind_rules
selectors_or_jsonpaths
pagination_rules
stop_conditions
```

Spec 必须先通过 schema、域名白名单、离线 fixture replay、契约测试和人工批准。
运行时不得生成并执行 Python/JavaScript 采集代码。

## 9. 原始归档与 SourceRun

写入顺序：

```text
query fingerprint
  → request attempt
  → raw bytes received
  → content hash
  → immutable BlobStore
  → EvidenceArtifact + provenance
  → SourceDocument receipt
  → parser/normalizer
  → JobIdentityLink / FieldResolution
```

若 Blob/Artifact 写入失败，该 document 不得进入解析。SourceRunReceipt 记录：

- source/channel/adapter version；
- query IDs 和时间；
- received/archived/normalized/deduplicated counts；
- auth/rate-limit/source-change warnings；
- artifact IDs 和公开 URL 摘要；
- completion status。

不记录 Cookie、请求头正文或 cURL。

本地 run 建议保留可重放交接产物：

```text
data/runs/<run_id>/sources/
  search_scope.json
  user_needs.md
  query_history.jsonl
  source_receipts.jsonl
  source_index.jsonl
  jobs_normalized.jsonl
  official_verifications.jsonl
  job_identity_links.jsonl
  field_resolutions.jsonl
  experience_normalized.jsonl
  role_profile_report.md
```

raw bytes 仍由统一 Evidence Store 管理。`user_needs.md` 是 CareerIntent/SearchScope 的可读投影，
不是新的事实源。

## 10. 招聘归一化和 hard scope

`NormalizedJobPosting` 采用 source collection contract 的 canonical schema。

hard scope 例子：

- recruitment type；
- graduation year；
- 明确城市限制；
- 用户明确指定的公司/行业硬约束。

能力、学校背景、薪资偏好和“可能不喜欢”不在 v0.5 作为自动排除理由。每个排除记录保留：

```text
status=excluded_hard_scope
exclusion_code
exclusion_evidence_fragment_ids
```

相关但信息不全的记录使用 `deferred`，留给后续补证或 v0.6 用户决策。

## 11. 来源权威与冲突

字段级 authority matrix：

| 字段 | employer official | recruitment platform | community experience |
| --- | --- | --- | --- |
| 岗位存在/申请 URL/截止时间 | primary | allowed | forbidden |
| 地点/职责/资格/要求 | primary | allowed | signal only |
| 平台展示薪资 | n/a/allowed | allowed with salary_source | anecdotal signal |
| 笔试/面试/项目偏好 | process description | limited | allowed |
| 工作氛围/加班/主观体验 | limited | limited | anecdotal only |

`primary/allowed` 不代表内容永远正确，仍需 freshness 和 conflict。community 的
`signal only` 不得升级为 confirmed hard fact。

第三方与官网冲突不是 record overwrite，而是：

```text
third_party Claim + official Claim
  → confirmed JobIdentityLink
  → FieldResolution(predicate)
  → resolved view
```

官网没有薪资时，第三方展示薪资可保留并明确 `salary_source=third_party_only`。
官网找不到岗位时，除非有明确关闭/过期证据，否则只标记核验状态，不声明岗位虚假。

## 12. 去重与岗位族聚合

Job exact key 建议：

```text
normalized_company
+ normalized_role_title
+ normalized_location
+ recruitment_type
+ graduation_year
+ canonical_application_id_or_content_signature
```

若缺少最后一个身份区分量，相同公司/标题/地点只能成为 merge candidate，因为同一公司可能同时发布多个同名岗位。

岗位族 requirement 统计：

```text
prevalence = supporting_unique_job_instances / eligible_job_instances
company_coverage = supporting_distinct_companies / eligible_distinct_companies
```

默认 band：

- `common`：eligible sample 至少 3 个岗位/2 家公司，prevalence ≥ 0.60，
  且至少 2 个岗位/2 家公司支持；
- `frequent`：0.30 ≤ prevalence < 0.60；
- `observed`：prevalence < 0.30；
- `insufficient_sample`：总样本或公司数低于基础门槛。

阈值进入配置和 snapshot provenance。明确“必须”的 hard qualification 由原文语义决定，不由 prevalence 决定。

Experience signal：

- 1 个独立帖子：`observed_signal`；
- 至少 2 个独立帖子才能展示频率；
- scope 不同的 signal 分开统计；
- 旧帖子可保留为 historical，不与当前周期等权。

## 13. 时效策略

- Job instance 保存 `published_at`、`retrieved_at`、`application_deadline` 和 `source_status`。
- 有明确截止时间时以截止时间判断 expired；无截止时间时只标记 freshness，不声称仍开放。
- Role family snapshot 保存 collection window 和 `valid_as_of`。
- 经验帖默认按 configurable 24 个月窗口统计，窗口外保留为 historical。
- 新旧来源冲突时不得只按置信度覆盖，必须展示时间与 authority。

## 14. Checkpoint 与幂等

- 复用官方 SQLite saver，role graph 使用独立 thread。
- query fingerprint 防止同一 query 无意义重跑。
- source batch key 包含 source/query/cursor/adapter version。
- artifact 继续按 owner + content hash 去重。
- normalized record、cluster、Claim 和 snapshot 使用 canonical hash。
- 中断前不执行非幂等写入；resume 后授权校验可重放。
- Evidence Store 与 checkpoint DB 分离。

## 15. 错误与安全回退

新增错误：

```text
authentication_required
credential_invalid
rate_limited
source_changed
robots_disallowed
official_not_found
official_unavailable
identity_ambiguous
adapter_required
policy_blocked
network_timeout
parse_error
normalization_error
authority_violation
```

回退：

- 单页解析失败：保留 raw Artifact 和错误，继续其他页；
- source changed：停止该 adapter，尝试 fixture/其他 source 或 unknown；
- official not found/unavailable：保留第三方记录与核验状态，继续其他来源或 unknown；
- identity ambiguous：不建立 confirmed link，必要时请求用户确认；
- adapter required：保存 raw 和站点指纹，进入开发期 adapter backlog；
- auth required：interrupt；用户跳过后不得循环请求；
- rate limit：遵守 retry-after/预算，不激进重试；
- authority violation：拒绝 Claim；
- 所有 source 不可用：以 unknown 完成，不生成无证据画像。

## 16. 测试与 Eval

### 单元测试

- source/role schema、fingerprint、reducer；
- normalizer、hard scope、authority validator；
- exact/fuzzy dedup；
- prevalence/frequency/denominator；
- query/route/budget policy；
- auth request/response redaction。

### 集成测试

- fixture raw→normalized→Claim→job/family profile；
- recruitment discovery/official verification/experience channel 分离；
- pagination/search_more、change_query、change_source、verify_official；
- auth interrupt/resume；
- checkpoint 跨实例恢复；
- duplicate batch 幂等；
- source changed/rate limit/empty result/parse failure；
- v0.1-v0.4 回归。

### Live smoke

- `boss_jobs`：用户正常登录后显式启用一次小范围查询，保存 raw 和 SourceRunReceipt。
- `official_careers`：从一个 BOSS 候选岗位出发，在企业官网完成一次身份链接和字段核验。
- `nowcoder_experience`：用户正常登录并导入本地凭据后执行小范围查询。
- live smoke 不在默认 CI，不提交 credential 或批量 raw 内容。

## 17. 迁移策略

- v0.3 `RoleProfile` 继续可读取，v0.5 以新 schema version 和 projector 演进。
- v0.4 CandidateProfile Graph 不修改。
- 新 Graph 位于 `workflows/role_profile/`，先独立验收。
- v1.0 Parent Graph 再串联 candidate/role；v0.6 先实现两画像比较。
- 不把 v0.5 web collection 称为 v0.8 Hybrid RAG。

## 18. 风险

- 站点结构快速变化：adapter/version/source_changed/fixture contract 测试。
- 通用官网解析覆盖不足：结构化数据优先、已注册 adapter、明确 adapter_required；
  不以 LLM 动态代码生成掩盖覆盖缺口。
- 开源项目供应链与许可：固定 commit、隔离运行、准入报告和第三方代码不直接入库。
- 网页 Prompt Injection：网页只作为数据，Tool 权限/域名/预算由 harness 和确定性 policy 控制。
- 登录与合规：用户正常登录、秘密隔离、限速、可跳过来源。
- 重复岗位扭曲统计：cluster 分母与 source refs 分离。
- 社区传闻污染资格：predicate authority validator。
- 岗位族过度概括：样本、公司数、prevalence band 和 insufficient_sample。
- 动态搜索无限循环：query history、边际价值和硬预算。
