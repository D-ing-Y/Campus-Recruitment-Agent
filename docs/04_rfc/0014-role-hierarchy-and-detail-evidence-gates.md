# RFC-0014：岗位层级归属与详情证据门禁

状态：Accepted / Implemented Offline / Amended by RFC-0015
日期：2026-08-03
关联需求：`docs/03_requirements/v0.7.1-wp3-role-hierarchy-evidence-gates.md`

> 2026-08-06 修订：本 RFC 的多 SearchScope、岗位族归属、详情门禁和 scope link 继续有效；
> “官网确认位于默认主路径”和“全部经验进入统一 HiringSignal”的后续解释由 RFC-0015 取代。
> 新 run 以招聘平台岗位详情为默认 Demand 证据，并把面经与在职评价分流。

## 1. 决策摘要

复用 v0.5 的 `JobInstanceRoleProfile → RoleFamilyProfile` 两级模型，在 WP3 正式接线前增加四个
窄而确定的边界对象：`RoleTargetBinding`、`RoleFamilyMembership`、
`RoleDetailEvidenceReceipt` 和 `ExperienceScopeLink`。

## 2. 多目标方向拆分

CareerIntent 的每个 target role 保存 canonical family binding。投影时按 family 分组，每组建立
独立 SearchScope、fingerprint 和 Handoff。这样每个 family 的查询历史、样本、分母、时间窗口和
RoleFamilyProfile 都独立可重放。

旧快照兼容策略：缺失 binding 时重新运行版本化确定性映射；不信任旧
`target_role_families[0]` 的隐式绑定。

## 3. 岗位族归属

归属分为两步：

1. 根据岗位标题和受控别名得到 primary family/secondary tags；
2. 与当前 SearchScope family 比较，输出 accepted/ambiguous/rejected receipt。

归属 receipt 是聚合门禁，不改写原始岗位。后续可替换分类器，但相同 policy version 和输入必须
得到相同结果。LLM 可以在未来提出 candidate mapping，不能直接修改岗位族分母。

## 4. 详情证据门禁

SourceDocument kind 是详情资格的唯一事实入口。search result 只提供 URL 和候选字段；即使页面
内嵌较长描述，也不标记为 job detail。cluster 在投影前聚合所有 member 和 confirmed official job
的 SourceDocument，生成门禁 receipt。

门禁失败是业务 partial，不是 storage fatal：保留搜索 Artifact 和候选记录，继续其他岗位，最终
以 insufficient/unknown 报告缺口。

## 5. 经验作用域链接

ExperienceScopeLink 与 JobIdentityLink 目的不同：前者表达“这条社区信号适用于哪个范围”，后者
表达“两个招聘页面是否为同一个岗位”。scope link 使用 role family、company、role title 和唯一
cluster 候选生成 confirmed/ambiguous/rejected 状态。

只有 confirmed link 参与投影；ambiguous 内容仍可由用户查看原文，但不得进入具体岗位结论。

## 6. 持久化与兼容

- 新对象使用现有 additive JSON record repository，不新增专用数据库表。
- CareerIntent/confirmation 增加列表字段并保留 singular 字段只读兼容。
- 旧 RoleProfile snapshot 保持可读；新门禁只约束新 WP3 run。
- 所有 ID 使用 canonical payload 生成，重复运行零重复写入。

## 7. 失败语义

```text
ambiguous_role_family_mapping
role_family_mismatch
role_family_ambiguous
detail_evidence_missing
experience_scope_ambiguous
experience_scope_mismatch
```

这些 reason code 不允许被转换为“未找到岗位”或“岗位不适合用户”。
