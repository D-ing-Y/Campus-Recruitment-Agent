# v0.7.1 执行日志

状态：WP1.3.2 与 WP1/WP2 Revalidation Passed；WP3 未开始
创建日期：2026-07-28

本文件只记录实际执行事实，不记录计划性成功。每个工作包追加一节，保留失败、修复、命令、结果、
本地产物路径和限制。真实材料内容、Cookie、API key 和 live raw 不得复制到本文档。

## 记录模板

```text
### WPx / 日期 / commit

Scope:
Documents read:
Baseline commands:
Baseline result:
Failure evidence:
Contract changes:
Code changes:
Validation commands:
Validation result:
Local-only artifact refs:
Known limitations:
Gate decision: pass / fail / blocked / partial
Next action:
```

## 文档阶段

- 日期：2026-07-28
- 范围：确认 v0.7.1 整体路线、Requirements、RFC、ADR、Run/CLI contract、Tasks 与 Eval plan。
- 代码变更：无。
- 测试结果：沿用路线确认前的当前基线取证；本节不作为 v0.7.1 实现验收结果。
- Gate decision：文档完成后为 Ready for Implementation；WP0 尚未开始。

## WP0 基线 / 2026-07-28 / b198525

- Scope：RuntimeFactory、RunSession、CLI 与可观测运行基座；不进入 WP1。
- Documents read：v0.7.1 版本入口、Requirements、Tasks、RFC-0008、ADR-0008、
  CLI/Run contract、Eval plan、项目开发规则与既有 State/Human/Evidence/Tool/LLM contracts。
- Working tree：`main...origin/main`；存在用户尚未提交的 v0.7.1 文档变更与新增文件，已保留并在其上增量工作。
- Commit：`b198525 feat: 完成 v0.7 准备计划与反馈闭环`。
- Python/package：Python 3.13.2；`pyproject.toml` 为 0.7.0，但已安装 distribution metadata 仍报告 0.5.0。
- Baseline command：`.venv/bin/python -m pytest -q`。
- Baseline result：338 passed in 6.47s。
- CLI command：`.venv/bin/campus-agent --help`。
- CLI result：仅有 `run` 和 `auth`；console script 正确解析到当前 `.venv`，但无正式 session/doctor/inspect。
- Offline smoke：`.venv/bin/campus-agent run '成都 AI Agent 2027 秋招'` 返回 `status: success`，实际仅运行旧 Mini Runtime 与 mock job search。
- Arbitrary-cwd smoke：从 `/tmp/campus-agent-wp0-cwd.*` 调用同一已安装命令后，把 cache/report/run 写入该临时目录下的相对 `data/`，确认 cwd-dependent 缺陷。
- Artifact/trace reality：旧 run 只写 `trace.json`、`state.json`、`llm_calls.json` 和独立 report；没有 WP0 manifest/events/errors/artifact index/handoffs bundle。
- Known trace defect：Candidate `_trace` 固定 `status=success`、`duration_ms=0`；正式统一事件尚不存在。
- Gate decision：failing baseline；开始 WP0 实现。

## WP0 完成 / 2026-07-28 / working tree（未提交）

- Scope：只完成 RuntimeFactory、RunSession、CLI skeleton、统一 Run artifacts/events/inspect 基座；
  未进入 WP1，未实现 v1.0 Parent Graph，代码版本保持 0.7.0。
- Contract changes：无新增越界语义；实现遵循现有 v0.7.1 CLI/Run contract，并将 Session refs、
  domain repositories、checkpoint 和 Run artifacts 保持为不同责任边界。
- RuntimeFactory：支持显式 `--data-root` → 环境变量 → package/repository 定位 → 用户数据目录的
  cwd-independent 解析；统一装配 repositories、BlobStore、checkpointers、LLM/cache、ToolRegistry、
  SourceAdapter registry、CredentialResolver 和五个既有业务 workflow runtime。
- RunSession：新增 SQLite 持久化、owner/type/schema/stale predecessor 校验、version/CAS、历史、幂等
  current refs 和 typed handoff 单次 resolution；重启后可恢复，重复 resume 不重复推进。
- Run artifacts：每次诊断运行生成 `run_manifest.json`、`events.jsonl`、`errors.jsonl`、
  `llm_calls.jsonl`、`state.json`、`artifact_index.json`、`handoffs.jsonl`、`report.md`；manifest
  先写 `running` 再终态，JSONL append-only 且 sequence 单调，artifact index 不复制 private content。
- Event truthfulness：公共 `NodeObserver` 使用实测 duration、当前输出计数、route/reason/status；Candidate
  图不再固定 `success/0 ms`。公共适配接口已就绪，各业务图的完整纵向事件接线随 WP1-WP6 验收。
- CLI：新增默认引导、全局 `--json`、`doctor`、`session start/status/resume/history`、
  `inspect run/node/llm/evidence/claims/profile/handoff`；旧 `run` 保留但明确标为
  `legacy-mini-runtime`，不得视为正式业务闭环。
- Privacy：writer 对 secret/private/raw 长文本执行 redaction/hash；doctor 只暴露依赖状态和
  `api_key_present` 布尔值；新增测试验证 private profile/raw/credential 不进入可展示产物。
- Install command：`.venv/bin/python -m pip install --no-deps --no-build-isolation --force-reinstall .`。
- Install result：本地 wheel 构建并安装成功，distribution 为 0.7.0。Python 3.13 会跳过 editable
  安装生成的隐藏 `.pth`，因此本轮已安装 CLI 黑盒采用非 editable 本地 wheel；该兼容限制保留记录。
- Targeted command：`.venv/bin/python -m pytest -q tests/unit/test_v071_runtime_foundation.py`。
- Targeted result：24 passed in 0.83s。
- CLI black-box command：`.venv/bin/python -m pytest -q tests/integration/test_v071_cli_blackbox.py`。
- CLI black-box result：5 passed in 12.69s；覆盖仓库外 cwd、human/JSON、LLM 配置不完整、
  session/restart/idempotency、全 inspect 域、退出码 2-6、损坏 artifact 和 legacy 标识。
