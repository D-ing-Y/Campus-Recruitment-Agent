# Web Candidate API Contract

状态：v0.7.2 Implemented / Offline Accepted
日期：2026-08-13

## 1. 通用 Envelope

成功：

```json
{"ok": true, "data": {}, "error": null}
```

失败：

```json
{
  "ok": false,
  "data": null,
  "error": {
    "type": "contract_violation",
    "message": "safe user-facing message",
    "retryable": false
  }
}
```

错误不得包含绝对临时路径、简历正文、API key、Prompt、Cookie 或堆栈。

## 2. Session

- `POST /api/sessions`：创建固定本地用户 Session。
- `GET /api/sessions/{session_id}`：返回 session navigation 与 next action。
- `GET /api/sessions/{session_id}/workspace`：返回当前页面所需的 Session、Resume、
  pending interaction、Candidate Snapshot、历史版本和最近 diff 组合视图。

客户端不能提交权威 `user_id`。服务端固定为 `local-web-user`。

## 3. ResumeEvidence

- `POST /api/sessions/{session_id}/resume`：multipart `file`，仅 PDF。
- `GET /api/sessions/{session_id}/resume/review`：返回 pending request 与 review view。
- `POST /api/sessions/{session_id}/resume/review`：提交 action、response_id 和可选 patch。
- `GET /api/resume/{resume_evidence_id}`：返回安全的结构化 ResumeEvidence。

Review action 必须由当前 request 的 `allowed_actions` 允许。response_id 重放沿用现有幂等契约。

## 4. CandidateProfile

- `POST /api/sessions/{session_id}/candidate`：使用 Session current ResumeEvidence 构建画像。
- `GET /api/sessions/{session_id}/candidate/interaction`：读取当前 Candidate pending request。
- `POST /api/sessions/{session_id}/candidate/interaction`：提交 answer/upload/skip/cancel 等响应。
- `GET /api/candidate/{snapshot_id}`：返回 immutable Candidate ProfileSnapshot。
- `GET /api/candidate/diff?from={id}&to={id}`：返回 ProfileVersionDiff。

Candidate build 不接受客户端指定任意 ResumeEvidence；adapter 从 Session current refs 读取，Application Service 再执行 identity 校验。

Candidate upload 使用 multipart：`payload` 是响应 JSON 字符串，`file` 是单个 10 MB 以内的
PDF/Markdown/TXT/README。其余 action 使用 `application/json`。所有上传路径均由服务端生成。

## 5. HTTP 映射

| 领域错误 | HTTP |
| --- | ---: |
| invalid/unsupported input | 400 |
| not found | 404 |
| permission/identity mismatch | 403 |
| stale input/idempotency conflict | 409 |
| LLM/source temporarily unavailable | 503 |
| storage/checkpoint/internal | 500 |

HTTP 状态只负责传输表达，领域 `status`、`next_action` 和 `pending_request` 仍是业务真值。
