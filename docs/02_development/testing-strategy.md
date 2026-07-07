# Testing Strategy

## 测试分层

- 单元测试：测试 schema、planner、tool registry、verifier 等小模块。
- 集成测试：测试一个完整 workflow 是否能跑通。
- Eval 测试：评估 Agent 输出质量、schema 合规率、证据追溯率和推荐覆盖率。

## v0.1 测试目标

- `AgentState` 可以正常初始化和更新。
- mock tool 可以被 registry 调用。
- graph 可以从用户目标运行到报告输出。
- trace log 中包含关键节点。

