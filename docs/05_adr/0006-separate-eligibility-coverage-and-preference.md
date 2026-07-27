# ADR-0006: 分离资格、证据覆盖与偏好决策

## 状态

Accepted

## 日期

2026-07-22

## 背景

CandidateProfile、CareerIntent 和 RoleProfile 表达三类不同事实：候选人当前可证明的能力、
用户想要什么、岗位要求什么。把三者压缩成一个 LLM “匹配分”会造成以下问题：

- unknown 被误当作不具备；
- 硬性资格失败被高能力分掩盖；
- 用户偏好变化被错误写成候选人能力变化；
- 模型给出的数字无法重放、校准或解释；
- 新证据或新岗位 snapshot 到达后，旧结果无法正确失效。

v0.6 还需要承接用户的目标选择和纠正，但完整 Parent Graph 计划在 v1.0 实现。

## 决策

### 1. 资格、能力证据覆盖与偏好使用独立结果

- 硬性资格输出 `passed/failed/unknown/conflicted/not_applicable`。
- 能力要求逐项输出 evidence outcome，并计算带分子/分母的 coverage breakdown。
- CareerIntent 单独输出 hard/negotiable preference compatibility。
- 不发布能掩盖上述维度的单一综合分。

### 2. unknown 不等于失败

- 候选人画像缺少能力项只说明没有足够信息。
- 只有候选人和岗位双方都有可比较证据，且确定性规则确认不足时，才产生 capability gap。
- 声称具备但证明强度不足产生 evidence gap。
- 缺失、冲突、过期或无法映射产生 epistemic uncertainty。

### 3. 所有计算由确定性 policy 完成

- 资格 operator、能力等级、ontology mapping、权重、分子、分母、GapType、严重度和排序由代码负责。
- LLM 只解释已经编号的事实，并建议枚举动作。
- 模型不能修改比较事实，失败时使用确定性模板。

### 4. 以具体岗位为目标，岗位族只作上下文

- JobInstanceRoleProfile 承担资格判断、申请目标和 TargetDecision。
- RoleFamilyProfile 帮助解释岗位特异项、常见能力与迁移价值。
- 岗位族聚合不能覆盖具体岗位的官方要求，也不能单独产生“可投递”结论。

### 5. 比较采用稳定字典序，不采用加权总分

审阅顺序依次考虑：hard status、hard preference conflict、core evidence coverage、
uncertainty 和 stable job ID。failed/unknown 岗位仍保留并解释。

### 6. 比较、决策和画像均不可变版本化

- GapAssessment 固定引用 exact candidate/intent/role snapshot。
- ComparisonSet 固定引用 assessment 集合和 policy version。
- 输入发生变化时旧结果标 stale/superseded，生成新结果。
- TargetDecision 独立于 CareerIntent 和画像；改变决定通过 supersede 保存历史。

### 7. matching subgraph 只发出跨域重建指令

- 候选人纠正 → `candidate_profile_required`，由 v0.4 证据化重建。
- 普通偏好变化且 SearchScope 未变 → `rematch_required`。
- SearchScope 变化 → `role_research_required`，由 v0.5 重检索。
- 岗位过期、存疑或需核验 → `role_refresh_required`。
- v0.6 不直接修改 CandidateProfile/RoleProfile，也不提前实现 Parent Graph。

## 备选方案

### 方案 A：让 LLM 直接输出 0-100 匹配分

优点：实现快，展示简单。

缺点：不可重放，容易暗示 Offer 概率，资格/偏好/unknown 混在一起。

结论：不采用。

### 方案 B：所有维度加权成一个确定性总分

优点：比模型打分稳定，容易排序。

缺点：权重仍会掩盖硬性失败和偏好冲突，也让用户误解精度。

结论：不采用。保留多维明细和稳定字典序。

### 方案 C：CandidateProfile 没出现技能就判能力差距

优点：规则简单。

缺点：把证据缺失当作事实缺失，制造系统性假阴性。

结论：不采用。只有明确不足才是 capability gap。

### 方案 D：任何偏好变化都重新检索岗位

优点：路由简单。

缺点：重复调用不稳定外部来源，浪费预算；普通偏好变化不影响候选岗位集合。

结论：不采用。用 SearchScope hash 决定 rematch 或 role research。

### 方案 E：v0.6 直接串联并调用 v0.4/v0.5 subgraph

优点：看似能立即展示端到端回退。

缺点：提前引入 Parent State、跨图事务和恢复语义，扩大版本范围。

结论：不采用。v0.6 输出显式 RebuildDirective，v1.0 负责父图编排。

### 方案 F：允许用户直接编辑画像字段

优点：交互直接。

缺点：破坏证据链、版本历史和字段归属。

结论：不采用。候选人纠正进入 v0.4，意图变化创建 CareerIntent 新 snapshot。

## 影响

### 收益

- 用户能区分“明确不满足”“证明不足”“偏好不合”和“还不知道”。
- coverage 可以重放、测试并引用每项贡献。
- 偏好变化不会污染候选人能力事实。
- v0.4/v0.5 与 matching 的所有权边界清晰。
- v1.0 Parent Graph 可直接消费 RebuildDirective。

### 成本

- schema 与报告比单分复杂。
- 需要维护 qualification comparator、ontology relation 和 weight policy version。
- 客户端必须同时展示 coverage 与 uncertainty，不能只显示百分比。
- 需要 snapshot 失效和 supersede repository 语义。

### 约束

- 禁止 Offer 概率声明。
- 禁止 unknown-as-failure。
- 禁止 LLM 修改确定性事实。
- 禁止跨画像直接写入。
- 禁止原地修改历史 assessment/comparison/decision。
- 用户是目标选择的最终决策者。
