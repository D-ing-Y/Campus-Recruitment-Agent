# Campus Job Agent

面向 2027 秋招的垂直领域 Job Intelligence Agent。

本项目采用文档驱动开发：先确认需求、RFC/ADR、接口契约和验收标准，再进入代码实现。

## 当前阶段

当前处于 `v0.2-llm-provider`：基于 LangGraph 的最小闭环 Agent Runtime，加上 LLM Provider 与结构化输出层。

v0.2 只实现：

- 单轮 CLI 运行。
- LangGraph 线性工作流。
- `parse_goal` 节点中的 LLM JSON 结构化目标解析。
- 默认 mock LLM provider。
- OpenAI-compatible Chat Completions provider 抽象。
- Pydantic 校验、一次结构化重试、本地 LLM cache。
- `ToolRegistry` 调用 `mock_job_search`。
- `state.json`、`trace.json`、`llm_calls.json` 和 Markdown report 输出。
- 单元测试、集成测试和 eval 测试。

v0.2 不接入真实招聘网站，不实现 RAG、Memory、多 Agent、Web UI 或服务器部署。默认运行不需要真实 API key。

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
