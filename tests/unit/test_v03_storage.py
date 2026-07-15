import hashlib
import sqlite3

import pytest

from campus_job_agent.schemas import (
    CareerIntent,
    ClaimExtractor,
    EvidenceArtifact,
    EvidenceClaim,
    EvidenceFragment,
    ProfileSnapshot,
    RoleProfile,
)
from campus_job_agent.storage import LocalBlobStore, SQLiteRepository


def _artifact(blob_uri: str) -> EvidenceArtifact:
    return EvidenceArtifact(
        owner_id="owner",
        source_type="fixture",
        content_type="text/plain",
        original_name="resume.txt",
        raw_uri=blob_uri,
        content_hash=hashlib.sha256(b"resume").hexdigest(),
    )


def test_blob_store_is_atomic_immutable_and_scoped(tmp_path) -> None:
    store = LocalBlobStore(tmp_path / "blobs")
    uri = store.put("raw/a.txt", b"first")
    assert store.get(uri) == b"first"
    assert store.put("raw/a.txt", b"first") == uri
    with pytest.raises(FileExistsError):
        store.put("raw/a.txt", b"different")
    with pytest.raises(ValueError):
        store.put("../escape", b"bad")
    store.delete(uri)
    assert not store.exists(uri)


def test_repository_migration_dedup_claim_idempotency_and_snapshots(tmp_path) -> None:
    repository = SQLiteRepository(tmp_path / "evidence.sqlite3")
    artifact = repository.save_artifact(_artifact("file:///tmp/resume"))
    duplicate = _artifact("file:///tmp/duplicate")
    assert repository.save_artifact(duplicate).artifact_id == artifact.artifact_id
    fragment = repository.save_fragment(
        EvidenceFragment(
            artifact_id=artifact.artifact_id,
            locator_type="char_range",
            locator={"start": 0, "end": 6},
            text="resume",
            text_hash=hashlib.sha256(b"resume").hexdigest(),
        )
    )
    claim = EvidenceClaim(
        subject_id="candidate",
        predicate="capability:Python",
        value={"level": "beginner"},
        claim_type="observed_fact",
        evidence_fragment_ids=[fragment.fragment_id],
        confidence=0.8,
        extractor=ClaimExtractor(provider="mock", model="mock"),
        prompt_version="v0.3.0",
    )
    assert repository.save_claim(claim).claim_id == claim.claim_id
    copy = claim.model_copy(update={"claim_id": "different-id"})
    assert repository.save_claim(copy).claim_id == claim.claim_id

    candidate = ProfileSnapshot(
        subject_id="candidate",
        profile_type="candidate",
        version=1,
        profile_data={"candidate_id": "candidate"},
    )
    intent = CareerIntent(user_id="candidate", target_roles=["AI Agent"])
    role = RoleProfile(
        role_profile_id="role-1",
        profile_scope="job_instance",
        role_title="AI Agent Engineer",
    )
    repository.save_profile(candidate)
    repository.save_profile(
        ProfileSnapshot(
            subject_id="candidate",
            profile_type="career_intent",
            version=1,
            profile_data=intent.model_dump(mode="json"),
        )
    )
    repository.save_profile(
        ProfileSnapshot(
            subject_id="role-1",
            profile_type="role",
            version=1,
            profile_data=role.model_dump(mode="json"),
        )
    )
    assert repository.get_latest_profile("candidate", "candidate") == candidate
    assert len(repository.list_profiles("candidate")) == 2
    assert repository.get_latest_profile("role-1", "role") is not None


def test_repository_foreign_key_failure_rolls_back_claim(tmp_path) -> None:
    repository = SQLiteRepository(tmp_path / "evidence.sqlite3")
    claim = EvidenceClaim(
        subject_id="candidate",
        predicate="capability:Python",
        value=True,
        claim_type="observed_fact",
        evidence_fragment_ids=["missing-fragment"],
        confidence=0.5,
        extractor=ClaimExtractor(provider="mock", model="mock"),
        prompt_version="v0.3.0",
    )
    with pytest.raises(sqlite3.IntegrityError):
        repository.save_claim(claim)
    assert repository.get_claim(claim.claim_id) is None
