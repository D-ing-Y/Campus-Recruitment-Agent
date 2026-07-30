# CLI、RunSession 与可观测性契约

状态：v0.7.1 Ready for Implementation  
日期：2026-07-28  
关联需求：`docs/03_requirements/v0.7.1-vertical-workflow-closure-and-cli.md`  
关联 RFC：`docs/04_rfc/0008-cli-observability-and-vertical-workflow-closure.md`

## 1. 目的

本契约定义正式 CLI、RunSession、RunManifest、事件、诊断产物、退出码、脱敏和 typed handoff。
它只规范运行与交接，不替代 Evidence、Profile、领域 repository、Human Interaction 或 checkpoint 契约。

## 2. 核心不变量

1. `session_id`、`run_id`、`thread_id`、`request_id` 和领域对象 ID 不得混用。
2. Session 只保存引用和导航状态，不保存完整 Graph State 或原始正文。
3. Run artifact 是诊断视图，不是事实源；事实必须从对应 repository/BlobStore 读取。
4. checkpoint 是执行恢复点，不是 Evidence Store。
5. 每个 node/tool/LLM/route/interrupt/handoff 都必须产生有序事件。
6. terminal event 必须反映真实 status、duration、error/fallback，不得固定写 success。
7. `--json` stdout 必须是单一合法 JSON 文档或文档化的 JSONL，不得混入提示文本。
8. 任何输出不得包含 API key、Cookie、credential payload、完整简历/反馈/网页正文。
9. 进程失败时仍应尽力写入 manifest 和安全 ErrorEvent；写入失败必须改变最终状态。
10. 任何 handoff 在 successor refs 校验成功前不得标 resolved。

## 3. RunSession

```json
{
  "session_id": "session-uuid",
  "schema_version": "v0.7.1",
  "session_version": 1,
  "user_id": "user-id",
  "status": "active",
  "current_stage": "candidate",
  "current_refs": {},
  "pending_request": null,
  "pending_handoff_ids": [],
  "latest_run_id": null,
  "created_at": "RFC3339",
  "updated_at": "RFC3339"
}
```

`status`：`active | interrupted | blocked | completed | cancelled | failed`。

更新规则：

- 使用 `session_version` 乐观并发校验或等价事务守卫；
- 写入 ref 前验证 owner、schema/version、current/stale 和允许的 predecessor/successor 关系；
- `pending_request` 只保存 HumanInteractionRequest ref；
- 重复写入相同 canonical refs 幂等复用；
- 不允许 CLI 参数直接覆盖未验证 current refs。

## 4. RunManifest

```json
{
  "run_id": "run-uuid",
  "schema_version": "v0.7.1",
  "session_id": "session-uuid",
  "thread_id": "workflow-thread-id",
  "parent_run_id": null,
  "workflow": "candidate_profile",
  "command": "candidate.build",
  "status": "running",
  "next_action": null,
  "input_refs": {},
  "output_refs": {},
  "pending_request_id": null,
  "pending_handoff_ids": [],
  "started_at": "RFC3339",
  "ended_at": null,
  "artifact_paths": {},
  "software_version": "0.7.1",
  "policy_versions": {},
  "warnings": []
}
```

允许 terminal status：

```text
completed
completed_with_unknowns
partial
blocked
blocked_by_auth
interrupted
reroute_required
awaiting_rebuild
cancelled
failed
```

Manifest 创建后立即持久化。状态只允许 `running → terminal`；恢复创建新 Run，并通过
`parent_run_id`/thread/session 连接，不原地伪造旧 Run 未中断。

## 5. RunEvent

公共字段：

```json
{
  "event_id": "event-uuid",
  "schema_version": "v0.7.1",
  "sequence": 1,
  "run_id": "run-uuid",
  "session_id": "session-uuid",
  "thread_id": "workflow-thread-id",
  "event_type": "node_started",
  "occurred_at": "RFC3339",
  "workflow": "candidate_profile",
  "node": "extract_and_validate_claims",
  "status": "running",
  "input_refs": {},
  "output_refs": {},
  "counts": {},
  "route": null,
  "duration_ms": null,
  "reason_codes": [],
  "error_ref": null,
  "fallback": null
}
```

`event_type` 至少支持：

