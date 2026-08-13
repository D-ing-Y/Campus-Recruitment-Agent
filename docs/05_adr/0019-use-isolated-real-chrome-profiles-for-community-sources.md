# ADR-0019：社区来源使用隔离的真实 Chrome Profile

状态：Accepted
日期：2026-08-13

## Context

Brave discovery 已真实通过，但匿名 Crawl4AI 无法访问牛客详情；MediaCrawler 需要长期小红书登录态。
两个来源的登录生命周期、平台风险和运行依赖不同，且牛客同一 adapter 的 search/detail 使用不同授权。

## Decision

采用项目统一管理、平台物理隔离的两个真实 Chrome Profile。牛客使用 9223 并由 Crawl4AI 通过外部
CDP 读取；小红书使用 9222 并由 MediaCrawler 通过 existing-CDP 读取。Profile 只通过
`BrowserProfileRef` 暴露。项目 CLI 管理 Profile/Chrome 生命周期，但所有登录和验证由用户人工完成。
MediaCrawler Sidecar 继续外部启动，Brave 继续使用独立 API Key。

## Consequences

- 登录态可跨运行复用，同时把平台锁、Cookie 和失效范围隔离。
- 增加本地 Chrome 进程、固定端口和 Profile 安全管理责任。
- 运行前必须保持两个 Chrome 会话与 MediaCrawler REST 可用。
- “CDP 可达”和“认证已验证”必须分开观测；只有真实 source operation 能确认后者。

## Rejected alternatives

- 两个平台共享日常或单一项目 Profile：最小权限和故障隔离不足。
- 自动登录或验证码处理：违反项目合规边界。
- 远程 CDP：会扩大登录材料暴露面。
- 恢复社区 MCP：不增加业务能力，只增加维护层。
