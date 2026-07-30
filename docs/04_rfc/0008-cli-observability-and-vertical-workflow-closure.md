# RFC-0008: CLI 可观测运行基座与子 Workflow 纵向闭环

状态：Accepted / Ready for Implementation  
日期：2026-07-28  
关联需求：`docs/03_requirements/v0.7.1-vertical-workflow-closure-and-cli.md`  
关联 ADR：`docs/05_adr/0008-adopt-cli-first-observable-workflow-hardening.md`  
关联契约：`docs/06_contracts/cli-run-observability-contract.md`

## 1. 背景

现有业务子图主要由测试或专项脚本手工装配。正式 CLI 仍运行旧 Mini Runtime，因此用户无法通过
稳定入口逐步执行现有业务图，开发者也无法使用同一种方式检查 node、LLM、Claim、snapshot、
checkpoint 和 handoff。

同时，现有 trace 存在格式不统一、固定 success/零耗时、逻辑节点为空而业务集中在后续节点等问题。
如果直接增加 Parent Graph，这些问题会被更大的编排层掩盖。v0.7.1 先建立共享运行基座，并用同一
九层门禁逐个加固既有 subgraph。

## 2. 决策摘要

- 保留现有 `campus-agent` console script，不另建第二套 CLI harness。
- 默认交互式引导与 one-shot 子命令并存，所有命令提供纯 JSON 模式。
- 采用 CC Switch 的 Provider/SQLite SSOT/原子 current switch 形状建立 ModelProfile；CLI UI 只是
  ModelProfileService 和既有 Application Services 的交互适配层。
- 引入 `RuntimeFactory` 作为生产装配根，统一解析项目数据根和依赖。
- 引入 `RunSession` 与 typed handoff；Session 只保存引用和当前阶段，不复制子图 State。
- 引入追加式 `NodeEvent`/`ErrorEvent` 与安全的 Run artifact bundle。
- 使用 CLI/application service 单步消费 directive，不实现自治 Parent Graph 循环。
- 按 Candidate → Intent → Role → Matching → Preparation → Feedback 的依赖顺序验收。
- 名义节点必须产生真实可检查输出；否则合并/重命名，不保留伪成功 no-op 节点。

## 3. 逻辑架构

```text
campus-agent CLI
  ├─ project CLI UI / guided prompts
  ├─ one-shot commands
  └─ --json
        ↓
Application Services
  ├─ ModelProfileService
  ├─ SessionService
  ├─ CandidateService
  ├─ IntentIntakeService
  ├─ RoleResearchService
  ├─ MatchingService
  ├─ PreparationService
  ├─ FeedbackService
  └─ HandoffDispatcher
        ↓
Existing independent LangGraph subgraphs
        ↓
Evidence/Profile/Domain repositories + SQLite checkpoints + BlobStore
        ↓
RunArtifactWriter / EventSink / RedactionPolicy
```

CLI、application service 和 Graph 都不能成为新的事实源。事实仍由 immutable Evidence、领域
repository 和 versioned ProfileSnapshot 保存；Run artifact 仅用于诊断和导航。

## 4. RuntimeFactory

`RuntimeFactory` 统一装配：

- 项目数据根、run root、blob root、cache root；
- EvidenceRepository、ProfileRepository、Role/Matching/Preparation/Feedback repositories；
- 各 subgraph SQLite checkpointer；
- LLM provider、structured output、cache 和 redaction policy；
- ToolRegistry、SourceAdapter registry、CredentialResolver；
- EventSink、RunArtifactWriter 和 application services。

路径解析不得依赖当前工作目录。默认数据根应从显式 CLI 参数、配置或安装包可定位的项目配置解析，
并在 `doctor` 中展示；不得因为从其他目录启动而把 `data/` 写入未知位置。

## 5. Session 与 Run 模型

### 5.1 Session

Session 是多次命令共享的轻量导航对象：

