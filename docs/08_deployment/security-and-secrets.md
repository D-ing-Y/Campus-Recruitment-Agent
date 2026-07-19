# Security and Secrets

## 原则

- `.env` 不进入 Git。
- API key、cookie、cURL、个人数据和批量/受限 live 招聘原文不进入 Git。
- 公开样例数据必须脱敏。
- 只有来源允许、最小化且记录 provenance 的 L2 快照才能作为可提交 fixture。
- 不绕过验证码或反爬验证。
- 高风险操作需要人工确认。

## v0.5 Live Source

- 用户只在真实 Chrome 中正常登录，系统不得自动填写账号、绕过验证码或伪造设备。
- Copy as cURL/Cookie 导入到 `data/cache/credentials/` 或等价 Git 忽略目录。
- Graph State、checkpoint、ToolResult、trace、Evidence metadata、SourceRunReceipt 和报告只保存 `credential_ref`。
- credential service 只在 SourceAdapter 调用边界解析秘密值。
- 日志和异常必须对 Cookie、Authorization、token、完整 headers 和 cURL 做 redact。
- live raw 招聘/社区内容默认只保存在本地，不批量提交或再分发。
- adapter 必须遵守限速、超时、robots 和来源服务条款；禁止时结构化返回 `robots_disallowed`。
- 用户可在授权 interrupt 中选择 `skip_source`，系统不得反复要求登录。
