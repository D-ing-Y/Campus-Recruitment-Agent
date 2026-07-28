# v0.7.1 执行日志

状态：WP0 Passed；WP1 Not Started  
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