- CLI failure evidence：补充 exit 4 时，首次 provider-error 场景命中前序 legacy cache 而 exit 0；关闭该
  场景 cache 后暴露旧 Graph 将 provider error 收进 state 并统一 exit 6。最终在旧 trace 中保留
  `error_type`，由 legacy CLI adapter 读取安全错误类型并稳定映射为 `external_dependency/4`；不改变
  Mini Runtime 的业务语义。损坏 manifest 按设计返回 `storage_failure/5`，内部错误 6 由损坏 Session
  持久化不变量的 subprocess failure injection 验证。
- Candidate regression command：`.venv/bin/python -m pytest -q tests/integration/test_v04_candidate_profile_graph.py tests/evals/test_v04_candidate_profile_eval.py`。
- Candidate regression result：14 passed in 1.49s。此前一次命令误写不存在的 v0.7 文件名，pytest
  exit 4/no tests ran；纠正为真实 v0.4 Candidate 文件后通过，不把误命令计作代码通过或失败。
- Full regression command：`.venv/bin/python -m pytest -q`。
- Full regression result：367 passed in 19.60s；相对 338 基线新增 29 个测试，0 regression。
- Static checks：`.venv/bin/python -m compileall -q src tests` 与 `git diff --check` 均 exit 0。
- Arbitrary-cwd smoke：从 `/tmp/campus-agent-v071-final.ejDTU3` 调用已安装 CLI；无参数引导、
  `doctor --json`、`session start --json`、`inspect run --json` 均 exit 0，data root 解析到显式绝对路径。
- Local-only artifact refs：`/tmp/campus-agent-v071-final.ejDTU3/runtime-data/runs/`
  `run-962fab9d-357d-4202-b14d-05ecb4135f04/`；仅为本机临时 smoke，不提交。
- Smoke artifact actual：8 个契约文件全部存在；manifest 从 running 收口为 completed；events sequence
  为 1..4，`persist_session` duration 为 1 ms、`session_version=2`、route=`candidate.build`。
- Failure/terminal coverage：测试覆盖 10 个终态、初始化/终态 writer failure、ErrorEvent recovery hint、
  concurrent sequence、stale CAS、duplicate resume/handoff、稳定退出码 2-6。
- Known limitations：WP0 只提供正式运行基座和诊断型 session 命令；Candidate、Intent、Role、Matching、
  Preparation、Feedback 的正式业务 CLI/纵向闭环尚未接入。未运行 DeepSeek semantic smoke、live source
  或真实材料验收；这些不是 WP0 成功声明的一部分。
- Gate decision：pass。Runtime/CLI 行适用 gate 全部通过；版本总状态仍为 Ready for Implementation。
- Suggested commit message（未执行）：`feat: 完成 v0.7.1 WP0 运行时与 CLI 可观测基座`。
- Next action：进入 WP1 前先增量收口 Candidate predicate/receipt/batch contracts，再以真实材料和
  DeepSeek semantic smoke 验收；不得以 WP0 诊断 session 代替业务 Workflow 成功。

## WP1 基线 / 2026-07-28 / 6e131f8

- Scope：Evidence + CandidateProfileGraph predicate、逐项 receipt、批事务、投影、正式 CLI 和恢复；
  不进入 WP2，不实现 Parent Graph。
- Working tree：`main...origin/main`，启动时 clean；WP0 已由用户提交为
  `6e131f8 feat: 完成 v0.7.1 WP0 运行时与 CLI 可观测基座`。
- Python/package：代码版本仍为 0.7.0；不得在 WP1 提前升级 v0.7.1。
- Baseline command：`.venv/bin/python -m pytest -q`。
- Baseline result：367 passed in 20.42s。
- CLI baseline：已安装 CLI 只有 doctor/session/inspect/legacy run/auth，没有
  `candidate build/resume/show/diff`。
- Contract/code mismatch：RFC 已规定 `capability:<id>`、`education:<record>.<field>`、
  `experience:<record>.<field>`；当前 prompt/Projector/人工 target path 同时使用 `education.<field>`、
  `education:<field>`、`experiences[<id>]` 等不同语法。
- Validator baseline：只验证 fragment/owner/JSON/supersede，不验证 Candidate predicate、value shape
  或 projector support，因此 schema-valid/domain-invalid Claim 可进入 active set。
- Batch baseline：`ExtractCandidateClaimsTool` 使用 list comprehension 逐项 `validate_and_save`；中间项
  异常时前项可能已经提交，却对调用方返回整批失败，且没有逐项 ValidationReceipt。
- Projection baseline：education 只有隐式单记录且没有稳定 education ID；Projector 静默忽略不支持
  predicate，可能出现 Claim 已保存但 supporting/projected 为 0 的空画像。
- Fragment baseline：只有 `processed_fragment_ids`，不能区分 accepted、all rejected、retryable 和 fatal。
- Gate decision：failing baseline；先更新 Candidate/Evidence/LLM/State 增量契约，再写失败测试。

## WP1 离线完成与 E4 阻塞 / 2026-07-28 / working tree（未提交）

- Scope：完成 Evidence + CandidateProfileGraph 的 predicate/receipt/batch/projector/正式 CLI 纵向修复；
  不进入 WP2，不实现 Parent Graph，不修改 0.7.0 版本号。
- Contract changes：Candidate Claim 新增 `candidate_claim_v0.7.1` 语法：
  `capability:<ontology-id>`、`education:<record-id>.<field>`、
  `experience:<record-id>.<field>`；Evidence contract 明确逐模型项 ValidationReceipt、Claim + receipt
  同批原子事务及 fragment 四态；LLM/State contracts 同步 prompt/schema 和运行字段。
- Failure-first evidence：首个测试在 collection 阶段因 `CandidateClaimValidator` 不存在而失败；补齐最小
  Validator 后，mixed batch 测试继续暴露“无逐项 receipt”和“多 education 不投影”；安装态 CLI 测试
  首次返回 argparse exit 2，确认正式 `candidate` 命令缺失。
