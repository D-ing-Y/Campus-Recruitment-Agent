"""Shared schemas for Campus Job Agent."""

from campus_job_agent.schemas.candidate import (
    CandidateProfile,
    CapabilityAssessment,
    EducationRecord,
    ExperienceRecord,
)
from campus_job_agent.schemas.evidence import (
    ClaimExtractionBatch,
    ClaimExtractor,
    EvidenceArtifact,
    EvidenceClaim,
    EvidenceFragment,
    ExtractedClaim,
    ProfileSnapshot,
    Provenance,
)
from campus_job_agent.schemas.gap import GapAssessment, GapItem
from campus_job_agent.schemas.goal import ParsedGoal, PlanTask, SearchGoal
from campus_job_agent.schemas.intent import CareerIntent
from campus_job_agent.schemas.llm import (
    LLMCallRecord,
    LLMConfig,
    LLMRequest,
    LLMResponse,
)
from campus_job_agent.schemas.tool import ToolResult
from campus_job_agent.schemas.role import HiringSignal, RoleProfile, RoleRequirement
from campus_job_agent.schemas.trace import (
    RuntimeErrorRecord,
    TraceEvent,
    VerificationResult,
)

__all__ = [
    "ParsedGoal",
    "SearchGoal",
    "PlanTask",
    "LLMConfig",
    "LLMRequest",
    "LLMResponse",
    "LLMCallRecord",
    "ToolResult",
    "TraceEvent",
    "VerificationResult",
    "RuntimeErrorRecord",
    "EvidenceArtifact",
    "EvidenceFragment",
    "EvidenceClaim",
    "ExtractedClaim",
    "ClaimExtractionBatch",
    "ClaimExtractor",
    "Provenance",
    "ProfileSnapshot",
    "CandidateProfile",
    "CapabilityAssessment",
    "EducationRecord",
    "ExperienceRecord",
    "CareerIntent",
    "RoleProfile",
    "RoleRequirement",
    "HiringSignal",
    "GapAssessment",
    "GapItem",
]
