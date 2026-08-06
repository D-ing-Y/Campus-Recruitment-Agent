# ADR-0014：保留两级岗位画像并增加归属与详情门禁

## 状态

Accepted / Amended by ADR-0015

## 日期

2026-08-03

2026-08-06 修订：两级岗位范围、family membership 和 detail gate 仍保留；新 run 不再将所有社区内容
投影到统一 HiringSignal，也不再强制每个岗位经过官网确认。画像分流与来源优先级见 ADR-0015。

## 背景

用户需要同时理解岗位大类的普遍要求和具体 JD 的差异，并能在选择具体岗位后回到招聘详情及
社区经验原文。现有两级 RoleProfile 可以表达这一关系，但缺少多 family 拆分、family membership、
detail evidence 和 experience scope 的强制边界。

## 决策

1. 保留 `JobInstanceRoleProfile → RoleFamilyProfile`，不增加第三层持久化画像。
2. 每个 CareerIntent target role 保存 RoleTargetBinding，并按 family 启动独立 SearchScope。
3. 以 RoleFamilyMembership 控制岗位族分母，原始岗位记录不可被分类器覆盖。
4. 具体岗位画像必须通过 RoleDetailEvidenceReceipt；搜索页不能满足门禁。
5. 社区内容必须通过 ExperienceScopeLink 后才能进入具体岗位或岗位族 HiringSignal。
6. 用户偏好、目标排序和准备顺序继续由 CareerIntent、Matching、TargetDecision 和 PreparationPlan
   负责，不写回客观岗位画像。

## 备选方案

### 直接让 LLM 合并相似岗位

拒绝。无法稳定控制分母、复算归属或解释为什么某个岗位进入大类。

### 建立完整岗位知识图谱

暂不采用。当前问题只需要两级画像、版本化 family mapping 和显式 link；图数据库会扩大代码和
运维范围。

### 搜索页描述足够长就视为详情

拒绝。页面字段可能是卡片摘要、缓存或拼接数据，不能替代可定位的具体岗位详情 Artifact。

### 未确认经验帖也挂到最相似岗位

拒绝。社区内容的作用域不确定时必须保留 ambiguous，不能污染具体 JD 回答。

## 影响

- 岗位族统计分母可审计且不会跨求职方向混合。
- 具体岗位回答可保证至少存在真实详情证据。
- 社媒信号可按 family/company/job 范围使用，不会自动升级为 hard requirement。
- 首次 WP3 live run 可能因缺少详情页而得到更少画像；这是正确的证据门禁结果。
