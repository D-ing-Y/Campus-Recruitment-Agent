# Roadmap

本文档是项目版本迭代的主索引。后续开发以本文件为入口，再进入对应版本的 requirements、RFC、ADR 和 implementation tasks。

## 版本总览

| 版本 | 主题 | 核心交付 |
| --- | --- | --- |
| v0 | 项目结构与开发流程 | 标准目录、文档驱动流程、旧资产归档 |
| v0.1 | Mini Agent Runtime | LangGraph 最小闭环、mock tool、trace、report、测试 |
| v0.2 | LLM Provider 与结构化输出 | API provider、JSON 输出、Pydantic 校验、重试、缓存 |
| v0.3 | Tool Registry 与 Evidence Store | 工具契约升级、本地证据存储、evidence_id 追踪 |
| v1.0 | 单 Agent 岗位检索闭环 | 招聘工具接入、岗位表、技能词频、岗位分布报告 |
| v1.1 | 舆情与经验帖采集 | 牛客/小红书等内容归档、经验帖总结 schema |
| v1.2 | 岗位筛选与最小能力包 | 共性/特异性要求分析、技能覆盖矩阵、项目建议 |
| v1.3 | Eval 与 Observability | 评估指标、trace 汇总、质量报告、失败案例分析 |
| v2.0 | Memory / RAG | 向量检索、历史证据查询、带引用回答 |
| v2.5 | Multi-Agent / Sub-Agent | 子 Agent 拆分、主 Agent 调度、上下文隔离 |
| v3.0 | 云服务器部署 | FastAPI、Docker Compose、持久化存储、远程运行 |

## v0：项目结构与开发流程

目标：

- 建立标准项目目录。
- 确认文档驱动开发流程。
- 归档旧 Skill 和旧爬虫资产。
- 明确 Mac Codex 与 VSCode Codex 的分工。

核心交付：

- `docs/00_project/`
- `docs/01_architecture/`
- `docs/02_development/`
- `archive/legacy-recruitment-crawler-skill-2026-07-03/`

完成标准：

- 新旧项目入口清晰。
- 后续开发从 `campus-job-agent/` 开始。

## v0.1：Mini Agent Runtime

目标：

- 实现基于 LangGraph 的最小 Agent Runtime。
- 用 mock tool 验证状态图、工具调用、校验、trace 和报告输出。

核心交付：

- `AgentState`
- `ParsedGoal`
- `PlanTask`
- `ToolResult`
- `TraceEvent`
- `ToolRegistry`
- `mock_job_search`
- LangGraph workflow
- CLI 入口
- 单元测试、集成测试、eval 测试

非目标：

- 不接入真实招聘网站。
- 不接入真实 LLM API。
- 不做 RAG、Memory、多 Agent、Web UI、服务器部署。

完成标准：

- `python apps/cli/main.py run "成都 AI Agent 2027 秋招"` 可运行。
- 输出 `state.json`、`trace.json`、Markdown report。
- 单元测试、集成测试、eval 测试通过。

关联文档：

- `docs/03_requirements/v0.1-mini-runtime.md`
- `docs/03_requirements/v0.1-implementation-tasks.md`
- `docs/04_rfc/0001-mini-agent-runtime.md`
- `docs/05_adr/0001-use-langgraph-for-runtime.md`

## v0.2：LLM Provider 与结构化输出

目标：

- 接入一个可配置的 LLM API provider。
- 将 v0.1 的规则解析逐步升级为 LLM JSON 解析。
- 建立结构化输出校验、失败重试和缓存机制。

核心交付：

- `LLMProvider` 抽象。
- OpenAI-compatible provider 实现。
- `.env` / config 读取。
- `SearchGoal` 或升级版 `ParsedGoal` schema。
- JSON-only prompt contract。
- Pydantic 校验。
- 校验失败后的有限重试。
- LLM 调用缓存。
- LLM 调用 trace 元信息。

非目标：

- 不接入真实招聘网站。
- 不做多 provider 复杂路由。
- 不做 RAG。
- 不做长期记忆。

完成标准：

- 使用 API 将用户目标解析为结构化 JSON。
- 输出不合规时能够被捕获并重试。
- 相同输入可以命中缓存。
- 测试可在 mock provider 下稳定运行。

文档状态：

