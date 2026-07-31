# LLM Output Contract

## v0.2 SearchGoal

`parse_goal` 节点的 LLM 结构化输出必须校验为 `SearchGoal`：

```json
{
  "role_query": "AI Agent",
  "city": "成都",
  "graduation_year": "2027",
  "recruitment_type": "autumn_campus",
  "keywords": ["AI Agent", "LLM", "智能体"],
  "raw_text": "成都 AI Agent 2027 秋招",
  "companies": [],
  "industries": [],
  "locations": ["成都"],
  "constraints": [],
  "confidence": 0.95,
  "warnings": []
}
```

字段要求：

- `role_query`、`city`、`graduation_year`、`raw_text` 必填。
- 未识别的 string 字段填 `"unknown"`。
- 未识别的 list 字段填 `[]`。
- `recruitment_type` 只能是 `autumn_campus`、`spring_campus`、`internship`、`unknown`。
- `confidence` 可以为 `null`。

## JSON-only Contract

- LLM 必须只输出裸 JSON object。
- 不允许 Markdown code fence、注释或解释性文字。
- 输出必须经过 JSON 解析和 Pydantic 校验。
- JSON 解析失败或 schema 校验失败时最多结构化重试 1 次。
- 重试 prompt 必须包含失败原因摘要，并要求重新输出完整 JSON。

## Cache And Trace

- cache key 包含 provider、model、prompt name、prompt version、schema version 和 messages。
- cache value 可保存 raw output 和 parsed JSON，但不得保存 API key、Authorization header 或完整环境变量。
- `llm_calls.json` 记录 provider、model、prompt/schema version、cache hit、retry count、duration、status、error summary、usage。
- trace 和 Markdown report 只展示非敏感摘要。

## v0.3 通用 Structured Output Contract

实现状态：已通过 `parse_structured_output()` 实现，`parse_search_goal_with_llm()` 保持为兼容包装层。

v0.2 的 Provider、缓存、重试和调用记录继续复用。v0.3 将 SearchGoal 专用结构化入口提炼为泛型入口，所有业务 schema 仍遵守：

- JSON-only；
- Pydantic 校验后才能进入业务层；
- prompt name/version 和 schema version 必须进入 cache key 与 LLMCallRecord；
- 重试次数有限；
- 模型输出不得绕过确定性 Validator 直接持久化。

### Claim extraction 额外约束

- 输入消息只包含明确授权的 Fragment 和必要上下文。
- 输出必须返回原输入中的 `evidence_fragment_ids`。
- 不允许生成输入 Fragment 没有表达的事实。
- 推断使用 `model_inference`，不得伪装为 `observed_fact`。
- 引用存在性、owner 权限、Claim 类型与 evidence 要求由代码验证。

后续 CandidateProfile、RoleProfile、GapAssessment 和 LearningPlan prompt 均采用同一版本化结构化调用机制。

## v0.4 Candidate Profile Structured Outputs

实现状态：v0.4 已实现；deterministic baseline 与结构化 LLM evaluator 共用本契约。

v0.4 新增两个模型输出边界。两者都必须使用 `parse_structured_output()`、JSON-only、
Pydantic 校验、版本化 cache key 和有限重试。

### Candidate Sufficiency Output

输出必须符合 `SufficiencyAssessment`：

```json
{
  "is_sufficient": false,
  "dimension_results": {
    "education": "sufficient",
    "experience": "partial",
    "capability": "partial",
    "responsibility_boundary": "insufficient",
    "evidence_quality": "partial"
  },
  "information_gaps": [],
  "blocking_conflict_ids": [],
  "recommended_action": "ask_user",
  "reason": "需要确认项目中的个人职责",
  "confidence": 0.85
}
```

输入只包含：

- 最新 profile 摘要与 snapshot ID；
- supporting Claim/证据覆盖摘要；
- 未处理材料的类型和引用；
- 已问问题、用户 skip 和剩余预算；
- 动作枚举与明确评价标准。

