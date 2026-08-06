# v0.7.1 版本入口：子 Workflow 纵向闭环与 CLI 加固

状态：Implementation In Progress（WP1/WP2 通过；WP3.1 设计修订 Ready for Implementation）
路线确认日期：2026-07-28  
代码版本：仍为 0.7.0；完成实现、测试和 Eval 后才升级 0.7.1

## 1. 版本结论

v0.7.1 的唯一目标是把现有 v0.3-v0.7 能力从“独立子图与离线测试已存在”推进到：

```text
可由正式 CLI 真实运行
+ 每个子图九层纵向闭环
+ 相邻子图通过 typed handoff 连通
+ 中断、恢复、幂等和失败可诊断
```

本版本不实现统一 LangGraph Parent Graph；v1.0 仍负责自动化父图编排和正式产品级完整 E2E。

## 2. 唯一事实源

本目录是 v0.7.1 导航与执行台账，不复制 canonical 设计。正式文档位于：

- Requirements：`docs/03_requirements/v0.7.1-vertical-workflow-closure-and-cli.md`
- WP1.1 Requirements：`docs/03_requirements/v0.7.1-wp1.1-langchain-integration-hardening.md`
- Tasks：`docs/03_requirements/v0.7.1-implementation-tasks.md`
- WP1.1 Tasks：`docs/03_requirements/v0.7.1-wp1.1-implementation-tasks.md`
- RFC：`docs/04_rfc/0008-cli-observability-and-vertical-workflow-closure.md`
- WP1.1 RFC：`docs/04_rfc/0009-standardize-model-tool-mcp-integration.md`
- ADR：`docs/05_adr/0008-adopt-cli-first-observable-workflow-hardening.md`
- WP1.1 ADR：`docs/05_adr/0009-adopt-langchain-integration-boundaries.md`
- Contract：`docs/06_contracts/cli-run-observability-contract.md`
- WP1.1 Contract：`docs/06_contracts/model-tool-mcp-integration-contract.md`
- Eval plan：`docs/07_evaluation/v0.7.1-eval-plan.md`
- WP1.1 Eval plan：`docs/07_evaluation/v0.7.1-wp1.1-eval-plan.md`
- WP1.1 Eval report：`docs/07_evaluation/v0.7.1-wp1.1-eval-report.md`
- WP1.2 经历 Taxonomy 补丁：`docs/03_requirements/v0.7.1-wp1.2-candidate-experience-taxonomy.md`、
  RFC-0011、ADR-0011、Candidate Profile Contract 与
  `docs/07_evaluation/v0.7.1-wp1.2-eval-report.md`
- WP1.3 结构化简历证据：Requirements/Tasks、RFC-0012、ADR-0012、
  `docs/06_contracts/resume-evidence-contract.md`、Eval plan 与阶段性 Eval report
- WP1.3.1 忠实度纠错：layout 解析、字段级 span、简化审核、reparse 版本链与真实 Draft 复验
- WP1.3.2 Claim 生命周期：当前 Resume basis、长期 overlay、显式 supersede 与增量投影
- WP2 文档包：`docs/03_requirements/v0.7.1-wp2-career-intent-intake.md`、RFC-0010、ADR-0010 与
  `docs/06_contracts/career-intent-contract.md`
- WP2 Eval report：`docs/07_evaluation/v0.7.1-wp2-eval-report.md`
- WP3 基础门禁：Requirements、RFC-0014、ADR-0014 与
  `docs/07_evaluation/v0.7.1-wp3-role-hierarchy-evidence-gates-eval-report.md`
- WP3.1 Demand/Reputation 分流：`docs/03_requirements/v0.7.1-wp3.1-role-demand-and-reputation-profiles.md`、
  RFC-0015、ADR-0015、`docs/06_contracts/role-demand-reputation-contract.md`、
  `docs/03_requirements/v0.7.1-wp3.1-implementation-tasks.md` 与
  `docs/07_evaluation/v0.7.1-wp3.1-eval-plan.md`
- 实际 Eval report：实现完成后新增 `docs/07_evaluation/v0.7.1-eval-report.md`
- 验收矩阵：`docs/09_versions/v0.7.1/workflow-acceptance-matrix.md`
- 执行日志：`docs/09_versions/v0.7.1/execution-log.md`

若本页与 canonical 文档冲突，以 Requirements → RFC/ADR → Contracts → Eval plan 的顺序解释，并先
修正文档，不通过实现静默选择。

## 3. 工作包顺序

