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
- 第三方参考代码固定 commit，下载到 Git 忽略目录；未通过 license/security/smoke 门禁不得成为运行时依赖。
- 上游 CLI/MCP/爬虫运行在受限 adapter 边界，只允许批准域名、只读动作和有界请求。
- 网页 HTML/text 一律视为非可信数据，不能修改系统提示、Tool 权限、域名白名单或预算。
- LLM 不得在 live run 中生成并执行 Python、JavaScript 或 shell 爬虫；声明式 adapter spec
  必须离线 replay、测试和人工批准。

## v0.7.1 Model Provider

- Model Provider 元数据和 current 状态保存到 SQLite；API key 不进入 Provider JSON。
- API key 只通过隐藏 prompt 或 stdin 进入本地 SecretStore，并以 `credential_ref` 传递。
- SecretStore 目录权限必须为 `0700`、文件为 `0600`；写入必须原子覆盖。
- 该文件权限是本机账户边界，不等同于加密或 macOS Keychain；不得把 credential root 同步到共享盘、
  云盘或 Git。后续若引入系统 Keychain，只替换 SecretStore adapter，不改变 Provider contract。
- 不支持命令行 `--api-key <value>`，避免 shell history 和 process list 泄露。
- 不自动加载项目 `.env`；显式环境变量仅作为自动化/兼容覆盖，不是普通用户配置事实源。
- `doctor/model list/show/test`、异常、Run artifacts、LLM receipts 和缓存不得包含 key。
- Model 健康检查不得携带 Candidate/Role/Feedback 等业务材料。
