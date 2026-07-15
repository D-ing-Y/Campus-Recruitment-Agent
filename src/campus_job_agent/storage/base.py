"""Repository boundaries used by the evidence domain."""

from typing import Protocol

from campus_job_agent.schemas import (
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


class ProfileRepository(Protocol):
    def save_profile(self, profile: ProfileSnapshot) -> ProfileSnapshot: ...

    def get_latest_profile(
        self, subject_id: str, profile_type: str
    ) -> ProfileSnapshot | None: ...

    def list_profiles(
        self, subject_id: str, profile_type: str | None = None
    ) -> list[ProfileSnapshot]: ...
