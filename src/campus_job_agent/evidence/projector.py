"""Evidence Claim to versioned candidate profile projection."""

from campus_job_agent.ontology import CapabilityOntology
from campus_job_agent.schemas import (
    CandidateProfile,
    CapabilityAssessment,
    EvidenceClaim,
    ProfileSnapshot,
    Provenance,
)
from campus_job_agent.storage.base import ProfileRepository


class CandidateProfileProjector:
    def __init__(
        self, repository: ProfileRepository, ontology: CapabilityOntology | None = None
    ) -> None:
        self.repository = repository
        self.ontology = ontology or CapabilityOntology.load_default()

    def project(self, subject_id: str, claims: list[EvidenceClaim]) -> ProfileSnapshot:
        capabilities: list[CapabilityAssessment] = []
        used_claims: list[str] = []
        for claim in claims:
            if claim.status != "active" or not claim.predicate.startswith("capability:"):
                continue
            raw_label = claim.predicate.split(":", 1)[1]
            resolution = self.ontology.resolve(raw_label)
            level = "unknown"
            if isinstance(claim.value, dict):
                level = str(claim.value.get("level", "unknown"))
            if level not in {"unknown", "beginner", "intermediate", "advanced", "expert"}:
                level = "unknown"
            capabilities.append(
                CapabilityAssessment(
                    capability_id=resolution.capability_id,
                    raw_label=resolution.canonical_name or raw_label,
                    level=level,
                    confidence=claim.confidence,
                    status="confirmed" if claim.claim_type != "model_inference" else "inferred",
                    supporting_claim_ids=[claim.claim_id],
                )
            )
            used_claims.append(claim.claim_id)
        profile = CandidateProfile(
            candidate_id=subject_id,
            capabilities=capabilities,
            supporting_claim_ids=used_claims,
        )
        latest = self.repository.get_latest_profile(subject_id, "candidate")
        snapshot = ProfileSnapshot(
            subject_id=subject_id,
            profile_type="candidate",
            version=1 if latest is None else latest.version + 1,
            profile_data=profile.model_dump(mode="json"),
            supporting_claim_ids=used_claims,
            provenance=Provenance(schema_version="v0.3"),
        )
        return self.repository.save_profile(snapshot)
