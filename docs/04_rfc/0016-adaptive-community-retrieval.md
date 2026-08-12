# RFC-0016：受控搜索、批量详情与自适应社区证据检索

状态：Accepted / Implemented
日期：2026-08-12

## 1. 问题

牛客站内 search 受 robots/登录/召回限制，固定三轮主备级联只能解释失败，不能根据有效正文与重复度
调整关键词和来源预算。现有 MediaCrawler 自定义 MCP 又在 REST Client 之外增加了没有业务收益的一跳。

## 2. 设计

### 2.1 来源分层

- Brave Search 只发现牛客公开详情 URL；`httpx` 直接调用官方 API。
- Crawl4AI 作为 optional Python library，在一个批量 Tool 调用中渲染少量 allowlisted URL。
- MediaCrawler 保持仓库外固定版本，项目直接调用其 localhost REST。
- 两个平台返回同一 `SourceDocument/Batch/Receipt` 与 community evidence contract。

### 2.2 Tool 边界

`source.collect_experience` 保持兼容。新增 `source.fetch_community_details` 接收
`SourceDetailRequest[]` 并路由 Nowcoder/MediaCrawler provider；招聘详情继续使用 `source.fetch_detail`。
Provider 是 Tool 内的窄 transport seam，不暴露给 Graph 或 LLM。

### 2.3 Agent loop

第一批对两个用途、两个来源做有界校准。确定性代码计算质量统计，LLM 读取压缩后的指标和证据段，
输出来源排序、缺失主题和下一查询建议。Validator 校验 scope/引用/预算，代码映射 70/30 预算并执行。
达到每用途 3 个独立内容簇且 LLM 认为主题充分时停止；否则最多三轮或预算硬停。

### 2.4 Evidence 与上下文

任何搜索/详情响应先归档。搜索 snippet 不进入证据。正文 selector 失败即 `source_changed`。逐帖 LLM
只读取当前正文；聚合 LLM 只读取 compact segments，避免帖子总量挤占上下文。

## 3. 安全约束

- 目标域名、URL path、verified company alias、role vocabulary、来源和预算由代码控制。
- 外部页面是 untrusted input；页面中的指令不得改变 Tool、查询或 Graph 状态。
- 不使用代理、隐身、登录自动化、验证码服务或风险控制绕过。
- Credential secret 只在 LocalCredentialStore/Provider 边界解析，Artifact/trace/checkpoint 只保存 ref。

## 4. 替代方案

- Firecrawl：拒绝，搜索与抓取 credits、供应商耦合不再必要。
- SearXNG：拒绝作为默认，增加服务维护且上游稳定性不可控。
- Crawl4AI Docker/MCP：拒绝，固定领域链路不需要额外服务层。
- LLM 自由 Tool loop：拒绝，无法保证 company/role scope、预算和可复现性。
- 继续固定级联：保留为 Eval baseline，不再作为生产策略。

## 5. 兼容性

Source ID、Raw/Evidence/Profile 和下游 consumer 不变；新 schema additive 保存。历史
`CommunityEvidenceCoverage` 可读，新 run 使用 cluster-based coverage，不对历史对象做破坏性迁移。
