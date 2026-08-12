# ADR-0017：对齐真实社区协议并使用受控公司别名

状态：Accepted / Implemented（content-level L2 partial）
日期：2026-08-12

## Context

WP3.1.1 的 fake Sidecar 使用了真实 MediaCrawler 固定版本不存在的路径和 JSONL 枚举能力，因此离线
通过不能证明小红书 live 可运行。牛客旧 Raw 能被当前 parser 正确读取，但搜索结果只有公司联想卡，
说明 `0 candidate` 是低召回而非解析失败。JD 中法定公司名和社区常用品牌名不同会进一步降低召回。

## Decision

Bridge 直接对齐固定 MediaCrawler 的 localhost `/api` HTTP 协议，并让 fake upstream 使用同一 wire
shape。MediaCrawler 安装在主仓库外，固定 commit，显式接受非商业学习许可证，只启用小红书
search/detail，关闭评论、creator、代理和发布能力。

`company_key` 保持招聘证据身份；版本化 allowlist 提供 `company_search_term` 和
`verified_company_aliases`。别名只负责 discovery，详情 scope 必须重新确认。牛客每轮保存确定性
diagnostic，不能把公司卡或空结果称为 parser changed。DeepSeek V4 分类固定为非思考 Tool Calling，
模型不参与查询放宽、别名确认或停止决策。

## Consequences

- 离线测试能提前发现真实上游协议漂移，而不是到 live 才暴露。
- 品牌别名提高召回，但通过 allowlist、policy version 和详情 scope 阻止公司串线。
- 同日 append 输出需要按本轮关键词/post ID 过滤，Bridge 代码略有增加。
- 登录、验证码、风控、空结果和解析漂移得到不同的可审计状态。

## Rejected alternatives

- 继续维护测试专用 Sidecar API：无法证明真实兼容。
- 让 LLM生成公司别名：不可审计，可能合并错误主体。
- 将搜索卡片作为评价证据：违反 detail gate。
- 修改或 vendoring MediaCrawler：扩大许可证和维护责任。
