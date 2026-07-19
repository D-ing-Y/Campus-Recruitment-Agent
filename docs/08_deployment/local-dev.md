# Local Development

个人开发版以 Mac 为主要交互和开发环境：

- VSCode 负责代码实现。
- Mac 端 Codex 负责设计、文档和审查。
- SQLite 和本地文件夹用于存储。
- `.env` 存放 API key，不进入 Git。
- v0.5 live source 的 Cookie/cURL 只导入 `data/cache/credentials/` 或等价本地秘密目录；
  Graph、业务代码和报告只使用 `credential_ref`，只有 credential service 在 adapter 边界读取秘密正文。
- 默认测试和开发命令保持离线；live smoke 必须通过显式配置单独启用。
