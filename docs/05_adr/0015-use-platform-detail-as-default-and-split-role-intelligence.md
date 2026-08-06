# ADR-0015：以招聘平台详情为默认岗位证据并拆分岗位情报投影

状态：Accepted
日期：2026-08-06

## Context

WP3 已实现岗位族归属、详情证据门禁和经验内容 scope link，但默认流程仍要求每个岗位经过企业官网确认，
并将面经、薪资和工作体验放在同一个 Role Profile 中。该结构既增加来源适配成本，也允许不同用途的
证据污染下游决策。

## Decision

1. 招聘平台具体 job detail Raw Artifact 是 Demand Profile 的默认充分来源；search card 不是。
2. 官方招聘页/ATS 作为可选证据升级，只在关键冲突、疑似过期、关键字段缺失或用户显式要求时触发。
3. 原始证据层保持共享，投影层拆为 Demand 与 Reputation。
4. 社区详情文档先归档，再分为 interview、employment、mixed 或 unknown；mixed 按引用片段拆分。
5. 面经只贡献 assessment signals；在职体验只贡献 job/company reputation。
6. 公司评价可以聚合不同岗位，但必须保留 role distribution、样本与冲突，禁止生成无依据综合分数。
7. Matching 只消费 Demand，Preparation 只消费 Demand 与 assessment signals，TargetDecision 和问答才可
   读取 Reputation。
8. 为控制体量，保留现有 RoleProfileGraph 外部入口，在其内部增加分流阶段，不立即复制两个完整 Graph。

## Preserved Decisions

- raw-before-parse、search-before-detail 和 append-only evidence 继续成立；
- RoleTargetBinding、RoleFamilyMembership、RoleDetailEvidenceReceipt 继续作为投影门禁；
- 每条结论必须追溯到具体 Artifact、Fragment 和定位引用；
- scope 不明确、用途不合法或结构校验失败的内容只保留，不投影。

## Consequences

正向影响：

- 默认闭环不再依赖逐公司 adapter，live 成本与失败面显著下降；
- 岗位能力差距、备考信号和主观评价具有清晰的数据边界；
- 公司评价可以复用跨岗位证据，同时不会冒充具体岗位体验；
- 用户询问具体 JD 时仍可回到原始详情与社区引用。

代价与风险：

- 招聘平台信息仍可能过期或冲突，因此必须保留 freshness、conflict 和可选升级状态；
- 社区 mixed 文档需要片段级拆分与 scope 校验；
- 旧 `hiring_signals` 不能无损自动迁移，只能历史只读。

## Rejected Alternatives

### 所有岗位强制官网二次确认

拒绝。质量收益不稳定，却使适配成本随公司数增长，并把平台已有的招聘详情重复采集。

### 完全删除官方来源

拒绝。高优先级岗位、平台冲突、过期和申请入口异常仍需要证据升级通道。

### 继续由一个 LLM 输出统一 Role Profile

拒绝。它无法通过类型系统保证面经、要求和在职评价只进入允许的消费者。

### 一篇社区文章只能选择一种文档类型

拒绝。真实帖子可能同时包含面试和入职体验；必须拆成带独立引用的片段，而不是丢失其中一类内容。
