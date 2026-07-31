# ADR-0012：分离结构化简历证据与 CandidateProfile

状态：Accepted  
日期：2026-07-31

## Context

当前 PDF Fragment 直接生成 Candidate Claim。该设计无法完整保存通用简历字段，也让用户在画像生成后
才能发现解析错误。简历转录是否忠实与候选人能力判断是否充分是不同问题，不能共享同一个领域模型。

## Decision

1. 新增独立 ResumeEvidenceGraph 和 ResumeEvidenceSnapshot；
2. PDF Artifact 是不可变原件，ResumeEvidenceSnapshot 是经用户确认的结构化原始证据；
3. ResumeEvidence 阶段不创建 Claim、不评价置信度、不运行 Agent 信息缺口策略；
4. CandidateProfileGraph 只消费 confirmed Snapshot；
5. 用户在简历审核中只能纠正转录，新增事实进入后续 user_reported Evidence；
6. 个人信息本地提取并在模型边界脱敏；本版本不支持 OCR。

## Consequences

- PDF 解析、证据确认和画像分析各自拥有清晰验收标准；
- 增加一个 Graph、三类持久化对象和显式 CLI handoff；
- Candidate 首次入口产生破坏性 CLI 变化，但旧事实数据保持可读；
- WP1/WP2 需要以新 ResumeEvidence 重新运行真实验收，既有历史结果保留为旧契约证据。

## WP1.3.1 Amendment

真实 E4 证明“Fragment 存在”不等于“字段可精确定位”。因此发布门禁改为每个非空叶子都必须存在
字段级 span。同时，不可变 Snapshot 的纠错不走就地更新，而走显式 reparse 和新版本发布。