- Domain repair：Prompt v3 只允许版本化 predicate、ontology ID、稳定 record ID 和字段 value shape；
  Validator 拒绝 legacy/unsupported/unknown capability/invalid value，Projector 支持稳定多 education/
  experience，并保留旧 schema 只读回放兼容。不可投影模型项只产生 rejected receipt，不进入 active Claim。
- Persistence repair：每个模型项保留 item index、fragment refs、extractor、prompt/schema 和 reason codes；
  accepted/duplicate Claim 与全部 receipts 在一个 SQLite transaction 中提交，中间写失败回滚整批。
  fragment 可区分 `processed_with_accepted_claims`、`processed_all_rejected`、
  `retryable_extraction_failure`、`fatal_validation_failure`。
- Graph/HITL repair：人工回答 target path 确定性转换成 canonical predicate；list 字段在人工边界规范化；
  request ID 使用 `request-`，跨进程 checkpoint resume 可切换到新 run_id。模型结构化输出持续无效时不再
  错误路由为“补材料”，而是 failed terminal run + `llm_invalid_output` ErrorEvent。
- CLI：新增安装态 `candidate build/resume/show/diff`，`resume` 支持 answer/upload/correct/confirm/
  skip/cancel；`inspect claims` 同时返回 Claim 和 ValidationReceipt。显式 upload 只在 Application 边界
  授权所提交文件的父目录，底层 Graph 仍拒绝越界路径。
- Idempotency repair：响应 canonical hash 排除自动时间 `submitted_at`，所以跨进程同内容重试可复用；
  重复 build 在 Claim 集未变时复用 current Snapshot，修复“无语义变化却先清空 completion_reason、
  再新增两个 Snapshot”的虚假版本推进。
- Metrics：Candidate run 从实际 repository/checkpoint 输出计算 archive、locator、逐项 receipt、引用、
  predicate support、projection、reject reason、snapshot trace 和 silent-unprojected 数量；不再把这些值
  作为预填成功常量。
- Unit/CLI command：`.venv/bin/python -m pytest -q tests/unit/test_v071_candidate_claim_contract.py tests/integration/test_v071_candidate_cli.py`。
- Unit/CLI result：11 passed in 15.75s；覆盖 mixed/all-rejected/fatal-validation、事务中间失败回滚、多记录投影、安装态
  build/show/diff/inspect、五类 HITL action、跨进程 resume、duplicate response/build 和模型失败诊断。
- Candidate regression command：`.venv/bin/python -m pytest -q tests/integration/test_v04_candidate_profile_graph.py tests/evals/test_v04_candidate_profile_eval.py`。
- Candidate regression result：14 passed in 1.65s。
- Full regression command：`.venv/bin/python -m pytest -q`。
- Full regression result：378 passed in 35.87s；相对 WP1 基线新增 11 个测试，0 regression。
- Installed package：本地 wheel 重新安装成功；`campus-agent doctor` 报 package 0.7.0、
  `feature_stage=v0.7.1-wp1`、SQLite/checkpointer/path 全部可用。
- Arbitrary-cwd smoke：从 `/private/tmp/campus-agent-wp1-final.OToOmX` 调用已安装 CLI，正式
  `candidate build` completed；八项 Candidate rate 为 1.0，
  `duplicate_resume_write_count=0`、`silent_unprojected_active_claim_count=0`。
- Local-only artifact refs：`/private/tmp/campus-agent-wp1-final.OToOmX/runtime-data/runs/`
  `run-2c0c2f5a-02f0-4499-ba91-855d29ff29d7/`；仅为 mock/fixture 本机 smoke，不提交。
- Static/privacy checks：`.venv/bin/python -m compileall -q src tests` 与 `git diff --check` exit 0；
  对最终 smoke 的 run bundle 搜索 fixture 正文片段、Bearer/API key 模式为 0 命中。
- Working tree：本轮修改保持未提交；未覆盖用户 WP0 commit，未创建分支或 commit。
- E4 blocker：doctor 当前 provider=`mock`、`api_key_present=false`；仓库中没有用户授权简历 PDF，只有
  README/fixture。未发送私人材料，未运行 DeepSeek，未做真实候选人画像人工复核。因此真实材料、
  DeepSeek projection 和 real review 不得标 passed，Candidate 不得用于最终 Matching 验收。
- Gate decision：E0-E3 离线/契约/安装态 CLI passed；E4 与真实人工复核 blocked；WP1 总状态 partial，
  v0.7.1 仍为 Ready for Implementation。
- Next action：由用户明确提供/授权简历 PDF、项目 README 和 DeepSeek 配置后运行 E4，并由用户复核
  education、experience/responsibility、Agent/Python/LLM 能力、unknown/conflict 和不应采集字段；通过后
  才能把 WP1 设为 Passed 并进入 WP2。

## WP1 E4 前置：CLI UI / Model Provider 基线 / 2026-07-29 / 6e131f8

- User decision：不以自动加载 `.env` 作为正式配置；采用 CC Switch 风格 Provider/Profile，并开始建设
  项目统一 CLI UI，当前只开放 Model 配置。
- Working tree：保留 2026-07-28 未提交 WP1 修复，不提交、不进入 WP2。
- CC Switch reference：官方仓库 commit `87b0e3fb85335bc4436aa1a7281c688952d942ae`；确认
  Provider 字段、SQLite SSOT、`is_current` 事务切换、active Provider 不可删除和 UI/service/DAO 分层。
- Baseline：正式 CLI 只有 `doctor/session/inspect/candidate/run/auth`；无 `model` 命令，真实 TTY 无项目
  CLI UI。`RuntimeFactory/load_llm_config` 只读进程环境变量。
- Historical diagnosis：根目录 `.env` 存在且被 Git ignore，但正式 CLI 从未自动加载；历史 DeepSeek
  Eval 脚本自带 `_load_env`，所以 Eval 可用而 `doctor` 当前为 mock。现有 `LocalCredentialStore` 只接入
  Source Cookie/Authorization，未实现 LLM API key import/resolve。
- Security baseline：`.env` 当前 mode 0644；本轮不读取/输出 key 值，也不以该文件作为新实现来源。
- Gate decision：failing baseline；先写 ModelProfile/SecretStore/CLI UI failure tests，再实现。

