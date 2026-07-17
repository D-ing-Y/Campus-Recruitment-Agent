# ADR-0004: 使用有状态、可中断的 LangGraph 子图构建候选人画像

## 状态

Accepted

## 日期

2026-07-17

## 背景

v0.3 已完成统一证据层，但证据管线尚未接入 LangGraph 业务循环。候选人材料的数量、类型、完整度和冲突情况不可预先穷举：有的用户只需读取现有材料，有的需要定向提问，有的必须补充证明文件，还有的应在信息不足时诚实保留 unknown。

继续扩展 v0.1/v0.2 的线性 Graph 会把动态判断堆入单个节点，也无法自然表达暂停、恢复、循环预算和用户纠正。

## 决策

### 1. 候选人画像使用独立 LangGraph subgraph

- 固定节点集合和业务边界由代码定义。
- 使用 conditional edge 表达继续读取、向用户提问、请求材料、保留 unknown 和完成。
- 使用 loop 进行增量证据处理和画像重建。
- v0.4 先独立验收该 subgraph，v1.0 再接入 Parent Graph。

### 2. 使用持久化 checkpoint 和 LangGraph interrupt

- 本地运行使用 SQLite checkpointer，测试可使用内存 checkpointer。
- 通过 `thread_id` 恢复同一任务。
- 用户交互使用 `interrupt()` 和结构化 request/response contract。
- checkpoint 保存执行状态，不承担 Evidence Store 的事实职责。

### 3. 采用“模型建议、代码裁决”的动态决策

- LLM 评价高价值未知项并提出枚举 `next_action`。
- 确定性 policy 校验数据可用性、预算、用户选择和停止条件。
- LLM 不得创建任意节点、突破预算或直接写画像。

### 4. 用户输入必须先证据化

- 回答、补充材料和纠正先成为 Artifact/Fragment/Claim。
- 用户纠正通过新 Claim 和 `supersedes_claim_id` 保存。
- ProfileSnapshot 从有效 Claim 重建，聊天记录和 checkpoint 不能直接成为画像事实。

### 5. 真实 Tool 与 mock 基线并存

- PDF、Markdown、TXT、README 读取、证据保存和 checkpoint 使用真实本地实现。
- deterministic mock evaluator/provider 只用于可重复测试和离线默认路径。
- 外部 MCP/插件可在未来实现 Tool adapter，但不是 v0.4 的依赖。

## 备选方案

### 方案 A：继续使用线性 Graph

优点：代码改动小。

缺点：无法清晰表达多轮收集、条件路由和恢复；动态逻辑会隐藏在节点内部。

结论：不采用。

### 方案 B：单个 ReAct Agent 自由调用全部工具

优点：表面上自主性更强，开发初期节点较少。

缺点：停止条件、证据写入顺序、隐私边界和恢复幂等难以保证，评估也难以定位错误。

结论：不采用。

### 方案 C：由表单一次性收集全部字段

优点：确定、便于实现。

缺点：不能利用用户已有材料，问题冗余，也无法根据证据动态选择高价值缺口。

结论：保留表单用于 CareerIntent 等明确偏好，不用于替代候选人画像 Agent。

### 方案 D：立即依赖外部 MCP 文档服务器

优点：可能快速获得更多文件处理能力。

缺点：引入外部可用性、权限和数据传输风险，且不能替代本项目的证据契约与持久化。

结论：v0.4 使用仓库内真实本地 Tool；未来 MCP 通过相同接口可插拔接入。

### 方案 E：把用户回答直接写入 Graph State

优点：实现最快。

缺点：回答无法跨版本审计、重建和纠错，checkpoint 清理后事实丢失。

结论：不采用。

## 影响

### 收益

- 项目首次真实展示 subgraph、conditional edge、loop、checkpoint 和 interrupt。
- 动态决策发生在明确边界内，可解释、可测量、可恢复。
- 用户回答与材料共享统一证据语义，画像可以重建和版本比较。
- 为 v0.5 岗位画像 Graph 和 v1.0 Parent Graph 提供可复用模式。

### 成本

- 需要新增状态 reducer、人工交互契约、checkpointer 依赖和恢复测试。
- 所有中断前后写操作必须考虑重放与幂等。
- 充分性评价需要维护 deterministic baseline、LLM schema 和 gold fixture。

### 约束

- State 不保存完整原文。
- checkpoint 不是事实源。
- 事实性画像字段必须引用 Claim。
- 用户纠正不删除历史 Claim。
- Graph 必须有硬预算和可验证停止条件。
- v0.4 不借机引入 RAG、分布式存储或 Multi-Agent。
