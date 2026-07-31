PRAGMA foreign_keys = OFF;

CREATE TABLE resume_drafts_v2 (
  draft_id TEXT PRIMARY KEY,
  owner_id TEXT NOT NULL,
  candidate_id TEXT NOT NULL,
  artifact_id TEXT NOT NULL REFERENCES artifacts(artifact_id) ON DELETE RESTRICT,
  revision INTEGER NOT NULL CHECK(revision >= 1),
  status TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

INSERT INTO resume_drafts_v2
SELECT draft_id, owner_id, candidate_id, artifact_id, revision, status,
       payload_json, created_at, updated_at
FROM resume_drafts;

DROP TABLE resume_drafts;
ALTER TABLE resume_drafts_v2 RENAME TO resume_drafts;

CREATE INDEX idx_resume_drafts_owner_artifact
  ON resume_drafts(owner_id, artifact_id, updated_at DESC);

PRAGMA foreign_keys = ON;
