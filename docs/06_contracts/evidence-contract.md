# Evidence Contract

证据追溯先于 RAG。每条证据必须支持回溯到原始来源。

初始草案：

```json
{
  "evidence_id": "uuid",
  "source_url": "https://example.com",
  "platform": "mock",
  "content_type": "job_posting",
  "retrieved_at": "2026-07-07T00:00:00+08:00",
  "raw_path": "data/evidence/raw/example.html",
  "text_path": "data/evidence/text/example.txt",
  "hash": "sha256",
  "metadata": {}
}
```

