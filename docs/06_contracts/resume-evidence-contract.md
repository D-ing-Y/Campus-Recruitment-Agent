# Resume Evidence Contract v0.7.1

## 1. Section order

```text
personal_information
personal_advantage
career_expectations
work_experiences
project_experiences
education_experiences
professional_skills
custom_sections
```

标准区块容器必须存在；没有内容时保存空值并由用户确认 `confirmed_empty`。

## 2. ResumeDraft

Draft 包含 draft_id、owner_id、candidate_id、artifact_id、revision、status、parser diagnostics、八个标准
区块、field_sources、section_statuses、review_receipt_ids 和时间戳。Draft 可以通过 CAS 更新，但不能被
CandidateProfileGraph 读取。

字段结构：

- personal_information：姓名、性别、出生年月、求职状态、身份、电话、微信、邮箱、出生地；
- personal_advantage / professional_skills：保留原始段落；
- career_expectations：求职类型、职位、薪资、城市；
- work_experiences：单位、职位、角色、起止时间、正文；
- project_experiences：名称、角色、起止时间、原始子类型、正文；
- education_experiences：学校、学历、专业、起止时间、课程或研究方向；
- custom_sections：原始标题、宽松类型、名称、角色、起止时间、正文。

## 3. SourceRef

每个非空叶子字段必须可从 `field_sources[json_pointer]` 取得一个或多个 SourceRef：

```json
{
  "artifact_id": "...",
  "fragment_id": "...",
  "page_number": 1,
  "text_hash": "sha256",
  "start_offset": 0,
  "end_offset": 20
}
```

JSON Pointer 由应用根据 typed 字段位置生成；模型只返回允许的 Fragment ID。
`start_offset/end_offset` 必须覆盖当前叶子在 Fragment 中的归一化匹配范围，不得用整页范围补齐。

## 4. Review

`ResumeReviewRequest` 必须绑定 request、thread、run、owner、candidate、draft revision 和 review key。
`ResumeReviewResponse` 必须匹配 request/identity，correct patch 只能修改当前 review target。Review Receipt
append-only 保存 before/after hash、action、result revision 和响应 Artifact/Fragment 引用。

全部 review target 完成后才允许发布。非空列表的记录逐条完成后由应用自动确认区块，不再
追加重复的 section-complete 交互；空列表必须显式确认 `confirmed_empty`。

## 5. Snapshot and handoff

ResumeEvidenceSnapshot 不可变，保存 final structured data、field_sources、review receipt IDs、来源 Artifact
和 parser provenance。Candidate build 必须验证：status=confirmed、owner/user 一致、candidate_id 一致。

Resume 派生 Claim 必须同时保留 `source_evidence_ids=[resume_evidence_id]` 和原始
`evidence_fragment_ids`。期望职位暂不投影 CandidateProfile。

## 6. Persistence and security

使用 `resume_drafts`、`resume_review_receipts`、`resume_evidence_snapshots` 三张 additive 表。Graph State、
run artifacts 与日志只保存 ID、哈希、页码、计数和有限安全摘要；禁止完整简历、API key 和未脱敏 PII。

## 7. Reparse

`resume import --reparse` 复用原 Artifact，但创建新 draft_id，并在确认后创建候选人维度的下一个
Snapshot version。不得更新旧 Draft、Receipt 或 Snapshot payload。
