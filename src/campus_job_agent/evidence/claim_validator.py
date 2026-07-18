"""Hard validation boundary between model output and confirmed evidence."""

import json

from campus_job_agent.schemas import EvidenceClaim
from campus_job_agent.storage.base import EvidenceRepository


class ClaimValidationError(ValueError):
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


def _reject_non_json(value: object) -> None:
    raise TypeError(f"not JSON serializable: {type(value).__name__}")
