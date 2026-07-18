"""Evaluation package."""
"""Evaluation contracts and deterministic metrics."""

from campus_job_agent.evals.evidence import EvidenceEvalReport, evaluate_evidence
from campus_job_agent.evals.candidate_profile import (
    CandidateProfileEvalCase,
    CandidateProfileEvalReport,
    evaluate_candidate_profile,
)

__all__ = [
    "EvidenceEvalReport",
    "evaluate_evidence",
    "CandidateProfileEvalCase",
    "CandidateProfileEvalReport",
    "evaluate_candidate_profile",
]
