# 文档目录

本目录是项目开发的事实来源。所有代码开发前，必须先在这里明确需求、设计、接口契约和验收标准。

当前项目从 v0.3 起采用“统一证据层 + 候选人画像 + 求职意图 + 岗位需求画像 + 反馈闭环”的架构。v0.4 候选人画像 subgraph 已于 2026-07-18 完成代码、测试和 Eval 验收。v0.1-v0.3 文档作为已完成版本的历史记录保留，后续变化通过新版本 requirements、RFC 和 ADR 描述。

## 目录说明

- `00_project/`：项目目标、路线图、术语表。
- `01_architecture/`：总体架构、DeerFlow 参考、模块边界、runtime 设计。
- `02_development/`：开发流程、编码规范、Git 流程、测试策略、完成定义。
- `03_requirements/`：按版本维护的需求文档。
- `04_rfc/`：重要功能或模块的设计提案。
- `05_adr/`：架构决策记录。
- `06_contracts/`：状态、工具、证据、LLM 输出等接口契约。
- `07_evaluation/`：评估指标、评估数据和评估报告模板。
- `08_deployment/`：本地开发、云服务器部署、安全和密钥管理。

## 标准开发流

```text
需求确认
  -> 写版本需求文档
  -> 写 RFC 或 ADR
  -> 拆任务
  -> VSCode 实现
  -> 单元测试
  -> 集成测试
  -> eval 验证
  -> 文档更新
  -> 提交代码
```

当前 v0.4 已完成需求、RFC/ADR、任务拆解、contracts、实现、测试和 `v0.4-eval-report.md` 收口，版本状态为 Implemented。下一版本按 Roadmap 进入 v0.5 岗位需求画像 Graph。

## Codex 协作边界

- 桌面端 Codex：维护 roadmap、requirements、RFC、ADR、contracts、eval 设计和验收标准。
- VSCode 端 Codex：按已确认文档实现源代码、测试和运行产物。
- 代码实现发现设计缺口时，先回到桌面端文档确认，不允许静默改变跨模块契约。