```json
{
  "session_id": "session-...",
  "user_id": "user-...",
  "status": "active",
  "current_stage": "candidate",
  "current_refs": {
    "candidate_profile_snapshot_id": null,
    "career_intent_snapshot_id": null,
    "role_profile_snapshot_ids": [],
    "comparison_set_id": null,
    "target_decision_ids": [],
    "learning_plan_id": null
  },
  "pending_handoff_ids": [],
  "latest_run_id": null
}
```

Session 更新使用 compare-and-set/version 或等价事务守卫；只允许引用已验证、属于同一 user 的 current
或被显式允许的历史对象。

### 5.2 Run

每次命令执行产生一个 Run。Run 可以结束为：

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

这些业务状态不直接等价于进程失败。RunManifest 记录 workflow、thread、父/前序 run、输入输出 refs、
状态、next action、时间、版本和 artifact paths。

## 6. CLI 命令树

```text
campus-agent
├─ model
│  ├─ add
│  ├─ edit
│  ├─ list
│  ├─ show
│  ├─ use
│  ├─ remove
│  └─ test
├─ doctor
├─ session
│  ├─ start
│  ├─ status
│  ├─ resume
│  └─ history
├─ candidate
│  ├─ build
│  ├─ resume
│  ├─ show
│  └─ diff
├─ intent
│  ├─ create
│  ├─ revise
│  └─ show
├─ role
│  ├─ research
│  ├─ resume
│  └─ show
├─ match
│  ├─ run
│  ├─ resume
│  └─ show
├─ target
│  ├─ list
│  ├─ select
│  ├─ defer
│  └─ reject
├─ constraints
│  ├─ create
│  ├─ revise
│  └─ show
├─ plan
│  ├─ build
│  ├─ resume
│  └─ show
├─ feedback
│  ├─ add
│  ├─ resume
│  └─ show
├─ handoff
│  ├─ list
│  ├─ consume
│  └─ resolve
└─ inspect
   ├─ run
   ├─ node
   ├─ llm
   ├─ evidence
   ├─ claims
   ├─ profile
   └─ handoff
```

无参数进入交互式引导；交互层最终调用同一 application service，不维护第二套业务实现。

### 6.1 Model Provider SSOT

参考 CC Switch 的 Provider 形状和 SQLite SSOT，但不复制其面向外部 CLI 的 live-file 覆盖逻辑：

```json
{
  "id": "deepseek-default",
  "name": "DeepSeek Default",
  "settingsConfig": {
    "provider": "openai_compatible",
    "base_url": "https://api.deepseek.com",
    "model": "deepseek-v4-flash",
    "credential_ref": "local-secret://llm/deepseek-default"
  },
  "category": "cn_official",
  "websiteUrl": "https://platform.deepseek.com/",
  "createdAt": 0,
  "sortIndex": 0,
  "notes": null,
  "icon": "deepseek",
  "iconColor": null,
  "isCurrent": true
}
```

Repository 使用 `(id, app_type)` 主键、`settings_config` JSON 和 `is_current`；切换在同一事务中先清除
旧 current 再设置目标。配置读取顺序为显式 `CAMPUS_AGENT_MODEL_PROFILE` → 显式进程环境变量 →
SQLite current Provider。项目不隐式加载 cwd 下 `.env`，避免重新引入 cwd-dependent 配置源。

API key 不进入 Provider 表。SecretStore 保存 `api_key_ref`，运行装配只在 Provider 边界解析；
`doctor/model list/show` 只显示 `api_key_present` 和 credential ref，不显示 payload。

## 7. 交互原则

- 每一步开始前展示即将使用的材料、scope、provider、source 和可能的外部传输。
- 涉及真实材料发送给 LLM 或真实网站访问时沿用显式授权边界。
- 每个节点完成后显示简短结果：接受/拒绝数、unknown、阻塞、下一动作和 inspect 路径。
- interrupt 展示 request ID、允许动作和恢复命令。
- `--json` 模式不得向 stdout 写 banner、prompt 或日志；诊断文本进入 stderr 或事件文件。
- 非交互模式遇到必须人工确认的门禁时返回 `interrupted`，不得替用户选择。

