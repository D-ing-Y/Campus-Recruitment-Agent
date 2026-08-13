# RFC-0017：隔离的认证社区浏览器会话

状态：Accepted / Ready for Implementation
日期：2026-08-13

## 1. 问题

WP3.2 已证明 Brave 能发现牛客详情 URL，但匿名 Crawl4AI 会得到登录墙。MediaCrawler REST 可启动，
却只能把 Sidecar idle 当作外部会话可达，不能证明小红书已登录。单一 source-level
`authorization_mode` 也无法表达牛客 search 使用 API Key、detail 使用浏览器登录态。

## 2. 设计

### 2.1 深模块与隔离

新增 `BrowserProfileManager` 作为唯一 Profile interface，隐藏目录、端口、Chrome 进程和 CDP 探测。
调用方只持有 `BrowserProfileRef`。牛客和小红书分别使用 9223/9222，Profile 与进程物理隔离。

### 2.2 真实 Chrome 与人工登录

CLI 启动系统真实 Chrome 和官方入口页。用户人工完成登录或验证后保持浏览器运行；Crawl4AI 与
MediaCrawler 只连接 loopback CDP。自动化不得执行登录步骤、隐藏浏览器身份或处理验证码。

### 2.3 按操作授权

SourceCapabilities additive 增加 operation authorization。Graph 依据当前操作生成
`SourceAuthRequirement`：Brave discovery 校验 `CredentialRef`，牛客详情校验 `BrowserProfileRef`，
小红书 search/detail 同时校验 Profile/CDP 与外部 Sidecar。旧 source-level 字段继续读取。

### 2.4 生命周期与恢复

Profile init/open/status/stop 由 CLI 显式触发。Graph 遇到失效会话时 interrupt；同一 thread 在用户
重新登录后 resume。Profile 内容不进入 checkpoint，重复 resume 复用既有幂等键和 Raw Artifact。

## 3. 安全约束

- Profile 只能位于受管 Git 忽略目录，且不得是符号链接。
- CDP 只监听 127.0.0.1；不接受远程 endpoint 或调用方自定义端口。
- stop 只操作项目拥有且身份完全匹配的 PID，不扫描或终止未知 Chrome。
- doctor、ToolResult、异常和报告不得输出路径、PID 命令行、Cookie、token 或 CDP WebSocket。
- 外部页面仍是不可信输入，不能改变域名、预算、Tool 或 Graph 状态。

## 4. 替代方案

- 单一共享 Profile：拒绝，存在锁、端口、Cookie 串扰和共同失效风险。
- Crawl4AI ManagedBrowser 自行接管 Profile：拒绝，其生命周期会清理端口/锁，不适合人工真实 Chrome。
- 只注入 Cookie：拒绝为默认，无法稳定覆盖 localStorage 和平台会话变化。
- 项目启动 MediaCrawler Sidecar：拒绝，保持外部固定版本 REST 边界更小。

## 5. 兼容性

Source ID、CredentialRef、Raw/Evidence/Profile 和下游投影不变。新增字段 additive；历史 checkpoint 缺少
operation requirement 时由旧 `pending_auth_source_id` 和当前 node 推导，不做破坏性存储迁移。
