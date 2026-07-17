# Evidence Contract

实现状态：v0.3 已实现。对应代码位于 `schemas/`、`storage/`、`evidence/` 和 `ontology/`；SQLite migration 为 `storage/migrations/0001_evidence.sql`。

证据追溯先于画像、RAG 和推荐。原始 Artifact 是事实源，Fragment 是引用单位，Claim 是结构化派生事实，Profile 是 Claim 的版本化投影。

## v0.3 EvidenceArtifact

```json
{
  "artifact_id": "uuid",
  "owner_id": "anonymous-fixture-user",
  "source_type": "user_upload",
  "content_type": "resume_pdf",
  "source_url": null,
  "original_name": "resume.pdf",
  "raw_uri": "file://data/evidence/raw/<id>/resume.pdf",
  "text_uri": "file://data/evidence/text/<id>.txt",
  "content_hash": "sha256",
  "published_at": null,
  "retrieved_at": "2026-07-15T00:00:00+08:00",
  "parser_name": "pdf_text",
  "parser_version": "v1",
  "metadata": {}
}
```

要求：

- `content_hash` 用于内容去重。
- 原始 Artifact 写入后不可原地修改。
- `source_url` 适用于网页；本地上传可为 null。
- Cookie、Authorization header、API key 不得进入 metadata。

## v0.3 EvidenceFragment

```json
{
  "fragment_id": "uuid",
  "artifact_id": "uuid",
  "locator_type": "page_and_char_range",
  "locator": {"page": 1, "start": 120, "end": 260},
  "text": "...",
  "text_hash": "sha256",
  "embedding_ref": null,
  "metadata": {}
}
```

要求：Fragment 必须能定位回原始 Artifact；v0.3 可保存文本，后续可只保存 text URI 或受控摘要。

## v0.3 EvidenceClaim

```json
{
  "claim_id": "uuid",
  "subject_id": "candidate:<id>",
  "predicate": "capability.python.level",
  "value": "intermediate",
  "claim_type": "model_inference",
  "evidence_fragment_ids": ["fragment-id"],
  "confidence": 0.72,
  "extractor": {"provider": "mock", "model": "mock-claim-extractor"},
  "prompt_version": "claim_extractor_v1",
  "schema_version": "v0.3",
  "status": "active",
  "created_at": "2026-07-15T00:00:00+08:00",
  "supersedes_claim_id": null
}
```

`claim_type`：

- `observed_fact`
- `user_reported`
- `model_inference`
- `feedback_signal`

约束：

- `observed_fact`、`user_reported` 和 `feedback_signal` 必须有 Fragment 引用。
- `model_inference` 必须有支撑 Fragment、confidence 和模型/prompt/schema 版本。
- Claim 更新写新版本，不原地覆盖历史。
- 引用不存在或越权 Artifact 的 Claim 必须拒绝写入。

## Profile Snapshot 引用规则

CandidateProfile、CareerIntent、RoleProfile 和 GapAssessment 中的事实性字段必须：

- 保存 supporting claim IDs；
- 保存 snapshot ID、schema version、created_at；
- 区分 confirmed、inferred、unknown 和 conflicted；
- 能够仅通过 Claim 重建。

v0.3 实现中的 `ProfileSnapshot` 是 Candidate/CareerIntent/Role 的统一持久化外壳，使用 `(subject_id, profile_type, version)` 唯一约束，并保存 `supporting_claim_ids`。`CandidateProfileProjector` 只投影通过 `ClaimValidator` 并已持久化的 Claim。

## Web 与社区证据

- 岗位存在、职责、城市和硬性条件优先使用企业或招聘平台原始岗位页。
- 社区来源用于笔面试、薪资、氛围和实践信号，必须保留平台、作者/匿名状态、发布时间、获取时间和独立置信度。
- 原始 HTML/文本必须先归档，再由模型总结。

## v0.4 候选人画像 Graph 增量

实现状态：Design Accepted / Pending Implementation。

### 材料摄取

- 文本型 PDF、Markdown、TXT 和 README 必须先保存不可变 Artifact，再解析 Fragment。
- PDF Fragment locator 至少包含页码和页内范围；Markdown/TXT/README 使用行号、字符范围或二者。
- 扫描件或不可解析 PDF 返回 `unsupported_input`，不得生成空文本却标记成功。
- 相同 owner 下的相同内容继续按 hash 去重。

### 用户回答 Artifact

用户通过 interrupt/resume 提交的回答必须保存为：

```json
{
  "source_type": "human_interaction",
  "content_type": "conversation_response",
  "original_name": "response-<response_id>.json",
  "metadata": {
    "thread_id": "thread-1",
    "request_id": "request-1",
    "response_id": "response-1",
    "question_ids": ["question-1"]
  }
}
```

约束：

- 原始 response payload 进入本地不可变 BlobStore。
- 每个回答使用 `json_pointer` 或等效 locator 生成 Fragment。
- 回答生成 `user_reported` Claim；不能从聊天摘要直接更新 Profile。
- trace 只保存 ID、数量和状态，不保存完整回答正文。

### 用户纠正与 supersede

- 纠正生成新的 `user_reported` Claim，并引用 response Fragment。
- `supersedes_claim_id` 必须属于同一 owner、subject 和允许纠正的字段。
- 被替代 Claim 保留历史并标为 `superseded`。
- 若用户自述与材料中的 observed fact 暂时无法调和，两条 Claim 均保留，Profile 标为 `conflicted`。
- `remove` 或 `mark_unknown` 也是可审计事件，不允许直接删除 Claim 或 Profile 字段。

### Snapshot 重建和幂等

- CandidateProfile 只能从 active、已验证、已持久化 Claim 投影。
- 每个 snapshot 保存完整 supporting claim ID 集合、schema version 和 previous snapshot 引用。
- profile canonical hash 未变化时复用最新 snapshot，不增加版本。
- 新 Claim、supersede、冲突变化或 schema 迁移时创建递增版本。
- checkpoint 不得被 ProfileProjector 当作 Claim 来源。
