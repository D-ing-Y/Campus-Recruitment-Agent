# 第三方参考源码目录

该目录用于保存 v0.5 SourceAdapter 开发前审查的开源项目浅克隆。第三方源码只作为
本地参考和可行性验证材料，不直接进入本项目提交历史，也不能直接写入 Evidence Store、
Graph State 或 RoleProfile。

## 使用规则

1. 每个项目必须先登记到 `manifest.yaml`。
2. 记录 repository URL、固定 commit、license、用途、准入状态和 smoke 结果。
3. 本地克隆目录被 `.gitignore` 排除；不得移除上游 LICENSE 或版权声明。
4. `adopt` 只表示允许在本项目 SourceAdapter 后方复用或包装，不表示目标网站授权采集。
5. 未声明许可证、依赖绕过验证码/风控、泄露 Cookie 或无法满足只读边界的项目只能
   `reference_only` 或 `rejected`。
6. live 测试使用用户正常登录和最小查询，凭据与原始 cURL 只保存在 Git 忽略的本地目录。

## 准入状态

- `candidate`：等待静态或运行验证。
- `adopt`：通过当前版本的许可证、安全、契约和 smoke 门禁。
- `reference_only`：可以学习架构或解析方式，但不能直接成为运行时依赖。
- `blocked_by_auth`：静态检查通过，仍需用户正常登录后完成 live smoke。
- `rejected`：不满足许可证、安全、维护或合规边界。

具体结论见：

- `manifest.yaml`
- `docs/07_evaluation/v0.5-source-feasibility-report.md`