```text
run_started
run_finished
node_started
node_finished
tool_started
tool_finished
llm_started
llm_finished
route_selected
interrupt_created
resume_received
artifact_archived
validation_finished
snapshot_published
handoff_created
handoff_consumed
handoff_resolved
error_recorded
```

规则：

- sequence 在单 Run 内严格递增；
- started/finished 成对，崩溃恢复可以用 synthetic `abandoned` terminal event 补齐并注明 recovery；
- counts 必须从节点实际输出或 repository receipt 取得，不能读取更新前 state 冒充；
- reason_codes 使用稳定枚举，message 只作人类说明。

## 6. LLMCallReceipt

必须记录：provider、model、prompt/schema version、request hash、response hash、status、retry、cache hit、
token/latency（可得时）、validation result、fallback 和 error ref。

不得记录 API key、Authorization header、完整简历、完整 feedback、完整网页正文或未脱敏 prompt。
如需调试结构化输出，只保存安全摘要、本地受限 blob ref 或经用户授权且 Git 忽略的 encrypted/private ref。

## 7. ValidationReceipt

模型批量输出中的每一项必须产生 receipt：

```json
{
  "receipt_id": "validation-uuid",
  "run_id": "run-uuid",
  "workflow": "candidate_profile",
  "node": "extract_and_validate_claims",
  "item_index": 0,
  "candidate_hash": "sha256:...",
  "subject_ref": "candidate-id",
  "fragment_ids": ["fragment-id"],
  "predicate": "capability:cap:python",
  "status": "accepted",
  "reason_codes": [],
  "persisted_claim_id": "claim-id"
}
```

`status`：`accepted | rejected | duplicate | retryable_error | fatal_error`。

拒绝项必须保留足够的安全字段用于定位 prompt/schema/validator/projector 不一致；不得只返回总数。

## 8. ErrorEvent

```json
{
  "error_id": "error-uuid",
  "run_id": "run-uuid",
  "workflow": "candidate_profile",
  "node": "extract_and_validate_claims",
  "error_type": "contract_violation",
  "message": "candidate predicate is unsupported by schema v0.7.1",
  "retryable": false,
  "related_refs": {},
  "recovery_hint": "inspect validation receipts and rebuild with a supported predicate schema",
  "occurred_at": "RFC3339"
}
```

错误类型：`invalid_input | contract_violation | permission_denied | not_found | stale_input |
auth_required | rate_limited | source_changed | adapter_required | llm_invalid_output | llm_unavailable |
storage_failure | checkpoint_failure | budget_exhausted | internal_error`。

## 9. ArtifactIndex

ArtifactIndex 只做导航，条目至少包含：

- logical type；
- object ID/ref；
- repository/blob/checkpoint/report 的安全路径或 locator；
- content/canonical hash；
- owner；
- schema/policy version；
- created_at；
- sensitivity classification。

不得在 ArtifactIndex 内嵌原始正文。

## 10. Handoff

```json
{
  "handoff_id": "handoff-uuid",
  "schema_version": "v0.7.1",
  "session_id": "session-uuid",
  "user_id": "user-id",
  "handoff_type": "candidate_profile_rebuild_required",
  "origin_run_id": "run-uuid",
  "origin_object_refs": {},
  "required_input_refs": {},
  "status": "pending",
  "handler_version": "candidate-rebuild-v1",
  "attempt_count": 0,
  "resolved_refs": {},
  "created_at": "RFC3339",
  "resolved_at": null
}
```

状态：`pending | processing | resolved | failed_retryable | rejected | cancelled`。

不变量：

- owner/session/type/required refs 校验后才能 processing；
- 同一 handoff 只能有一个成功 resolution；
- resolved refs 必须属于同 user，并满足对应 predecessor/successor 契约；
- role family aggregation candidate 不能直接携带未经过聚合 policy 的新 family conclusion；
- dispatcher 不得自动递归消费新产生的 handoff。

## 11. CLI 输出

人类模式最少展示：

```text
workflow
run_id / session_id
status
重要计数与 warnings
pending interaction 或 handoff
next action
report/inspect 路径
```

JSON 模式最少返回：

```json
{
  "schema_version": "v0.7.1",
  "command": "candidate.build",
  "run_id": "run-uuid",
  "session_id": "session-uuid",
  "status": "interrupted",
  "next_action": "candidate.resume",
  "output_refs": {},
  "pending_request": {},
  "artifact_paths": {},
  "warnings": [],
  "errors": []
}
```

