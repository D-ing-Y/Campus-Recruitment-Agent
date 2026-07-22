CREATE TABLE IF NOT EXISTS role_records (
  record_id TEXT PRIMARY KEY,
  record_kind TEXT NOT NULL,
  idempotency_key TEXT NOT NULL UNIQUE,
  payload_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_role_records_kind
  ON role_records(record_kind, created_at);

CREATE TABLE IF NOT EXISTS source_batch_receipts (
  idempotency_key TEXT PRIMARY KEY,
  batch_id TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS source_credentials (
  credential_ref TEXT PRIMARY KEY,
  source_id TEXT NOT NULL,
  credential_type TEXT NOT NULL,
  secret_path TEXT NOT NULL,
  created_at TEXT NOT NULL
);
