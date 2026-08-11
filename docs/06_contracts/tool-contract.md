# Tool Contract

工具层统一返回 `ToolResult`。

## v0.1 ToolResult

```json
{
  "tool_name": "mock_job_search",
  "status": "success",
  "records": [],
  "evidence_ids": [],
  "error": null,
  "metadata": {}
}
```

字段要求：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `tool_name` | string | 工具名称 |
| `status` | `"success"` 或 `"failed"` | 工具执行状态 |
| `records` | array | 工具返回的结构化记录 |
| `evidence_ids` | array | 关联证据 ID，v0.1 可为空 |
| `error` | string 或 null | 错误信息 |
| `metadata` | object | 调试或扩展信息 |

## v0.1 默认工具

```text
mock_job_search
```

工具输入：

```json
{
  "role_query": "AI Agent",
  "city": "成都",
  "graduation_year": "2027"
}
```

v0.1 工具必须通过 `ToolRegistry` 调用，不允许工作流节点直接调用具体工具函数。

## v0.3 Evidence-aware ToolResult

v0.1 `ToolResult` 保持兼容。v0.3 新工具应满足：

- 产生原始材料的工具先保存 Artifact，再返回 `evidence_ids`。
- `records` 只保存结构化摘要，不复制完整二进制或长文本。
- `metadata` 可包含 parser/version、deduplicated、content_hash、record_count 和 warning，不得包含密钥或 Cookie。
- 工具失败必须返回结构化错误类型和可重试性；不允许只返回“失败”。
- Agent 节点不得绕过 ToolRegistry 直接访问具体采集器或存储实现。

候选工具分组：

```text
evidence.ingest_file
evidence.extract_text
evidence.create_fragments
evidence.save_claims
evidence.load_fragments
profile.save_snapshot
profile.load_snapshot
```

## v0.4 Candidate Profile Tool

实现状态：v0.4 已实现；最终集成使用仓库内真实本地 Tool。

v0.4 最终集成必须使用真实本地实现；mock 只用于单元测试、错误注入和
deterministic eval baseline。外部 MCP/插件可以在未来实现同一契约，但不是完成条件。

### 通用 ToolResult 扩展

```json
{
  "tool_name": "candidate.ingest_material",
  "status": "success",
  "records": [],
  "evidence_ids": ["artifact-1"],
  "error": null,
  "metadata": {
    "error_type": null,
    "retryable": false,
    "needs_user_action": false,
    "idempotency_key": "sha256",
    "parser_name": "pdf_text",
    "parser_version": "v1"
  }
}
```

失败时 `metadata.error_type` 为：

```text
validation_error
unsupported_input
permission_denied
llm_output_error
tool_retryable_error
storage_error
checkpoint_error
budget_exhausted
idempotency_conflict
```

### 必须实现的 Tool

| Tool | 输入摘要 | 输出/副作用 |
| --- | --- | --- |
| `candidate.ingest_material` | owner、candidate、path/content type | 归档 Artifact，返回 ID 和去重状态 |
| `evidence.extract_pdf_text` | artifact ID | 保存带页边界的标准化文本与 parser metadata；只支持文本型 PDF |
| `evidence.extract_plain_text` | artifact ID | 保存 Markdown/TXT/README 标准化文本与行号映射 |
| `evidence.create_fragments` | artifact ID、parser version | 幂等创建 Fragment |
| `evidence.extract_candidate_claims` | subject、fragment IDs | 结构化提取、校验并保存 Claim |
| `evidence.archive_user_response` | request/response contract | 保存 response Artifact/Fragment/Claim |
| `profile.project_candidate` | candidate ID、active claim IDs | 创建或复用 Candidate ProfileSnapshot |
| `profile.load_candidate` | candidate ID 或 snapshot ID | 返回小型画像摘要和引用 |
| `profile.diff_candidate_versions` | 两个 snapshot ID | 返回确定性字段差异 |

### 调用边界

- Graph 节点通过 ToolRegistry 或显式 repository service 调用，不直接拼 SQL/路径。
- 摄取工具先归档 Artifact，解析失败也不得丢失原始材料登记。
- Claim 工具必须经过 ClaimValidator，不允许模型直接保存。
- Profile 工具只读取已持久化 active Claim。
- `evidence.archive_user_response` 必须校验 request/response/owner 并使用稳定幂等键。
- 文件型 resume 还必须校验路径处于 Graph 初始化时固定的 `allowed_path_roots`。
- ToolResult 不复制完整材料、完整回答或二进制。

