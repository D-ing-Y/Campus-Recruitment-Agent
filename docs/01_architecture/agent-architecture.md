# Job Intelligence Agent 架构引导文档

版本：v0.1
日期：2026-07-03
语言：中文

## 1. 项目定位

本项目从“秋招检索 Skill + 爬虫脚本”重构为一个垂直领域 Agent 系统：

> 基于 LangGraph 从零实现一个 DeerFlow 风格的 Job Intelligence Agent，用于完成 2027 秋招岗位检索、证据归档、岗位需求分析、面试经验总结、最小能力包生成和项目策略建议。

项目不是要复刻 Codex、WorkBuddy 或 DeerFlow 的完整平台能力，而是实现一个面向秋招场景的迷你 Agent Runtime。它需要具备主流 Agent 工程中的关键能力：状态管理、工作流编排、工具调用、证据存储、结构化输出、人工介入、评估和部署。

旧招聘爬虫和旧 Skill 已归档到：

`archive/legacy-recruitment-crawler-skill-2026-07-03/`

旧资产后续只作为工具层参考，不再作为新项目的主流程入口。

## 2. 技术参考边界

本项目参考 DeerFlow 2.0 的分层思想，但不直接 fork 或照搬 DeerFlow。

参考资料：

- [DeerFlow 2.0 GitHub](https://github.com/bytedance/deer-flow)：DeerFlow 2.0 定位为 Super Agent harness，包含 filesystem、memory、skills、sandbox-aware execution、sub-agents 等能力，并建立在 LangGraph 和 LangChain 之上。
- [LangGraph Overview](https://docs.langchain.com/oss/python/langgraph/overview)：LangGraph 是低层 Agent 编排运行时，重点支持 long-running、stateful agents、durable execution、human-in-the-loop、persistence、streaming。
- [LangChain Overview](https://docs.langchain.com/oss/python/langchain/overview)：LangChain 提供 Agent harness、模型接口、工具接口、middleware、structured output 等组件，LangChain agent 构建在 LangGraph 之上。

本项目的技术取舍：

| 技术 | 在本项目中的角色 |
| --- | --- |
| LangGraph | Agent Runtime 的核心状态图和工作流编排 |
| LangChain | 工具包装、模型接口、结构化输出的可选组件 |
| DeerFlow 2.0 | 架构参考，不作为直接依赖的主框架 |
| Skill | 项目工作流和数据契约说明，不负责具体执行 |
| LLM API | 模型能力来源，不本地训练大模型 |
| SQLite + 本地文件 | 个人开发版的数据和证据存储 |
| Docker + 云服务器 | 后续服务器部署方式 |

## 3. Chain、Graph、Harness 的区别

### Chain

Chain 是线性或近似线性的步骤组合。

示例：

```text
输入目标 -> 生成搜索词 -> 搜索岗位 -> 总结结果
```

Chain 适合简单流程，但不适合长任务中的条件分支、失败重试、人工介入和状态恢复。

### Graph

Graph 是带状态的流程图，可以有分支、循环、中断和恢复。

示例：

```text
parse_goal -> plan_tasks -> execute_tool -> verify_result
                  ↑              ↓              |
                  |          need_login          |
                  |              ↓              |
                  └-------- human_interrupt <----
```

Graph 要解决的问题：

- 长任务可能失败，需要从中间继续。
- 不同任务需要走不同分支。
- 工具调用结果需要验证，不合格要重试或换工具。
- 需要用户登录、确认、筛选时，Agent 可以暂停。

### Harness

Harness 是 Agent 运行的完整外壳。它不只是一个流程图，还包含工具、记忆、文件系统、沙箱、子 Agent、权限、追踪和部署。

DeerFlow 2.0 属于 harness 级别。我们的项目先实现 mini harness：

```text
LangGraph 状态图
+ Tool Registry
+ Evidence Store
+ LLM Provider
+ Human-in-the-loop
+ Eval / Trace
```

## 4. 总体架构层级

```text
User Interface
  ↓
Gateway / API Layer
  ↓
Agent Workflow Layer
  ↓
Mini Agent Runtime
  ↓
Tool Layer
  ↓
Evidence / Memory Layer
  ↓
LLM Provider Layer
  ↓
Evaluation / Observability Layer
```

### 4.1 用户交互层

个人开发版优先级：

1. CLI：最快验证 Agent 流程。
2. Streamlit：便于个人交互和展示。
3. Web UI：后续可用 FastAPI + 前端实现。

Mac 的角色：

- 作为主要交互窗口。
- 负责需要真实浏览器登录的网站操作。
- 负责本地调试、查看报告、确认人工介入步骤。

### 4.2 Gateway / API 层

v0-v1 可以没有独立 Gateway，只通过 CLI 调用。

v2 后引入 FastAPI：

- 创建任务。
- 查询任务状态。
- 查看证据。
- 导出报告。
- 提供前端或 Streamlit 调用接口。

### 4.3 Agent Workflow 层

业务工作流保留四个主阶段：

```text
岗位检索 -> 岗位筛选 -> 笔试准备 -> 面试准备 / 项目策略
```

但实现方式从“Skill 指挥 Codex 检索”改为“Agent 调用工具并维护状态”。

每个阶段必须定义：

- 输入 schema
- 输出 schema
- 可调用工具
- 证据归档要求
- 失败处理方式
- 是否允许人工介入
- 验收指标

### 4.4 Mini Agent Runtime 层

这是 v0-v1 最重要的开发对象。

最小包必须实现：

| 模块 | 最小功能 |
| --- | --- |
| `AgentState` | 保存用户目标、计划、工具结果、证据 ID、分析结果、错误 |
| `StateGraph` | 用 LangGraph 定义节点、边、条件跳转 |
| `Planner` | 把用户目标转为可执行任务列表 |
| `ToolRegistry` | 注册工具，按名称和参数调用工具 |
| `Executor` | 执行工具，记录结果和错误 |
| `Verifier` | 检查工具结果是否满足 schema 和证据要求 |
| `HumanInterrupt` | 需要登录、筛选、确认时暂停并等待用户 |
| `TraceLog` | 记录每一步节点、输入、输出、耗时、错误 |
| `ReportWriter` | 输出 Markdown / JSON 报告 |

最小闭环不是“能聊天”，而是：

```text
用户目标
  -> 目标解析
  -> 计划生成
  -> 工具调用
  -> 证据保存
  -> 结果校验
  -> 报告生成
```

v0 不做：

- 完整 Skill 引擎。
- 子 Agent。
- Docker 沙箱。
- 分布式数据库。
- 向量数据库。
- 复杂前端。
- 本地大模型训练。

### 4.5 工具层

工具层负责执行确定性或半确定性动作。工具不负责最终业务判断。

初始工具分组：

```text
tools/
  recruitment/
    boss_search
    zhaopin_search
  reputation/
    nowcoder_collect
    xiaohongshu_collect
  extraction/
    html_to_text
    job_normalizer
    post_normalizer
  llm/
    summarize_text
    extract_json
    analyze_requirements
  storage/
    save_evidence
    load_evidence
```

工具调用统一返回：

```json
{
  "tool_name": "boss_search",
  "status": "success",
  "records": [],
  "evidence_ids": [],
  "error": null,
  "metadata": {}
}
```

### 4.6 Evidence / Memory 层

证据追溯先于 RAG。

v1 证据存储：

```text
SQLite
+ data/evidence/raw/
+ data/evidence/text/
+ data/runs/
```

每条证据至少包含：

```json
{
  "evidence_id": "uuid",
  "source_url": "https://...",
  "platform": "boss",
  "content_type": "job_posting",
  "retrieved_at": "2026-07-03T00:00:00+08:00",
  "raw_path": "data/evidence/raw/...",
  "text_path": "data/evidence/text/...",
  "hash": "sha256",
  "metadata": {}
}
```

RAG 和向量数据库是 v2 能力，用于从大量岗位、帖子和公司评价中检索相关证据。证据追溯本身不依赖向量数据库。

### 4.7 LLM Provider 层

本项目默认使用 API 调用模型，不训练大模型。

Provider 设计目标：

- 支持 OpenAI-compatible API。
- 支持 DeepSeek、Qwen、OpenAI、OpenRouter 等切换。
- 所有关键输出用 Pydantic 校验。
- 输出失败时允许有限重试。
- 所有 LLM 调用做缓存，避免重复花费。

模型层职责：

- 文本抽取。
- 经验帖总结。
- 岗位要求聚类。
- 准备路径生成。
- 项目策略建议。

模型层不直接操作浏览器、文件系统或数据库，必须通过工具层完成。

### 4.8 Sandbox / Permission 层

个人开发版不做完整 Docker 沙箱，但必须保留权限边界：

- 需要登录的网站由用户在真实 Chrome 完成登录。
- Agent 不尝试绕过验证码或反爬验证。
- Cookie / cURL 导入必须由用户主动提供。
- 高风险操作必须要求用户确认。
- 文件写入限制在项目目录内。

服务器版再考虑 Docker sandbox。

### 4.9 Evaluation / Observability 层

评估是项目从 demo 变成 Agent 工程项目的关键。

至少记录：

- 每个节点输入输出。
- 每次工具调用参数、结果、错误。
- 每次 LLM 调用 provider、model、token、耗时、缓存命中。
- 每个结论绑定的 evidence_id。

v1 最小评估指标：

| 指标 | 含义 |
| --- | --- |
| `schema_valid_rate` | LLM 输出 JSON 合规率 |
| `tool_success_rate` | 工具调用成功率 |
| `evidence_trace_rate` | 结论能追溯到证据的比例 |
| `extraction_completeness` | 岗位字段完整率 |
| `duplicate_rate` | 重复岗位比例 |
| `recommendation_coverage` | 最小能力包覆盖岗位需求的比例 |

## 5. 开发环境规划

### 5.1 个人开发版

Mac：

- 主要交互窗口。
- 本地开发 Python / LangGraph。
- 运行 CLI 或 Streamlit。
- 处理浏览器登录、Cookie 导入、人工筛选。

本地存储：

- SQLite。
- 本地文件夹证据归档。
- `.env` 保存 API key，不进入 Git。

模型：

- 使用 API。
- 不训练大模型。
- 不强依赖本地 GPU。

### 5.2 云服务器部署版

云服务器用于后续部署：

- Linux + Docker Compose。
- FastAPI 后端。
- Agent runtime 服务。
- SQLite 或 Postgres。
- Qdrant / pgvector 可选。
- Langfuse / LangSmith 可选。
- 文件归档卷或对象存储。

服务器不负责处理必须登录的个人浏览器流程。需要登录的网站仍由 Mac 完成登录和 Cookie/cURL 导入，服务器只处理后续 API 化工具调用、归档、分析和报告。

### 5.3 A100 服务器的角色

A100 不是 v1 必需条件。

可选用途：

- 本地 embedding。
- reranker。
- 文本分类小模型。
- vLLM 部署中小模型做低成本摘要。

不作为当前主线：

- 不训练基础大模型。
- 不把本地模型作为系统必要依赖。

## 6. 推荐项目目录

```text
campus-job-agent/
  docs/
    AGENT_ARCHITECTURE_GUIDE.md

  apps/
    cli/
    web/

  agent/
    state.py
    graph.py
    planner.py
    executor.py
    verifier.py
    interrupts.py
    prompts/

  tools/
    base.py
    registry.py
    recruitment/
    reputation/
    extraction/
    llm/
    storage/

  schemas/
    goal.py
    tool.py
    evidence.py
    job.py
    post.py
    analysis.py
    report.py

  memory/
    evidence_store.py
    repositories.py
    vector_store.py

  workflows/
    job_search.py
    job_filter.py
    written_exam.py
    interview_prep.py
    project_strategy.py

  evals/
    schema_eval.py
    trace_eval.py
    recommendation_eval.py

  data/
    evidence/
    runs/
    cache/
    reports/
```

## 7. 版本路线

### v0：架构重构与学习入口

目标：

- 归档旧爬虫和旧 Skill。
- 确定新项目目录。
- 完成本架构引导文档。
- 明确 DeerFlow / LangGraph / LangChain 的学习边界。

验收：

- 新旧项目入口清晰。
- 后续开发只从 `campus-job-agent/` 开始。

### v0.1：LangGraph 最小闭环

目标：

- 创建 Python 项目骨架。
- 实现 `AgentState`。
- 实现 5 个节点：
  - `parse_goal`
  - `plan_tasks`
  - `run_mock_tool`
  - `verify_result`
  - `write_report`
- 用 mock tool 跑通流程。

验收：

- 输入“成都 AI Agent 2027 秋招”。
- 输出结构化计划和 Markdown 报告。
- 生成 trace log。

### v0.2：LLM API 与结构化输出

目标：

- 接入一个 LLM Provider。
- 实现 JSON 输出约束。
- 用 Pydantic 校验。
- 实现一次自动重试。
- 实现 LLM 调用缓存。

验收：

- 模型能把用户目标解析成 `SearchGoal`。
- 输出不合规时能被捕获。
- 缓存命中可见。

### v0.3：Tool Registry 与 Evidence Store

目标：

- 实现统一工具接口。
- 实现本地 Evidence Store。
- 工具结果绑定 evidence_id。
- 把 mock tool 改成真实保存证据。

验收：

- 每次工具调用产生可追溯证据。
- 报告中的结论能引用 evidence_id。

### v1.0：单 Agent 岗位检索闭环

目标：

- 接入招聘工具。
- 完成岗位检索、归档、结构化抽取、报告生成。
- 仍保持单主控 Agent，不拆子 Agent。

验收：

- 针对一个城市和岗位关键词生成岗位表。
- 至少输出岗位分布、公司列表、技能词频。
- 噪声岗位允许存在，但必须能追溯来源。

### v1.1：舆情与经验帖工具接入

目标：

- 接入牛客、小红书等讨论帖采集工具。
- 保存 HTML / 文本 / source_url。
- 用 LLM 总结为统一 schema。

验收：

- 能按公司和岗位聚合经验帖。
- 输出笔试、面试、工作体验相关信号。

### v1.2：岗位筛选与最小能力包

目标：

- 对岗位要求做聚类。
- 区分共性要求和特异性要求。
- 生成最小准备能力包。

验收：

- 输出技能覆盖矩阵。
- 输出准备优先级。
- 输出“一个项目覆盖最多岗位”的项目建议。

### v1.3：评估体系

目标：

- 增加 schema、工具、证据、推荐质量评估。
- 输出 eval report。

验收：

- 每次 run 都能生成评估摘要。
- 能发现字段缺失、证据缺失、重复岗位和推荐覆盖不足。

### v2.0：Memory 与 RAG

目标：

- 引入向量化。
- 支持历史岗位、帖子、公司信息检索。
- 支持跨 run 的长期记忆。

验收：

- Agent 可以基于历史证据回答问题。
- 回答必须带 evidence_id 和 source_url。

### v2.5：多 Agent / Sub-Agent

目标：

- 拆分子 Agent：
  - `JobSearchAgent`
  - `ReputationAgent`
  - `RequirementAnalysisAgent`
  - `InterviewPrepAgent`
  - `ProjectStrategyAgent`
- 主 Agent 负责规划、调度和综合。

验收：

- 子 Agent 上下文隔离。
- 子 Agent 输出结构化结果。
- 主 Agent 能合并结果并生成最终报告。

### v3.0：云服务器部署

目标：

- Docker Compose 部署。
- FastAPI 服务化。
- Streamlit 或 Web UI。
- 数据库和文件归档持久化。

验收：

- Mac 可作为交互端访问云服务器。
- 服务器可持续运行任务。
- API key、Cookie、证据文件不进入 Git。
- 服务默认不公开暴露危险执行能力。

## 8. 下一步开发任务

下一步只做 v0.1：

1. 创建 Python 项目骨架。
2. 安装 LangGraph、Pydantic、pytest。
3. 定义 `AgentState`。
4. 实现最小 5 节点 StateGraph。
5. 实现 mock tool。
6. 输出 trace log 和 Markdown report。

暂不接入：

- Boss、智联、牛客、小红书真实爬虫。
- RAG。
- 向量数据库。
- 多 Agent。
- 服务器部署。

## 9. 面试项目表达

推荐表达：

> 基于 LangGraph 从零实现一个 DeerFlow 风格的垂直领域 Job Intelligence Agent，围绕秋招岗位情报分析构建任务规划、工具调用、证据归档、结构化抽取、Memory/RAG、评估和部署能力。项目不训练大模型，而是通过可切换的 LLM API Provider、严格 JSON schema、证据追溯和评估闭环提升 Agent 的可靠性。

避免表达：

- “我写了一个招聘爬虫。”
- “我写了一个 Skill。”
- “我套了一个大模型聊天接口。”

核心能力点：

- Agent Runtime
- LangGraph 状态编排
- Tool Registry
- Human-in-the-loop
- Evidence Store
- Structured Output
- RAG / Memory
- Evaluation
- Deployment
