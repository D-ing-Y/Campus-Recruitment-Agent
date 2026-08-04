# v0.7.1 Workflow 验收矩阵

状态：WP1/WP2 Passed；WP3 设计门禁补丁 Offline Passed；WP3 live source 未开始
日期：2026-08-03

状态枚举：`not_started | failing | blocked | partial | passed | not_applicable`。

## 1. 总矩阵

| Workflow | Contract | Evidence | Model | Validator | Projection/Policy | Persistence | Graph | CLI/Observability | Eval | 总状态 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Runtime/CLI | passed | not_applicable | not_applicable | passed | passed | passed | not_applicable | passed | passed | passed |
| ResumeEvidence | passed | passed | passed | passed | passed | passed | passed | passed | passed | passed |
| Candidate | passed | passed | passed | passed | passed | passed | passed | passed | passed | passed |
| CareerIntent | passed | passed | passed | passed | passed | passed | passed | passed | passed | passed |
| Role | passed | partial | partial | passed | partial | passed | partial | failing | partial | partial |
| Matching/Decision | partial | not_applicable | partial | partial | partial | partial | partial | failing | partial | partial |
| Preparation | partial | not_applicable | partial | partial | partial | partial | partial | failing | partial | partial |
| Feedback | partial | partial | partial | partial | partial | partial | partial | failing | partial | partial |
| Handoff/Connected | not_started | not_applicable | not_applicable | not_started | not_started | partial | not_started | not_started | not_started | not_started |

说明：`partial` 只表示现有离线代码/测试存在，不表示 v0.7.1 门禁已经通过；实际开发开始后必须附证据更新。

## 2. 单项记录模板

每一格按以下格式追加，禁止只改状态不留证据：

```text
Gate:
Expected:
Command or operation:
Actual:
Evidence path:
Status:
Blocker or limitation:
Reviewed at:
```

## 3. WP0 Runtime/CLI

| Gate | 预期 | 状态 | 证据 |
| --- | --- | --- | --- |
| RuntimeFactory | 生产依赖统一装配，cwd-independent | passed | installed CLI 仓库外 cwd smoke；`test_v071_cli_blackbox.py` |
| Run artifacts | running/terminal/error 均落盘 | passed | 8 件套 smoke；10 终态与 writer failure unit tests |
| Event truthfulness | status/duration/count/route 真实 | passed | NodeObserver/Candidate regression；concurrent sequence tests |
| Interactive CLI | 默认引导可用 | passed | 无参数 installed CLI exit 0；CLI black-box |
| JSON CLI | stdout 可纯解析 | passed | doctor/session/inspect/error subprocess JSON parse |
| Inspect | run/node/llm/evidence/claim/profile/handoff 可查 | passed | 全 inspect 域 black-box，含缺失/成功路径 |
| Restart/replay | 恢复与幂等 | passed | Session SQLite restart、CAS、duplicate resume/handoff tests |
| Privacy | secret/private leak 为 0 | passed | redaction/private profile/artifact index tests 与源码命中复核 |

WP0 证据汇总：24 unit + 5 CLI black-box；Candidate 回归 14 passed；全量 367 passed in 19.60s。
这里的 `passed` 仅表示公共 Runtime/CLI 基座通过，不表示 Candidate 至 Feedback 的业务纵向闭环已通过。

### 3.1 CLI UI / Model Provider 增量门禁

| Gate | 预期 | 状态 | 证据 |
| --- | --- | --- | --- |
| Provider contract | CC Switch 风格字段，SQLite 为 SSOT | passed | `test_v071_model_profiles.py` shape/DB tests |
| Secret boundary | Provider/输出不含 key，本地目录/文件为 0700/0600 | passed | unit permission/DB byte scan；CLI redaction subprocess |
| Current switch | 单事务最多一个 current，current 不可删除 | passed | failure injection + concurrent switch tests |
| Config resolution | active Provider 生效，不隐式读取 cwd `.env` | passed | installed doctor subprocess with hostile `.env` fixture |
| CLI UI | 真 TTY 默认进入；只开放 Model；非 TTY 不阻塞 | passed | pseudo-TTY smoke + explicit UI/non-TTY subprocess |
| Model commands | add/edit/list/show/use/remove/test 共用 service | passed | installed CLI CRUD/edit/switch/test subprocess |
| Real provider | 用户真实 key 的最小网络 health check | passed | `model test deepseek-main`：available；business material=false |

