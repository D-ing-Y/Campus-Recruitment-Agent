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
- v0.4：信息充分/不足、需要提问、用户纠正和保留 unknown 的候选人案例。
- v0.5：岗位族、具体岗位、官方/社区冲突和过期信息案例。
- v0.8：带 relevance judgement、无答案问题和 citation gold 的检索集。
- v1.1：重复消息、worker 崩溃、对象写入失败和数据库冲突场景。

## 数据规则

- 真实个人信息必须脱敏或替换后才能成为 L1 fixture。
- 原始网页快照和提取文本分开保存，便于解析器重放。
- 每条 gold Claim、岗位要求和检索相关性标注应记录标注依据。
