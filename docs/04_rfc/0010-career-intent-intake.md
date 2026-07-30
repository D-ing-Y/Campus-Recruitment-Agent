# RFC-0010：CareerIntent 首次采集、确认与 SearchScope 投影

状态：Implemented  
日期：2026-07-30  
关联需求：`docs/03_requirements/v0.7.1-wp2-career-intent-intake.md`

## 1. 架构

新增独立 `CareerIntentGraph`，节点为：

```text
validate_context
→ archive_raw_intent
→ extract_structured_candidate
→ validate_candidate
→ plan_confirmation
→ interrupt_for_confirmation
→ apply_confirmation
→ persist_intent_snapshot
→ project_search_scope
→ emit_role_research_handoff
→ finalize
```

Graph 只在 checkpoint 保存恢复所需的结构化 candidate/request/response；原始正文的事实源是 Evidence
Store，RunSession 只保存 refs 与 navigation。

## 2. Schema 与 canonical source

LLM 输出 `CareerIntentCandidate`：target role candidates、typed constraint candidates、unresolved fields
和 fragment refs。所有字段使用封闭枚举和具体 list/object 类型，不使用裸 `dict[str, Any]` 作为主协议。

v0.7.1 snapshot 的 canonical 规则：

- target role/role family 使用 top-level canonical fields；family 由 alias policy 产生；
- location/year/recruitment/industry/company/company_type/work mode 的分类与来源以 confirmed
  `IntentConstraint` 为 canonical source；
-兼容 top-level discovery fields 是 constraints 的确定性投影，持久化前检测不一致；
- legacy v0.3/v0.6 snapshot 继续可读，不被原地改写。

## 3. LLM 与 Validator

`IntentCandidateExtractor` 复用 StructuredOutputGateway：DeepSeek capability 选择 `tool_calling`，adapter
注入 `thinking=disabled`。Pydantic 只证明协议 shape；领域 Validator 继续检查 fragment owner/ref、枚举、
hard/preference 边界、显式 year 和 unresolved recruitment type。

模型候选无权直接写 snapshot。无法安全分类的 candidate 形成 validation issue 或 unresolved field，不通过
Prompt 猜测修复。

## 4. Evidence 与持久化

- raw text 按 owner + content hash 存入 immutable BlobStore/EvidenceArtifact；
- 单一 text fragment 使用 char-range locator；
- intent repository 保存 candidate、validation receipt、confirmation receipt 和 SearchScope；
- CareerIntent 继续使用通用 ProfileSnapshot repository；
- handoff 使用 Runtime session repository。

发布顺序为 snapshot → scope → handoff → session ref/navigation。每一步均幂等；中途失败可根据已持久化
对象恢复，不把未完成发布伪装成 completed。

## 5. Human Gate

create 总是产生 `review_career_intent` request。request 展示结构化摘要和 unresolved fields，不嵌入原始
长文本。response 支持：

- `confirm`：无 unresolved fields 时确认当前 candidate；
- `revise`：提交 allowlisted canonical patch，再次验证并确认；
- `cancel`：终止，不发布 snapshot/scope/handoff。

`campus_unspecified` 必须通过 revise 明确为 autumn/spring/unknown 后才能 confirm。

## 6. CLI 与 handoff

```text
campus-agent intent create SESSION --text "..."
campus-agent intent resume SESSION --action revise --response-id ID --patch JSON
campus-agent intent resume SESSION --action confirm --response-id ID
campus-agent intent show SNAPSHOT_ID
```

完成后创建 `role_research_required` handoff，引用 CareerIntent snapshot 和 SearchScope。Dispatcher 不在
WP2 自动消费它，CLI 只返回 `next_action=role.research`。

## 7. 失败语义

| 场景 | 状态/错误 |
| --- | --- |
| 空文本、无 Candidate current ref | invalid_input/contract_violation |
| LLM/Pydantic 失败 | failed + llm_invalid_output |
| 领域分类冲突 | interrupted，展示 validation issues |
| unresolved 直接 confirm | 再次 interrupted/needs_confirmation，零 snapshot 写入 |
| stale/wrong-owner response | stale_input/permission_denied |
| checkpoint/storage failure | terminal failed，可 inspect |
| duplicate response | 返回首次结果，零重复写 |

## 8. 测试

覆盖 archive-before-model、Tool Calling receipt、边界分类、confirmation/revision、cross-process resume、
duplicate/conflict、snapshot/scope/handoff trace、secret/raw redaction、failure injection 与全量回归。
