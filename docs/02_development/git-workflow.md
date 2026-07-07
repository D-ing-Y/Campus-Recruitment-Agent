# Git Workflow

## 分支策略

采用轻量 trunk-based workflow：

```text
main
  ↑
feature/v0.1-mini-runtime
feature/v0.2-llm-provider
feature/v0.3-tool-registry
```

## 提交信息

使用 Conventional Commits：

```text
docs: add mini runtime requirements
feat: implement langgraph runtime skeleton
test: add runtime state tests
refactor: split planner and executor
chore: update project structure
```

## 提交前检查

- 文档是否同步。
- 测试是否通过。
- 是否包含密钥、cookie、真实个人数据。
- 是否产生无关大文件。