## WP1 E4 前置：CLI UI / Model Provider 完成 / 2026-07-29 / working tree（未提交）

- Scope：按用户决策实现 CC Switch 风格 Model Provider 与项目统一 CLI UI；当前只开放 Model 配置，
  不进入 WP2、不实现 Parent Graph、不修改 0.7.0 版本号。
- Failure-first：新增测试首次在 collection 阶段因 `campus_job_agent.runtime.model_profiles` 不存在失败；
  随后依次补齐 Provider repository、SecretStore、RuntimeFactory、one-shot CLI 和 CLI UI。
- Solved：正式 Runtime 不再要求用户手工把已保存 key 注入当前 shell；active Provider 元数据/current
  状态由 SQLite 持久化，RuntimeFactory 启动时解析 credential ref，并只在 LLM 边界读取 key。
- Provider contract：对外采用 `id/appType/name/settingsConfig/websiteUrl/category/createdAt/sortIndex/
  notes/icon/iconColor/isCurrent`；内置 `mock-default`，支持 DeepSeek、custom OpenAI-compatible、mock。
- CLI：真实 TTY 无参数进入项目首页；当前 Model Configuration 支持 list/add/edit/switch/show/test/remove；
  未开放的 Workflow、Data & Privacy、Diagnostics 明确显示 unavailable。非 TTY 无参数仍返回安全引导。
- One-shot：新增 `model add/edit/list/show/use/remove/test`；add/edit 的 key 仅从隐藏输入或
  `--api-key-stdin` 读取，显式 `--api-key` 返回 `invalid_input/2` 且不回显参数值。
- Secret safety：Provider SQLite 只保存 `local-secret://llm/<profile-id>`；SecretStore 目录 `0700`、文件
  `0600`、临时文件 + fsync + 原子替换。该机制是本机文件权限边界，不宣称系统 Keychain 加密。
- Atomicity：current switch 使用 `BEGIN IMMEDIATE` 且唯一部分索引保证至多一个 current；current
  Provider 不可删除。edit 轮换 key 后若 SQLite 更新失败，会恢复旧 key，避免跨存储半成功。
- `.env` boundary：正式 Runtime 不隐式加载 cwd `.env`；安装态 subprocess 在 cwd 放置带假 key 的
  `.env` 后仍使用 SQLite active Provider。显式进程环境变量只保留给自动化兼容。
- Install：首次 wheel 重装因 build isolation 尝试联网获取 setuptools 而失败；改用现有环境的
  `pip install --no-build-isolation --no-deps --force-reinstall .` 后成功，未下载依赖。
- Model tests：`.venv/bin/python -m pytest -q tests/unit/test_v071_model_profiles.py
  tests/integration/test_v071_model_cli_ui.py` → 9 passed in 5.76s。
- Candidate/Runtime targeted regression：41 passed in 27.54s。
- Full regression：`.venv/bin/python -m pytest -q` → 387 passed in 38.22s，0 regression。
- TTY smoke：从 `/private/tmp/campus-agent-model-ui.RHweUg` 通过 pseudo-TTY 调用已安装、无参数
  `campus-agent`，显示统一首页，输入 `0` 后 `Goodbye`/exit 0。
- Privacy/static：测试扫描 Provider DB bytes、doctor/list/edit/remove stdout/stderr 和异常，假 key 无泄漏；
  `git diff --check`、`compileall` 通过，非测试 diff 未命中测试 key 或 `sk-` 模式。
- Gate decision：CLI UI / Model Provider 离线门禁 passed；真实 DeepSeek `model test` 未运行，真实 key、
  简历和 README 未读取/发送，因此 WP1 E4/真实人工复核仍 blocked，WP1 总状态仍为 partial。
- Solved problems：解决“本地保存的 LLM key 无正式读取链路”“只能靠 shell 环境配置”“没有 Provider
  CRUD/current switch”“没有统一 CLI UI”“配置输出可能缺少统一脱敏边界”“编辑失败可能形成半成功”六类问题。
- Next action：用户在 CLI UI 中添加并激活 DeepSeek，显式执行最小 `model test`；随后用
  `candidate build --input` 选择授权简历/README，完成 E4 与人工复核后才进入 WP2。

## Model Add 默认值交互修复 / 2026-07-29 / working tree（未提交）

- Problem：Add Provider 强制普通用户理解并填写内部 Provider ID；Preset、显示名和 activate 等可推导
  字段也要求逐项输入，无法形成“连续回车直到 API key”的本地配置体验。
- Failure-first：新增 UI 测试首先因 `style_defaults` 参数和 `suggest_profile_id` 不存在而失败。
- Repair：Add Provider 默认选择 DeepSeek；Provider ID 默认 `deepseek-main`，已存在时依次建议
  `deepseek-main-2/-3`；显示名、官方 Base URL、默认 Model 和 activate 均提供默认值。
- Interaction：真实 TTY 使用 ANSI dim 显示行内默认值；空输入接受默认值，非空输入覆盖默认值。
  custom OpenAI-compatible 的 Base URL/model 无可靠预设，仍保持必填；API key 始终隐藏且必填。
- Edit parity：Edit Provider 的现值和 key rotation/删除确认默认项也使用同一浅色提示函数。
- Unit/model CLI：`.venv/bin/python -m pytest -q tests/unit/test_v071_cli_ui_defaults.py
  tests/unit/test_v071_model_profiles.py tests/integration/test_v071_model_cli_ui.py` → 11 passed in 6.47s。
- Installed pseudo-TTY：从 `/private/tmp/campus-agent-ui-defaults.KgFSqy` 进入安装态 UI，连续回车采用
  DeepSeek 全部默认值并停在 API key；输入测试 key 后回车保存并激活 `deepseek-main`，终端不回显 key。
- Full regression：`.venv/bin/python -m pytest -q` → 389 passed in 39.21s，0 regression。
- Gate decision：默认值/覆盖/自动避重交互 passed；真实 DeepSeek 网络调用仍未执行，WP1 E4 状态不变。

