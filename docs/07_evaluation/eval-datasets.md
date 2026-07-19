# Eval Datasets

## 数据等级

- L0 合成数据：schema、错误、边界和恢复测试。
- L1 匿名 fixture：简历、项目说明、JD、面经和反馈的稳定回归集。
- L2 公开来源快照：保留 source URL、发布时间、获取时间和使用说明。
- L3 私有真实数据：只在本地/受控环境使用，不进入 Git、报告样例或公开 trace。

## 版本数据集

- v0.1：mock 岗位与 Graph smoke 数据。
- v0.2：合法/非法 JSON、schema error、provider error 和 cache fixture。
- v0.3：匿名简历、项目说明、JD、重复文件、无引用 Claim 和冲突 Claim。
- v0.4：候选人画像 Graph 固定数据集：
  - `candidate_sufficient`：简历与 README 已覆盖教育、经历、职责和能力证据，无需中断。
  - `candidate_missing_responsibility`：项目存在但个人职责不清，应选择 `ask_user`。
  - `candidate_unprocessed_material`：已有未处理材料可补足缺口，应先 `read_more`。
  - `candidate_scanned_pdf`：PDF 无可提取文本，应请求支持格式或保留 unknown。
  - `candidate_conflicting_claims`：简历与用户自述冲突，应保留 `conflicted`。
  - `candidate_user_correction`：用户纠正职责范围，应生成 superseding Claim 和新 snapshot。
  - `candidate_user_skip`：用户跳过问题，应停止重复提问并保留 unknown。
  - `candidate_budget_exhausted`：连续缺口达到循环预算，应安全终止。
  - `candidate_resume_duplicate`：相同 response 重放，不得重复写入。
  - `candidate_checkpoint_restart`：重建 Graph 后使用同一 thread 恢复。
- v0.5：岗位需求画像 Graph 固定数据集：
  - `role_sufficient_sample`：至少 3 个去重岗位、2 家公司和招聘/经验证据，可完成岗位族画像。
  - `role_pagination_available`：当前 query 有 next cursor，应选择 `search_more`。
  - `role_low_relevance`：结果与目标岗位族相关性低，应选择 `change_query`。
  - `role_low_company_diversity`：岗位只来自一家企业，应选择 `change_source` 或继续补证。
  - `role_auth_required`：经验来源要求登录，应 interrupt；authorized/skip 分别有 gold route。
  - `role_cross_source_duplicate`：同一岗位跨来源出现，只计一个 job cluster。
  - `role_fuzzy_not_duplicate`：相似标题但公司/地点/招聘周期不同，不应错误合并。
  - `role_official_identity_confirmed`：第三方与官网为同一岗位，应建立 confirmed identity link。
  - `role_official_identity_ambiguous`：标题相似但招聘周期/职责不足，不得强行链接。
  - `role_official_field_conflict`：官网与第三方学历要求冲突，官网字段胜出且冲突 Claim 保留。
  - `role_official_salary_missing`：官网无薪资，第三方薪资保留为 third_party_only。
  - `role_official_not_found`：官网未找到岗位，不得自动删除或标记虚假。
  - `role_official_adapter_required`：通用解析链失败，保留 raw 并返回 adapter_required。
  - `role_web_prompt_injection`：网页指令不得扩大 Tool 域名、预算或触发代码执行。
  - `role_community_authority_violation`：面经声称“必须硕士”，不得创建 hard qualification。
  - `role_experience_scope_unknown`：帖子未明确公司/岗位，不能归到具体岗位。
  - `role_expired_job`：明确截止时间已过，岗位 snapshot 标 expired。
  - `role_source_changed`：页面结构不匹配，返回 source_changed 并换源/unknown。
  - `role_budget_exhausted`：连续空结果或失败达到预算，安全终止。
  - `role_source_batch_duplicate`：相同 batch 重放，不重复写入。
  - `role_checkpoint_restart`：重建 Graph 后从 collection/auth 边界恢复。
  - `role_raw_before_parse_failure`：raw 写入失败时 parser 不得执行。
- v0.8：带 relevance judgement、无答案问题和 citation gold 的检索集。
- v1.1：重复消息、worker 崩溃、对象写入失败和数据库冲突场景。

## 数据规则

- 真实个人信息必须脱敏或替换后才能成为 L1 fixture。
- 原始网页快照和提取文本分开保存，便于解析器重放。
- 每条 gold Claim、岗位要求和检索相关性标注应记录标注依据。
- v0.4 每个 fixture 还必须标注 gold `next_action`、高价值 gap、允许问题目标、最终状态和预期 snapshot 变化。
- interrupt fixture 的原始回答、response ID、request ID 与期望 Claim 必须分别保存，便于幂等和追溯测试。
- v0.5 每个 fixture 标注 source/channel/query、公开来源时间、gold normalized record、
  job cluster、identity link、field resolution、Claim authority、profile 字段、样本分母、
  freshness 和 next action。
- v0.5 live smoke 数据与固定集分开；默认测试不得依赖实时网页。
