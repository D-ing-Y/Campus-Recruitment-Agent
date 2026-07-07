# Development Workflow

## 标准流程

```text
需求确认
  -> 写版本需求文档
  -> 写 RFC 或 ADR
  -> 拆任务
  -> VSCode 实现
  -> 单元测试
  -> 集成测试
  -> eval 验证
  -> 文档更新
  -> 提交代码
```

## 角色分工

Mac 端 Codex：

- 维护项目设计文档。
- 审查需求、架构和模块边界。
- 生成 RFC、ADR、contract 和验收标准。
- 做代码 review 和重构建议。

VSCode Codex：

- 根据文档实现代码。
- 编写测试。
- 运行验证命令。
- 修复实现问题。

## 每个版本必须产出

- `docs/03_requirements/<version>.md`
- 必要时增加 `docs/04_rfc/<number>-<topic>.md`
- 必要时增加 `docs/05_adr/<number>-<decision>.md`
- 相关 contract 更新
- 测试或 eval
- `reports/<version>-report.md`

