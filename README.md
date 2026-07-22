# Campus Job Agent

面向 2027 秋招的垂直领域 Job Intelligence Agent。

本项目采用文档驱动开发：先确认需求、RFC/ADR、接口契约和验收标准，再进入代码实现。

## 当前阶段

v0.4 候选人画像 Graph 已于 2026-07-18 完成代码、测试和 Eval 验收。

v0.5 岗位需求画像 Graph 已完成代码、fixture、离线 adapter、RoleProfile Graph、测试和 Eval；
当前代码版本为 `0.5.0`。140 项全量测试全部通过，其中 v0.5 新增 72 项，v0.1-v0.4
的 68 项回归全部通过。2026-07-21 实现后 Live Smoke 中 DeepSeek、牛客、企业官网传输和真实
auth interrupt/resume 已通过。2026-07-22 决定停止 BOSS 集成并将核心第三方招聘来源切换为
智联招聘；`zhaopin_jobs` 单页 raw-first live smoke 和 20 条真实页面重放解析已经通过。
真实智联候选到美团官网同岗已生成 `confirmed` JobIdentityLink 和字段级 FieldResolution，
v0.5 状态为 Implemented / Accepted。

v0.1/v0.2 保留为 Runtime 与 LLM 基座，v0.3 提供统一证据层、领域契约、版本化画像快照和证据质量评估；v0.4 已将这些能力接入第一个可循环、可中断、可恢复的候选人画像 LangGraph subgraph。

项目从 v0.3 起定位为“证据驱动的双画像求职 Agent”：原始材料进入统一证据层，系统构建候选人画像、求职意图和岗位需求画像，通过 LangGraph 完成画像充分性评价、岗位检索、差距分析、人工决策、准备计划和反馈更新。

v0.1/v0.2 的 LangGraph Mini Runtime、ToolRegistry、trace、LLM Provider、结构化输出、重试和缓存继续复用；线性拓扑与早期 `SearchGoal` 业务 schema 将在后续版本升级。

v0.2 已实现：

- 单轮 CLI 运行。
- LangGraph 线性工作流。
- `parse_goal` 节点中的 LLM JSON 结构化目标解析。
- 默认 mock LLM provider。
- OpenAI-compatible Chat Completions provider 抽象。
- Pydantic 校验、一次结构化重试、本地 LLM cache。
- `ToolRegistry` 调用 `mock_job_search`。
- `state.json`、`trace.json`、`llm_calls.json` 和 Markdown report 输出。
- 单元测试、集成测试和 eval 测试。

v0.3 已实现：

- `EvidenceArtifact → EvidenceFragment → EvidenceClaim → ProfileSnapshot` 证据链。
- SQLite Repository 与带原子写入的本地不可变 BlobStore。
- TXT/Markdown/HTML 文本抽取，PDF/二进制文件登记，SHA-256 去重。
- 确定性分片、可验证 locator、Claim 引用/越权/更新校验。
- 通用 Pydantic structured output，同时保持 v0.2 `SearchGoal` 兼容。
- 版本化 Capability Ontology，未知技能保留 raw label。
- Candidate/CareerIntent/Role 画像快照持久化，证据 trace/report/eval。

v0.4 已实现：

- PDF、Markdown、TXT 和项目 README 的真实本地摄取 Tool。
- `candidate_profile` subgraph 与充分性评价。
- `read_more`、`ask_user`、`request_more_materials`、`finalize_with_unknowns` 条件路由。
- SQLite checkpoint、LangGraph interrupt/resume 和循环预算。
- 用户回答、补充材料与纠正先证据化，再重建版本化 CandidateProfile。
- CandidateProfile、Human Interaction、State、Evidence、LLM 和 Tool contract。
- 真实 Tool 统一通过 `ToolRegistry`，checkpoint 使用官方 SQLite saver。
- 回答与纠正先归档为 Artifact/Fragment/Claim，再重建画像；重复 resume 幂等。

v0.5 已按最新来源验证架构完成离线实现与验收：

- recruitment discovery、employer official verification 与 experience 分离的
  SourceAdapter 和 raw-before-parse 证据链。
- `zhaopin_jobs`、`official_careers`、`official_careers_meituan` 与 `nowcoder_experience` opt-in live adapter；
  live 默认关闭，CI 使用相同 raw-first 路径的离线 fixture。
- 第三方岗位去重后再做官网核验；两侧原始证据分别保存，通过 JobIdentityLink 和
  FieldResolution 形成字段级 resolved view。
