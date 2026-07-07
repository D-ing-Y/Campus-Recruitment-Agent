# Campus Job Agent

面向 2027 秋招的垂直领域 Job Intelligence Agent。

本项目采用文档驱动开发：先确认需求、RFC/ADR、接口契约和验收标准，再进入 VSCode 中的代码实现。

## 项目结构

- `docs/`：项目开发文档、架构、需求、RFC、ADR、契约、评估和部署说明。
- `src/`：Agent Runtime、工具层、schema、memory、workflow、eval 的代码实现。
- `apps/`：CLI、Web 或 Streamlit 等用户交互入口。
- `tests/`：单元测试、集成测试和 eval 测试。
- `scripts/`：开发、数据处理和评估脚本。
- `configs/`：本地配置模板。
- `data/`：本地运行数据、证据、缓存和报告。默认不提交真实数据。
- `reports/`：版本验收报告和评估报告。

## 当前阶段

当前处于 `v0`：项目结构和开发流程确认阶段。

下一步进入 `v0.1-mini-runtime`：

1. 确认版本需求文档。
2. 编写 RFC / ADR。
3. 拆分开发任务。
4. 在 VSCode 中实现 LangGraph 最小闭环。
5. 完成测试、eval 和验收报告。

