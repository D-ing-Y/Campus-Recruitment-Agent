"""Evidence ingestion, extraction, validation and projection."""

from campus_job_agent.evidence.claim_extractor import ClaimExtractorService
from campus_job_agent.evidence.claim_validator import ClaimValidationError, ClaimValidator
from campus_job_agent.evidence.fragmenter import DeterministicFragmenter
from campus_job_agent.evidence.ingestion import ArtifactIngestor, IngestionResult
from campus_job_agent.evidence.projector import CandidateProfileProjector

__all__ = [
    "ArtifactIngestor",
    "IngestionResult",
    "DeterministicFragmenter",
    "ClaimExtractorService",
    "ClaimValidator",
    "ClaimValidationError",
    "CandidateProfileProjector",
]
