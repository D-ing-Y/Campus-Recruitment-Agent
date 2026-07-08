# ADR-0001: 使用 LangGraph 作为 Mini Agent Runtime 编排框架

## 状态

Accepted

## 日期

2026-07-08

## 背景

本项目目标是构建一个面向秋招岗位情报分析的垂直领域 Agent。v0.1 需要先实现 Mini Agent Runtime，用于验证状态管理、工作流编排、工具调用、结果校验、trace 和报告输出。

该 Runtime 后续需要扩展：

- 条件分支。
- 工具失败重试。
- Human-in-the-loop。
- Checkpoint / 状态恢复。
- 多步骤长任务。
- Memory / RAG。
- Sub-Agent。

因此 v0.1 不应只实现一个线性函数调用链，也不应直接引入过重的完整 Agent 平台。

## 决策

v0.1 使用 LangGraph 作为 Agent Runtime 的工作流编排框架。

初始实现只使用 LangGraph 的最小能力：

- `StateGraph`
- `START`
- `END`
- `add_node`
- `add_edge`
- `compile`
- `invoke`

v0.1 暂不使用：

- checkpoint。
- interrupt。
- conditional edge。
- subgraph。
- streaming。

这些能力在后续版本根据真实需求逐步引入。

## 备选方案

### 方案 A：纯 Python 手写状态机

优点：

- 实现简单。
- 依赖少。
- 便于快速理解。

缺点：

- 后续分支、重试、人工介入和状态恢复都需要自行实现。
- 难以体现主流 Agent graph runtime 的工程实践。
- 后续迁移到 LangGraph 仍需要重构。

结论：不采用。

### 方案 B：LangChain Agent

优点：

- 更高层，集成模型和工具较方便。
- 适合快速构建工具调用型 Agent。

缺点：

- v0.1 目标是学习和实现 Runtime 基础结构，直接使用高层 Agent harness 会隐藏状态图和节点设计。
- 对本项目的 trace、校验、证据追溯和版本化工作流不够显式。

结论：暂不作为 Runtime 核心。后续可在工具层或 LLM Provider 层复用 LangChain 组件。

### 方案 C：直接基于 DeerFlow 二次开发

优点：

- 架构完整。
- 包含较成熟的 harness 思想。
- 与本项目长期目标接近。

缺点：

- v0.1 会引入过多非必要概念，包括 filesystem、memory、skills、sandbox、sub-agents、gateway 等。
- 学习路径容易变成配置和改造现有框架，而不是理解 Runtime 的核心构成。
- 初期难以控制范围。

结论：不直接 fork 或依赖 DeerFlow。将 DeerFlow 作为架构参考。

### 方案 D：OpenAI Agents SDK

优点：

- 官方 SDK。
- 支持 Agent、tool、handoff、guardrail、trace 等能力。
- 适合基于 OpenAI API 的 agentic app。

缺点：

- 本项目希望重点学习 LangGraph / DeerFlow 风格的 graph runtime。
- 后续模型 provider 需要保持可切换，不应过早绑定单一 SDK。
- v0.1 不需要 handoff、guardrail 等较高层能力。

结论：暂不采用为 Runtime 核心。后续可作为对比或可选 provider / orchestration 实验。

### 方案 E：LangGraph

优点：

- 适合有状态、多步骤、可扩展的 Agent 工作流。
- 与 DeerFlow 架构方向一致。
- 可以从最小 `StateGraph` 开始，逐步引入 checkpoint、interrupt、conditional edge 和 subgraph。
- 便于显式设计 `AgentState`、节点、边、工具调用和 trace。

缺点：

- 比纯函数链复杂。
- 需要学习 LangGraph 的状态更新和图编排方式。
- 工具层、证据层、评估层仍需自行设计。

结论：采用。

## 影响

### 正向影响

- v0.1 可以用最小 graph 跑通 Agent Runtime。
- 后续可自然扩展到分支、重试、人工介入和状态恢复。
- 项目能够体现主流 Agent 工程中的 graph-based orchestration 能力。
- 便于解释 Chain、Graph、Harness 的区别。
- 便于后续参考 DeerFlow 分层逐步扩展。

### 成本

- 需要学习 LangGraph 的基础 API。
- 需要维护 `AgentState` 和节点边界。
- 初期代码会比普通线性脚本多一些结构。

### 约束

- v0.1 节点必须通过 `AgentState` 传递数据。
- 节点不得通过全局变量共享运行时状态。
- 工具调用必须通过 `ToolRegistry`。
- Runtime 不直接依赖具体招聘网站工具。
- LangGraph 高级能力必须等需求明确后再引入。

## 后续复盘点

在 v0.3 或 v1.0 后复盘该决策：

- LangGraph 是否降低了工具接入和状态维护成本。
- `AgentState` 是否需要从 `TypedDict` 调整为其他形式。
- 是否需要引入 checkpoint / interrupt。
- 是否需要将部分 workflow 拆为 subgraph。
- 是否需要对比 OpenAI Agents SDK 或 DeerFlow 的实现方式。

