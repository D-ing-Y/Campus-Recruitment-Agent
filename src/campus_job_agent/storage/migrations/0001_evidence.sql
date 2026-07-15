CREATE TABLE IF NOT EXISTS schema_migrations (
  version TEXT PRIMARY KEY,
  applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS artifacts (
  artifact_id TEXT PRIMARY KEY,
  owner_id TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(owner_id, content_hash)
);

CREATE TABLE IF NOT EXISTS fragments (
  fragment_id TEXT PRIMARY KEY,
  artifact_id TEXT NOT NULL REFERENCES artifacts(artifact_id) ON DELETE CASCADE,
  payload_json TEXT NOT NULL,
  UNIQUE(artifact_id, fragment_id)
);

CREATE TABLE IF NOT EXISTS claims (
  claim_id TEXT PRIMARY KEY,
  subject_id TEXT NOT NULL,
  predicate TEXT NOT NULL,
  idempotency_key TEXT NOT NULL UNIQUE,
  payload_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS claim_fragments (
  claim_id TEXT NOT NULL REFERENCES claims(claim_id) ON DELETE CASCADE,
  fragment_id TEXT NOT NULL REFERENCES fragments(fragment_id) ON DELETE RESTRICT,
  PRIMARY KEY(claim_id, fragment_id)
);

CREATE TABLE IF NOT EXISTS profile_snapshots (
  snapshot_id TEXT PRIMARY KEY,
  subject_id TEXT NOT NULL,
  profile_type TEXT NOT NULL,
  version INTEGER NOT NULL CHECK(version >= 1),
  payload_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(subject_id, profile_type, version)
);

CREATE INDEX IF NOT EXISTS idx_fragments_artifact ON fragments(artifact_id);
CREATE INDEX IF NOT EXISTS idx_claims_subject ON claims(subject_id);
CREATE INDEX IF NOT EXISTS idx_profiles_subject_type
  ON profile_snapshots(subject_id, profile_type, version DESC);
