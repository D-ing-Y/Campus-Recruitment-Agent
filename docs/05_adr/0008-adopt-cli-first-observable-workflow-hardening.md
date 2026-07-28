# ADR-0008: 采用 CLI-first 可观测纵向加固，不提前实现 Parent Graph

## 状态

Accepted / Ready for Implementation

## 日期

2026-07-28

## 背景

v0.3-v0.7 已实现多个独立业务子图，但正式 CLI 仍停留在旧 Mini Runtime。真实材料测试暴露出
Candidate Claim 可以通过通用引用校验、却无法被领域 Projector 消费的问题；现有日志和预填式
Eval 又不足以快速定位这种语义断层。

项目需要同时解决两件事：让用户通过 CLI 真实操作每一步，以及让开发者按契约、证据、模型、
Validator、投影、持久化、Graph、CLI 和 Eval 逐层定位。此时直接实现 v1.0 Parent Graph 会扩大
故障面，并可能让多个尚未闭合的子图被错误描述为端到端可用。

## 决策

### 1. v0.7.1 采用 CLI-first 横向基座

- 保留 `campus-agent` 作为唯一正式命令入口。
- 提供交互式引导、one-shot 命令和 `--json`。
- 先建立生产 RuntimeFactory、RunSession、事件、诊断产物、doctor 和 inspect。
- CLI 与测试调用同一 application service，不复制业务逻辑。

### 2. 采用九层纵向验收门禁

每个 Workflow 都必须依次验证契约、证据、模型、Validator、投影/策略、持久化、Graph、
CLI/可观测性和 Eval。上游未通过时，不允许以下游“有输出”作为成功证据。

### 3. 按依赖顺序修复

固定顺序为：

```text
CLI/Run 基座
→ Evidence + Candidate
→ CareerIntent
→ Role
→ Matching + Decision
→ PreparationConstraints + Plan
→ Feedback + Directive
→ CLI 串联验收
```

### 4. 相邻子图通过显式 handoff 连通

- 使用 typed Handoff/Directive 和 application service dispatcher。
- 每次 CLI 操作只消费一个明确 handoff，验证 successor refs 后原子 resolve。
- Session 只保存 current refs、pending handoffs 和导航状态。
- 不建立自动选择和循环执行全部子图的 LangGraph Parent Graph。

### 5. 真实节点必须与 trace 一致

名义节点如果没有独立输出、路由或失败边界，必须拆实或合并；不得固定记录 success 来模拟节点执行。

### 6. 真实案例分层验收

- Candidate 使用用户授权的真实 PDF、项目 README 和 DeepSeek；
- Role 不使用本地 JD，使用 opt-in live source；
- auth、CAPTCHA、rate limit 和 adapter 缺失是合法阻塞，不绕过；
- 真实个人材料和 live raw 只保存在 Git 忽略的本地运行目录；
- 离线 fixture、DeepSeek smoke 和 live source smoke 分开报告。

## 备选方案

### 方案 A：直接实现 v1.0 Parent Graph

优点：较快出现一个统一入口。

缺点：子图语义和诊断缺陷会被父图掩盖；失败难以确定来自 Claim、Projector、route 还是 handoff。

结论：不采用。先纵向加固，Parent Graph 保留到 v1.0。

### 方案 B：只增加更多 fixture 测试

优点：改动小，CI 稳定。

缺点：无法覆盖真实 provider predicate 漂移、真实来源状态、CLI 恢复和用户交互。

结论：不采用。fixture 是底层门禁，不能代替真实 smoke。

### 方案 C：只写专项脚本继续测试

优点：可以快速验证单个问题。

缺点：装配、日志和路径各自为政，不能形成产品入口或稳定回归。

结论：不采用。专项脚本可用于取证，但正式能力必须进入 CLI/application service。

### 方案 D：先实现 Web

优点：用户界面直观。

缺点：增加前后端状态与部署复杂度，不解决底层契约和诊断问题。

结论：不采用。v0.7.1 只做 CLI。

### 方案 E：让 CLI 自动循环消费所有 directive

优点：看起来接近端到端 Agent。

缺点：形成隐藏 Parent Graph，模糊人工门、预算和跨域权限。

结论：不采用。采用用户可见的单步 dispatcher。

## 影响

### 收益

- 真实运行和测试共享同一入口及装配。
- 每个失败可定位到节点、对象 ID、拒绝原因和恢复动作。
- 先证明子图真实可靠，再实现 Parent Graph，降低集成风险。
- CLI 可用于真实用户验收，也可用于 Agent 和自动化脚本。
- v1.0 能复用稳定 subgraph、handoff、Run event 和 Eval 基线。

### 成本

- 需要跨模块 RuntimeFactory、Session、Event、CLI 和 contract 工作。
- 需要重构若干 no-op/聚合式节点，使执行边界与 trace 对齐。
- 真实来源和真实模型 smoke 不稳定，必须维护分层报告与 replay fixture。
- 交互式 CLI 和 one-shot/JSON 需要额外黑盒测试。

### 约束

- v0.7.1 完成只表示“子图纵向闭环且相邻契约可连通”。
- 一条自动运行到底的统一 LangGraph Parent Graph 仍属于 v1.0。
- 不以 schema 成功、fixture 成功、搜索页成功或退出码 0 代替真实语义验收。
- Session、Run artifact、checkpoint 和 Evidence Store 不得相互冒充。
- 不绕过外部来源的登录、验证码、限流或风险控制。