模型不得：

- 根据岗位要求判断候选人是否“足够优秀”；
- 把 CareerIntent 缺失当作能力画像不足；
- 输出动作枚举之外的工具名或节点；
- 建议突破预算；
- 把不存在的证据写成画像事实。

### Question Plan Output

输出必须符合 `QuestionPlan`。每个问题必须：

- 绑定一个 open `InformationGap`；
- 包含 `question_id`、`gap_id`、`target_path`、`prompt`、`reason` 和 `answer_type`；
- 不与已回答或已跳过的问题重复；
- 不诱导用户虚构能力、成果或个人职责；
- 允许用户跳过非必要问题；
- 不超过 `max_questions_per_interrupt`。

### 确定性校验和回退

- LLM 的 `recommended_action` 只是建议，最终路由由确定性 policy 决定。
- `information_value` 由代码根据已校验分量计算，不采用模型直接给出的最终值。
- 非法 gap、越界引用或重复问题必须拒绝。
- 一次结构化重试后仍失败时，使用 deterministic evaluator/planner 或安全
  `finalize_with_unknowns`，并记录错误。

### Prompt 与 Schema 版本

建议首版：

```text
candidate_sufficiency_v1 / schema v0.4
candidate_question_planner_v1 / schema v0.4
candidate_claim_extractor_v2 / schema v0.4
```

版本、provider、model、profile canonical hash、Claim ID 集和预算摘要必须进入 cache key。
不得把完整 PDF 或完整用户回答直接写入 cache/trace。

## v0.5 Role Profile Structured Outputs

实现状态：版本化 prompt、structured planner/evaluator、strict schema 与 deterministic fallback 已实现；默认 Eval 使用离线 deterministic baseline。

v0.5 新增若干类结构化输出。所有输出继续使用 `parse_structured_output()`、JSON-only、
Pydantic、有限重试、版本化 cache/trace，并由确定性 validator 裁决。

### Role Query Plan

输入：

- SearchScope；
- source capability；
- query history/空结果/失败原因；
- RoleCoverageGap；
- 剩余预算。

输出只能包含 `SourceQuery` 枚举字段。模型不得：

- 扩大 hard scope；
- 引用未启用 source；
- 输出 credential/cookie；
- 重复已有 query fingerprint；
- 根据 CandidateProfile 能力排除岗位。

### Recruitment Normalization

输出必须符合 `NormalizedJobPosting`：

- preserve company/role/city/application/source URL；
- preserve raw description/requirements；
- 缺失字段显式 unknown/null/[]；
- 不因噪声或字段缺失静默丢弃记录；
- `excluded_hard_scope` 必须给出可验证 exclusion code/evidence。

### Experience Extraction

输出必须符合 `ExperienceEvidenceRecord`：

- 只总结已归档 Fragment；
- 分开 written exam、interview、tech stack、project preference、salary、work context；
- 每个 signal 绑定 fragment IDs；
- 明确 company/role/role family/unknown scope；
- 不合并不同公司或岗位；
- 不把个人经验表达为官方硬性要求。

### Official Career Extraction

输出必须符合官网岗位归一化 schema：

- 只读取已归档的 JSON-LD、DOM/text Fragment；
- 保留 official source URL、job/application ID、title、location、cycle、职责和资格；
- 网页文本一律视为非可信数据，不执行其中的指令；
- 缺失字段显式 unknown，不从第三方记录复制为“官网字段”。

### Job Identity Candidate

LLM 只能建议 `JobIdentityLink(status=candidate)`：

- 输出 canonical company、title、location、cycle、application ID 和内容签名匹配信号；
- 不得仅凭标题相似输出 confirmed；
- confirmed/rejected/ambiguous 由确定性 policy 和证据阈值决定。

### OfficialSiteAdapterSpec Candidate