- 具体岗位画像与带样本/分母的岗位族画像。
- 查询规划、翻页、换词、换源、官网核验、覆盖度评价和授权 interrupt/resume。
- 招聘事实与社区笔面试信号的字段级来源权威校验。
- 跨来源岗位去重、经验帖去重、时效标签和 SourceRunReceipt。
- 用户正常登录与本地 cURL/Cookie 导入；秘密值不进入 State、Evidence、trace 或 Git。
- 开源采集项目先完成 license/security/smoke 准入；JSON-LD、声明式官网 adapter spec、
  同域预算和运行时禁止执行 LLM 生成代码均已有测试覆盖。
- 72 项 v0.5 schema、adapter、raw-before-parse、官网核验、authority、画像聚合、路由、
  auth resume、SQLite checkpoint 和 Eval 测试。

v0.5 不实现双画像匹配、学习计划、RAG、分布式存储、Multi-Agent、Web UI 或自动投递。默认测试不访问真实招聘网站，不需要登录或真实 API key。

后续路线会实际实现 LangGraph 高级编排、Hybrid RAG、分布式存储和必要的 Sub-Agent；每项技术必须对应真实业务问题、简单基线和量化验收，而不是仅作为技术展示。

## 项目结构

- `docs/`：项目开发文档、架构、需求、RFC、ADR、契约、评估和部署说明。
- `src/`：Agent Runtime、工具层、schema、memory、workflow、eval 的代码实现。
- `apps/`：CLI、Web 或 Streamlit 等用户交互入口。
- `tests/`：单元测试、集成测试和 eval 测试。
- `scripts/`：开发、数据处理和评估脚本。
- `configs/`：本地配置模板。
- `data/`：本地运行数据、证据、缓存和报告。默认不提交真实数据。
- `reports/`：版本验收报告和评估报告。

## 安装依赖

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

如果本机没有 `python` 命令，可使用 `python3` 创建虚拟环境。

### 本地 Chrome 登录态装配

先在真实 Chrome 中正常登录，然后在项目根目录显式执行：

```bash
.venv/bin/campus-agent auth import-chrome --source zhaopin
.venv/bin/campus-agent auth import-chrome --source nowcoder
```

命令只读取 `.zhaopin.com` 或 `.nowcoder.com` 的 Cookie，并将对应的
`local-secret://<source>/default` 安全覆盖写入 `data/cache/credentials/`。该目录权限为
`700`，凭据文件权限为 `600`，且已被 Git 忽略。终端不会输出 Cookie 值。

## 运行 v0.2 CLI

```bash
python apps/cli/main.py run "成都 AI Agent 2027 秋招"
```

运行后输出：

```text
run_id: <run_id>
status: success
report_path: data/reports/<run_id>.md
trace_path: data/runs/<run_id>/trace.json
llm_calls_path: data/runs/<run_id>/llm_calls.json
```

同时生成：

- `data/runs/<run_id>/state.json`
- `data/runs/<run_id>/trace.json`
- `data/runs/<run_id>/llm_calls.json`
- `data/reports/<run_id>.md`

默认使用 mock provider。可通过环境变量配置 OpenAI-compatible provider：

```bash
CAMPUS_AGENT_LLM_PROVIDER=openai_compatible \
OPENAI_BASE_URL="https://example.com/v1" \
OPENAI_MODEL="example-model" \
OPENAI_API_KEY="<local-secret>" \
python apps/cli/main.py run "成都 AI Agent 2027 秋招"
```

可用环境变量：

```text
CAMPUS_AGENT_LLM_PROVIDER=mock
OPENAI_API_KEY=
OPENAI_BASE_URL=
OPENAI_MODEL=
CAMPUS_AGENT_LLM_CACHE_ENABLED=true
CAMPUS_AGENT_LLM_FALLBACK_TO_RULE_PARSER=false
```

## 测试

```bash
pytest
```

v0.3 验收基线为 45 项测试全部通过。v0.4 全量验收为 68 项测试全部通过，其中 v0.1-v0.3 的 45 项回归全部保留通过；指标和限制见 `docs/07_evaluation/v0.4-eval-report.md`。

v0.5 离线验收为 72/72，通过后全量为 140/140。实际指标、Live Smoke 和真实 confirmed
官网身份链接结果见 `docs/07_evaluation/v0.5-eval-report.md`。
