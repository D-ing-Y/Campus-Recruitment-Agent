# RFC-0003: 统一证据层与双画像领域基础

状态：Proposed
日期：2026-07-15
关联需求：`docs/03_requirements/v0.3-evidence-domain-foundation.md`

## 1. 背景

后续候选人画像、岗位画像、匹配、学习计划和反馈更新都依赖真实材料。如果模型直接从聊天上下文生成画像，错误无法追溯和重建。v0.3 先建立可复用证据与领域边界，再进入 Graph 业务循环。

## 2. 设计概览

```text
local fixture/file
  → ArtifactIngestor
  → LocalBlobStore
  → ArtifactRepository
  → TextExtractor
  → Fragmenter
  → ClaimExtractor(LLM structured output)
  → ClaimValidator
  → ClaimRepository
  → ProfileProjector / SnapshotRepository
```

v0.3 使用 SQLite + 本地文件，但所有调用通过 repository/blob-store 接口，避免未来分布式迁移污染 Agent 节点。

## 3. 模块建议

```text
src/campus_job_agent/
  schemas/
    evidence.py
    candidate.py
    intent.py
    role.py
    gap.py
  evidence/
    ingestion.py
    fragmenter.py
    claim_extractor.py
    claim_validator.py
    repositories.py
  storage/
    base.py
    local_blob.py
    sqlite.py
    migrations/
  ontology/
    capabilities.py
  prompts/
    claim_extractor.py
  evals/
    evidence.py
```

## 4. 存储模型

SQLite 表建议：

- `artifacts`
- `fragments`
- `claims`
- `claim_fragments`
- `profile_snapshots`
- `schema_migrations`

文件目录：

```text
data/evidence/
  raw/<artifact_id>/<original_name>
  text/<artifact_id>.txt
data/profiles/
data/runs/
```

原始文件不可变；派生文本允许按 parser version 重建，但必须使用新 URI 或版本字段。

## 5. 接口边界

```python
class BlobStore(Protocol):
    def put(self, namespace: str, name: str, data: bytes) -> str: ...
    def get(self, uri: str) -> bytes: ...
    def exists(self, uri: str) -> bool: ...

class EvidenceRepository(Protocol):
    def save_artifact(self, artifact: EvidenceArtifact) -> EvidenceArtifact: ...
    def get_artifact(self, artifact_id: str) -> EvidenceArtifact | None: ...
    def find_artifact_by_hash(self, content_hash: str) -> EvidenceArtifact | None: ...
    def save_fragments(self, fragments: list[EvidenceFragment]) -> None: ...
    def get_fragment(self, fragment_id: str) -> EvidenceFragment | None: ...
    def save_claim(self, claim: EvidenceClaim) -> EvidenceClaim: ...
    def list_claims(self, subject_id: str) -> list[EvidenceClaim]: ...

class ProfileRepository(Protocol):
    def save_snapshot(self, snapshot: ProfileSnapshot) -> ProfileSnapshot: ...
    def get_latest(self, subject_id: str, profile_type: str) -> ProfileSnapshot | None: ...
```

AgentState 只保存 ID 和小型结构化摘要，不保存二进制或完整原文。

## 6. Claim 提取

复用 v0.2：

- `LLMProvider`
- cache
- prompt/schema version
- JSON/Pydantic validation
- retry
- `LLMCallRecord`

需要先将 structured output 从 SearchGoal 专用函数抽出泛型入口：

```python
parse_structured(
    request,
    output_model,
    prompt_name,
    prompt_version,
    schema_version,
)
```

Claim prompt 只能看到明确传入的 Fragment；输出必须包含引用 ID。引用存在性由确定性 Validator 检查，而不是相信模型。

## 7. 原子性与幂等

本地阶段采用以下近似：

1. 计算 hash 并检查重复。
2. 写入临时 blob。
3. 原子 rename 到最终路径。
4. 在 SQLite 事务中插入 Artifact metadata。
5. metadata 写入失败时清理本次新 blob；重复 hash 返回已有 Artifact。

Claim 使用稳定幂等键：

```text
hash(subject_id + predicate + normalized_value + sorted(fragment_ids) + schema_version)
```

## 8. Capability Ontology

v0.3 只实现小型、版本化的 YAML/JSON ontology：

```text
capability_id
canonical_name
aliases
parent_id
category
version
```

禁止把自由文本技能名直接用于最终双画像比较。无法规范化时保存 raw label 和 unknown mapping，后续人工或模型补充。

## 9. 安全

- fixture 必须匿名。
- Artifact metadata 不保存 Cookie、Authorization header 或 API key。
- trace 只记录 ID、hash、大小、类型、状态和错误摘要。
- 删除接口不在 v0.3 对外暴露，但 repository 设计必须支持未来按 owner 清理。

## 10. 测试

- schema 单元测试；
- hash/dedup 单元测试；
- blob + SQLite repository 集成测试；
- Claim 引用校验测试；
- structured output mock 测试；
- Artifact→Fragment→Claim 端到端 fixture 测试；
- v0.1/v0.2 回归测试。

## 11. 迁移策略

- 不删除当前 `agent/graph.py` 和 `SearchGoal`。
- 新 evidence/domain 模块与旧 Mini Runtime 并存。
- v0.3 先通过独立 CLI 命令或测试入口验收。
- v0.4 再把 Evidence Store 接入候选人画像 subgraph。