| 工作包 | 内容 | 进入条件 | 退出条件 |
| --- | --- | --- | --- |
| WP0 | RuntimeFactory、RunSession、CLI、events、inspect | 文档门禁完成 | CLI 黑盒与可观测契约通过 |
| WP1 | Evidence + Candidate 真实材料/DeepSeek | WP0 可定位节点失败 | 支持 Claim 100% 可投影/追溯 |
| WP1.1 | LangChain Model/Tool/MCP 接入层规范化 | WP1 真实失败已定位 | 标准 provider/tool/MCP 协议与策略可诊断 |
| WP1.3 | ResumeEvidence 与 CandidateProfile 解耦 | WP1.2 历史验收完成 | confirmed ResumeEvidence typed handoff 与人工审核 |
| WP2 | CareerIntent 首次采集/确认/snapshot | Candidate 可用 | Passed；已产生 WP3 typed handoff |
| WP3 | Role 非本地岗位与社区来源 | Intent 已确认 | 平台 detail→Demand；社区 detail→typed Demand/Reputation；官网按需升级 |
| WP4 | Matching + TargetDecision | Candidate/Intent/Role current | 四类 Gap、解释、决策和 handoff 通过 |
| WP5 | Constraints + Preparation | selected decision | 最小包、排期和 review 可恢复 |
| WP6 | Feedback + HandoffDispatcher | accepted/current plan | raw-first、归因、successor、rematch/replan 通过 |
| WP7 | CLI 串联验收 | WP0-WP6 分别通过 | 同一 Session 真实链、恢复与重放通过 |

上一个工作包未通过，不得用下游 fixture 绕过。

## 4. 当前已知首要问题

1. 正式 Runtime/Session/doctor/inspect CLI 基座与 Candidate build/resume/show/diff 已完成；其他业务
   workflow 命令尚未接入，旧 `run` 仅作为明确标记的 `legacy-mini-runtime` 保留。
2. WP1.3 已将 PDF 转录与画像分析拆图；WP1.3.2 进一步隔离旧 Resume/legacy model Claim，保留
   conversation/feedback overlay。真实 CandidateSnapshot 与 WP2 typed-input 重验均已通过。
3. CareerIntent 已有首次生产入口；PreparationConstraints 仍没有首次生产入口。
4. Role live 必须要求真实 detail raw，搜索页不能冒充详情；招聘平台 job detail 可默认发布 Demand，
   官网只在冲突、过期、字段缺失或用户要求时升级。
5. 社区内容尚未实现 interview/employment typed segment 分流，旧混合 HiringSignal 只作兼容读取。
6. Matching/Preparation/Feedback 存在名义节点与真实业务边界不一致的问题。
7. Feedback 只有 Candidate 特例 saga，缺少通用 typed dispatcher。
8. Role 至 Feedback 子图仍需逐工作包接入统一节点日志、运行产物和 inspect。

## 5. 版本里程碑

### M0：文档完成

- Requirements/RFC/ADR/Contract/Tasks/Eval plan 均为 Ready for Implementation。
- Roadmap 和文档索引已同步。
- 不修改业务代码，不声称 v0.7.1 Implemented。

### M1：可诊断

- 正式 CLI 可从任意 cwd 调用。
- 公共 run/node/llm/evidence/claims/profile/handoff inspect 与失败事件基座已通过 WP0；各业务图节点的
  完整接线随 WP1-WP6 逐图验收。

### M2：双画像真实可用

- 真实 Candidate + confirmed Intent 完成。
- Role 真实来源完成或诚实记录外部阻塞。

### M3：决策与准备可用

- Matching/TargetDecision/Constraints/Preparation 使用真实上游产物闭环。

### M4：反馈与相邻交接连通

- Feedback 产生并单步消费 directive，完成 successor snapshot、rematch、replan。

### M5：版本验收

- WP0-WP7 验收矩阵通过。
- 全量回归、真实 smoke、隐私检查和 Eval report 完成。
- 此时才更新代码版本和文档状态。

## 6. 启动规则

每次开始一个工作包：

1. 读取该工作包关联的历史 Requirements/RFC/ADR/Contracts。
2. 在 execution log 记录基线 commit、命令、失败和证据路径。
3. 先写或更新失败测试/Eval case。
4. 做最小契约一致修复。
5. 运行 unit → integration → CLI → DeepSeek/live → replay/failure。
6. 更新 acceptance matrix；未通过项保持 failing/blocked/partial。
7. 发现跨模块缺口先回文档，不静默扩大实现。

## 7. 下一步

WP1.3.2 已完成 Claim 生命周期、增量投影、真实 Candidate 重建与 WP2 重验。下一步按 WP3.1
先实现招聘平台 detail→Demand，再实现社区 detail→typed segment→Demand/Reputation；官方来源只做
条件升级。仍不实现 v1.0 Parent Graph，也不把文档完成或 WP3 typed handoff 的产生表述为已消费。
