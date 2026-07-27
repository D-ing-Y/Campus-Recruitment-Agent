# ADR-0007: 采用确定性准备优先级与证据优先反馈归因

## 状态

Implemented / Accepted

## 日期

2026-07-27

## 背景

v0.6 已帮助用户选择岗位，但“选择岗位”不能直接推出一份可靠学习路线。计划必须在多个目标、
岗位截止日期、用户容量、差距类型和招聘信号之间取舍。反馈又存在明显的因果与作用域风险：

- 完成任务不等于已掌握能力；
- 一次面试失败可能来自时间、表达、竞争、岗位关闭等多种原因；
- 一个岗位问过的问题不代表整个岗位族普遍考察；
- 用户偏好变化不应改写候选人能力；
- 模型生成的“优先级分”和失败解释通常不可重放。

## 决策

### 1. Preparation 与 Feedback 使用独立 subgraph

- PreparationPlanGraph 负责目标、活动、优先级、最小包、排期和计划审阅。
- FeedbackGraph 负责 raw 归档、observation、diagnosis、归因确认、Claim 和影响路由。
- 两者可以通过 plan/feedback/directive ID 串联，但不共享可变 State。

### 2. 采用 MinimumPreparationPackage，不追求 100% gap closure

- 计划优先解决当前窗口内可处理的 blocker、核心 gap、必要申请材料和有证据的招聘阶段练习。
- 容量不足时输出 partial 与 deferred，不降低门槛或假装完整。
- 不可改变的 hard qualification 输出 target review，不生成虚假学习任务。
- package status 只描述计划策略，不表示 Offer readiness。

### 3. Priority 与 schedule 由确定性代码负责

- 使用 P0-P4 band 和稳定排序 factors。
- factors 包括 selected target 数、岗位重要性、hiring signal、迁移价值、截止紧迫度、可提升性和成本。
- scheduler 校验 DAG、容量和截止日期。
- LLM 只建议活动拆解和说明，不能改变 priority、package 或 schedule。

### 4. 反馈必须 raw-before-interpret

- 文本、文件、分数、评价和 outcome 先保存 Artifact/Fragment。
- Observation 只表达原文；Diagnosis 显式标为推断并引用 Observation。
- 高影响归因需要用户确认；确认不提升原来源 authority。

### 5. 禁止从结果直接推断原因

- rejection/no offer/failed 但无明确评价时，只保存 outcome。
- task completed 只更新 progress。
- evaluator 明确评论可形成 diagnosis candidate，但不能单独确认整体能力等级。
- 所有 diagnosis 保存 alternative explanations 与 limitations。

### 6. 单次反馈不能直接改变岗位族画像

- job/company feedback 可形成 signal_only/experience signal。
- role family 只接收 aggregation candidate。
- 是否更新 common/frequent requirement 仍由 v0.5 独立来源、分母和聚合 policy 决定。

### 7. 跨域变化使用 directive 和 resume

- candidate/role/intent/matching 变化不由 FeedbackGraph 原地写入。
- Graph 输出 typed directive，并在 application service 完成已有边界后验证 resolved refs。
- 新 snapshot 使旧计划 stale；重新匹配/计划产生新版本并 supersede 历史。

## 备选方案

### 方案 A：让 LLM 一次生成完整学习路线和时间表

优点：实现快、文本自然。

缺点：容量和依赖不可验证，优先级不可重放，容易生成无依据资源。

结论：不采用。LLM 只能产生受约束 activity candidate。

### 方案 B：为每项活动计算一个综合成功分

优点：排序简单。

缺点：暗示虚假精度，容易被误解为 Offer 收益，掩盖 blocker 和不可处理项。

结论：不采用。使用 priority band 和公开 tuple。

### 方案 C：所有 gap 都进入计划直到关闭

优点：看起来完整。

缺点：无视时间容量和边际价值，也不符合秋招真实决策。

结论：不采用。使用 MinimumPreparationPackage。

### 方案 D：面试失败后让 LLM自动更新 CandidateProfile

优点：反馈闭环短。

缺点：把相关性当因果，绕过证据、用户确认和画像 projector。

结论：不采用。先归档、归因，再发 candidate rebuild directive。

### 方案 E：单个面试问题直接写入 RoleFamilyProfile

优点：画像更新快。

缺点：单公司/单岗位/单事件无法代表岗位族，造成过度泛化。

结论：不采用。只能形成 scope 明确的 signal 或 aggregation candidate。

### 方案 F：v0.7 直接实现完整 Parent Graph

优点：可以内部串联全部步骤。

缺点：扩大到 v1.0 的编排、事务与入口范围，难以隔离验证计划和反馈语义。

结论：不采用。使用 directive/resume saga 验证边界。

## 影响

### 收益

- 计划在真实容量和截止日期内可执行、可重放。
- 用户能看到为何活动优先、哪些被推迟、哪些无法解决。
- 反馈保留原文、推断和作用域，降低画像污染。
- v0.4-v0.6 既有边界可复用，v1.0 可直接消费 typed directive。
- 能真实展示一次反馈如何形成证据、版本变化和计划重排。

### 成本

- 需要两个 StateGraph、两个 repository namespace 和跨 run saga 测试。
- 需要维护 priority/package/scheduler/feedback impact policy version。
- 用户需确认高影响归因，交互步骤增加。
- 无 v0.8 RAG 时活动资源只能来自现有证据或通用模板。

### 约束

- plan priority 不是成功概率。
- raw-before-feedback-interpret。
- observation 与 diagnosis 分离。
- outcome 不自动产生 cause。
- progress 不自动升级能力。
- single feedback 不直接修改 role family。
- LLM 不控制 priority、schedule、causality 或 profile mutation。
- 所有跨域变化保留 immutable version chain。