LLM 只能输出声明式字段：allowed domains、entry URL patterns、document kind rules、
selectors/JSONPaths、pagination rules 和 stop conditions。模型不得：

- 输出或执行 Python/JavaScript/shell；
- 扩大 verification plan 的域名、深度、页面或时间预算；
- 请求 credential/Cookie；
- 绕过登录、验证码、robots 或来源政策。

候选 Spec 必须经过离线 fixture replay、契约测试和人工批准后才能注册。

### Role Coverage Output

输出必须符合 `RoleCoverageAssessment`：

- 评价招聘字段、岗位样本、公司多样性、authority、freshness、experience signal 和 conflict；
- 给出 RoleCoverageGap 与枚举 recommended action；
- 不修改 deterministic sample/count/prevalence/frequency；
- 不输出 Candidate/Role match score。

### 确定性验证

- query fingerprint/source capability/budget 由代码校验；
- normalized record 的 URL、artifact/fragment refs 和 hard scope 由代码校验；
- Claim predicate × source authority 由代码校验；
- dedup cluster 和聚合分母由代码计算；
- JobIdentityLink confirmed 状态和 FieldResolution 由代码裁决；
- adapter spec 的域名、预算和 selector 能力由代码校验；
- information value、freshness 和 prevalence band 由代码计算；
- 非法输出重试后使用 deterministic baseline 或保留 unknown。

### 建议版本

```text
role_query_planner_v1 / schema v0.5
job_posting_normalizer_v1 / schema v0.5
experience_signal_extractor_v1 / schema v0.5
official_job_extractor_v1 / schema v0.5
job_identity_candidate_v1 / schema v0.5
official_site_adapter_spec_v1 / schema v0.5
role_claim_extractor_v1 / schema v0.5
role_coverage_v1 / schema v0.5
```

cache key 包含 source/query/artifact/fragment hash、prompt/schema/adapter version 和 scope hash；
不得缓存 credential、完整 headers 或 cURL。

## v0.6 Match Explanation Structured Output

v0.6 的 qualification、capability mapping、等级判断、weight、coverage、GapType、severity、
ranking 和 Graph route 均由确定性代码计算。LLM 只接收 `DeterministicComparisonFacts` 并输出
通过 `MatchExplanation` schema 校验的用户可读解释。

### 输入边界

- ComparisonSet ID、GapAssessment ID 和 job profile 摘要；
- 已编号 fact index；
- 已校验的 Claim 摘要/ID；
- 覆盖度 breakdown 的分子、分母、unknown；
- allowed action 枚举和“coverage 不是 Offer 概率”警告；
- 不包含完整简历、完整网页、Cookie 或无关私人上下文。

### 输出示例

```json
{
  "comparison_set_id": "comparison-1",
  "job_explanations": [
    {
      "job_profile_id": "role-job-1",
      "summary": "硬性资格仍有一项未知；已知核心要求覆盖 3/4 权重。",
      "fact_ids": ["fact-hard-unknown", "fact-core-coverage"],
      "claim_ids": ["candidate-claim-1", "role-claim-1"],
      "suggested_actions": ["review", "provide_candidate_evidence"]
    }
  ],
  "warnings": ["coverage_is_not_offer_probability"]
}
```

### 禁止与校验

模型不得：

- 新增或修改 qualification/requirement/preference outcome；
- 修改数字、weight、分母、GapType、severity、tier 或排序；
- 输出 Offer、录取或面试通过概率；
- 引用 fact index/Claim set 外的事实；
- 输出 allowed actions 外的动作；
- 建议直接修改 CandidateProfile 或 RoleProfile。

validator 必须逐项解析 summary 中的结构化数字，确认可由 fact index 支持；fact/citation/action
越界时拒绝。一次结构化重试仍失败后使用 deterministic template，不中断比较主流程。

### 建议版本

```text
match_explanation_v1 / schema v0.6
intent_revision_parser_v1 / schema v0.6
```