Model 配置增量证据：10 unit + 3 installed CLI tests；全量 391 passed in 39.81s；真实 DeepSeek
最小健康检查 passed。这里的 passed 不表示真实 Candidate E4 或人工画像复核通过。

### 3.2 WP1.1 LangChain Model / Tool / MCP 接入门禁

| Gate | 预期 | 状态 | 证据 |
| --- | --- | --- | --- |
| Model factory | 生产路径由 LangChain provider integration 装配 | passed | `LangChainChatProvider`/factory tests；真实 model test |
| Capability strategy | native/tool/json/unsupported 与有界 fallback 可诊断 | passed | `test_v071_model_gateway.py` |
| DeepSeek request policy | Thinking + forced tool_choice 冲突由 adapter 处理 | passed | 最小 live schema + 简历 E4 |
| Strategy observability | receipt 保留 integration/requested/effective/capabilities/fallback | passed | `run-189e9c9a-fde1-47da-8d12-a98be289c35f/llm_calls.jsonl` |
| Tool exposure | 旧 Tool 默认不可见，Pydantic args，write 确认门 | passed | `test_v071_tool_integrations.py` |
| MCP allowlist | stdio fixture 可调用，空 allowlist/重名拒绝 | passed | `test_v071_mcp_integrations.py` |
| MCP failure isolation | 不可达 server 脱敏诊断且不阻断其他 server | passed | unreachable + math fixture test |
| Regression | Candidate 业务骨架和历史版本不回退 | passed | 410 passed in 66.90s |

WP1.1 限制：MCP 仅完成协议接入与本地 fixture，未验证任意真实第三方 Server；
MCP 配置尚未开放到 CLI UI。

## 4. WP1 Candidate

| Gate | 预期 | 状态 | 证据 |
| --- | --- | --- | --- |
| Predicate contract | Prompt/Schema/Validator/Projector 一致 | passed | `candidate_claim_v0.7.1` + unit mixed batch/projector tests |
| Per-item receipt | 每项 accepted/rejected 有原因 | passed | mixed/all-rejected tests；inspect claims 返回 receipts |
| Batch semantics | 无不可解释半提交 | passed | SQLite trigger 注入中间失败，Claim/receipt 均回滚 |
| Real material | 已授权材料 raw/fragment/locator | passed | 真实简历 PDF 成功归档并保留 locator；项目 README 经用户明确省略且未读取 |
| DeepSeek projection | accepted 支持项 100% 投影 | passed | WP1 原验收 run + WP1.1 `run-d85876df-03f9-4d42-a488-f5f09f72caa9`；18 accepted/2 reasoned rejected，投影/引用/追溯指标均 1.0 |
| HITL | ask/upload/correct/skip/cancel 可恢复 | passed | installed CLI 五类 action + 跨进程 resume |
| CLI/inspect | 可定位 extract/validate/project | passed | `candidate build/resume/show/diff` + run/node/llm/claims/profile |
| Duplicate/rebuild | 重放不重复 Artifact/Claim/Snapshot | passed | WP1.1 同 owner 断网回放 `run-189e9c9a-fde1-47da-8d12-a98be289c35f`；cache hit，18 duplicate，snapshot/Claim IDs 不变 |
| Failure diagnosis | model/storage/checkpoint/fatal 可诊断 | passed | model terminal ErrorEvent；Graph storage/checkpoint injection tests |
| Real review | 用户复核画像 | passed | 2026-07-29 用户确认教育、经历边界与能力等级正确 |

WP1 原验收按旧 PDF→Claim 契约完成，本段保留历史证据。WP1.3.2 已用 confirmed
ResumeEvidence 与 scoped Claim resolution 完成新的 CandidateSnapshot 重验，当前状态恢复为 passed。

