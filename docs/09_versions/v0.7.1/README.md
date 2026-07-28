# v0.7.1 版本入口：子 Workflow 纵向闭环与 CLI 加固

状态：Ready for Implementation（WP0 Passed；WP1 Not Started）  
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
- Tasks：`docs/03_requirements/v0.7.1-implementation-tasks.md`
- RFC：`docs/04_rfc/0008-cli-observability-and-vertical-workflow-closure.md`
- ADR：`docs/05_adr/0008-adopt-cli-first-observable-workflow-hardening.md`
- Contract：`docs/06_contracts/cli-run-observability-contract.md`
- Eval plan：`docs/07_evaluation/v0.7.1-eval-plan.md`
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
| WP2 | CareerIntent 首次采集/确认/snapshot | Candidate 可用 | confirmed Intent 可投影 SearchScope |
| WP3 | Role 非本地 JD 真实来源 | Intent 已确认 | search→detail raw→official status→Profile 或诚实阻塞 |
| WP4 | Matching + TargetDecision | Candidate/Intent/Role current | 四类 Gap、解释、决策和 handoff 通过 |
| WP5 | Constraints + Preparation | selected decision | 最小包、排期和 review 可恢复 |
| WP6 | Feedback + HandoffDispatcher | accepted/current plan | raw-first、归因、successor、rematch/replan 通过 |
| WP7 | CLI 串联验收 | WP0-WP6 分别通过 | 同一 Session 真实链、恢复与重放通过 |

上一个工作包未通过，不得用下游 fixture 绕过。

## 4. 当前已知首要问题

1. 正式 Runtime/Session/doctor/inspect CLI 基座已完成；业务 workflow 命令尚未接入，旧 `run` 仅作为
   明确标记的 `legacy-mini-runtime` 保留。
2. Candidate predicate 在 Prompt、Schema、Validator 和 Projector 之间不一致。
3. Claim batch 需要明确原子/逐项 receipt，避免不可解释半成功。
4. CareerIntent 和 PreparationConstraints 没有首次生产入口。
5. Role live 必须要求真实 detail raw，搜索页不能冒充详情。
6. Matching/Preparation/Feedback 存在名义节点与真实业务边界不一致的问题。
7. Feedback 只有 Candidate 特例 saga，缺少通用 typed dispatcher。
8. 业务子图没有统一节点日志、运行产物和 inspect。

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

WP0 已通过。下一步进入 WP1 前先增量更新 Candidate predicate、receipt 和 batch contracts，再修复
`extract_and_validate_claims` 并以真实材料/DeepSeek semantic smoke 验收。不得先开发 Parent Graph，
也不得把 WP0 诊断 session 或旧专项 runner 当成正式业务闭环。
