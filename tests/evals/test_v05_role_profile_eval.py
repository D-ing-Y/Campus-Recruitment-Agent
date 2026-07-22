import hashlib

from campus_job_agent.evals.role_profile import (
    credential_secret_leak_count, raw_before_parse_rate, role_claim_trace_rate,
    runtime_generated_code_execution_count, source_authority_violation_count,
)
from campus_job_agent.schemas import EvidenceArtifact, EvidenceClaim, EvidenceFragment, SourceDocument
from campus_job_agent.schemas.evidence import ClaimExtractor
from campus_job_agent.storage import LocalBlobStore, SQLiteRepository


def _evidence(tmp_path, channel="recruitment_discovery"):
    repo = SQLiteRepository(tmp_path / "db.sqlite"); blob = LocalBlobStore(tmp_path / "blob")
    raw = b"AI Agent role"; digest = hashlib.sha256(raw).hexdigest()
    artifact = repo.save_artifact(EvidenceArtifact(owner_id="u", source_type="fixture", content_type="text/plain", original_name="raw",
                                                    raw_uri=blob.put("raw", raw), content_hash=digest, metadata={"channel":channel}))
    fragment = repo.save_fragment(EvidenceFragment(artifact_id=artifact.artifact_id, locator_type="char", locator={"start":0,"end":len(raw)},
                                                   text=raw.decode(), text_hash=digest))
    return repo, artifact, fragment


def test_eval_raw_before_parse_rate_is_full_for_archived_documents(tmp_path):
    repo, artifact, fragment = _evidence(tmp_path)
    doc = SourceDocument(source_id="fixture", channel="recruitment_discovery", query_id="q", source_url="fixture://job",
                         document_kind="job_detail", raw_artifact_id=artifact.artifact_id, content_hash=artifact.content_hash)
    assert raw_before_parse_rate([doc], repo) == 1.0


def test_eval_role_claim_trace_rate_is_full(tmp_path):
    repo, artifact, fragment = _evidence(tmp_path)
    claim = EvidenceClaim(subject_id="job:1", predicate="requirement.item:0", value="Python", claim_type="observed_fact",
                          evidence_fragment_ids=[fragment.fragment_id], confidence=.9,
                          extractor=ClaimExtractor(provider="deterministic", model="eval"), prompt_version="v1", schema_version="v0.5")
    assert role_claim_trace_rate([claim], repo) == 1.0


def test_eval_detects_community_authority_violation(tmp_path):
    repo, artifact, fragment = _evidence(tmp_path, "experience")
    claim = EvidenceClaim(subject_id="role:1", predicate="qualification.degree", value="硕士", claim_type="observed_fact",
                          evidence_fragment_ids=[fragment.fragment_id], confidence=.5,
                          extractor=ClaimExtractor(provider="deterministic", model="eval"), prompt_version="v1", schema_version="v0.5")
    assert source_authority_violation_count([claim], repo) == 1


def test_eval_secret_leak_count_zero_for_refs():
    state = {"credential_refs":{"zhaopin_jobs":"local-secret://zhaopin_jobs/default"}}
    assert credential_secret_leak_count(state, ["cookie-secret", "Bearer abc"]) == 0


def test_eval_runtime_never_executes_llm_generated_code():
    trace = [{"node":"collect","action":"source.discover_jobs","generated_by":"deterministic"}]
    assert runtime_generated_code_execution_count(trace) == 0