### 4.1 WP1.2 经历 Taxonomy 与 typed Claim 补丁

| Gate | 预期 | 状态 | 证据 |
| --- | --- | --- | --- |
| Resume taxonomy | 校招及有工作经验人群的常见经历可表示 | passed | 12 类 kind + 18 类 context + raw label；课程、毕设、论文、纵向、横向、实习/工作项目、个人/开源均有映射 |
| Tool contract | 固定值进入模型可见 Schema | passed | Pydantic discriminated union；capability id/level 与 experience kind/context 均为 enum |
| Open world | 未知经历不丢失、不误默认为 project | passed | `other/unspecified + raw_label` 单元与 projector 测试 |
| Semantic gate | 模型不能把未建模技能强塞进近似 capability | passed | `raw_label -> capability_id` ontology 一致性测试；拒绝原因可检查 |
| Real DeepSeek | 无缓存、非思考 Tool Calling、Pydantic 与投影通过 | passed | `run-a0047b90-e893-4ced-8df8-314cdd0d3838`；18 accepted/18 reasoned rejected，0 conflict，下一步 `intent.create` |
| WP2 regression | CareerIntent 与 handoff 不回退 | passed | WP1/WP2 聚焦 41 passed |
| Full regression | 全项目无回归 | passed | 428 passed in 85.47s；`compileall`、`git diff --check` 通过 |

本补丁保证“经历类型表达差异”不再造成合法经历拒绝，并保证未建模技能有拒绝回执而不是伪造
capability。荣誉奖项和更细粒度语言/库/工具仍不属于当前 Candidate projection contract；原始 PDF
仍在 Evidence Store，扩展它们需要独立 taxonomy/ontology 变更，不能把本补丁表述为完整简历语义覆盖。

### 4.2 WP1.3 结构化简历证据纠偏

| Gate | 预期 | 状态 | 证据 |
| --- | --- | --- | --- |
| Contract | ResumeDraft/Review/Snapshot/SourceRef 分层 | passed | RFC/ADR-0012 + resume-evidence-contract |
| PDF/PII | 双解析门禁、本地 PII、模型输入脱敏 | passed | resume unit/integration；真实 run PII/文件名 0 命中 |
| Review | 固定顺序、非空列表无重复整体确认、空区块确认、CAS+Receipt 原子幂等 | passed | `test_v071_resume_evidence_graph.py` |
| Candidate handoff | 仅 confirmed Snapshot；旧 --input 拒绝 | passed | Graph + installed CLI tests |
| Fidelity patch | layout、无标签头部、“至今”、奖项边界、字段 span | passed | 444 tests + `v0.7.1-wp1.3.1-eval-report.md` |
| Real extraction | DeepSeek 生成字段对齐 Draft，36/36 精确 SourceRef | passed | `run-ceb5ff01-4ab0-4dc6-9dd7-e3f6bf6a0b8a` + `run-82482f23-7a67-4545-b476-e98c82c6935f` |
| Human review | 八区块全部由用户确认 | passed | confirmed `resume-evidence-7a793727c907b79065397a35` |
| WP1/WP2 replay | 新 CandidateSnapshot 与 CareerIntent 重验 | passed | Candidate `89514ab4-758e-4d54-90af-f740519c40b1`；WP2 confirmation `run-56709fb6-8489-4fdd-8339-3d1e3cc0938d` |

WP1.3.1 阶段结果见 `docs/07_evaluation/v0.7.1-wp1.3.1-eval-report.md`；原 WP1.3 报告仅保留为
修复前基线。WP1.3.2 生命周期与重验结果见对应 Eval report。

### 4.3 WP1.3.2 Claim 生命周期与增量投影

