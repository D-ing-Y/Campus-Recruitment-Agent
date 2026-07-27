CREATE TABLE IF NOT EXISTS matching_records (
  record_id TEXT PRIMARY KEY,
  record_kind TEXT NOT NULL,
  owner_id TEXT NOT NULL,
  idempotency_key TEXT NOT NULL UNIQUE,
  lifecycle_status TEXT,
  payload_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_matching_records_kind_owner
  ON matching_records(record_kind, owner_id, created_at);

CREATE TABLE IF NOT EXISTS matching_response_receipts (
  response_id TEXT PRIMARY KEY,
  payload_hash TEXT NOT NULL,
  result_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);
