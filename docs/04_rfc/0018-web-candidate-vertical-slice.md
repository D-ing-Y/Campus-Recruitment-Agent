# RFC-0018: 本地 Web Candidate 纵向切片

状态：Accepted / Implemented
日期：2026-08-13

## 背景

ResumeEvidence 与 CandidateProfile 已通过 Application Service 暴露稳定的构建、恢复和查询边界，但正式交互入口仍是 CLI。项目需要一个可在面试中直接展示、也便于早期真实用户体验的产品界面，同时不能因提前服务化而复制现有业务逻辑或掩盖证据门禁。

ADR-0008 的“v0.7.1 只做 CLI”已经完成了其保护目标：Runtime、RunSession、可观测性以及 Candidate/Resume 纵向闭环均已有稳定入口。本 RFC 不修改 v0.7.1 的历史决策，而是在 v0.7.2 新增一个受限 Web adapter。

## 架构

```text
Browser React UI
  → local JSON/multipart API adapter
  → RuntimeFactory
  → SessionService / ResumeApplicationService / CandidateApplicationService
  → existing Graph / Tool / Validator / Projector / Repository
```

Web API 不通过 subprocess 调用 CLI。CLI 与 Web 是同级 adapter，共用 Application Service。

## 交互状态

前端只根据以下稳定字段决定页面：

- `status`
- `current_stage`
- `current_refs`
- `pending_request`
- `next_action`
- `output_refs`
- `errors`

Resume 审核内容由 `ResumeApplicationService.review_view()` 返回；Candidate 展示读取 immutable ProfileSnapshot。前端不得自行推断 Graph 节点状态或改写业务引用。

## 上传边界

- 浏览器 multipart 上传进入 Web adapter 的受控临时目录。
- adapter 只把服务器生成的临时路径交给 Application Service。
- Application Service 完成归档或失败返回后，adapter 清理临时文件。
- 首次 Resume import 只接受 PDF；Candidate pending material 沿用现有文本 PDF、Markdown、TXT、README 能力。
- 原文件和业务证据仍由现有 BlobStore 管理，Web 不建立第二份权威存储。

## 同步执行

首版保持单进程、同步请求和现有 SQLite/checkpoint。界面显示 processing 状态并禁止重复提交。长任务队列、流式事件和多 worker 属于后续服务化工作包。

## 可替换模型边界

Web 不感知 Deterministic 或 LLM evaluator。只要后端继续返回 `SufficiencyAssessment`、`HumanInteractionRequest` 和 Candidate response envelope，未来切换 evaluator/question planner 不改变页面路由。

## 备选方案

### CLI subprocess wrapper

拒绝。它会重复解析 JSON、丢失 Python 异常类型并使上传路径与进程生命周期复杂化。

### 在前端重写状态机

拒绝。会绕过 Graph checkpoint、CAS、幂等和证据化的人机交互。

### 立即建设公开多用户平台

拒绝。当前没有完整认证、用户删除、异步队列、远程 SecretStore 或多 worker 验收。

## 影响

- 收益：尽早获得可使用的产品入口，并用真实用户交互发现 Candidate 充分性问题。
- 成本：增加前端、ASGI adapter、上传安全和浏览器状态映射测试。
- 限制：只能声明本地单用户产品切片，不能声明 production-ready。
