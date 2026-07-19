# Glossary

- Agent Runtime：负责状态管理、图编排、工具调用、中断恢复和执行追踪的运行时。
- Parent Graph：串联候选人画像、岗位画像、匹配、决策、准备和反馈的顶层 LangGraph。
- Subgraph：具有独立状态边界和完成条件的可复用 Graph；不等同于 Sub-Agent。
- Tool：Agent 可调用的确定性或半确定性能力，例如文档解析、岗位检索、证据保存和向量检索。
- Evidence Artifact：不可变的原始材料载体，例如 PDF、网页 HTML、项目文件、JD 或面试反馈。
- Evidence Fragment：Artifact 中可精确引用的片段，包含页码、行号、选择器或文本范围。
- Evidence Claim：由一个或多个 Fragment 支撑的结构化事实、用户自述或模型推断。
- Provenance：证据和派生结论的来源、时间、解析器、模型、prompt/schema 版本及置信度。
- Evidence Store：保存 Artifact、Fragment、Claim、hash 和 provenance 的事实存储层。
- Candidate Profile：由证据派生的候选人能力、教育、科研、项目、实习和能力证明画像。
- Career Intent：与能力画像分离的岗位、城市、薪资、行业及硬性/可协商约束。
- Role Profile：具体岗位或岗位族的资格、工作能力、加分项和招聘筛选信号画像。
- Job Instance Role Profile：由一个去重后的具体岗位集群构建的画像，保留公司、地点、招聘周期、职责、资格、申请入口和公司特异项。
- Role Family Profile：由多个具体岗位画像和去重经验信号聚合的岗位族画像，必须展示样本、分母、公司覆盖、时间窗口和差异。
- Source Channel：外部证据采集通道；v0.5 区分 recruitment discovery、
  employer official verification 与 experience，三者不能共享业务输出 schema。
- Source Adapter：封装特定招聘发现、企业官网核验或经验来源查询、分页、授权、
  raw 归档和错误语义的可替换实现。
- Source Authority：字段级来源权限，决定某类来源可否确认某个 predicate；不是来源的单一总分。
- SourceRunReceipt：一次来源运行的非敏感收据，记录查询、数量、时间、adapter 版本、Artifact 引用和错误，不记录 Cookie/cURL。
- Search Scope：从 CareerIntent 派生的岗位检索硬范围，包括目标岗位、城市、毕业年份、招聘类型和显式 hard constraints。
- Job Posting Cluster：把跨平台重复招聘记录归为一个具体岗位统计单位，同时保留所有原始来源。
- Job Identity Link：连接第三方岗位 cluster 与企业官网岗位的可审计身份关系，保存匹配信号、
  证据、置信度和 confirmed/ambiguous/rejected 状态。
- Field Resolution：在身份链接成立后，以 predicate 为单位根据 authority、freshness 和
  冲突证据选择 resolved value 的派生记录；不会删除来源 Claim。
- Official Site Adapter Spec：未知企业官网的声明式采集候选，只能描述允许域名、入口、
  selectors/JSONPaths、分页和停止条件；通过离线验证和人工批准后才能注册。
- Raw-before-parse：任何网页或接口响应必须先以不可变 Artifact 归档，再进行解析、归一化或模型总结。
- Prevalence：某要求在去重岗位样本中的出现比例；必须同时展示分子、分母和公司覆盖。
- Freshness：根据发布时间、获取时间、截止时间和配置窗口计算的时效标签。
- Capability Ontology：候选人画像与岗位画像共享的能力概念、别名、层级和版本。
- Profile Snapshot：某一时间点由证据和 claim 构建的不可变画像版本。
- Sufficiency Assessment：评价当前候选人画像能否在明确未知边界下用于下一阶段，并给出分维度结果、信息缺口和建议动作；不等于岗位匹配评分。
- Information Gap：画像中需要补充、核验或保留未知的字段级缺口，包含重要性、不确定性、可回答性、证据成本和信息价值。
- Question Plan：把高价值 Information Gap 转换为有限、可回答、可跳过且不重复的问题集合。
- Profile Correction：用户对画像字段的纠正请求；必须转换为新 Claim 并关联被替代 Claim，不能直接覆盖画像。
- Information Value：用于选择下一步收集动作的排序信号，综合缺口重要性、不确定性、可回答性和证据成本。
- Gap Assessment：双画像比较结果，包括能力差距、证据差距、偏好冲突和认知不确定性。
- Memory：跨任务保留的事实、偏好和历史状态；不得用未追溯的聊天摘要替代证据。
- RAG：Retrieval-Augmented Generation，通过检索真实证据增强模型输出。
- Hybrid RAG：结合稀疏/全文检索、稠密向量检索、metadata filter 和 reranker 的检索方案。
- Checkpoint：LangGraph 在节点边界持久化的状态快照，用于恢复长任务。
- Interrupt：Graph 主动暂停并等待用户输入、确认、登录或判断的机制。
- Resume：使用相同 `thread_id` 和匹配的交互 request 恢复已中断 Graph；重复提交必须幂等。
- Credential Ref：指向本地秘密存储中登录材料的非敏感引用；Cookie、Authorization 和 cURL 原文不得进入 Graph State。
- Sub-Agent：由主 Agent 动态委派、具有隔离上下文和终止条件的工作单元。
- Distributed Storage：由元数据数据库、对象存储、向量存储等组成并支持多 worker 的持久层；不是单机 SQLite 的别名。
- Eval：评估 schema、证据追溯、检索、路由、匹配、恢复、成本和最终任务质量的体系。
- RFC：Request for Comments，重要功能或模块的设计提案。
- ADR：Architecture Decision Record，记录架构选择、替代方案和影响。