## 8. Run Artifact Bundle

```text
data/runs/<run_id>/
├─ run_manifest.json
├─ events.jsonl
├─ state.json
├─ llm_calls.jsonl
├─ errors.jsonl
├─ artifact_index.json
├─ handoffs.jsonl
└─ report.md
```

写入规则：

1. Run 创建后立即写入 `running` manifest；初始化失败也必须留下可诊断记录。
2. event 使用 append-only、单调 sequence；节点 start 和 terminal event 成对。
3. terminal event 必须记录真实 status/duration、input/output refs、计数、route 和 fallback。
4. 失败后先写 ErrorEvent，再写 terminal NodeEvent/RunManifest。
5. `state.json` 只包含安全摘要和引用；长文本、简历正文、网页 raw、反馈全文不复制进去。
6. ArtifactIndex 只索引业务对象/BlobStore/checkpoint/report 路径和 hash。
7. 写 artifact bundle 失败不得误报业务成功；按故障位置返回可恢复或 fatal 状态。

## 9. Node 真实性规则

每个图节点必须满足至少一项：

- 产生新的已校验领域对象或对象引用；
- 执行一个明确、可观察的确定性变换；
- 做出一个有 reason code 的 route 决策；
- 建立或恢复一个人工门；
- 完成一个具有独立失败边界的外部副作用。

如果多个名义节点实际由一个函数一次完成，必须二选一：

1. 把逻辑拆到对应节点，并保存各节点输出；或
2. 合并/重命名节点，使 trace 与真实执行边界一致。

不得保留永远成功且没有输出的节点来制造“分层已执行”的假象。v0.7.1 重点审查：

- Matching 的 qualification/alignment/coverage/preference 节点；
- Preparation 的 objective/activity/priority/package/schedule 节点；
- Feedback 的 observation/diagnosis/attribution 节点；
- Graph 外预处理但图内又记录 `already_archived` 的 ingestion 边界。

## 10. Candidate Claim 修复设计

### 10.1 Predicate contract

Candidate predicate 必须是版本化 discriminated union 或等价领域类型，而不是任意字符串。至少覆盖：

```text
capability:<capability_id>
education:<record_id>.<field>
experience:<record_id>.<field>
```

是否增加 award/project/contact 等领域必须先在 Candidate contract 中定义投影语义；没有投影语义的
模型项只能进入 rejected receipt 或明确的 unprojected candidate queue，不能作为 active Claim 静默入库。

### 10.2 Batch semantics

每个模型输出项必须产生 accepted/rejected receipt，包含原始 item index、fragment refs、predicate、
reason codes、extractor/prompt/schema version。Claim batch 采用原子提交，或采用有明确事务边界的逐项
receipt；不得发生前项已提交、后项异常导致调用方误以为整批失败的不可解释半成功。

### 10.3 Processed fragment

Fragment 完成一次提取尝试后，状态必须区分：

```text
processed_with_accepted_claims
processed_all_rejected
retryable_extraction_failure
fatal_validation_failure
```

这防止“全部拒绝”无限重试，也允许 prompt/schema 升级后按版本显式重放。

## 11. CareerIntent intake

CareerIntent 首次入口必须归档用户原始意图并经过确认。Canonical source 只能有一处；兼容扁平字段与
constraints 的迁移逻辑必须检测双写漂移。

示例意图验收：

- `Agent 开发` → role query，允许别名但保留原文；
- `成都，工作地点必须成都` → confirmed hard location constraint；
- `2027 年毕业` → confirmed graduation year；
- `校招` → recruitment type 需要用户确认具体范围，不擅自推成秋招或春招；
- `优先大型企业以及互联网科技公司` → negotiable preference。

SearchScope 必须从 confirmed CareerIntent 确定性投影，并记录投影版本。

## 12. Role live 验收边界