| Gate | 预期 | 状态 | 证据 |
| --- | --- | --- | --- |
| Lifecycle | origin/effective/multi-supersede 且 legacy 可读 | passed | schema/legacy payload/lineage unit |
| Resolution | 当前 Resume + 有效 overlay；旧派生隔离 | passed | pure resolution unit + Resume v1→v2 installed CLI |
| Semantics | metadata 等价、日期 refinement、真实差异冲突 | passed | semantic unit 与 projector regression |
| Atomic correction | 一条 successor 原子替代多条旧 Claim | passed | SQLite transaction + Graph correction test |
| Feedback | event effective_at 且共享 resolution | passed | Feedback Graph/Saga regression |
| Recovery | cancel→session.resume→candidate.build | passed | CLI unit、provider failure 与真实 checkpoint |
| Real Candidate | basis/trace/selection 指标通过 | passed | `run-22685159-b474-4100-9a02-7d8e9d43a261` |
| WP2 replay | 新 Candidate typed input 产生 Intent/Scope/Handoff | passed | `run-a07e2376-2ae9-4b86-ad56-082d8070ebdb` 至 `run-56709fb6-8489-4fdd-8339-3d1e3cc0938d` |

## 5. WP2 CareerIntent

| Gate | 预期 | 状态 | 证据 |
| --- | --- | --- | --- |
| Contract | constraints 为 canonical source，扁平字段不漂移 | passed | CareerIntent Contract + Pydantic consistency validator |
| Evidence | raw/response 先归档，constraint 有 fragment ref | passed | E2 provider failure raw-first + E4 final snapshot |
| Model | DeepSeek non-thinking Tool Calling | passed | `run-b2b9ddee-610a-476d-bf51-4c54310352c5`；cache miss，effective tool calling |
| Validator | Pydantic + hard/preference/key/owner 领域门 | passed | gold 4 cases + real semantic mismatch correction |
| Projection | confirmed hard constraint 才进入 SearchScope | passed | `intent-snapshot:f50118c95f38e33dfa79f129` |
| Persistence | draft/receipt/response/snapshot/scope/handoff 幂等 | passed | repository regression + duplicate confirm 零写 |
| Graph | checkpoint interrupt/revise/confirm/cancel/failure | passed | WP2 Graph integration tests |
| CLI/Observability | installed create/resume/show/inspect 和 8 件套 | passed | installed CLI E3 + E4 run artifacts |
| Eval | E0-E4 与全量回归 | passed | `v0.7.1-wp2-eval-report.md`；424 tests |

WP2 已用新 CandidateSnapshot 重验；领域模型未重写，新的 create manifest 保留 typed-input 引用。

## 6. WP3 岗位层级与证据门禁补丁

| Gate | 预期 | 状态 | 证据 |
| --- | --- | --- | --- |
| Multi-family scope | 每个 family 独立 Scope/Handoff | passed | multi-family CareerIntent Graph + legacy fallback unit |
| Family membership | mismatch/ambiguous 不进入统计分母 | passed | cross-family noise Role Graph case |
| Detail gate | search-only 不产生岗位画像 | passed | search-only/detail Role Graph cases |
| Experience scope | family/company/job 可追溯，ambiguous 不投影 | passed | scope-link unit/integration |
| Replay | gate 记录幂等，旧 official plan 可恢复 | passed | cross-thread fixture replay |
| Live source | 真实 search → detail Raw Artifact | not_started | `v0.7.1-wp3-live-source-support-matrix.md` |

补丁的详细数据见 `docs/07_evaluation/v0.7.1-wp3-role-hierarchy-evidence-gates-eval-report.md`。
这里的 passed 是 contract/deterministic gate/offline replay，不表示 Role Workflow 已完成 live 验收。

## 7. WP3-WP7

后续工作包开始时，按照 Requirements 第 6 节和 Eval plan 补充对应 Gate。不得预填 `passed`。

## 8. 版本放行

只有总矩阵所有适用项为 `passed`，或外部 live source 项具有经用户接受且不影响版本核心声明的
明确 `blocked/partial` 限制，才可进入最终 Eval report。任何 Candidate、Intent、Matching、
Preparation、Feedback 核心项不得以 blocked/partial 标记为 Implemented / Accepted。
