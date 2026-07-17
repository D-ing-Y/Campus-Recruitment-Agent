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
- v0.5：岗位族、具体岗位、官方/社区冲突和过期信息案例。
- v0.8：带 relevance judgement、无答案问题和 citation gold 的检索集。
- v1.1：重复消息、worker 崩溃、对象写入失败和数据库冲突场景。

## 数据规则

- 真实个人信息必须脱敏或替换后才能成为 L1 fixture。
- 原始网页快照和提取文本分开保存，便于解析器重放。
- 每条 gold Claim、岗位要求和检索相关性标注应记录标注依据。
- v0.4 每个 fixture 还必须标注 gold `next_action`、高价值 gap、允许问题目标、最终状态和预期 snapshot 变化。
- interrupt fixture 的原始回答、response ID、request ID 与期望 Claim 必须分别保存，便于幂等和追溯测试。
