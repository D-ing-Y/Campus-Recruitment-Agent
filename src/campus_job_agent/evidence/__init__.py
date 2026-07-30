"""Evidence ingestion, extraction, validation and projection."""

from campus_job_agent.evidence.claim_extractor import ClaimExtractorService
from campus_job_agent.evidence.claim_validator import (
    CandidateClaimValidationError,
    CandidateClaimValidator,
    ClaimValidationError,
    ClaimValidator,
)
from campus_job_agent.evidence.candidate_predicates import (
    CandidatePredicate,
    CandidatePredicateError,
    normalize_human_candidate_value,
    parse_candidate_predicate,
    profile_path_to_candidate_predicate,
)
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
    "CandidateClaimValidationError",
    "CandidateClaimValidator",
    "CandidatePredicate",
    "CandidatePredicateError",
    "normalize_human_candidate_value",
    "parse_candidate_predicate",
    "profile_path_to_candidate_predicate",
    "CandidateProfileProjector",
]