## Model 行内默认占位修正 / 2026-07-29 / working tree（未提交）

- Clarification：用户要求 `Preset : 1`，而不是 `Preset [1]:`；浅色 `1` 是可接受的占位值，首个用户
  输入出现时整段默认值必须消失，不能与用户输入并列。
- Failure-first：新增 key-level 渲染测试，旧实现因 `_read_default_keys` 不存在在 collection 阶段失败。
- Repair：真实 TTY 使用标准库 `termios/tty` 进入 raw mode；每次按键清行重绘。空输入返回默认值，
  首字符重绘为纯用户输入；支持 UTF-8 输入/粘贴、退格、Ctrl-U、Enter、Ctrl-C 和 Ctrl-D。
- Fallback：非 TTY 和注入式测试仍走普通 reader，不新增 `prompt_toolkit/readchar` 依赖，不影响 JSON CLI。
- Installed pseudo-TTY：`Preset : dim-1` 输入 `3` 后只显示 `Preset : 3`；
  `Provider ID : dim-mock-local` 输入 `custom-mock` 后默认 ID 完整消失，保存成功。
- Focused tests：Model UI/Profile/installed CLI 共 12 passed in 6.47s。
- Full regression：`.venv/bin/python -m pytest -q` → 390 passed in 38.48s，0 regression。

## Linux 提示风格与真实 Provider 健康检查 / 2026-07-29 / working tree（未提交）

- UI rule：Preset 等枚举项必须显式输入，显示 `Preset:`，空回车返回 `Invalid preset`；不得设置默认
  占位值。自由文本的可推导默认值继续使用可替换 dim placeholder。
- Boolean rule：Activate 统一为 `[Y/n]`；Rotate API key、Remove 统一为 `[y/N]`；空回车选择大写项，
  `y/yes/n/no` 大小写不敏感。三处共用 `_read_yes_no`，不各自解释默认语义。
- Failure-first：新增 Linux yes/no prompt test，首先因 `_read_yes_no` 不存在在 collection 阶段失败。
- Installed pseudo-TTY：`Preset:` 空回车显示 `Invalid preset`；显式选择 Mock 后，Activate 显示
  `Activate now? [Y/n] `，空回车保存并激活成功。
- Safe discovery：`model list` 确认 current=`deepseek-main`、key present=true、model=`deepseek-v4-flash`；
  命令只读取安全元数据，未输出 key。
- Live health check：首次沙箱内 `model test deepseek-main` 因 DNS/网络隔离返回
  `external_dependency/4`；经用户请求所包含的真实 Provider 测试授权联网重试后，返回
  `status=available`、`provider=openai_compatible`、`business_material_sent=false`。
- Focused tests：Model UI/Profile/installed CLI 共 13 passed in 5.97s。
- Full regression：`.venv/bin/python -m pytest -q` → 391 passed in 39.81s，0 regression。
- Gate decision：CLI Linux style 与真实 Provider health gate passed；尚未发送简历/项目 README，
  Candidate E4/人工复核仍 blocked，下一步需用户明确选择实际文件路径。

## WP1 真实简历 E4 与回放修复 / 2026-07-29 / working tree（未提交）

- Authorization boundary：用户明确上传真实简历 PDF 并授权 DeepSeek 流程测试；项目 README
  尚未准备好且明确省略。本轮未读取仓库 README，未向 Provider 发送项目 Agent 材料。
- PDF verification：1 页 PDF 可视化渲染和文本提取均通过；只记录结构化检查结果，不将电话等
  私密字段写入本执行日志或 CandidateProfile。
- Failure 1：`run-0c221f17-...` 首次调用在 30 秒发生 read timeout，且被错误压平为
  `llm_output_error`。修复 Provider/StructuredOutput/Graph 错误类型和 retryable 传播，并为 Provider
  profile 增加可配置 `timeout_seconds`，DeepSeek 默认调整为 90 秒。
- Failure 2：Prompt v3 真实输出使用通用 `education/project_experience/skill` predicate，6 项全部被
  domain validator 拒绝。Prompt v4 增加 canonical predicate 和 ontology 示例后，真实输出可逐项投影。
- Failure 3：结构化输出第二次重试成功时，cache 只保存 retry request key，原始 request 回放仍
  试图联网。修复为将已验证输出同时 alias 到原始与 retry cache key。
- Failure 4：真实重建中模型对同一记录产生 `exp1/proj1`、`edu1/school1` 等不同临时 ID，
  并使用 `2024/2024-07` 不同粒度，造成重复经历与伪冲突。修复为在 Claim 构建前按教育身份
  字段/经历标题生成 canonical hash ID，并将毕业日期归一化为四位年份；模型 ID 只作为
  当次 batch 分组标签。
- Failure 5：`run-17593916-6377-4cfe-8f7f-2e91a9b470e8` 中 DeepSeek 两次将 `confidence`
  输出为 `"high"`，严格 float schema 终止且未生成 snapshot。Prompt v5 改用完整 Claim JSON 示例并
  强制数字范围；schema 边界将精确 `high/medium/low` 别名确定性归一化为 `0.9/0.6/0.3`，
  其他非法值仍拒绝。
- Final live E4：`run-3c7ea885-1dfe-44b7-bf14-040c303c9459` completed，Prompt v5，17/17
  Claim accepted；model item receipt、predicate support、fragment reference、projection、snapshot trace 指标均为 1.0，
  silent unprojected count=0，无冲突。产物只包含简历支持的教育、研究/项目经历和能力，
  未包含未提供的项目 Agent 经历。
- Offline replay：`run-c118fb8b-8717-42bb-8b54-d282fe14a1ff` 在未授权网络的环境中 completed，
  `cache_hit=true`、17 个 receipt 均为 duplicate，snapshot ID 和全部 Claim IDs 与 live E4 完全一致。
- Tests：安装态 Candidate/Model 聚焦回归 20 passed；稳定 ID/prompt/confidence 单元回归 14 passed；
  全量 398 passed in 40.11s，`compileall` 与 `git diff --check` 通过。
- Gate decision：WP1 E4 passed；WP1 总状态仍为 partial，唯一剩余门禁是用户人工复核真实画像。
  用户确认前不进入 WP2。

