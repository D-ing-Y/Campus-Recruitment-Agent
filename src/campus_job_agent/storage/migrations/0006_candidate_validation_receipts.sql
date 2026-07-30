CREATE TABLE IF NOT EXISTS candidate_validation_receipts (
  receipt_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  subject_ref TEXT NOT NULL,
  status TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_candidate_validation_receipts_subject
  ON candidate_validation_receipts(subject_ref, created_at);

CREATE INDEX IF NOT EXISTS idx_candidate_validation_receipts_run
  ON candidate_validation_receipts(run_id, created_at);
