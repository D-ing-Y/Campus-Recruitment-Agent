# ADR-0018：使用 Brave Search 与 Crawl4AI 领域 Tool

状态：Accepted
日期：2026-08-12

## Context

牛客禁止抓取 search 路径，而公开详情可由外部搜索发现。Firecrawl 已证明该链路可行，但其 search/
scrape credits 与供应商语义不再必要。Crawl4AI 能以本地 Python library 渲染详情；MediaCrawler 已有
localhost REST API，自定义 MCP 只增加重复维护。

## Decision

1. Brave Search 是 `nowcoder_experience` 的唯一默认 discovery provider，代码强制 `site:nowcoder.com`。
2. Crawl4AI 0.9.2 作为 `community` optional extra，由批量领域 Tool 直接调用；不启 Docker、MCP 或
   LLM extraction。
3. MediaCrawler 由领域 Tool 直接调用现有 localhost REST Client；删除社媒专用 MCP Server。
4. Brave/Crawl4AI/MediaCrawler 原始结果全部遵守 raw-before-parse；search snippet 只做 discovery。
5. 社区检索改为确定性 guard 下的有限 LLM 决策；来源预算、scope、去重、停止硬下限仍由代码控制。
6. 每个用途至少 3 个独立内容簇；固定三级级联保留为 Eval baseline。

## Consequences

- 详情抓取无按页 credits，来源职责更清晰，业务链路少一层 MCP。
- 新增 Playwright/Chromium optional runtime、Brave credential 和牛客 DOM selector 维护责任。
- 搜索质量仍受 Brave index 影响；额度耗尽、robots、DOM 漂移和风控必须显式保留。
- MediaCrawler 非商业学习许可证继续限制部署用途。

## Rejected alternatives

- Firecrawl 全链路：重复付费且形成 provider-specific extraction。
- SearXNG 默认：需要自托管并承担上游封禁。
- 整页 Markdown 直接交给 LLM：无法证明主帖唯一性，并放大 prompt injection/context 风险。
- 删除全部 SourceAdapter/MCP 基础设施：超出社区链路范围并破坏招聘、官网和平台扩展能力。

