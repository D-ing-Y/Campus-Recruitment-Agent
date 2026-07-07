# Roadmap

## v0：项目结构与开发流程

- 建立标准项目目录。
- 确认文档驱动开发流程。
- 归档旧 Skill 和旧爬虫资产。

## v0.1：Mini Agent Runtime

- 实现 LangGraph 最小闭环。
- 使用 mock tool 验证状态图、工具调用、校验和报告输出。

## v0.2：LLM Provider 与结构化输出

- 接入一个 API provider。
- 实现 JSON schema 输出、Pydantic 校验、重试和缓存。

## v0.3：Tool Registry 与 Evidence Store

- 统一工具接口。
- 实现本地证据存储和 evidence_id 追踪。

## v1.0：单 Agent 岗位检索闭环

- 接入招聘工具。
- 生成岗位表、技能词频和岗位分布报告。

## v2.0：Memory / RAG

- 引入向量化检索。
- 支持跨 run 的历史证据查询。

## v3.0：云服务器部署

- FastAPI 服务化。
- Docker Compose 部署。
- 云服务器持久化运行。