### Checkpointer 边界

LangGraph checkpointer 是 runtime dependency，不伪装为业务 Tool：

- 在 Graph compile 时注入；
- 本地运行使用 SQLite 持久化实现；
- 测试可以使用内存实现；
- 通过 `thread_id` 恢复；
- 不被 Evidence Repository 或 ProfileProjector 当作事实来源。

## v0.5 Source 与 Role Profile Tool

实现状态：Implemented / Accepted；live adapter 默认关闭，opt-in smoke 与真实官网链接验收已完成。

### Source Tool

| Tool | 输入摘要 | 输出/副作用 |
| --- | --- | --- |
| `source.plan_role_queries` | SearchScope、coverage、history、budget | 版本化 RoleQueryPlan |
| `source.discover_jobs` | SourceQuery、credential ref | raw-first SourceCollectionBatch |
| `source.plan_official_verification` | JobPostingCluster、domain evidence、budget | OfficialVerificationPlan |
| `source.verify_official_career` | OfficialVerificationPlan、credential ref | raw-first SourceCollectionBatch |
| `source.collect_experience` | SourceQuery、credential ref | raw-first SourceCollectionBatch |
| `source.extract_document` | raw artifact、parser version | DocumentExtraction 与 Fragment refs |
| `source.normalize_job_posting` | recruitment fragment IDs | NormalizedJobPosting |
| `source.normalize_experience` | experience fragment IDs | ExperienceEvidenceRecord |
| `source.deduplicate_jobs` | normalized job IDs | JobPostingCluster |
| `source.link_job_identity` | cluster、official job IDs、evidence refs | JobIdentityLink |
| `source.resolve_job_fields` | confirmed identity link、Claim IDs | FieldResolution 列表 |
| `source.deduplicate_experience` | experience record IDs | 去重统计单位 |
| `source.import_credential` | source ID、本地 cURL 文件路径 | CredentialRef；不得返回秘密值 |
| `source.validate_credential_ref` | source ID、credential ref | 授权状态摘要 |

### Role Tool

| Tool | 输入摘要 | 输出/副作用 |
| --- | --- | --- |
| `evidence.extract_role_claims` | discovery/official/experience fragments | 经过 authority validator 的 Claim |
| `profile.project_job_instance` | cluster、active Claim IDs | 创建/复用具体岗位 snapshot |
| `profile.aggregate_role_family` | scope、job snapshots、experience signals | 确定性岗位族 snapshot |
| `profile.load_role` | snapshot/subject ID | 小型画像摘要与 refs |
| `profile.diff_role_versions` | 两个 snapshot ID | 确定性版本差异 |

### SourceAdapter 规则

- Graph 节点只选择 source/query，不直接实现站点请求。
- adapter 只有在 raw bytes 已成功进入 BlobStore/Artifact 后才能返回 document success。
- live adapter 默认关闭；fixture adapter 遵守相同 raw-before-parse 路径。
- source adapter 返回分页 cursor、auth、rate limit、source changed 和 retryable 状态。
- `zhaopin_jobs`、`official_careers` 与 `nowcoder_experience` 的站点细节封装在 adapter，
  不进入 Agent Runtime。
- 上游 CLI、MCP 或开源爬虫只作为 adapter 后端，不能直接写 Evidence Store、State 或 Profile。
- `official_careers` 严格遵守域名白名单、页面/深度预算和解析链。
- 未知官网只能输出声明式 `OfficialSiteAdapterSpec` candidate；runtime 不执行 LLM 生成代码。

### 新增错误类型

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

### Credential 边界

- Tool 输入只能包含 credential ref，不包含 Cookie/cURL/Authorization。
- import tool 从调用方授权的本地路径读取，并写入 Git 忽略的 credential store。
- ToolResult 只能返回 ref、source、类型、验证时间和错误摘要。
- trace、checkpoint、Artifact metadata 和 report 不得记录秘密正文。

## v0.7.1 WP3.1 Role Intelligence Tool

实现状态：Implemented；招聘 L1 live accepted，社区 L2 因无合格帖子详情样本保持 partial。

