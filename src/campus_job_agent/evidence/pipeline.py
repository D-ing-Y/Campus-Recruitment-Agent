"""Independent v0.3 evidence pipeline; the legacy Mini Runtime remains intact."""

from pathlib import Path

from pydantic import BaseModel, Field

from campus_job_agent.evals import EvidenceEvalReport, evaluate_evidence
from campus_job_agent.evidence.claim_extractor import ClaimExtractorService
from campus_job_agent.evidence.claim_validator import ClaimValidator
from campus_job_agent.evidence.fragmenter import DeterministicFragmenter
from campus_job_agent.evidence.ingestion import ArtifactIngestor
from campus_job_agent.evidence.projector import CandidateProfileProjector
from campus_job_agent.schemas import (
    EvidenceArtifact,
    EvidenceClaim,
    EvidenceFragment,
    LLMCallRecord,
    ProfileSnapshot,
)
from campus_job_agent.storage.base import BlobStore, EvidenceRepository, ProfileRepository


class EvidenceTraceEvent(BaseModel):
    event: str
    artifact_id: str | None = None
    fragment_count: int | None = None
    claim_count: int | None = None
    metadata: dict[str, str | int | bool] = Field(default_factory=dict)


class EvidencePipelineResult(BaseModel):
    artifacts: list[EvidenceArtifact]
    fragments: list[EvidenceFragment]
    claims: list[EvidenceClaim]
    profile: ProfileSnapshot
    llm_calls: list[LLMCallRecord]
    trace: list[EvidenceTraceEvent]
    evaluation: EvidenceEvalReport

    def markdown_report(self) -> str:
        return "\n".join(
            [
                "# v0.3 Evidence Pipeline Report",
                "",
                f"- Artifacts: {len(self.artifacts)}",
                f"- Fragments: {len(self.fragments)}",
                f"- Validated claims: {len(self.claims)}",
                f"- Candidate profile version: {self.profile.version}",
                f"- Evidence trace rate: {self.evaluation.evidence_trace_rate:.2%}",
                f"- Unsupported claims: {self.evaluation.unsupported_claim_count}",
                f"- Valid locator rate: {self.evaluation.valid_locator_rate:.2%}",
                "",
                "> Reports contain identifiers and aggregates only; source text remains in the evidence store.",
            ]
        )

    def write_report(self, path: str | Path) -> Path:
        report_path = Path(path)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(self.markdown_report(), encoding="utf-8")
        return report_path


class EvidencePipeline:
    def __init__(
        self,
        *,
        blob_store: BlobStore,
        evidence_repository: EvidenceRepository,
        profile_repository: ProfileRepository,
        claim_extractor: ClaimExtractorService,
        fragmenter: DeterministicFragmenter | None = None,
    ) -> None:
        self.blob_store = blob_store
        self.repository = evidence_repository
        self.profile_repository = profile_repository
        self.ingestor = ArtifactIngestor(blob_store, evidence_repository)
        self.fragmenter = fragmenter or DeterministicFragmenter()
        self.claim_extractor = claim_extractor
        self.validator = ClaimValidator(evidence_repository)
        self.projector = CandidateProfileProjector(profile_repository)

    def run(
        self, paths: list[str | Path], *, owner_id: str, subject_id: str
    ) -> EvidencePipelineResult:
        artifacts: list[EvidenceArtifact] = []
        fragments: list[EvidenceFragment] = []
        trace: list[EvidenceTraceEvent] = []
        seen_artifact_ids: set[str] = set()
        for path in paths:
            result = self.ingestor.ingest_file(path, owner_id=owner_id)
            if result.artifact.artifact_id in seen_artifact_ids:
                trace.append(
                    EvidenceTraceEvent(
                        event="artifact_deduplicated",
                        artifact_id=result.artifact.artifact_id,
                        metadata={"deduplicated": True},
                    )
                )
                continue
            seen_artifact_ids.add(result.artifact.artifact_id)
            artifacts.append(result.artifact)
            artifact_fragments = self.repository.list_fragments(
                result.artifact.artifact_id
            )
            if not artifact_fragments and result.artifact.text_uri:
                text = self.blob_store.get(result.artifact.text_uri).decode("utf-8")
                artifact_fragments = self.fragmenter.fragment(result.artifact, text)
                artifact_fragments = [
                    self.repository.save_fragment(fragment)
                    for fragment in artifact_fragments
                ]
            fragments.extend(artifact_fragments)
            trace.append(
                EvidenceTraceEvent(
                    event="artifact_processed",
                    artifact_id=result.artifact.artifact_id,
                    fragment_count=len(artifact_fragments),
                    metadata={"deduplicated": result.deduplicated},
                )
            )

        extracted, llm_calls = self.claim_extractor.extract(subject_id, fragments)
        allowed_artifacts = {artifact.artifact_id for artifact in artifacts}
        claims = [
            self.validator.validate_and_save(claim, allowed_artifacts)
            for claim in extracted
        ]
        trace.append(
            EvidenceTraceEvent(
                event="claims_validated",
                claim_count=len(claims),
                metadata={"rejected": 0},
            )
        )
        profile = self.projector.project(subject_id, claims)
        evaluation = evaluate_evidence(
            artifacts=artifacts,
            fragments=fragments,
            claims=claims,
            profile=profile,
            ingestion_attempts=len(paths),
        )
        return EvidencePipelineResult(
            artifacts=artifacts,
            fragments=fragments,
            claims=claims,
            profile=profile,
            llm_calls=llm_calls,
            trace=trace,
            evaluation=evaluation,
        )
