"""Hard validation boundary between model output and confirmed evidence."""

import json

from campus_job_agent.evidence.candidate_predicates import (
    CURRENT_CANDIDATE_CLAIM_SCHEMAS,
    CandidatePredicateError,
    parse_candidate_predicate,
    validate_candidate_value,
)
from campus_job_agent.ontology import CapabilityOntology
from campus_job_agent.schemas import EvidenceClaim
from campus_job_agent.schemas.candidate_taxonomy import CapabilityClaimValue
from campus_job_agent.storage.base import EvidenceRepository


class ClaimValidationError(ValueError):
    def __init__(self, message: str, *, reason_code: str = "invalid_evidence_reference") -> None:
        super().__init__(message)
        self.reason_code = reason_code


class CandidateClaimValidationError(ClaimValidationError):
    pass


class ClaimValidator:
    def __init__(self, repository: EvidenceRepository) -> None:
        self.repository = repository

    def validate(
        self,
        claim: EvidenceClaim,
        allowed_artifact_ids: set[str] | None = None,
        expected_owner_id: str | None = None,
    ) -> EvidenceClaim:
        if not claim.evidence_fragment_ids:
            raise ClaimValidationError("claim must cite at least one evidence fragment")
        if len(set(claim.evidence_fragment_ids)) != len(claim.evidence_fragment_ids):
            raise ClaimValidationError("claim contains duplicate fragment references")
        for fragment_id in claim.evidence_fragment_ids:
            fragment = self.repository.get_fragment(fragment_id)
            if fragment is None:
                raise ClaimValidationError(f"unknown evidence fragment: {fragment_id}")
            if allowed_artifact_ids is not None and fragment.artifact_id not in allowed_artifact_ids:
                raise ClaimValidationError("claim cites a fragment outside the current evidence set")
            artifact = self.repository.get_artifact(fragment.artifact_id)
            if artifact is None:
                raise ClaimValidationError("claim cites a fragment without an artifact")
            if expected_owner_id is not None and artifact.owner_id != expected_owner_id:
                raise ClaimValidationError("claim cites evidence owned by another user")
        try:
            json.dumps(claim.value, ensure_ascii=False, default=_reject_non_json)
        except (TypeError, ValueError) as exc:
            raise ClaimValidationError("claim value must be JSON serializable") from exc
        if claim.supersedes_claim_id:
            previous = self.repository.get_claim(claim.supersedes_claim_id)
            if previous is None:
                raise ClaimValidationError("superseded claim does not exist")
            if (previous.subject_id, previous.predicate) != (
                claim.subject_id,
                claim.predicate,
            ):
                raise ClaimValidationError("superseding claim must keep subject and predicate")
            if previous.status != "active":
                raise ClaimValidationError("only an active claim may be superseded")
            if expected_owner_id is not None:
                for fragment_id in previous.evidence_fragment_ids:
                    fragment = self.repository.get_fragment(fragment_id)
                    artifact = (
                        self.repository.get_artifact(fragment.artifact_id)
                        if fragment is not None
                        else None
                    )
                    if artifact is None or artifact.owner_id != expected_owner_id:
                        raise ClaimValidationError(
                            "superseded claim belongs to another evidence owner"
                        )
        return claim

    def validate_and_save(
        self,
        claim: EvidenceClaim,
        allowed_artifact_ids: set[str] | None = None,
        expected_owner_id: str | None = None,
    ) -> EvidenceClaim:
        return self.repository.save_claim(
            self.validate(claim, allowed_artifact_ids, expected_owner_id)
        )


class CandidateClaimValidator(ClaimValidator):
    """Validate that a Candidate Claim is both evidenced and projectable."""

    def __init__(
        self, repository: EvidenceRepository, ontology: CapabilityOntology | None = None
    ) -> None:
        super().__init__(repository)
        self.ontology = ontology or CapabilityOntology.load_default()

    def validate(
        self,
        claim: EvidenceClaim,
        allowed_artifact_ids: set[str] | None = None,
        expected_owner_id: str | None = None,
    ) -> EvidenceClaim:
        try:
            super().validate(claim, allowed_artifact_ids, expected_owner_id)
            parsed = parse_candidate_predicate(
                claim.predicate,
                allow_legacy=claim.schema_version not in CURRENT_CANDIDATE_CLAIM_SCHEMAS,
            )
            if (
                parsed.kind == "capability"
                and not parsed.legacy
                and self.ontology.get(parsed.capability_id or "") is None
            ):
                raise CandidateClaimValidationError(
                    f"unknown capability_id: {parsed.capability_id}",
                    reason_code="unknown_capability_id",
                )
            validate_candidate_value(parsed, claim.value)
            if (
                parsed.kind == "capability"
                and not parsed.legacy
                and claim.schema_version == "candidate_claim_v0.7.1.2"
            ):
                normalized = CapabilityClaimValue.model_validate(claim.value)
                resolved = self.ontology.resolve(normalized.raw_label)
                if resolved.capability_id is None:
                    raise CandidateClaimValidationError(
                        "capability raw_label is not represented by the current ontology",
                        reason_code="unmapped_capability_label",
                    )
                if resolved.capability_id != parsed.capability_id:
                    raise CandidateClaimValidationError(
                        "capability raw_label does not match capability_id",
                        reason_code="capability_id_mismatch",
                    )
        except CandidateClaimValidationError:
            raise
        except CandidatePredicateError as exc:
            raise CandidateClaimValidationError(
                str(exc), reason_code=exc.reason_code
            ) from exc
        except ClaimValidationError as exc:
            raise CandidateClaimValidationError(
                str(exc), reason_code=exc.reason_code
            ) from exc
        return claim


def _reject_non_json(value: object) -> None:
    raise TypeError(f"not JSON serializable: {type(value).__name__}")
