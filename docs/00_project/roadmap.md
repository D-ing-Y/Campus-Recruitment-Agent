# Roadmap

本文档是项目版本迭代的主索引。项目从 v0.3 起转向“证据驱动的双画像求职 Agent”：统一证据层是事实来源，候选人画像与岗位需求画像是可版本化的派生状态，LangGraph 负责长期闭环、条件路由、人工介入和恢复。

## 开发协作方式

- 桌面端 Codex：需求澄清、架构讨论、roadmap、requirements、RFC、ADR、contracts 和验收设计。
- VSCode 端 Codex：依据已确认文档实现代码、测试、eval 和运行产物。
- 历史版本文档不事后改写；架构变化通过新版本文档和 ADR 记录。
- 每项前沿技术必须承担真实业务职责并有可测验收标准，不做仅用于展示的装饰性接入。

## 版本总览

| 版本 | 状态 | 主题 | 核心交付 |
| --- | --- | --- | --- |
| v0 | 已完成 | 项目结构与开发流程 | 标准目录、文档驱动流程、旧资产归档 |
| v0.1 | 已完成 | Mini Agent Runtime | LangGraph 最小图、ToolRegistry、trace、report、测试 |
| v0.2 | 已完成 | LLM Provider 与结构化输出 | Provider、JSON/Pydantic、重试、缓存、LLM trace |
| v0.3 | 已完成 | 统一证据层与领域契约 | Evidence Store、Claim、Provenance、画像契约、能力本体 |
| v0.4 | 已完成 | 候选人画像 Graph | 文档摄取、画像构建、充分性评价、定向提问、interrupt/resume |
| v0.5 | 设计完成 / 待实现 | 岗位需求画像 Graph | 招聘/面经证据、岗位族与具体岗位画像、检索循环 |
| v0.6 | 计划中 | 双画像匹配与用户决策 | 四类差距、可解释匹配、偏好调整、回退与重检索 |
| v0.7 | 计划中 | 准备计划与反馈闭环 | 能力路线、练习/笔面试反馈、画像更新、动态重排 |
| v0.8 | 计划中 | Hybrid RAG 与长期记忆 | 稀疏+稠密检索、metadata filter、rerank、引用回答 |
| v1.0 | 计划中 | 单 Agent 端到端产品 | 父图、subgraph、checkpoint、interrupt、完整 eval |
| v1.1 | 计划中 | 分布式存储与异步执行 | PostgreSQL、对象存储、向量存储、队列、幂等与恢复 |
| v1.2 | 条件引入 | Multi-Agent / Sub-Agent | 动态并行研究、上下文隔离、主 Agent 综合 |
| v1.3 | 计划中 | 服务化与可观测部署 | FastAPI、容器化、追踪、指标、远程运行与安全边界 |

## v0.1 与 v0.2 的复用结论

### v0.1：可复用 Runtime 骨架

保留：

- LangGraph `StateGraph` 入口和节点化执行方式；
- `AgentState` 作为跨节点状态契约的设计；
- `ToolRegistry`、Executor、Verifier、trace helper、ReportWriter 和 CLI；
- mock tool、单元测试、集成测试和 eval 的组织方式。

需要升级：

- 线性五节点拓扑升级为父图、子图、条件边、循环和 interrupt；
- 扁平 `AgentState` 升级为证据、画像、差距、反馈和预算状态；
- 固定 `create_plan()` 升级为基于当前不确定性的动作决策；
- Verifier 从字段存在性检查升级为证据和完成条件评价。

### v0.2：可复用 LLM 基础设施

保留：

- `LLMProvider`、mock provider、OpenAI-compatible provider；
- JSON 解析、Pydantic 校验、有限重试、缓存；
- prompt/schema 版本和 `LLMCallRecord`；
- 密钥隔离和可测试性设计。

需要升级：

- `SearchGoal` 和 `goal_parser` 是早期业务样例，将逐步替换为 `CareerIntent`、`EvidenceClaim`、`CandidateProfile` 和 `RoleProfile` 等契约；
- structured output 层从单一 schema 函数升级为可复用的泛型结构化调用入口。

结论：在当前仓库内演进，不新建项目、不重写 Git 历史。

