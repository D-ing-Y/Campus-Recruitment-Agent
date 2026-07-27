CREATE TABLE IF NOT EXISTS preparation_records (
  record_id TEXT PRIMARY KEY,
  record_kind TEXT NOT NULL,
  owner_id TEXT NOT NULL,
  idempotency_key TEXT NOT NULL UNIQUE,
  lifecycle_status TEXT,
  payload_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_preparation_records_kind_owner
  ON preparation_records(record_kind, owner_id, created_at);

CREATE TABLE IF NOT EXISTS feedback_records (
  record_id TEXT PRIMARY KEY,
  record_kind TEXT NOT NULL,
  owner_id TEXT NOT NULL,
  idempotency_key TEXT NOT NULL UNIQUE,
  lifecycle_status TEXT,
  payload_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_feedback_records_kind_owner
  ON feedback_records(record_kind, owner_id, created_at);

CREATE TABLE IF NOT EXISTS v07_response_receipts (
  namespace TEXT NOT NULL,
  response_id TEXT NOT NULL,
  payload_hash TEXT NOT NULL,
  result_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY(namespace, response_id)
);

CREATE TABLE IF NOT EXISTS feedback_resolution_receipts (
  directive_id TEXT PRIMARY KEY,
  response_id TEXT NOT NULL,
  payload_hash TEXT NOT NULL,
  result_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);
