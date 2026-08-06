# RFC-0015：拆分岗位需求画像与评价画像 Workflow

状态：Accepted / Ready for Implementation
日期：2026-08-06
关联 Requirements：`docs/03_requirements/v0.7.1-wp3.1-role-demand-and-reputation-profiles.md`
关联 ADR：`docs/05_adr/0015-use-platform-detail-as-default-and-split-role-intelligence.md`
关联 Contract：`docs/06_contracts/role-demand-reputation-contract.md`

## 1. 决策摘要

WP3 的岗位情报不再由单一 Role Profile 混合承载。后续实现拆为共享证据层和两个独立投影：

- Demand：招聘详情中的岗位要求，以及招聘面经中的考察信号；
- Reputation：在职体验中的岗位评价和公司评价；
- RoleIntelligenceBundle：只负责把两个画像及其原始证据引用组合给问答、决策和展示层。

招聘平台具体岗位详情页成为 Demand 默认主证据。企业官网或官方 ATS 从强制主路径改为条件触发的
证据升级通道。search card、搜索结果页和社区摘要仍然只能发现候选详情，不得直接生成画像。

## 2. 为什么修改现有拓扑

强制逐公司进入招聘官网并不能与公司数量线性扩展，而且很多公司采用不同 ATS、登录和动态页面。
招聘平台详情由招聘方发布，足以作为有来源限制的岗位要求证据；真正需要保留的是详情页、时间、
冲突和过期语义，而不是无条件复制一次官网确认。

另一方面，面经中的“考了什么”和在职评价中的“工作强度如何”具有不同消费者。把它们放进同一个
`hiring_signals` 会使 Matching、Preparation 和公司选择互相污染。因此必须在原始证据归档后按
文档和片段用途分流。

## 3. 目标拓扑

```text
CareerIntent / SearchScope
  -> RecruitmentCollection
       search Raw -> platform job-detail Raw -> normalize
       -> family membership -> deduplicate -> CompanyRoleGroup
  -> CommunityCollection
       bounded queries -> post-detail Raw -> typed segments
       -> scope validation -> usage validation
  -> DemandProjection
       JD requirements + interview assessment signals
       -> JobDemandProfile / RoleFamilyDemandProfile
  -> ReputationProjection
       employment experience segments
       -> JobReputationProfile / CompanyReputationProfile
  -> RoleIntelligenceBundle
       -> Matching / TargetDecision / Preparation / Role Q&A
```

v0.7.1 保留公开的 `RoleProfileGraph` 名称和现有 CLI 入口以控制代码增量；图内部改成上述确定性阶段，
而不是为了概念拆分立刻复制两个完整 Graph。只有在后续运行、恢复或权限边界确实不同时，才考虑拆成
两个可独立启动的子图。

## 4. RecruitmentCollection

固定顺序为：

1. 依据每个 SearchScope 搜索并归档 search Raw Artifact；
2. 打开候选岗位的招聘平台详情，归档 detail Raw Artifact；
3. 解析 JobPosting，建立 JobIdentity 和 RoleFamilyMembership；
4. 去重后按 `company_key + role_family_id` 建立 CompanyRoleGroup；
5. 详情缺失、冲突、疑似过期或用户要求时，创建可选 OfficialVerificationPlan。

详情页不是永久权威来源。系统必须保留 `retrieved_at`、source kind、URL、平台岗位 ID、冲突和
可用性状态。没有 detail Raw 的 search result 不得进入 DemandProjection。

## 5. CommunityCollection

每个 CompanyRoleGroup 生成有界查询计划，按以下优先级执行：

1. company + exact role/job keywords；
2. company + role family；
3. company-only reputation；
4. generic role-family interview experience（仅在通用考察样本不足时）。

每个来源都必须执行 search -> detail -> Raw Artifact。详情文档先分类为 interview、employment、
mixed 或 unknown；mixed 必须按可定位 quote 拆成 typed segments。检索预算分别按 group、source、
query kind 和 detail count 限制，不能因公司岗位数量相乘而无界扩散。

## 6. 投影规则

### 6.1 DemandProjection

- `jd_requirements` 仅接受招聘详情或可选官方详情；
- `assessment_signals` 仅接受 interview segments；
- JD 普遍性和面经出现频率使用各自分母，不混合计算；
- 单篇面经只能形成 observed signal，不能升级成“必考”或 hard requirement；
- RoleFamilyDemandProfile 只合并已通过 RoleFamilyMembership 的岗位。

### 6.2 ReputationProjection

- job-scoped employment segment 可进入 JobReputationProfile；
- company-scoped segment 可进入 CompanyReputationProfile；
- company-only segment 不得下放到某个岗位；
- 公司聚合保留角色分布、样本数、独立来源数、时间窗和冲突；
- 不生成隐藏综合分数，分歧必须保留为 disputed。

## 7. 消费权限

| Consumer | 可读取 | 禁止读取为决策事实 |
| --- | --- | --- |
| Matching / GapAssessment | JD requirements | reputation、单篇面经 |
| Preparation | JD requirements、assessment signals | work intensity、atmosphere 等评价 |
| TargetDecision | Demand 匹配结果、Reputation 维度 | 将评价改写为岗位能力要求 |
| Role Q&A | RoleIntelligenceBundle 和引用 | 不区分来源类型的统一结论 |

## 8. 兼容策略

- 旧 RoleProfileSnapshot、旧 `hiring_signals` 和 ExperienceScopeLink 保持可读，不回写。
- 旧混合信号不通过猜测批量迁移；新 run 只发布新 Contract 对象。
- 现有 RoleTargetBinding、RoleFamilyMembership 和 detail evidence gate 继续使用。
- `employer_official` adapter 保留，但仅由 OfficialVerificationPlan 调用。

## 9. 明确失败状态

- `platform_detail_missing`
- `community_detail_missing`
- `community_segment_unknown`
- `community_segment_mixed_requires_split`
- `community_scope_ambiguous`
- `evidence_usage_violation`
- `official_escalation_unavailable`
- `reputation_sample_insufficient`

失败或样本不足不能伪装成画像已完成。Reputation 缺失不阻止 Demand 发布，但必须在 Bundle 中显式展示。

## 10. 实施顺序

1. 扩展来源详情能力与新 Contract；
2. 建立 CompanyRoleGroup、查询计划和社区 typed segment；
3. 实现两个确定性投影与消费权限校验；
4. 接入现有 RoleProfileGraph、CLI、持久化和可观测性；
5. 完成 fixture、集成和受控 live 验收后再收口 WP3。