## v0.3：统一证据层与领域契约

状态：已完成（2026-07-15）。45 项单元、集成和 eval 测试全部通过，且 v0.1/v0.2 回归通过。

目标：

- 把原始材料设为事实来源，把画像设为证据派生的版本化视图。
- 建立后续所有 Graph、RAG、匹配和反馈共同依赖的数据契约。

核心交付：

- `EvidenceArtifact`：简历、论文、项目文件、JD、网页和反馈的原始载体。
- `EvidenceFragment`：带页码、行号、选择器或文本范围的可引用片段。
- `EvidenceClaim`：由片段支撑的结构化事实、用户自述或模型推断。
- `Provenance`：来源、获取时间、解析器、模型、prompt/schema 版本和置信度。
- `CandidateProfile`、`CareerIntent`、`RoleProfile`、`GapAssessment` 的首版 schema。
- `CapabilityOntology`：双画像共享的能力概念、别名和层级。
- SQLite + 本地文件的 Evidence Store，保留未来远程存储接口。
- 内容 hash 去重、不可变原始证据、画像 snapshot/version。
- 证据追溯 eval。

完成标准：

- 给定本地简历、项目说明和JD fixture，可归档原文、提取 claim，并从每条 claim 回溯到原始片段。
- 不带 evidence 引用的事实性画像字段不得进入确认画像；推断必须显式标记。
- v0.1/v0.2 回归测试继续通过。

## v0.4：候选人画像 Graph

状态：已完成（2026-07-18）。Requirements / ADR / RFC / Contracts、代码、真实本地 Tool、测试和 Eval 均已验收；68 项测试全部通过。

版本定位：

- 把 v0.1 LangGraph Runtime、v0.2 Structured Output 和 v0.3 Evidence Pipeline 接成第一个真实业务 subgraph。
- 固定业务边界由人定义，LLM 在动作枚举、证据约束和预算内判断高价值未知项及下一动作。
- checkpoint 保存执行状态，Evidence Store 保存事实；二者不得混用。

核心交付：

- 文本型 PDF、Markdown、TXT、项目 README 的真实本地摄取工具；扫描件和复杂版式显式返回 unsupported，不伪装为已解析。
- 候选人能力、经历、教育和能力证据画像。
- 独立 `CareerIntent`，保存岗位、城市、薪资、行业和硬性/可协商约束。
- `candidate_profile` subgraph：摄取、claim 提取、画像生成、充分性评价、继续读材料/提问/保留 unknown 的条件路由。
- `read_more`、`ask_user`、`request_more_materials`、`finalize_with_unknowns`、`complete` 动作枚举。
- LangGraph conditional edge、loop、SQLite checkpoint、human interrupt 和相同 thread resume。
- 用户回答、补充材料和纠正先作为新证据保存，画像可重建、可比较。
- 循环次数、单轮问题数、LLM 和 Tool 调用预算，以及确定性停止守卫。

本版本不包含：

- 岗位检索、岗位画像、双画像匹配和学习计划。
- OCR、完整代码仓库分析、RAG、分布式存储和 Multi-Agent。
- 外部 MCP 不是完成依赖；仓库内 Tool 必须真实完成本地解析、保存和恢复。

完成标准：

- 完成“上传材料→初始画像→发现高价值缺口→定向提问→回答证据化→更新画像”的可恢复闭环。
- 进程重启后可用相同 `thread_id` 从 SQLite checkpoint 恢复。
- 重复 resume 不产生重复 Artifact、Claim 或 ProfileSnapshot。
- 用户跳过、材料不可解析或预算耗尽时显式保留 unknown 并安全终止。
- v0.1-v0.3 回归通过，生成实际 v0.4 eval report 后才能标记已完成。

设计文档：

- `docs/03_requirements/v0.4-candidate-profile-graph.md`
- `docs/03_requirements/v0.4-implementation-tasks.md`
- `docs/04_rfc/0004-candidate-profile-graph.md`
- `docs/05_adr/0004-use-stateful-candidate-profile-subgraph.md`
- `docs/06_contracts/candidate-profile-contract.md`
- `docs/06_contracts/human-interaction-contract.md`