## 12. 退出码

| 退出码 | 含义 |
| ---: | --- |
| 0 | 命令成功执行并安全保存业务状态，包括 completed/partial/interrupted/blocked_by_auth |
| 2 | CLI 参数或本地配置错误 |
| 3 | 契约、owner、权限、path 或 stale 校验失败 |
| 4 | 外部 provider/source/adapter 当前不可用，且没有形成可恢复业务状态 |
| 5 | storage/checkpoint/artifact 写入失败 |
| 6 | 内部未分类错误 |

业务状态必须从 stdout JSON/RunManifest 读取，不能只看退出码。

## 13. 目录与原子写入

- Run 目录使用显式绝对 data root 下的精确 run ID，不从任意 cwd 推导。
- manifest/state/report 使用临时文件 + 原子替换；JSONL 使用进程内串行 writer 和文件锁或等价机制。
- Session 更新、handoff resolution 和关键领域 publish 使用事务/compare-and-set。
- 不删除历史 Run；清理/保留策略另行设计，v0.7.1 不提供破坏性清理命令。

## 14. 脱敏与隐私

必须脱敏：API key、Cookie、Authorization、credential payload、浏览器 profile、私人联系方式、完整
简历/反馈/网页正文。允许保留：对象 ID、hash、页码/行号/selector、短安全摘要、provider/model、
错误类型、计数、版本和本地受限路径。

测试必须扫描 tracked diff 和 Run artifact schema，确认真实材料没有进入 Git。

## 15. 兼容

- 旧 Mini Runtime `run` 命令保留时必须标记 legacy，输出不得称为完整 campus workflow。
- 新事件不要求迁移历史 trace；历史 Run 以 schema/version 区分。
- 各领域 contract 优先；本契约遇到冲突时不得放宽 Evidence、Human、Source 或 Profile 安全边界。

## 16. CLI UI 与 ModelProviderProfile

真实 TTY 下，无子命令进入项目 CLI UI；非 TTY 不读取 stdin，继续返回安全引导。CLI UI 当前只开放
Model 配置，所有操作调用 `ModelProfileService`，不得在 UI 中直接读写 SQLite 或 secret 文件。

`ModelProviderProfile` 对外采用 CC Switch 风格字段：

```text
id, name, settingsConfig, websiteUrl, category, createdAt, sortIndex,
notes, icon, iconColor, isCurrent
```

不变量：

- SQLite 是 Provider 元数据/current 状态的 SSOT；切换使用单事务，最多一个 current；
- 枚举菜单选择必须显式输入，不设置默认占位值；
- Add Provider 中可推导的自由文本字段必须以 `Label : dim-default` 展示，不使用方括号；空输入接受
  默认值，首个非空输入触发整行重绘并完整移除默认占位值；
- 布尔确认统一显示 `[Y/n]` 或 `[y/N]`，空输入选择大写项，显式输入大小写不敏感；
- Provider ID 默认由 preset 生成，冲突时使用稳定数字后缀递增，不要求普通用户理解内部主键；
- 内置 `mock-default` 保证首次启动可用；current Provider 不能删除；
- `settingsConfig.credential_ref` 是安全引用，不是 key；
- API key 文件目录 `0700`、文件 `0600`，写入使用临时文件、fsync 和原子替换；
- `model add/edit` 只从隐藏 prompt 或 `--api-key-stdin` 读取 key，不接受 `--api-key` 参数；
- `model edit --api-key-stdin` 轮换 key 时，若 Provider 元数据更新失败，必须恢复旧 key；
- `model list/show/doctor/test` 的 JSON 和 human 输出不得包含 secret payload；
- `model test` 只发送最小健康检查，不发送简历、README 或其他 Evidence；
- `settingsConfig.timeout_seconds` 可由 add/edit/UI 配置；DeepSeek preset 默认 90 秒；
- `httpx.TimeoutException` 映射 `network_timeout/retryable=true`，401/403 映射 `auth_required`，429 映射
  `rate_limited/retryable=true`；Candidate 必须保留该分类并返回外部依赖 exit 4；
- `--json` 不启动交互 UI；需要输入却未提供 stdin 时返回 `invalid_input/2`。
