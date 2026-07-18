"""Repository boundaries used by the evidence domain."""

from typing import Protocol

from campus_job_agent.schemas import (
    DocumentExtraction,
    EvidenceArtifact,
    EvidenceClaim,
    EvidenceFragment,
    ProfileSnapshot,
)


class BlobStore(Protocol):
    def put(self, key: str, data: bytes) -> str: ...

    def get(self, uri: str) -> bytes: ...

    def exists(self, uri: str) -> bool: ...

    def delete(self, uri: str) -> None: ...


class EvidenceRepository(Protocol):
    def save_artifact(self, artifact: EvidenceArtifact) -> EvidenceArtifact: ...

    def get_artifact(self, artifact_id: str) -> EvidenceArtifact | None: ...

    def find_artifact_by_hash(
        self, content_hash: str, owner_id: str | None = None
    ) -> EvidenceArtifact | None: ...

    def save_fragment(self, fragment: EvidenceFragment) -> EvidenceFragment: ...

    def get_fragment(self, fragment_id: str) -> EvidenceFragment | None: ...

    def list_fragments(self, artifact_id: str) -> list[EvidenceFragment]: ...

    def save_claim(self, claim: EvidenceClaim) -> EvidenceClaim: ...

    def get_claim(self, claim_id: str) -> EvidenceClaim | None: ...

    def list_claims(self, subject_id: str) -> list[EvidenceClaim]: ...

    def list_active_claims(self, subject_id: str) -> list[EvidenceClaim]: ...

    def mark_claim_superseded(self, claim_id: str) -> EvidenceClaim: ...

    def save_extraction(
        self, extraction: DocumentExtraction
    ) -> DocumentExtraction: ...

    def get_extraction(self, artifact_id: str) -> DocumentExtraction | None: ...

    def save_response_receipt(
        self,
        *,
        response_id: str,
        idempotency_key: str,
        payload_hash: str,
        result: dict,
    ) -> dict: ...

    def get_response_receipt(self, response_id: str) -> dict | None: ...


class ProfileRepository(Protocol):
    def save_profile(self, profile: ProfileSnapshot) -> ProfileSnapshot: ...

    def get_latest_profile(
        self, subject_id: str, profile_type: str
    ) -> ProfileSnapshot | None: ...

    def list_profiles(
        self, subject_id: str, profile_type: str | None = None
    ) -> list[ProfileSnapshot]: ...

    def get_profile(self, snapshot_id: str) -> ProfileSnapshot | None: ...