- 已创建：`docs/03_requirements/v0.2-llm-provider.md`
- 已创建：`docs/03_requirements/v0.2-implementation-tasks.md`
- 已创建：`docs/04_rfc/0002-llm-provider-and-structured-output.md`
- 已创建：`docs/05_adr/0002-use-llm-provider-abstraction-for-structured-output.md`

## v0.3：Tool Registry 与 Evidence Store

目标：

- 将工具层从 mock 验证升级为可扩展工具系统。
- 实现本地证据归档和 `evidence_id` 追踪。

核心交付：

- 工具元数据。
- 工具输入/输出 schema。
- 工具错误分类。
- 本地 Evidence Store。
- 原始文本/HTML 文件归档。
- `evidence_id` 与 tool result 绑定。
- 基础去重 hash。

非目标：

- 不做复杂数据库设计。
- 不做向量检索。
- 不做大规模爬虫调度。

完成标准：

- 每次工具调用可以产生或关联 evidence。
- 报告中的关键结论能引用 evidence_id。
- 重复证据可以通过 hash 识别。

## v1.0：单 Agent 岗位检索闭环

目标：

- 接入招聘工具，完成岗位检索到报告的单 Agent 闭环。

核心交付：

- 招聘工具适配层。
- 岗位结构化 schema。
- 岗位归档。
- 岗位表导出。
- 公司分布、岗位分布、技能词频报告。

非目标：

- 不做最终岗位筛选决策。
- 不保证完全清除招聘网站噪声。
- 不绕过验证码或反爬。

完成标准：

- 针对一个城市和岗位关键词生成岗位表。
- 每条岗位记录可追溯来源。
- 输出岗位分布和技能词频。

## v1.1：舆情与经验帖采集

目标：

- 接入牛客、小红书等经验帖和讨论帖采集工具。
- 将非结构化文本归档并总结为统一 schema。

核心交付：

- 经验帖采集工具。
- 原始 HTML / 文本归档。
- 经验帖总结 prompt。
- 面试、笔试、加班、氛围、技术栈等信号字段。

完成标准：

- 可按公司和岗位聚合经验帖。
- 总结结果符合统一 JSON schema。
- 关键总结带 evidence quote。

## v1.2：岗位筛选与最小能力包

目标：

- 分析目标岗位的共性要求和特异性要求。
- 生成最小准备能力包和项目策略建议。

核心交付：

- 技能需求聚类。
- 技能覆盖矩阵。
- 岗位匹配评分。
- 最小能力包生成。
- 项目建议生成。

完成标准：

- 输出高频技能、低频特异技能和准备优先级。
- 给出一个覆盖最多岗位需求的项目方案。
- 每个建议可追溯到岗位或经验帖证据。

## v1.3：Eval 与 Observability

目标：

- 建立 Agent 质量评估体系。
- 将 trace、工具调用、LLM 调用和证据追溯转化为可读评估报告。

核心交付：

- `schema_valid_rate`
- `tool_success_rate`
- `evidence_trace_rate`
- `extraction_completeness`
- `duplicate_rate`
- `recommendation_coverage`
- eval report

完成标准：

- 每次 run 可生成评估摘要。
- 能定位字段缺失、证据缺失、工具失败和推荐覆盖不足。

## v2.0：Memory / RAG

目标：

- 引入向量化检索和跨 run 记忆。
- 支持基于历史证据回答问题。

核心交付：

- embedding provider。
- vector store。
- evidence retriever。
- source-grounded answer。
- 历史岗位和经验帖检索。

完成标准：

- Agent 可以检索历史证据回答问题。
- 回答必须包含 evidence_id 和 source_url。

## v2.5：Multi-Agent / Sub-Agent

目标：

- 将单 Agent 拆分为多个职责明确的子 Agent。

候选子 Agent：

- `JobSearchAgent`
- `ReputationAgent`
- `RequirementAnalysisAgent`
- `InterviewPrepAgent`
- `ProjectStrategyAgent`

完成标准：

- 子 Agent 上下文隔离。
- 子 Agent 输出结构化结果。
- 主 Agent 能合并结果并生成最终报告。

## v3.0：云服务器部署

目标：

- 将个人开发版迁移为可远程运行的服务。

核心交付：

- FastAPI 服务。
- Docker Compose。
- 持久化数据库。
- 文件归档卷。
- API key 和 secrets 管理。
- 云服务器部署文档。

完成标准：

- Mac 可作为交互端访问云服务器。
- 服务器可持续运行任务。
- 服务默认不公开危险执行能力。
- 密钥、cookie、真实数据不进入 Git。
