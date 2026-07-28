# v0.7.1 Workflow 验收矩阵

状态：WP0 Passed；WP1 Not Started  
日期：2026-07-28

状态枚举：`not_started | failing | blocked | partial | passed | not_applicable`。

## 1. 总矩阵

| Workflow | Contract | Evidence | Model | Validator | Projection/Policy | Persistence | Graph | CLI/Observability | Eval | 总状态 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Runtime/CLI | passed | not_applicable | not_applicable | passed | passed | passed | not_applicable | passed | passed | passed |
| Candidate | not_started | not_started | failing | failing | failing | partial | partial | failing | failing | failing |
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

## 4. WP1 Candidate

| Gate | 预期 | 状态 | 证据 |
| --- | --- | --- | --- |
| Predicate contract | Prompt/Schema/Validator/Projector 一致 | failing | 已知真实 DeepSeek predicate 错位 |
| Per-item receipt | 每项 accepted/rejected 有原因 | not_started | - |
| Batch semantics | 无不可解释半提交 | not_started | - |
| Real material | PDF + README raw/fragment/locator | partial | 本地历史试跑，需正式 CLI 重跑 |
| DeepSeek projection | accepted 支持项 100% 投影 | failing | 历史运行 supported=0 |
| HITL | ask/upload/correct/skip/cancel 可恢复 | partial | 仅离线 integration 基线 |
| CLI/inspect | 可定位 extract/validate/project | failing | 正式命令缺失 |
| Real review | 用户复核画像 | not_started | - |

## 5. WP2-WP7

后续工作包开始时，按照 Requirements 第 6 节和 Eval plan 补充对应 Gate。不得预填 `passed`。

## 6. 版本放行

只有总矩阵所有适用项为 `passed`，或外部 live source 项具有经用户接受且不影响版本核心声明的
明确 `blocked/partial` 限制，才可进入最终 Eval report。任何 Candidate、Intent、Matching、
Preparation、Feedback 核心项不得以 blocked/partial 标记为 Implemented / Accepted。
