# Campus Job Agent

面向 2027 秋招的垂直领域 Job Intelligence Agent。

本项目采用文档驱动开发：先确认需求、RFC/ADR、接口契约和验收标准，再进入代码实现。

## 当前阶段

当前处于 `v0.1-mini-runtime`：基于 LangGraph 的最小闭环 Agent Runtime。

v0.1 只实现：

- 单轮 CLI 运行。
- LangGraph 线性工作流。
- 规则目标解析。
- `ToolRegistry` 调用 `mock_job_search`。
- `state.json`、`trace.json` 和 Markdown report 输出。
- 单元测试、集成测试和 eval 测试。

v0.1 不接入真实招聘网站，不接入真实 LLM API，不实现 RAG、Memory、多 Agent、Web UI 或服务器部署。

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

## 运行 v0.1 CLI

```bash
python apps/cli/main.py run "成都 AI Agent 2027 秋招"
```

运行后输出：

```text
run_id: <run_id>
status: success
report_path: data/reports/<run_id>.md
trace_path: data/runs/<run_id>/trace.json
```

同时生成：

- `data/runs/<run_id>/state.json`
- `data/runs/<run_id>/trace.json`
- `data/reports/<run_id>.md`

## 测试

```bash
pytest
```
