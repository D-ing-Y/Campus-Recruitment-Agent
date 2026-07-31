CREATE TABLE IF NOT EXISTS resume_drafts (
  draft_id TEXT PRIMARY KEY,
  owner_id TEXT NOT NULL,
  candidate_id TEXT NOT NULL,
  artifact_id TEXT NOT NULL REFERENCES artifacts(artifact_id) ON DELETE RESTRICT,
  revision INTEGER NOT NULL CHECK(revision >= 1),
  status TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(owner_id, artifact_id)
);

CREATE TABLE IF NOT EXISTS resume_review_receipts (
  receipt_id TEXT PRIMARY KEY,
  response_id TEXT NOT NULL UNIQUE,
  draft_id TEXT NOT NULL REFERENCES resume_drafts(draft_id) ON DELETE RESTRICT,
  payload_hash TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS resume_evidence_snapshots (
  resume_evidence_id TEXT PRIMARY KEY,
  draft_id TEXT NOT NULL REFERENCES resume_drafts(draft_id) ON DELETE RESTRICT,
  owner_id TEXT NOT NULL,
  candidate_id TEXT NOT NULL,
  artifact_id TEXT NOT NULL REFERENCES artifacts(artifact_id) ON DELETE RESTRICT,
  version INTEGER NOT NULL CHECK(version >= 1),
  payload_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(draft_id),
  UNIQUE(owner_id, artifact_id, version)
);

CREATE INDEX IF NOT EXISTS idx_resume_drafts_owner_artifact
  ON resume_drafts(owner_id, artifact_id);
CREATE INDEX IF NOT EXISTS idx_resume_snapshots_owner_candidate
  ON resume_evidence_snapshots(owner_id, candidate_id, version DESC);
