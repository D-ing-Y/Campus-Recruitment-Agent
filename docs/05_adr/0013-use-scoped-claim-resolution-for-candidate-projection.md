# ADR-0013：使用有范围的 Claim Resolution 构建 CandidateProfile

状态：Accepted  
日期：2026-08-02

## Context

EvidenceClaim 已支持 active/superseded 和人工单前驱纠正，但 Candidate 投影仍读取全部 active Claim。
这使证据世代、长期增量证据和代码重放不可区分。

## Decision

1. Claim 保持 append-first，增加最小来源、生效时间和多前驱 lineage；
2. 当前 Resume 是 Candidate 的基础证据范围，长期 conversation/feedback 是 overlay；
3. 独立纯确定性 resolution 负责选择、等价、精化和冲突，不交给 LLM；
4. 旧 Resume/legacy model Claim 保留但隔离，不删除、不猜测回填；
5. ProfileSnapshot 继续不可变并显式保存 evidence basis；
6. 跨来源不同值必须经 HITL，不按时间或 confidence 静默覆盖。

## Consequences

- 同一候选人可长期追加事实而不反复全量重建证据历史；
- Parser/Prompt 重放不会和旧派生结果并列污染当前画像；
- 增加一个小型 resolution 模块和兼容字段，不引入新数据库或服务；
- Feedback rebuild 与 Candidate 首次构建共享同一当前 Claim 选择规则。
