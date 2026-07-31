# RFC-0012：结构化简历证据 Workflow

状态：Accepted  
日期：2026-07-31

## 1. 方案

新增 ResumeEvidenceGraph，负责从单份 PDF 生成经用户确认的结构化证据快照。Graph State 只保存 ID、
审核游标、状态和安全诊断；PDF 正文、Draft、Review Receipt 与 Snapshot 通过 Repository 读取。

```text
archive_pdf → extract_text → assess_quality → build_draft → validate_schema
            → select_review_target → interrupt → apply_review ─┐
                                      └─ all confirmed → finalize_snapshot
```

Graph 不运行充分性评价，不选择高价值问题，不创建 Candidate Claim。审核顺序由固定 section order 和
record order 决定，属于确定性 workflow，不属于 Agent 决策。

## 2. 数据表示

- `ResumeDraft`：带 CAS revision 的待审核结构；
- `ResumeSectionStatus`：pending、confirmed、corrected、confirmed_empty；
- `ResumeSourceRef`：Artifact/Fragment/page/hash/span；
- `field_sources`：应用根据 typed extractor 输出建立的 JSON Pointer 到 SourceRef 映射；
- `ResumeReviewReceipt`：append-only response、before/after hash、审核目标和结果；
- `ResumeEvidenceSnapshot`：全部审核完成后的不可变版本。

期望职位保存在证据中，但 WP1.3 不自动创建 CareerIntent。个人优势与专业技能分别保真，Candidate
分析时可以联合使用。自定义区块保存原始标题与宽松类型，避免再次用封闭枚举丢弃简历内容。

## 3. 解析与模型边界

pypdf 结果满足以下默认条件时直接使用：非空白字符不少于 100、非空页面比例不少于 0.8、替换符和
非法控制字符比例不高于 0.02。否则运行 pdfplumber 并选择通过门禁且质量分更高的结果；两者均失败
则停止，不生成 Draft。

本地 personal-info extractor 先抽取并脱敏个人信息。DeepSeek 接收脱敏后的 Fragment，使用
`ResumeExtractionBatch` Tool Schema 输出允许的区块和 Fragment 引用；不得概括、推断或填补缺失值。
应用负责 record ID、JSON Pointer 和 SourceRef，模型不能输出任意持久化路径。

## 4. 审核与幂等

简单区块一次审核；列表区块按记录审核，记录结束后增加 section-complete 审核目标。用户可以确认、
纠正、删除误提取记录、要求重新提取或取消。重复 response ID + 相同 payload 返回既有结果；同 ID
不同 payload 返回 idempotency_conflict。中断前不做非幂等写入，成功归档 response 后清除正文。

## 5. Candidate 迁移

CandidateProfileGraph 初始路径改为加载 confirmed ResumeEvidenceSnapshot，然后从结构化证据生成
Candidate Claim。简历原文事实使用 user_reported，分析性结论使用 model_inference；Claim 增加可选
`source_evidence_ids`。其余 Validator、Projector、Sufficiency 和后续画像 HITL 保留。

旧数据库只做 additive migration。旧 `candidate build --input` 不兼容；旧运行可 inspect，但旧
checkpoint 不能继续写入新 Graph，必须明确返回 legacy_session_incompatible。

## 6. 拒绝的替代方案

- 继续扩张 Claim Prompt：仍然混合证据转录和画像语义；
- PDF 后直接生成画像再让用户确认：无法证明结构化原始证据曾被确认；
- 全字段逐项确认：CLI 成本过高；
- 本次加入 OCR：增加本地运行时和跨平台交付范围，留待后续独立工作包。

## 7. WP1.3.1 纠错修订

- Resume PDF 使用 pypdf layout 文本作为主解析结果，避免 content-stream 顺序破坏视觉分组。
- SourceRef 必须是字段级原文 span；整页 Fragment 只能作为 PDF 页导航，不能通过发布门禁。
- 非空列表在所有当前记录完成后由应用自动设置 section status；空列表保留一次确认。
- 显式 reparse 创建新 Draft 和 version+1 Snapshot，不修改旧证据对象。
