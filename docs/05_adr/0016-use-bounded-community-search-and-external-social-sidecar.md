# ADR-0016：使用确定性有界社区检索与外部 Social Sidecar

状态：Accepted / Implemented / Offline Verified
日期：2026-08-11

## Context

WP3.1 的社区链路一次性生成两条牛客精确查询。真实运行得到 0 个合格详情候选时，系统无法确认是
样本不存在、查询词过窄、来源覆盖不足，还是登录/风控阻断。把放宽策略交给 LLM 会降低可复现性，
并可能扩大 company-role scope。小红书适合补充工作体验，但其登录态与传输参数不应进入 Graph。

## Decision

采用每用途、每来源最多三轮的确定性状态机：完整岗位名、岗位族展示名、公司。达到两篇独立合格
详情即停止；主来源不足或阻断后切换备用来源；全局预算始终优先。查询只发现候选，详情正文负责
scope 验证。

面经来源顺序为牛客到小红书，工作体验为小红书到牛客。MediaCrawler 作为非必装、localhost、
固定版本的外部 Sidecar；主仓库只实现只读 MCP Bridge 和 adapter mapping，不复制其源码。Cookie、
xsec_token 等只存在 Sidecar，本应用保存 opaque ref。首版仅准入小红书，其他上游平台保持
`available_upstream_not_admitted`。

## Consequences

- 最坏每组 12 次搜索，但具有三轮、来源数和全局预算硬上限。
- 空结果能够形成逐轮审计证据，区分低召回与真实样本不足。
- 外部 Sidecar 不存在、登录失效、验证码或风控时结果保持 partial/blocked。
- 增加了本地运行依赖和非商业许可证确认，但没有把第三方采集代码或敏感会话写入主仓库。

## Rejected alternatives

- 由 LLM 自由改写查询和决定循环：不可复现且可能越过 SearchScope。
- 将搜索摘要直接当证据：违反 raw-before-parse 与 detail gate。
- 把 MediaCrawler vendoring 到主仓库：扩大许可证、升级和安全责任。
- 自动启用 MediaCrawler 的全部平台：缺少逐平台 adapter、fixture、授权与 live smoke。
