# 文档目录

本目录是项目开发的事实来源。所有代码开发前，必须先在这里明确需求、设计、接口契约和验收标准。

当前项目从 v0.3 起采用“统一证据层 + 候选人画像 + 求职意图 + 岗位需求画像 + 反馈闭环”的架构。v0.4 候选人画像 subgraph、v0.5 岗位需求画像 Graph、v0.6 双画像匹配 Graph 与 v0.7 Preparation/Feedback Subgraphs 均已完成离线验收。v0.7 新增 116 项测试、全量 338 项通过，v0.1-v0.6 的 222 项回归全部保留；21 案例离线固定集达到既定门槛。当前活动版本 v0.7.1 进入 Ready for Implementation，目标是补齐正式 CLI、节点级可观测性、真实材料/来源语义验收和相邻 subgraph typed handoff，不提前实现 v1.0 Parent Graph。已完成版本文档作为历史记录保留，后续变化通过新版本 requirements、RFC 和 ADR 描述。

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
- `09_versions/`：活动版本的导航入口、验收矩阵和实际执行台账；不复制 canonical requirements/contracts。

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

当前 v0.5 已完成需求、RFC/ADR、任务拆解、contracts、代码实现、离线/集成测试、
eval 报告和实现后 opt-in live 验收。2026-07-22 起 BOSS 仅保留历史决策证据，核心招聘
发现来源改为智联招聘；智联、美团官网和牛客 raw-first 采集均已验证，智联候选到美团官网
同岗已形成 confirmed JobIdentityLink 与字段级 FieldResolution。版本状态已收口为 Implemented / Accepted。

v0.6 已完成实现与验收：硬性资格、能力证据覆盖、偏好兼容和认知不确定性分开表达；
确定性代码负责所有判定与数字，LLM 只解释；用户通过 interrupt 选择目标或请求纠正。
普通偏好变化只 rematch，只有 SearchScope 变化才请求 v0.5 重检索。实现任务、固定集和
实际指标见 `docs/03_requirements/v0.6-implementation-tasks.md` 与
`docs/07_evaluation/v0.6-eval-report.md`。

v0.7 已完成实现与验收：selected target 生成带依赖、容量和截止日期约束的最小准备包；反馈先
raw-before-interpret，再分离 observation、diagnosis 和 impact。无解释拒绝不推断能力原因，
单次反馈不改岗位族；跨域变化通过 typed directive 和后继 snapshot refs 恢复。实现任务与实际
指标见 `docs/03_requirements/v0.7-implementation-tasks.md` 和 `docs/07_evaluation/v0.7-eval-report.md`。

v0.7.1 正在实施。WP1/WP2 已通过；WP3.2 已实现 Demand 与 Job/Company Reputation 分流、招聘
detail 门禁、Brave 受控发现、Crawl4AI 批量详情、三层内容聚类和 MediaCrawler REST。招聘 L1
accepted；社区内容 L2 因 Brave 凭据被拒绝、牛客登录墙和 MediaCrawler 服务未启动保持 partial。
版本入口见 `docs/09_versions/v0.7.1/README.md`；不得把离线门禁表述为社区内容级 L2 accepted。

## Codex 协作边界

- 桌面端 Codex：维护 roadmap、requirements、RFC、ADR、contracts、eval 设计和验收标准。
- VSCode 端 Codex：按已确认文档实现源代码、测试和运行产物。
- 代码实现发现设计缺口时，先回到桌面端文档确认，不允许静默改变跨模块契约。
