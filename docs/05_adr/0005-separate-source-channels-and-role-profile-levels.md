# ADR-0005: 分离招聘与经验来源并构建两级岗位画像

## 状态

Accepted

## 日期

2026-07-18

## 背景

v0.4 已证明证据驱动、有状态 subgraph 的实现方式可行。岗位画像需要引入外部招聘网页和社区经验帖，但两类来源的权威范围、时效性、登录方式和结构化目标完全不同：

- 招聘/企业岗位页用于确认岗位存在、资格、职责、地点、申请入口和截止时间；
- 社区经验帖用于补充笔试、面试、项目偏好和实践信号。

同时，一个具体岗位的公司特异要求不能直接代表整个岗位族；岗位族画像必须显示样本、分母和差异。

## 决策

### 1. 招聘来源与经验来源使用独立 channel 和 adapter

- `recruitment` 与 `experience` 分别定义 raw、normalized 和 structured output schema。
- 两类 adapter 可以共享网络、归档、限速和错误基础设施，但不能共享业务输出模型。
- 任意模型总结前先归档原始 HTML/JSON/text。
- Source policy 决定某类来源允许支持哪些 predicate。

### 2. 采用 JobInstanceRoleProfile 与 RoleFamilyProfile 两级模型

- 具体岗位画像忠实保存公司、地点、招聘周期、职责、资格和公司特异项。
- 岗位族画像从去重后的具体岗位与经验 signal 聚合。
- prevalence、frequency、分母和公司覆盖由确定性代码计算。
- 样本不足时保留 `insufficient_sample`，不得把单个公司要求称为通用要求。

### 3. 岗位发现与候选人匹配分离

- v0.5 的检索范围来自 CareerIntent 和 hard scope。
- CandidateProfile 不参与岗位筛除或排序。
- 噪声但相关的记录保留到后续过滤/匹配。
- v0.6 再进行硬性条件、能力覆盖、偏好冲突和用户选择。

### 4. 首版采用一个真实招聘 adapter 和一个真实经验 adapter

- 招聘 adapter 目标：`zhaopin_jobs`，以智联校园为发现入口。
- 经验 adapter 目标：`nowcoder_experience`，以牛客讨论/面经内容为经验入口。
- 默认 CI 使用 fixture adapter；live smoke 由用户在本地显式启用。
- 站点需要登录时，用户在真实 Chrome 正常登录并本地导入 Copy as cURL/Cookie。
- 不使用 Playwright 或其他方式绕过登录、验证码和风控。

### 5. 凭据与 Graph 状态隔离

- Graph 只保存 `credential_ref` 和授权状态。
- Cookie、Authorization 和 cURL 原文只存在于 Git 忽略的本地 credential store。
- 授权使用结构化 interrupt/resume；用户可以跳过来源。
- live source 临时不可用不能导致 fixture 回归失败，也不能伪造 live 验收。

## 备选方案

### 方案 A：把招聘和面经统一为一个通用网页摘要

优点：schema 少，开发快。

缺点：社区传闻可能污染硬性资格，无法评价来源权威和时效，也难以独立测试。

结论：不采用。

### 方案 B：只构建岗位族画像

优点：输出简洁。

缺点：看不到公司、城市和岗位实例差异，聚合结论无法验证分母。

结论：不采用。

### 方案 C：只构建具体岗位画像

优点：事实最直接。

缺点：无法回答岗位方向的共同能力和变化范围，后续学习计划缺少岗位族层。

结论：不采用。

### 方案 D：让 LLM 直接搜索、去重并总结

优点：表面实现量小。

缺点：无法保证原始归档、重复计数、来源权威、停止条件和恢复幂等。

结论：不采用。LLM 只负责查询建议、结构化提取和解释。

### 方案 E：立即接入所有招聘和社区平台

优点：来源覆盖广。

缺点：反爬、登录、schema 和故障矩阵过大，难以证明核心闭环正确。

结论：不采用。v0.5 真实实现 1+1 adapter，其余先用 fixture 验证协议。

### 方案 F：以企业官网作为首版通用发现入口

优点：单条事实权威高。

缺点：公司站点结构高度碎片化，不适合未知公司范围的首版岗位发现。

结论：不作为核心发现源；用户指定公司或已有 URL 时用于验证与补充。

## 影响

### 收益

- 招聘事实和经验信号不会相互污染。
- 具体岗位与岗位族结论均可审计样本和分母。
- Graph 能真实展示查询规划、换词、换源、授权中断和持久化恢复。
- v0.6 可以直接比较 CandidateProfile 与两个层级的 RoleProfile。

### 成本

- 需要维护两类 adapter、schema、prompt 和 fixture。
- live source 结构变化、限流和登录会增加错误分支。
- 必须实现去重、来源权威、时效性和样本充分性评估。

### 约束

- raw-before-parse。
- community evidence 不得创建 hard requirement。
- 聚合计数由确定性代码负责。
- CandidateProfile 不参与 v0.5 检索过滤。
- live credential 不进入 Graph/Evidence/Git。
- 所有搜索循环有硬预算和停止条件。