## v0.5：岗位需求画像 Graph

状态：Requirements / ADR / RFC / Contracts / Tasks / Eval Design 已按三段式来源链修订；
开源候选 P0 静态/离线门禁和三个 P1 浏览器来源 smoke 已完成（2026-07-19），
Ready for Implementation，待代码实现与 adapter 验收。

版本定位：

- 复用 v0.4 的 subgraph、checkpoint、预算和 interrupt 模式，构建第二个独立业务 Graph。
- 第三方招聘发现、企业官网核验和社区经验帖使用不同 channel、schema、authority 和覆盖评价。
- CareerIntent 决定搜索范围；CandidateProfile 不参与本版本的岗位排除或匹配。

核心交付：

- 第三方发现、企业官网核验和经验来源分离的采集/归档契约。
- `boss_jobs`、`official_careers` 和 `nowcoder_experience` 三个首版 live adapter；
  默认 CI 使用 fixture。
- raw-before-parse、SourceRunReceipt、query/source/batch 幂等和字段级来源权威校验。
- `JobIdentityLink` 与 `FieldResolution`：各来源分别证据化后再链接同一岗位并按字段消解。
- 开源项目准入报告与本地 Git 忽略参考代码目录。
- 具体岗位画像与岗位族画像。
- 硬性资格、职责、核心能力、加分项、工作场景、招聘筛选信号和公司特异项分层。
- 岗位族画像保留样本、分母、公司数、prevalence、时间窗口和 insufficient sample。
- `role_profile` subgraph：查询规划、发现、归档、去重、官网核验、身份链接、
  字段消解、画像聚合、覆盖度评价、换词/换源/停止。
- `search_more`、`change_query`、`change_source`、`verify_official`、`await_user_auth`、
  `finalize_with_unknowns`、`complete`、`fail` 动作枚举。
- 用户正常登录与本地 Copy as cURL/Cookie 导入；Graph 只保存 credential ref。
- 所有岗位事实保留 source URL、发布时间、获取时间和置信度。

本版本不包含：

- Candidate/Role 匹配百分比、能力差距、岗位排序和用户目标选择。
- RAG、向量检索、分布式存储、Multi-Agent 和自动投递。
- 验证码绕过、攻击性反爬或全平台覆盖。
- LLM 在运行时生成并执行新爬虫代码。

完成标准：

- 针对一个岗位方向生成有证据支持的岗位族画像和若干具体岗位画像。
- 第三方招聘、官网和经验 raw 均分别先归档；100% 事实性岗位字段可回溯到允许该
  predicate 的来源及字段消解原因。
- 跨平台重复岗位在岗位族分母中只计一次，所有来源仍保留。
- Graph 能换词、换源和在预算内停止；样本不足时诚实输出 insufficient sample。
- 授权来源可 interrupt/resume，Cookie/cURL 不进入 State、Evidence、trace 或 Git。
- 三个 live adapter 分别完成本地 opt-in smoke；至少一条第三方岗位完成官网身份链接和
  字段级核验；默认 CI 保持完全离线。
- v0.1-v0.4 回归通过并生成实际 v0.5 eval report 后才能标记完成。

设计文档：

- `docs/03_requirements/v0.5-role-profile-graph.md`
- `docs/03_requirements/v0.5-implementation-tasks.md`
- `docs/04_rfc/0005-role-profile-graph.md`
- `docs/05_adr/0005-separate-source-channels-and-role-profile-levels.md`
- `docs/06_contracts/source-collection-contract.md`
- `docs/06_contracts/role-profile-contract.md`
- `docs/07_evaluation/v0.5-source-feasibility-report.md`

## v0.6：双画像匹配与用户决策

核心交付：

- 硬性条件校验与能力覆盖度分离。
- 能力差距、证据差距、偏好冲突和认知不确定性四类结果。
- 由确定性代码计算的可解释覆盖度，LLM 负责证据解释而不直接拍分。
- 用户确认、画像纠正、目标选择和偏好调整 interrupt。
- 偏好变化只更新 `CareerIntent`，并触发岗位重检索，不错误修改候选人能力。

完成标准：

