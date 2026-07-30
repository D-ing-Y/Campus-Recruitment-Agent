# ADR-0010：以已确认 IntentConstraint 作为 CareerIntent 约束事实源

## 状态

Accepted

## 日期

2026-07-30

## 背景

现有 CareerIntent 同时包含 locations/industries 等扁平字段和 constraints。若两者都允许独立写入，
matching、SearchScope 和 revision 会读取不同事实。首次入口还必须区分“模型理解”和“用户确认”。

## 决策

1. v0.7.1 新 snapshot 中，约束/偏好值、kind、status 和 source_ref 以 confirmed `IntentConstraint`
   为 canonical source。
2. 扁平 discovery 字段只由确定性 projector 生成，并在 CareerIntent Pydantic validator 中检查一致性。
3. target role 保持独立 top-level canonical field；role family 由版本化 alias policy 投影。
4. LLM 只生成 candidate；领域 Validator 与 Human Gate 后才能发布 snapshot。
5. SearchScope 只从 confirmed snapshot 投影，不读取 raw text 或 CandidateProfile。
6. legacy snapshot 继续兼容读取，但任何新版本发布都必须满足 v0.7.1 一致性规则。

## 备选方案

- 仅保留扁平字段：无法表达 hard/negotiable、status 和来源；不采用。
- 仅保留 constraints 并删除扁平字段：会破坏 v0.5/v0.6 消费者；本版本不采用大爆炸迁移。
- 信任 LLM 同时生成两套字段：会制造双写漂移；不采用。

## 影响

- 新增 projector/consistency validator 和迁移测试；
- confirmed constraint 均可追溯到 raw fragment 或 human response；
- matching 与 role research 复用同一 canonical intent，降低跨 Workflow 漂移。
