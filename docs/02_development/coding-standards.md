# Coding Standards

## Python

- 使用 Python 3.11+。
- 使用类型标注。
- 使用 Pydantic 定义跨模块数据结构。
- 业务 schema 放在 `src/campus_job_agent/schemas/`。
- Agent runtime 不直接依赖具体爬虫实现。
- 工具层通过统一 `ToolResult` 返回结果。

## 代码边界

- `agent/`：只负责运行时、状态图、规划、执行、校验和中断。
- `tools/`：负责外部能力和确定性动作。
- `memory/`：负责证据、数据库、向量检索。
- `workflows/`：负责业务阶段组合。
- `evals/`：负责评估逻辑。

## 禁止事项

- 不把 API key、cookie、真实个人数据写入 Git。
- 不在 Agent Runtime 中硬编码具体招聘网站逻辑。
- 不让 LLM 直接写数据库或文件，必须经过工具层。