- discovery 成功至少需要真实 search raw 和一个真实 job detail raw Artifact；搜索首页或搜索卡不等于详情。
- official verification 记录 confirmed/not_found/unavailable/ambiguous/unsupported，不要求所有公司均有通用适配器。
- company domain/official entry 的来源与 allowlist 必须可追溯。
- experience search 与 detail 分开验收；需要登录时进入 interrupt/resume。
- live support matrix 明确每个 adapter 的 search/detail/auth/official 能力和 Partial 语义。
- 任何 auth/CAPTCHA/rate limit/risk control 均停止在合法阻塞状态。

## 13. HandoffDispatcher

Dispatcher 是显式 application service，不是自动循环：

```text
list pending handoff
  → user/CLI chooses consume
  → validate owner/type/source refs/current session
  → call exactly one permitted handler
  → verify successor refs
  → resolve handoff atomically
  → update Session current refs
```

首版 handler：

- candidate profile rebuild；
- intent review；
- role instance refresh；
- role family aggregation candidate submission；
- rematch；
- replan。

同一 handoff 只能成功消费一次。失败保持 pending/failed-retryable，不得生成重复 successor snapshot。

## 14. 中断、恢复和幂等

- CLI session ID、Graph thread ID、Run ID 分离但互相引用。
- pending request 由 request/thread/owner/path/action 校验。
- 进程退出后通过同一 Session/Thread 恢复，不依赖进程内对象。
- 重复 response、feedback event、source batch 和 handoff consume 不产生重复业务对象。
- checkpoint、Evidence Store 和 Run artifact 各司其职，不互相代替。
- stale snapshot 在副作用前返回 reroute/handoff，不继续下游计算。

## 15. 错误模型

错误至少分类为：

```text
invalid_input
contract_violation
permission_denied
not_found
stale_input
auth_required
rate_limited
source_changed
adapter_required
llm_invalid_output
llm_unavailable
storage_failure
checkpoint_failure
budget_exhausted
internal_error
```

ErrorEvent 保存安全 message、error type、retryability、node、related refs 和 recovery hint；不保存 secret
或完整原始正文。

## 16. 兼容与迁移

- 保留旧 `campus-agent run` 一段兼容期，但帮助文本必须标记为 `legacy-mini-runtime`，不得作为正式流程。
- 新 CLI 复用现有 console script 和 package，不新增平行安装包。
- 旧 v0.3-v0.7 数据不原地改写；需要新 predicate/schema 时提升版本并提供显式 rebuild。
- 已有 subgraph public constructors 保持兼容；生产装配通过新 RuntimeFactory 调用。
- v1.0 Parent Graph 将复用 Session refs、Run event、handoff 和各 subgraph contract。

## 17. 实施阶段

1. CLI/Run contract、RuntimeFactory、EventSink、ArtifactWriter 和 doctor/inspect skeleton；
2. Candidate predicate/receipt/batch semantics 与真实材料修复；
3. CareerIntent intake/confirm/snapshot/SearchScope；
4. Role live search/detail/official/auth；
5. Matching 节点真实性、decision 与 handoff；
6. Constraints intake、Preparation 节点真实性与 review；
7. Feedback 节点真实性、directive dispatcher 与 successor validation；
8. 相邻 handoff、CLI subprocess、真实案例、failure/replay 和 Eval report。

每阶段都先生成失败基线，再做最小修复；完成前不得把文档状态改为 Implemented。

## 18. 风险

- 真实来源不稳定：使用 opt-in smoke、raw replay 与明确 Partial/blocked 状态，不降低门槛。
- CLI 范围膨胀：只做项目专用 CLI UI、当前业务流程、配置、诊断和恢复，不做通用 TUI 框架、
  Web 或桌面应用；未开放页面只保留稳定导航占位。
- 双重状态源：Session 只保存 refs，事实/执行状态仍分别归 repository/checkpoint。
- 日志泄露：默认最小摘要、引用优先、统一 redaction 和 Git ignore 检查。
- 为了“节点化”而过度拆分：以独立业务责任和失败边界决定节点，不按数量追求复杂度。
- Application service 变成隐藏 Parent Graph：dispatcher 每次只消费一个显式 handoff，不做自治循环。
