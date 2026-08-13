# Community Retrieval Contract

状态：Accepted / Implemented
日期：2026-08-12

本契约是 `source-collection-contract.md` 与 `role-demand-reputation-contract.md` 的 WP3.2 additive
扩展。历史对象保持只读。

## 1. Tool contracts

```text
source.collect_experience
  input: run_id, SourceQuery, credential_ref?
  output: ToolResult[SourceBatch + SourceRunReceipt]

source.fetch_community_details
  input: run_id, requests: SourceDetailRequest[1..10], max_concurrency=2,
         credential_ref?, browser_profile_ref?
  output: ToolResult[SourceBatch + SourceRunReceipt]*
```

Nowcoder search provider 必须把用户关键词规范化后追加 `site:nowcoder.com`，并拒绝输入中的 URL、域名
operator 和控制字符。只允许 canonical detail path；request 与 final URL 都需校验。

## 2. Raw artifact payloads

Brave search Raw 保存 API JSON 与非敏感 request metadata。Crawl4AI detail Raw 保存 requested/final URL、
status、rendered HTML、cleaned HTML、raw/fit Markdown、metadata 和错误摘要。MediaCrawler Raw 沿用现有
JSON payload。Authorization/header/credential ref 解析结果不得进入 Raw。

## 3. New domain models

```text
CommunityContentCluster
  cluster_id, company_role_group_id, evidence_purpose
  representative_document_id, member_document_ids[]
  member_segment_ids[], methods[], similarity_receipt_ids[]

CommunitySourceEvaluation
  evaluation_id, run_id, evidence_purpose, source_id
  sampled_detail_count, relevant_detail_count, valid_body_count
  scope_hit_count, accepted_segment_count, duplicate_detail_count
  failed_detail_count, relevance/valid_body/scope_hit/duplicate/failure rates
  latency_ms, search_cost_units, reason_codes[]

CommunitySearchDecisionReceipt
  decision_id, run_id, evidence_purpose
  source_evaluation_ids[], cluster_ids[]
  ranked_source_ids[], budget_allocation, missing_topics[], proposed_keywords[]
  semantic_duplicate_segment_groups[], verdict, hard_floor_met
  provider, model, prompt/schema version, reason_codes[]
```

`CommunityEvidenceCoverage` 新 run 使用 `target_cluster_count=3`、`accepted_cluster_ids` 和
`independent_cluster_count`。`sufficient` 要求独立簇数量达到目标且已接受的 decision receipt 判定充分。

## 4. Deterministic validators

- exact duplicate：canonical URL、platform post ID 或 normalized body SHA-256 相同。
- near duplicate：中文/字母数字规范化正文的 5-char shingles Jaccard `>=0.85`。
- semantic duplicate：LLM 只能引用已存在 segment ID 并提出 cluster merge；无引用或跨 scope 拒绝。
- query suggestion：公司必须来自 group legal/verified aliases；角色必须来自 exact/family vocabulary；
  purpose term 来自固定 allowlist；最终域名和 source 由代码注入。
- source allocation：两个可用来源按 LLM 排名映射 70/30；一个可用来源为 100/0；LLM 数字不生效。

## 5. Failure mapping

```text
missing Brave key -> authentication_required
missing Crawl4AI/Chromium -> adapter_required
robots rejection -> robots_disallowed
HTTP 429 -> rate_limited
login/challenge/risk page -> risk_controlled or authentication_required
invalid/redirected URL -> policy_blocked
main-body selector mismatch -> source_changed
no valid result -> empty
```

失败 batch 不得创建成功 SourceDocument；已写 Raw 诊断可保留，但不得进入分类和投影。

## 6. Context and budget

逐帖正文单独分类，最大输入沿用现有 extractor 限制。聚合 decision payload 最多包含当前用途 12 个簇、
24 个 segment；每段 quote 最多 400 字符、limited summary 最多 200 字符。每次 provider attempt 按
`1 + retry_count` 计入 `RoleSearchCounter.llm_calls`，调用前必须验证 `max_llm_calls`。

## 7. WP3.2.1 Browser Profile amendment

```text
BrowserProfileRef
  profile_ref: local-browser-profile://<source_id>/<name>
  source_id: nowcoder_experience | xiaohongshu_experience

SourceAuthRequirement
  source_id
  operation: collect | fetch_detail
  mode: credential_ref | browser_profile_ref | external_sidecar | none
```

- 牛客 collect 解析现有 Brave CredentialRef；牛客 fetch_detail 必须另外收到牛客 BrowserProfileRef。
- 小红书 collect/fetch_detail 必须收到小红书 BrowserProfileRef，并验证 CDP 与 MediaCrawler health。
- `browser_profile_ref` 只能传递 opaque ref；ToolResult、Raw metadata、receipt、checkpoint 和 trace 不得
  保存其本地路径、端口、PID、Cookie、Storage State 或 CDP WebSocket。
- Crawl4AI 使用 Profile Manager 返回的 loopback CDP 连接。关闭 crawler 时只断开 Playwright/CDP，
  不关闭真实 Chrome。
- Profile/CDP 可达不等于认证成功；只有实际详情/search 成功才更新本地最近认证验证时间。
