"""Shared schemas for Campus Job Agent."""

from campus_job_agent.schemas.candidate import (
    CandidateProfile,
    CapabilityAssessment,
    EducationRecord,
    EvidenceCoverage,
    ExperienceRecord,
    ResponsibilityBoundary,
)
from campus_job_agent.schemas.candidate_graph import (
    BudgetState,
    CandidateProfileGraphState,
    CounterState,
    InformationGap,
    ProfileCorrection,
    ProfileVersionDiff,
    QuestionItem,
    QuestionPlan,
    SufficiencyAssessment,
)
from campus_job_agent.schemas.document import DocumentExtraction, ExtractionUnit
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
from campus_job_agent.schemas.human_interaction import (
    HumanAnswer,
    HumanInteractionRequest,
    HumanInteractionResponse,
    RequestedMaterial,
)
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
    "ResponsibilityBoundary",
    "EvidenceCoverage",
    "InformationGap",
    "SufficiencyAssessment",
    "QuestionItem",
    "QuestionPlan",
    "ProfileCorrection",
    "ProfileVersionDiff",
    "HumanAnswer",
    "RequestedMaterial",
    "HumanInteractionRequest",
    "HumanInteractionResponse",
    "CandidateProfileGraphState",
    "BudgetState",
    "CounterState",
    "DocumentExtraction",
    "ExtractionUnit",
    "CareerIntent",
    "RoleProfile",
    "RoleRequirement",
    "HiringSignal",
    "GapAssessment",
    "GapItem",
]
