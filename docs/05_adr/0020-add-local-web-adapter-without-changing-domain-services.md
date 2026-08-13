# ADR-0020: 新增本地 Web Adapter，保持领域服务不变

状态：Accepted
日期：2026-08-13

## 决策

在 `apps/web` 新增 React 前端和本地 ASGI API adapter。Web adapter 直接组合现有 `RuntimeFactory` 与 Application Service，禁止复制或修改 ResumeEvidence、CandidateProfile、Session、Validator、Projector、RoutePolicy 和 Repository 业务逻辑。

ADR-0008 对 v0.7.1 的 CLI-first 决策继续作为历史事实；本 ADR 只覆盖 v0.7.2 Web Candidate Vertical Slice。

## 约束

- 固定本地用户，不把请求中的任意 `user_id` 作为认证事实。
- Web API 使用现有 object refs、pending request、CAS 和 idempotency key。
- 浏览器状态不是业务事实源。
- 本阶段不更换数据库、不部署公网、不引入 Parent Graph。
- evaluator 和 question planner 通过现有 Graph 构造边界保持可替换，Web 不引用具体实现类。

## 结果

CLI 与 Web 可以独立演进，但共享相同的业务闭环和验收证据。后续引入认证、队列或远程存储时替换 Web/Runtime 组合边界，不要求重写 Candidate Graph。
