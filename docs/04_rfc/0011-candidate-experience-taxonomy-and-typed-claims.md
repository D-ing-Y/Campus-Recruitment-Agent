# RFC-0011：Candidate 经历 Taxonomy 与 Typed Claim Tool Schema

状态：Accepted  
日期：2026-07-30

## 1. 方案

新增 `candidate_taxonomy` 作为 Candidate Schema、Prompt、Validator 和 Projector 共享的单一枚举源。
`ExperienceKindValue` 同时保存 canonical `kind`、`context`、`raw_label` 和可选
`raw_context`。`CapabilityClaimValue` 同时保存 canonical `level`、技能原始标签和可选
`raw_level`。

LLM 边界不再暴露 `ExtractedClaim(predicate: str, value: Any)`，而是暴露 Pydantic
discriminated union。Pydantic 官方推荐 tagged/discriminated union 来提高 union 校验的可预测性，
且其 JSON Schema 会生成 discriminator；LangChain ToolStrategy 支持 Pydantic、JSON Schema 和
Union，并能把 `Literal` 输出为 enum。

## 2. 为什么不是扩大一个 kind 枚举

单轴枚举无法同时表达“这是一项项目”和“它发生在课程、毕设、纵向课题、横向合作、实习或工作中”。
不断加入 `internship_project`、`employment_project` 会产生组合爆炸，也会让下游匹配难以稳定聚合。

两轴模型保持：

- `kind` 回答“这类经历是什么”；
- `context` 回答“它发生在哪里/由什么场景产生”；
- `raw_label` 回答“用户原文怎么写”；
- responsibilities/outputs/results 回答“用户实际做了什么、产生了什么结果”。

## 3. 兼容迁移

- 旧 `experience.kind="project"` 在解析/Validator/Projector 中继续有效，并投影为
  `kind=project, context=unspecified, raw_kind_label=project`；
- 旧中文 kind 或未知 kind 由确定性 normalizer 映射；不能可靠映射时使用 `other`，不丢弃；
- 旧 capability `{"level":"proficient"}` 保留 `raw_level=proficient` 并降为
  `level=unknown`，避免把含义不确定的熟练度强行升级为 advanced；
- 当前 versioned CapabilityOntology 的 `capability_id` 同步进入 Pydantic Tool enum；新 Claim
  同时校验 source-faithful `raw_label` 的 ontology resolution，禁止把 C++、Java、Pandas 等
  未建模技能强塞进 `engineering.backend` 或 `programming.python`；
- 新 Prompt/schema version 与旧 cache 隔离。

## 4. 拒绝的替代方案

### 只改 Prompt

拒绝。Prompt 约束不会出现在 Tool JSON Schema 中，无法修复 `value: Any` 根因。

### 将所有中文标签硬映射到现有五类

拒绝。无法表达 employment、leadership、volunteering、teaching 等常见招聘经历，且会把未知内容
错误压成 project。

### Validator 拒绝所有未知值

拒绝。安全但会丢失用户信息。新方案用 canonical fallback + raw label 保留开放世界输入。

### 删除领域 Validator，只信 Pydantic

拒绝。Pydantic 不验证 evidence owner、fragment 是否属于本次材料、ontology 和事实真实性。

## 5. 调研来源

- MIT CAPD Resumes：https://capd.mit.edu/resources/resumes/
- MIT Resume Checklist：https://capd.mit.edu/resources/resume-checklist/
- Stanford Resume Examples：https://careered.stanford.edu/sites/g/files/sbiybj22801/files/media/file/resume-and-cover-letter-examples.pdf
- Yale Telling Your Story：https://cdn.ocs.yale.edu/wp-content/uploads/sites/77/2020/05/Telling-Your-Story_Interviewing-or-Networking-Worksheet_All-populations.pdf
- Europass Profile：https://europass.europa.eu/en/europass-tools/europass-profile
- 中国大学生在线《求职就业简历基本构成》：https://dxs.moe.gov.cn/zx/a/jobs_syzd/220314/1749650.shtml
- LangChain Structured Output：https://docs.langchain.com/oss/python/langchain/structured-output
- Pydantic Discriminated Unions：https://docs.pydantic.dev/latest/concepts/unions/#discriminated-unions
