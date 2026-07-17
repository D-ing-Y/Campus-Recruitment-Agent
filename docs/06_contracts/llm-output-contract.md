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

实现状态：Design Accepted / Pending Implementation。

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