| Tool | 输入摘要 | 输出/副作用 |
| --- | --- | --- |
| `source.discover_job_detail_candidates` | recruitment search document IDs | 只保存 `JobDetailCandidate`，不投影画像 |
| `source.discover_community_post_candidates` | community search document IDs、query scope | 只保存 `CommunityPostCandidate` |
| `source.fetch_detail` | `SourceDetailRequest`、credential ref | 详情 Raw Artifact、SourceDocument、batch receipt |
| `role.build_company_role_groups` | accepted cluster IDs、SearchScope | 幂等 `CompanyRoleGroup` |
| `role.plan_community_search` | group IDs、source、预算 | 旧 WP3.1 首轮查询兼容计划 |
| `role.plan_next_community_attempt` | group、purpose、source priority、round | 单条确定性分层查询 |
| `role.assess_community_coverage` | attempt、已确认详情 IDs、预算 | coverage、下一轮/切源/停止动作 |
| `role.classify_community_documents` | post-detail SourceDocument IDs、scope hints | exact-quote Document、Segment、classification receipt |
| `role.build_official_escalation_receipts` | eligible clusters、用户指定岗位 | `not_required` 或条件升级回执/plan |
| `profile.project_role_intelligence` | eligible clusters、typed segments、receipts | Demand、Reputation 与 `RoleIntelligenceBundle` |

WP3.1 调用边界：

- search 页、card 和 snippet 的画像投影数固定为 0；
- `source.fetch_detail` 只调用声明 `supports_detail_fetch=true` 的 adapter，实际详情类型必须与
  request 一致；
- 社区模型输出不包含内部 ID 或自由 locator；quote 必须由应用在归档正文中唯一定位；
- `profile.project_role_intelligence` 分别校验 Demand 与 Reputation allowlist，不允许评价 ID
  进入 Matching，也不允许工作体验进入 assessment signal；

WP3.1.1 Social Media MCP Bridge 只读 allowlist：

```text
social.health
social.auth_status
social.search_posts
social.fetch_post_detail
```

Bridge 只接受 localhost MediaCrawler Sidecar，拒绝评论、创作者遍历、代理与发布参数。MCP 返回必须
先归档 Raw Artifact；主应用只接收 opaque candidate ref，不接收 Cookie 或 xsec_token。未固定 commit、
未确认非商业许可证或外部会话无效时返回明确失败，不降级为搜索摘要证据。
- ToolResult 与运行日志只返回 ID、计数、状态和脱敏错误，不复制完整 JD、帖子或 Prompt。

## v0.6 Profile Matching Service/Tool

实现状态：Implemented / Accepted。

matching 中的纯计算器可实现为 domain service；若由 Graph 调用并需要统一 trace，则注册为下列
Tool。无论实现形式如何，都必须遵守相同输入、输出和确定性规则。

| Tool/Service | 输入摘要 | 输出/副作用 |
| --- | --- | --- |
| `matching.load_input_set` | owner、candidate/intent/role snapshot IDs | 校验并创建/复用 MatchingInputSet |
| `matching.evaluate_qualifications` | input set、job snapshot | QualificationAssessment 列表 |
| `matching.align_requirements` | candidate/job snapshot、ontology version | RequirementAssessment 列表 |
| `matching.compute_coverage` | requirement assessments、weight policy | core/bonus CoverageBreakdown |
| `matching.evaluate_preferences` | intent/job snapshot | PreferenceAssessment 列表 |
| `matching.project_gap_assessment` | 确定性 item refs | 创建/复用 GapAssessment |
| `matching.build_comparison` | ordered assessment refs、ranking policy | 创建/复用 ComparisonSet |
| `matching.explain_comparison` | deterministic fact index | validated MatchExplanation 或模板 fallback |
| `decision.save_targets` | validated review response | 原子创建/复用 TargetDecision batch |
| `intent.revise` | previous intent、confirmed patch | 创建/复用 CareerIntent snapshot |
| `intent.assess_impact` | old/new intent、scope policy | IntentImpactAssessment |
| `matching.create_rebuild_directive` | comparison、reason、input refs | 创建/复用 RebuildDirective |

调用边界：

- evaluator 只读取 snapshot/repository，不访问招聘网站或本地凭据。
- 所有明确判定返回双方 Claim refs 和 policy version。
- LLM explanation Tool 不得拥有 assessment/comparison repository 的修改权限。
- target decision batch 必须全部校验后原子写入。
- intent service 不得创建 Candidate Claim；directive service 不得修改 Candidate/Role Profile。
- ToolResult 只返回 ID、计数、状态和错误摘要，不复制完整简历、JD 或用户 note。

