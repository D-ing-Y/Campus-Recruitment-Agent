# ADR-0011：使用开放原始标签与封闭 Candidate Taxonomy 双层表示

状态：Accepted  
日期：2026-07-30

## Context

真实简历证明自然语言经历标签和熟练度标签属于开放世界，而业务匹配、聚合和策略需要稳定枚举。
只保存原文无法可靠计算，只保存枚举会丢失未覆盖信息。

## Decision

1. 原文 `raw_label/raw_level` 永久保留；canonical enum 只用于确定性计算。
2. 经历使用 `kind + context` 两轴分类，不使用组合型枚举爆炸。
3. 新 LLM 输出使用 Pydantic discriminated union，将枚举暴露给 LangChain Tool Calling。
4. 无法可靠映射时使用 `other/unknown`，不得拒绝整段经历或默认成 `project`。
5. CandidateClaimValidator 和 ValidationReceipt 继续作为最终业务门禁。

## Consequences

- 下游可以稳定聚合 research/project/internship 等大类；
- 课设、毕设、纵向、横向、实习项目、工作项目等上下文不再互相覆盖；
- 新类别可以先以 `other + raw_label` 安全进入，再经文档化迁移升级 taxonomy；
- Tool Schema 更大，但可预测性和错误定位优于 `value: Any`。