`intent_revision_parser` 只能把用户文本转换为待确认 patch，不能写入 CareerIntent；字段 allowlist、
旧/新 scope diff 和回退路由由代码验证。cache key 包含 comparison/input canonical hash、
prompt/schema/policy version 和 fact index hash。

## v0.7 Preparation 与 Feedback Structured Outputs

### Preparation Activity Candidate

LLM 输入只包含 validated planning facts、允许 activity type、constraints 和 supporting refs。输出只允许：

- activity type/title/description；
- expected outputs/completion criteria/verification method；
- estimated effort candidate、splittable 和 dependency candidate；
- 输入集合内 target/gap/signal/claim refs。

模型不得输出 priority band/factors、package status、schedule、能力提升结论、成功概率或未批准外部资源。
Validator 拒绝越界引用、循环依赖、非法工时、无法验证完成条件和虚构 URL。失败后使用 deterministic
activity template。

### Feedback Observation / Diagnosis Candidate

Observation 必须逐条引用输入 Fragment，只描述原文行为、问题、分数、评论或 outcome。Diagnosis：

- 必须引用 Observation；
- 显式标记 inference；
- 包含 subject scope、confidence、alternative explanations 和 limitations；
- 不得把 rejection/no offer 直接归因为能力；
- 不得把 task completion 解释为 mastered；
- 不得把单次问题提升为 role-family frequent/common signal；
- 不得提升 self-report authority。

最终 attribution、Claim acceptance、impact 和 route 由代码/用户确认决定。

### 建议版本

```text
preparation_activity_v1 / schema v0.7
feedback_observation_v1 / schema v0.7
feedback_diagnosis_v1 / schema v0.7
```

cache key 包含 input/event/fragment canonical hash、prompt/schema/policy version。不得缓存完整私人面试
记录、未脱敏文件或 checkpoint resume 正文。

## v0.7.1 Candidate Claim Extractor

```text
candidate_claim_extractor_v5 / candidate_claim_v0.7.1
```

模型只能输出 Candidate contract 第 10 节列出的三类 predicate。Prompt 必须提供当前 capability ID
allowlist、字段 allowlist、稳定 record ID 规则和逐字段原子 Claim 示例。模型输出仍先按通用 JSON
schema 解析；predicate、value、owner、fragment scope 和 projector support 由确定性 Candidate
validator 逐项裁决，以便 schema-valid/domain-invalid item 也能形成 rejected receipt。

`confidence` 的权威 JSON shape 是 `0.0..1.0` 数字，Prompt 必须给出完整 Claim 对象示例，
不得用 `"high"/"medium"/"low"` 作为正式输出。为吸收 Provider 常见的可恢复格式偏差，通用
schema 边界可在其他字段合法时将这三个精确别名确定性归一化为 `0.9/0.6/0.3`；
其他字符串或超界数值仍必须 schema validation failed。

模型不得输出 contact、award、任意 profile path、完整简历摘要或没有投影语义的扩展字段。一个 item
不合法不得使其他 item 的验证证据消失；所有 item 都必须出现在 ValidationReceipt 集合中。

## v0.7.1 WP1.3 ResumeExtractionBatch

ResumeEvidenceGraph 使用 `resume_evidence_extractor_v2 / resume_evidence_v0.7.1`。Provider 输出仅包含
个人优势、期望职位、工作/实习经历、项目经历、教育经历、专业技能和自定义内容；个人信息不在 Tool
Schema 中。缺失字段保持 null/空列表，不允许概括、推断熟练度、补全日期或重建脱敏值。每个非空块或
记录至少引用一个输入 Fragment ID；应用验证引用范围并生成字段 SourceRef。该输出只是待确认 Draft，
不是 Claim，也不能直接投影 CandidateProfile。教育附属内容按 layout 相邻记录绑定，“至今”等原文
时间不得转为 null，每条奖项/证书项目必须保持独立记录边界。