## WP1 用户画像复核 / 2026-07-29

- User review：用户确认 E4 CandidateProfile 的教育、经历责任边界和能力等级正确。
- Gate decision：Real review passed；WP1 退出条件已满足，总状态更新为 Passed。WP2 仍未开始。

## WP1.1 LangChain Model / Tool / MCP 接入层重构 / 2026-07-30 / working tree（未提交）

- Scope：仅重构 LLM、Tool、MCP 接入协议；保留 LangGraph State/条件边、Evidence、
  Validator、Projector、Repository、CLI 与 Run artifact；未进入 WP2/Parent Graph。
- Doc-first：新增 WP1.1 Requirements、RFC-0009、ADR-0009、Integration Contract、Tasks 和
  Eval plan，实现后新增实际 Eval report。
- Baseline：398 passed in 39.28s；失败测试首次 collection 因 LangChain provider/ToolSpec/MCP
  module 不存在而失败。
- Dependencies：生产依赖增加 LangChain provider integrations 和 `langchain-mcp-adapters`；
  `langchain-mcp-adapters 0.3.1` 导入时与 `mcp 2.0` 不兼容，显式约束
  `mcp>=1.24,<2`，实际使用 1.29.0。
- Model：新增 `ModelCapabilities`、capability-aware strategy、`LangChainChatProvider` 和统一
  factory；RuntimeFactory、ModelProfileService 和 legacy Planner 切到新 factory。旧 SQLite profile 可由
  URL/model 推导 integration，无需人工迁移。
- Structured output：auto 顺序为 native schema → tool calling → JSON mode；显式 strategy
  不静默回退，auto 运行回退记录 reason。Cache key 包含 strategy/capability fingerprint，
  receipt 包含 integration/requested/effective/capabilities/fallback。
- Tool：新增 `ToolSpec`、独立协议 `wire_name`、Pydantic args 校验和 LangChain
  `StructuredTool` adapter；旧 Tool 默认 internal-only，write/model_action 无确认时拒绝。
- MCP：新增 `MCPServerConfig` 和 `MCPToolCatalog`；stdio/streamable HTTP 配置、credential ref、
  tool allowlist、重名门、脱敏诊断和 per-server 失败隔离已实现。FastMCP math fixture 的
  allowlisted `add` 调用通过。
- Live health：`model test deepseek-main` 返回 available，integration=deepseek、
  tool_calling=true，business_material_sent=false。
- Live failure：首次简历 run `run-d64a0a09-69cd-4460-b225-b84cd1badd1c` 在 Tool Calling
  返回 HTTP 400。无业务数据最小 schema 定位为 DeepSeek Thinking 默认开启与强制
  `tool_choice` 冲突；Provider adapter 依 capability 对 structured call 关闭 Thinking 后最小
  Pydantic schema 通过。
- Final live E4：`run-d85876df-03f9-4d42-a488-f5f09f72caa9` completed；20 项模型
  输出中 18 accepted、2 rejected 且原因完整；归档、locator、receipt、predicate support、
  reference、projection、snapshot trace 均 1.0，silent unprojected=0。
- Offline replay：同 owner `run-189e9c9a-fde1-47da-8d12-a98be289c35f` 在未授权网络下
  completed，cache_hit=true，18 duplicate，Snapshot/Claim IDs 与 live 完全一致。不同 owner 不共享
  带 fragment provenance 的缓存，这是正确权限边界。
- Tests：WP1.1 定向 21 passed；unit 306 passed；full 410 passed in 66.90s，相对基线
  新增 12 项，0 regression；`compileall` 和 `git diff --check` 通过。
- Gate decision：WP1.1 Passed。限制是 MCP 仅 fixture/协议层验收，未验证任意第三方
  MCP Server，未在 CLI UI 开放 MCP 配置；WP2 仍未开始。

## WP2 CareerIntent 首次入口 / 2026-07-30 / working tree（未提交）

- Doc-first：先新增 WP2 Requirements、RFC-0010、ADR-0010、CareerIntent Contract、Tasks 和
  Eval plan，再实现代码。
- Scope：新增 raw intent evidence、structured candidate、领域 Validator、HITL、snapshot、
  SearchScope、typed handoff、CLI/inspect；未进入 WP3 真实岗位来源或 v1.0 Parent Graph。
- Failure 1：Intent repository 记录 ID 选择顺序使 validation receipt 错用 `draft_id`，与 draft
  主键冲突；已改为 receipt/confirmation/scope/request/draft 显式 ID 顺序。
- Failure 2：Graph list 无 reducer 导致跨 resume 诊断历史可丢失；已改 append reducer。
- Failure 3：同 key 偏好 revision 时覆盖，且未修改 constraint 的 provenance 被错重写为 patch；
  已改为聚合去重，仅修改字段引用 response fragment。
- Failure 4：模型失败曾统一记为 `internal_error`，且中断阶段的 confirmed/projection 指标曾预填
  1.0；已保留失败 LLM receipt、区分 unavailable/invalid output，未评估指标改为 null。
- Live semantic failure：DeepSeek 首次将“互联网科技公司”识别为 industry，最终非缓存 E4
  又将 recruitment constraint 标为 negotiable。这证明 Tool Calling 只控制结构；已由确定性
  policy 归一 company type 并校正 hard/preference，问题仍要求用户修订后才发布。
- Final live E4：`run-b2b9ddee-610a-476d-bf51-4c54310352c5`，DeepSeek
  `effective_strategy=tool_calling`、`cache_hit=false`、Pydantic accepted；修订 run
  `run-5f1e583e-9f42-4e24-a166-cf5c239672c3`，确认 run
  `run-8692976b-144b-4e33-b569-ae5af73952a3` completed。
- Outputs：CareerIntent `intent-snapshot:f50118c95f38e33dfa79f129`；SearchScope
  `0361ec3f-0445-487c-8b38-43f0910d5620`；Handoff `handoff:1a6cd482b65e65d63ac05dad`。
