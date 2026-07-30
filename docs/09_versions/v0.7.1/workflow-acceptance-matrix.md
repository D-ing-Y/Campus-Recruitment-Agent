# v0.7.1 Workflow 验收矩阵

状态：WP0 Passed；WP1 Passed；WP1.1 Passed
日期：2026-07-30

状态枚举：`not_started | failing | blocked | partial | passed | not_applicable`。

## 1. 总矩阵

| Workflow | Contract | Evidence | Model | Validator | Projection/Policy | Persistence | Graph | CLI/Observability | Eval | 总状态 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Runtime/CLI | passed | not_applicable | not_applicable | passed | passed | passed | not_applicable | passed | passed | passed |
| Candidate | passed | passed | passed | passed | passed | passed | passed | passed | passed | passed |
| CareerIntent | not_started | not_started | not_started | not_started | not_started | not_started | not_started | not_started | not_started | not_started |
| Role | partial | partial | partial | partial | partial | partial | partial | failing | partial | partial |
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

WP1 全量回归：398 passed in 40.11s，`compileall` 与 `git diff --check` 通过。DeepSeek Provider
健康检查、真实简历 E4 与断网缓存回放已通过；用户已完成真实画像人工确认。WP1
退出门禁已满足，但本结论不代表 WP2 已开始或 v0.7.1 整体已完成。

## 5. WP2-WP7

后续工作包开始时，按照 Requirements 第 6 节和 Eval plan 补充对应 Gate。不得预填 `passed`。

## 6. 版本放行

只有总矩阵所有适用项为 `passed`，或外部 live source 项具有经用户接受且不影响版本核心声明的
明确 `blocked/partial` 限制，才可进入最终 Eval report。任何 Candidate、Intent、Matching、
Preparation、Feedback 核心项不得以 blocked/partial 标记为 Implemented / Accepted。