- 用户调整目标后，Graph 能回退并重建相关岗位画像，再次输出带证据的比较结果。

## v0.7：准备计划与反馈闭环

核心交付：

- 基于岗位重要性、差距、面试频率、迁移价值、可提升性和时间成本生成准备优先级。
- 最低可投递/可面试能力包，不追求画像 100% 相等。
- 练习、笔试、面试反馈作为证据事件进入系统。
- 诊断反馈影响候选人画像、具体岗位画像、岗位族画像或学习计划。
- 防止一次面试反馈过度改写岗位族模型。

完成标准：

- 一次反馈能够产生有依据的画像版本变化和学习计划重排。

## v0.8：Hybrid RAG 与长期记忆

目标：

- 让 RAG 成为画像构建和长期任务的证据检索基础设施，而不是通用问答装饰。

核心交付：

- 文档切分、embedding、向量索引和全文/稀疏检索。
- metadata filter：用户、证据类型、公司、岗位族、时间和可信度。
- dense + sparse hybrid retrieval、reranker 和引用组装。
- profile-aware retrieval：围绕当前未知项检索最相关证据。
- 跨 run 的事实记忆与用户偏好记忆分离。
- 检索命中率、引用正确率、groundedness 和无答案测试。

完成标准：

- 画像和计划中的每个事实性结论可引用检索证据；与无 RAG 基线相比有量化提升。

## v1.0：单 Agent 端到端产品

核心交付：

- 父图整合 candidate、role、matching、decision、preparation 和 feedback subgraph。
- durable checkpoint、interrupt/resume、失败恢复、循环预算和停止条件。
- 完整 trace、证据图、画像版本、差距报告和准备计划。
- CLI 或最小 Web 交互入口。
- 端到端 eval 数据集和回归报告。

完成标准：

- 一名用户可以从材料上传走到岗位选择、学习计划和反馈更新，且关键结论全部可追溯。

## v1.1：分布式存储与异步执行

目标：

- 用真实多服务架构验证大文件、长任务、多 worker 和恢复场景，而不是把单机数据库包装成“分布式”。

核心交付：

- PostgreSQL：任务、画像、claim、版本和事务元数据。
- S3/MinIO：不可变原始文件、解析产物和报告。
- pgvector 或独立 vector store：embedding 索引。
- Redis/队列：异步任务、状态通知、限流和短期缓存。
- Storage/Repository 抽象，支持本地实现与远程实现切换。
- 幂等键、去重、重试、补偿、并发控制和故障恢复测试。

完成标准：

- 多 worker 可安全处理同一任务族；服务重启后 Graph 和证据状态可恢复；重复消息不产生重复证据。

## v1.2：Multi-Agent / Sub-Agent（条件引入）

只在单 Agent eval 证明存在动态并行、上下文污染或长任务瓶颈时引入。

候选职责：

- 多来源岗位研究 worker；
- 经验帖证据研究 worker；
- 候选人材料分析 worker；
- 主 Agent 负责选择未知项、委派、校验和综合。

完成标准：

- 与单 Agent 基线比较质量、延迟、成本和失败率，证明拆分确实产生收益。

## v1.3：服务化与可观测部署

核心交付：

- FastAPI、Docker Compose、任务 API、文件上传和结果查询。
- OpenTelemetry + LangSmith/Langfuse 可选集成。
- 节点、LLM、工具、检索、存储和队列指标。
- 权限、密钥、Cookie、个人数据删除和审计边界。

完成标准：

- 桌面端可远程发起和恢复任务；系统能够定位一次错误来自模型、工具、检索、存储还是 Graph 路由。

## 跨版本强制要求

- 每个版本先有 requirements，重要设计先有 RFC/ADR，跨模块对象先更新 contracts。
- 原始证据不可被模型输出覆盖；解析器或 prompt 更新后可从原始材料重建派生数据。
- Eval 从 v0.3 起贯穿所有版本，不推迟到单独的“评估版本”。
- RAG、分布式存储和 Multi-Agent 都必须保留可比较的简单基线。
- 不绕过验证码或反爬；登录和 Cookie/cURL 导入必须由用户主动完成。
- 真实简历、Cookie、API key 和个人敏感数据不得进入 Git。