- Idempotency/privacy：重复 confirm 返回 deduplicated，session version 不变；E4 Run artifacts 中raw
  intent 和 secret pattern 命中数均为 0。
- Tests：424 tests 分组全量验证（349 eval/unit + 58 历史 integration + 17 v0.7.1 installed
  integration）；wheel 重新安装，`compileall` 与 `git diff --check` 通过。
- Gate decision：WP2 Passed；下一工作包为 WP3 Role live source。代码版本保持 0.7.0，
  v0.7.1 整体仍未完成。

## WP1.2 Candidate 经历 Taxonomy 补丁 / 2026-07-30 / working tree（未提交）

- Problem：真实 Tool Calling 已通过 Pydantic，但旧 Candidate schema 未向模型暴露经历 kind、
  context、capability id/level 的完整固定值；中文经历标签和 `proficient` 被领域 Validator 拒绝，
  缺失 kind 还可能被 Projector 错默认为 project。
- Research：参考 MIT、Stanford、Yale、Europass、教育部求职材料，并结合 LangChain structured
  output 与 Pydantic discriminated union 规范，确定开放原文 + 封闭双轴 taxonomy。
- Contract：新增 12 类 experience kind、18 类 context 和 raw label；新模型协议改为
  `claim_kind` discriminated union；capability id/level 也作为 Tool enum 暴露；旧 predicate/value
  只在迁移边界保留。
- Semantic failure：首次 v6 真实 run `run-4e139cc7-ab98-4e44-bc38-8b971129d8d8` 虽然 Tool/Pydantic
  success，却把 C++/Java/Pandas 等强制映射到近似 capability，产生 3 个画像伪冲突。v7 增加
  exact ontology mapping Prompt 与 Validator 一致性门禁，未建模技能改为 reasoned rejection。
- Final live E4：`run-a0047b90-e893-4ced-8df8-314cdd0d3838`，cache miss、DeepSeek 非思考
  Tool Calling、Pydantic success；36 项中 18 accepted、18 reasoned rejected，0 conflict，状态
  completed，next action=`intent.create`。两段经历正确区分 public-funded 与 academic research，
  raw label/context 可追溯，九项 Candidate 指标均 1.0。
- Tests：WP1/WP2 聚焦 41 passed；全量 428 passed in 85.47s；`compileall`、`git diff --check`
  通过。
- Boundary：本补丁解决经历分类和固定值暴露，不声称已覆盖荣誉或所有技能 ontology；这些信息
  保留在原始 Evidence，未建模模型项有 rejection receipt。WP2 保持 Passed，下一工作包仍为 WP3。

## WP1.3 Structured Resume Evidence / 2026-07-31 / working tree（未提交）

- Doc-first：新增 Requirements/Tasks、RFC-0012、ADR-0012、Resume Evidence Contract、Eval Plan，
  随后更新 State/HITL/Tool/LLM/CLI/Candidate/版本验收文档。
- Problem：旧 PDF→Claim→Profile 直通流程把忠实转录和画像推断混在一个模型协议中，用户无法在
  Claim 生成前确认 PDF 解析结果，BOSS 风格字段也受 Candidate predicate 反向限制。
- Code：新增 ResumeDraft、ResumeReview、ResumeEvidenceSnapshot、SourceRef、additive migration、
  ResumeEvidenceGraph、Runtime/CLI；Candidate build 改为 confirmed Snapshot typed handoff。
- Safety：pypdf 主解析、pdfplumber 条件回退、无 OCR；PII 本地提取与模型输入脱敏；run artifacts
  不保存原文件名、完整正文或未脱敏 Prompt；Review Draft CAS 与 Receipt 单事务提交。
- Failure repaired：非法审核输入曾在 LangGraph interrupt 消费后污染 checkpoint；现已在 resume 前
  preflight。retry 曾因 owner/artifact 唯一键复用旧 extracting Draft；现对同 Draft 做 CAS 重建。
  Candidate 后续 upload 曾被错误套用 ResumeEvidence fragment scope；现拆分 confirmed resume 与
  conversation evidence batch。Session status 曾仅按 `current_stage=candidate` 误报
  `resume.import`；现优先按 pending request 类型返回 `resume.resume/candidate.resume/intent.resume`。
- Automated verification：全量 `439 passed`；`compileall` 与 `git diff --check` 通过；installed
  CLI `doctor` 完成且本地 DeepSeek profile 配置完整。
- Real DeepSeek：`run-13496fb4-f439-411e-be4d-73e7c9a34f7c` 成功生成 Draft
  `resume-draft-b51904fa-985d-49e3-bca5-7a80c90f51bd`，pypdf 质量通过，31 个非空叶子字段均有
  SourceRef；LangChain DeepSeek effective strategy 为 Tool Calling、cache miss、0 retry、Pydantic
  accepted；已识别 PII 与原 PDF 文件名在 run artifacts 中 0 命中。
- Current boundary：真实 E4 停在 `personal_information` 人工审核点；尚未发布 Snapshot，也未使用
  新 CandidateSnapshot 重跑 WP2。WP1/WP2 当前状态为 partial，不把代码通过冒充人工验收通过。

## WP1.3.1 Resume Fidelity Correction / 2026-07-31 / working tree（未提交）

- Problem：WP1.3 首轮真实审核暴露四类问题：pypdf 默认流式文本使双栏教育内容串位；无标签出生日期/
  籍贯未进入个人信息；列表逐条确认后仍要求整体确认；Source 展示来自整页兜底，既不精确也不适合 CLI。
- Contract correction：新增 WP1.3.1 Requirements 与 Eval Plan；规定 layout-aware 解析、字段级精确
  SourceRef、非空列表末项自动完成、空列表显式 `confirmed_empty`、显式 `--reparse` 和不可变版本链。
- Parser and extraction：pypdf 改用 layout extraction，低质量时仍只回退 pdfplumber；本地个人信息提取器
  在简历首部识别无标签出生日期和籍贯；DeepSeek Prompt 保留“至今”并按视觉邻接绑定教育附加信息；
  确定性后处理将组合奖项拆为独立记录。
