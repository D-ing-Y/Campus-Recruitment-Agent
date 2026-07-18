CREATE TABLE IF NOT EXISTS document_extractions (
  artifact_id TEXT PRIMARY KEY REFERENCES artifacts(artifact_id) ON DELETE CASCADE,
  payload_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS human_response_receipts (
  response_id TEXT PRIMARY KEY,
  idempotency_key TEXT NOT NULL UNIQUE,
  payload_hash TEXT NOT NULL,
  result_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_response_receipts_idempotency
  ON human_response_receipts(idempotency_key);
