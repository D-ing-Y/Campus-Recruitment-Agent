# Human Interaction Contract

状态：v0.4 Implemented
日期：2026-07-17

本契约统一 LangGraph `interrupt()` 与 resume 的结构化载荷。v0.4 用于候选人画像提问、补充材料和可选画像复核，后续版本可复用。

## 1. HumanInteractionRequest

```json
{
  "request_id": "hir-<stable-hash>",
  "schema_version": "v0.4",
  "thread_id": "thread-1",
  "run_id": "run-1",
  "user_id": "user-1",
  "interaction_type": "answer_questions",
  "reason": "需要确认项目中的个人职责",
  "questions": [],
  "requested_materials": [],
  "profile_snapshot_id": "snapshot-1",
  "target_paths": ["experiences[exp-1].responsibilities"],
  "related_artifact_ids": ["artifact-1"],
  "related_claim_ids": ["claim-1"],
  "allowed_actions": ["answer", "skip", "cancel"],
  "expires_at": null,
  "created_at": "2026-07-17T00:00:00+08:00"
}
```

`interaction_type`：

- `answer_questions`
- `provide_materials`
- `review_profile`

`request_id` 必须由稳定输入派生，至少包含 thread、interaction round、类型、目标 gap 和问题计划 hash。相同节点重放必须得到相同 request ID。

## 2. RequestedMaterial

```json
{
  "material_id": "material-request-1",
  "gap_id": "gap-1",
  "description": "请补充包含个人职责说明的项目 README 或说明文档",
  "accepted_content_types": ["text/markdown", "text/plain", "application/pdf"],
  "required": false,
  "reason": "现有扫描 PDF 无法提取文字"
}
```

请求不得诱导用户提交身份证号、账号密钥、Cookie 或与画像无关的敏感材料。

## 3. HumanInteractionResponse

```json
{
  "response_id": "response-1",
  "schema_version": "v0.4",
  "request_id": "hir-<stable-hash>",
  "thread_id": "thread-1",
  "user_id": "user-1",
  "action": "answer",
  "answers": [
    {
      "question_id": "question-1",
      "text": "我负责 LangGraph 工作流和评估，未负责爬虫。",
      "declined": false
    }
  ],
  "file_paths": [],
  "corrections": [],
  "confirmation": null,
  "submitted_at": "2026-07-17T00:05:00+08:00"
}
```

`action`：

- `answer`
- `upload`
- `correct`
- `confirm`
- `skip`
- `cancel`

载荷规则：

- `answer` 至少包含一个 answer。
- `upload` 至少包含一个允许访问的本地路径。
- `correct` 至少包含一个 `ProfileCorrection`。
- `confirm` 只对 request 中展示的 snapshot 生效。
- `skip` 可包含被跳过 question/material ID。
- `cancel` 终止本次画像收集，不删除已有证据。

## 4. Resume 校验

恢复前必须校验：

- `thread_id`、`request_id` 和 `user_id` 与 pending request 一致。
- response action 在 `allowed_actions` 中。
- question ID、material request ID 和 correction target 属于当前 request。
- request 未过期或已由 policy 允许恢复。
- 本地文件路径在调用方授权范围内。

实现中授权范围由 Graph 初始化时的 `allowed_path_roots` 固定；resume 只能提交该
范围内且实际存在的文件，不能通过响应扩大授权根目录。

校验失败时不得写 Evidence Store，也不得推进 Graph。

## 5. 回答证据化

用户回答不能只保存在 State。处理顺序：

```text
HumanInteractionResponse
  → canonical response hash
  → EvidenceArtifact(content_type=conversation_response)
  → EvidenceFragment(locator_type=json_pointer)
  → EvidenceClaim(claim_type=user_reported)
  → ClaimValidator
  → CandidateProfile projection
```

Artifact metadata 可保存 `request_id`、`response_id` 和 question IDs，但不得保存密钥或无关敏感上下文。每个回答 Fragment 使用 JSON Pointer 或等效 locator 精确定位。

## 6. 文件与纠正

- `file_paths` 只作为摄取请求；文件必须先复制到不可变 BlobStore。
- 文件 hash 重复时复用已有 Artifact。
- Correction 必须引用 response Artifact/Fragment。
- 新纠正 Claim 使用 `supersedes_claim_id`；旧 Claim 保留历史。
- `remove` 和 `mark_unknown` 也必须生成可审计 Claim/事件，不能直接删字段。

## 7. Interrupt/Resume 语义

- `interrupt_for_user` 节点在调用 `interrupt()` 前不得做非幂等外部写入。
- resume 后该节点可能从头执行，所有 request 构建必须确定性。
- `archive_human_input` 是 resume 后唯一允许消费 response 并写入证据的节点。
- 写入成功后 State 清空 response 正文，只保留 response artifact ID 和摘要。
- Graph 以相同 `thread_id` 继续；新任务不得复用旧 thread ID。

## 8. 幂等

响应幂等键：

```text
sha256(
  thread_id
  + request_id
  + response_id
  + canonical_response_payload
  + schema_version
)
```

- 相同幂等键返回第一次处理结果。
- 同一 `response_id` 携带不同 payload 必须报 `idempotency_conflict`。
- 重复 resume 不重复创建 Artifact、Claim 或 ProfileSnapshot。
- 部分写入失败时不得标记 response 已消费。

## 9. 隐私与日志

- checkpoint 可暂存 resume payload，但成功归档后应清除正文。
- trace 只记录 request/response ID、动作、数量、状态和错误摘要。
- report 不展示完整用户回答，除非用户界面明确需要且数据仍处于本地授权范围。
- checkpoint DB、用户材料和响应 Artifact 默认位于 `data/` 并排除 Git。

## 10. 版本兼容

- 未识别的 schema version 必须拒绝或先迁移。
- 后续版本可增加 interaction type，但不得改变 v0.4 action 的既有语义。
- Parent Graph 复用本契约时，每次 request 仍必须声明 stage 和目标对象引用。
