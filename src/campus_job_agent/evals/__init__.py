"""Evaluation contracts and deterministic metrics."""

from campus_job_agent.evals.evidence import EvidenceEvalReport, evaluate_evidence
from campus_job_agent.evals.candidate_profile import (
    CandidateProfileEvalCase,
    CandidateProfileEvalReport,
    evaluate_candidate_profile,
)
from campus_job_agent.evals.profile_matching import (
    ProfileMatchingEvalCase,
    ProfileMatchingEvalReport,
    evaluate_profile_matching,
)

__all__ = [
    "EvidenceEvalReport", "evaluate_evidence",
    "CandidateProfileEvalCase", "CandidateProfileEvalReport", "evaluate_candidate_profile",
    "ProfileMatchingEvalCase", "ProfileMatchingEvalReport", "evaluate_profile_matching",
]
from campus_job_agent.evals.preparation_feedback import V07EvalCaseResult, aggregate_v07_metrics

__all__ = ["V07EvalCaseResult", "aggregate_v07_metrics"]