- Provenance：移除整页 SourceRef 兜底；应用按 canonical JSON Pointer 为每个非空叶子字段定位归一化
  精确 span，无法定位则阻止进入审核及发布。CLI 仅显示当前字段附近的有限片段和页码。
- Review and versioning：非空列表确认最后一条后自动完成区块，空区块仍需用户确认；新增
  `resume import --reparse`，新 Draft 记录 predecessor，旧 Draft/Snapshot 不覆盖，新 Snapshot 只会在
  全部区块确认后以递增版本发布。跨进程 Claim 基线固定在 Draft，确认前增量 Claim 保持 0。
- Database：新增 additive migration `0008_resume_reparse_versions.sql`，在保留既有 Draft、Receipt、
  Snapshot 和外键完整性的前提下移除 owner/artifact 单 Draft 唯一限制；真实数据库副本迁移后
  `foreign_key_check` 为空。
- Automated verification：全量 `444 passed`；`compileall` 与 `git diff --check` 通过。
- Real DeepSeek：cache-miss Tool Calling run `run-ceb5ff01-4ab0-4dc6-9dd7-e3f6bf6a0b8a` 通过
  Pydantic；最终审核 run `run-82482f23-7a67-4545-b476-e98c82c6935f` 生成 Draft
  `resume-draft-6d718b55-ade8-406b-97ab-3d8df888ae67`。审计结果为 36/36 非空字段具有精确
  SourceRef，个人信息、两段项目、两段教育、技能和两条独立奖项与 PDF 对齐；缺失的个人优势、
  期望职位和工作经历保持空值，PII leak=0，pre-confirmation Claim delta=0。
- Current boundary：新 Draft 停在 `personal_information`，等待用户按八区块完成确认；旧 Snapshot
  保持不可变且不作为新链路输入。新 ResumeEvidenceSnapshot、CandidateSnapshot 和 WP2 replay 尚未
  发生，因此 WP1/WP2 保持 partial，v0.7.1 整体仍为实施中。

## WP1.3.2 Claim Lifecycle / 2026-08-02 至 2026-08-03 / working tree（未提交）

- Problem：旧 Resume、当前 Resume、长期对话和反馈的 active Claim 被直接混合投影，形成 SQL 元数据、
  Python 等级和毕业时间精度伪冲突；相同代码长期使用也会复现，并非只有代码升级才发生。
- Doc-first：新增 Requirements、RFC-0013、ADR-0013、Tasks、Eval Plan，并更新 Evidence、Candidate、
  State、Tool、HITL、CLI contracts。
- Minimal implementation：EvidenceClaim 增加 origin/effective/multi-lineage；新增一个纯确定性 resolution
  模块；SQLite JSON payload 不迁移、不新增表；Candidate/Feedback 共用选择规则。
- Correction：一次人工修订只创建一条 user_reported successor，在单事务内 supersede 所有前驱；
  跨 subject/predicate、cycle 和非 active predecessor 拒绝。
- Projection：Profile 保存 `evidence_basis_ids`；capability/kind/string/date 使用窄语义规则；跨来源不同
  值仍生成 conflict，不能按时间或 confidence 静默覆盖。
- Recovery：failed/pending/current refs 驱动准确 next_action。真实验收另发现 Candidate 最终投影漏传
  basis 与跨 Session 复用 immutable ObjectRef 的 identity conflict，均以最小接线修复并补回归。
- Automated：全量与安装态结果记录在 `v0.7.1-wp1.3.2-eval-report.md`；`compileall` 与
  `git diff --check` 通过。
- Real Candidate：保留旧错误 run，经公开 CLI cancel/resume/build，最终 run
  `run-22685159-b474-4100-9a02-7d8e9d43a261` 发布 Candidate
  `89514ab4-758e-4d54-90af-f740519c40b1`；17 selected，41 legacy model isolated，0 conflict，
  basis/trace 指标通过。
- WP2 replay：create `run-a07e2376-2ae9-4b86-ad56-082d8070ebdb` 明确引用新 Candidate；最终
  confirmation `run-56709fb6-8489-4fdd-8339-3d1e3cc0938d` 发布 CareerIntent、SearchScope 和 pending
  WP3 handoff。
- Gate decision：WP1/WP2 revalidation Passed；下一工作包为 WP3 Role live source。WP3 尚未消费
  handoff，v0.7.1 整体仍为 In Progress，代码版本保持 0.7.0。

## WP3 Role Hierarchy/Evidence Gates Preflight / 2026-08-03 / working tree（未提交）

- Problem：多个 target role 被绑定到第一个 family；搜索噪声可继承 Scope family；search card
  可绕过详情证据；经验内容靠字符串误挂具体 JD。
- Doc-first：新增 Requirements、RFC-0014、ADR-0014、Contracts、Tasks、Eval Plan/Report 和 live
  source support matrix。
- Minimal implementation：新增 RoleTargetBinding、RoleFamilyMembership、RoleDetailEvidenceReceipt
  和 ExperienceScopeLink；保留 JobInstanceRoleProfile → RoleFamilyProfile 两级结构，不引入图数据库、
  向量聚类或通用规则引擎。
- Multi-scope：CareerIntent 按 family 输出 plural Scope/Handoff，单 family 保留 singular 兼容；
  Matching intent impact 按完整 scope set 计算，RebuildDirective 传递 `requested_scopes`。
- Evidence gates：只有 accepted membership 进入 cluster；只有 `job_detail`/
  `employer_job_detail`/`official_job_detail` Raw Artifact 可产生具体岗位画像；只有 confirmed
  ExperienceScopeLink 可投影 HiringSignal。
- Replay correction：新线程复用已存在 official verification plan 时会恢复 plan ID，避免
  后续证据链因 State 缺失而降级。新门禁默认 tool budget 由 50 调整为 80，显式低预算仍按
  hard limit 终止。
- Verification：聚焦回归 43 passed；全量 `466 passed in 174.90s`；`compileall` 与
  `git diff --check` 通过。
- Boundary：现有 live HTTP adapters 只能证明 search/transport 代码存在，尚未完成并验收
  recruitment detail、experience post detail 和 official detail kind。下一阶段仍是 WP3 live source。