新增错误类型：

```text
snapshot_not_found
snapshot_owner_mismatch
snapshot_schema_unsupported
snapshot_stale
comparison_stale
unsupported_qualification_operator
unmapped_capability
invalid_fact_reference
llm_fact_mutation
invalid_decision_target
search_scope_impact_conflict
```

## v0.7 Preparation 与 Feedback Tool/Service

| Tool/Service | 输入摘要 | 输出/副作用 |
| --- | --- | --- |
| `preparation.load_input_set` | selected decisions、snapshot refs、constraints | 校验并创建 PreparationInputSet |
| `preparation.derive_objectives` | gap/role/signal refs | PreparationObjective 列表 |
| `preparation.generate_activities` | objectives、constraints | validated activity candidates |
| `preparation.compute_priority` | activities、planning facts、policy | PriorityFactors |
| `preparation.build_package` | objectives/activities/factors/capacity | MinimumPreparationPackage |
| `preparation.schedule` | package、DAG、calendar constraints | deterministic sessions/deferred reasons |
| `preparation.save_plan` | validated plan object graph | transactional publish/reuse LearningPlan |
| `feedback.ingest` | owner、text/file/structured input | raw-first Artifact/Fragment/Event |
| `feedback.extract_observations` | archived fragment IDs | validated Observation candidates |
| `feedback.propose_diagnoses` | observation IDs、allowed scopes | Diagnosis candidates |
| `feedback.archive_claims` | confirmed attributions | feedback_signal Claims |
| `feedback.assess_impact` | accepted attributions/progress | deterministic impact |
| `feedback.create_directives` | impact、current refs | typed FeedbackDirective |
| `feedback.resolve_directive` | directive、new snapshot refs/receipt | validated resolution |
| `plan.save_progress` | plan/activity/event refs | idempotent PlanProgressEvent |

边界：

- plan services 不写 Candidate/Role/Intent/Comparison repository。
- feedback ingestion 必须 raw-first；raw write 失败时 extractor 不执行。
- LLM services 无 priority/schedule/profile/directive repository 写权限。
- progress service 不能创建 capability-level Claim。
- role family feedback 只能创建 aggregation candidate，不能直接写 family snapshot。
- directive resolution 验证后继版本、owner、subject 和 directive type。
- ToolResult 只返回 ID、count、status 和错误摘要，不复制完整反馈或用户文件。

新增错误类型：

```text
target_selection_required
preparation_input_stale
invalid_activity_reference
dependency_cycle
schedule_infeasible
unaddressable_blocker
feedback_raw_archive_failed
feedback_scope_invalid
feedback_authority_violation
feedback_causality_violation
feedback_attribution_unconfirmed
single_event_family_mutation_blocked
directive_resolution_invalid
```

## v0.7.1 WP1.3 Resume Evidence Tools

- PDF 提取由内部 Tool 使用 pypdf layout mode，只有质量门禁失败才调用 pdfplumber；两者失败返回
  `unsupported_input`，不创建半成品 Snapshot；
- Resume 模型边界只暴露 `ResumeExtractionBatch` Tool Schema，不含 personal_information、任意 JSON
  Pointer 或 Profile/Claim 字段；个人信息由本地代码提取并在调用 Provider 前脱敏；
- 模型只能引用当前 PDF 的 Fragment ID，应用根据 Pydantic 对象生成 canonical field pointers；
- 应用使用 NFKC/空白/隐形格式字符归一化生成字段级 span，无精确 span 的 Draft 拒绝进入审核；
- Candidate 初始 Claim Tool 接受 `resume_evidence_id`，把 confirmed 结构化视图与原 Fragment ID 一起
  送入抽取器；后续 conversation_response 上传仍按独立 Artifact 处理，不伪装成 ResumeEvidence。

## v0.7.1 WP1.3.2 Candidate Projection Tool

`profile.project_candidate` 的必填输入为 `candidate_id + resume_evidence_id`。Tool 先执行确定性 Claim
Resolution，再将 selected claims 交给 Projector，并在 metadata 返回安全 summary。禁止 Tool 内部把
`list_active_claims(candidate_id)` 直接作为最终投影集合。Feedback rebuild 必须复用同一入口或同一
resolution function，不得维护第二套选择规则。
