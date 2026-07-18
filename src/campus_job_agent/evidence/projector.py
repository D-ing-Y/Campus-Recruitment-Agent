"""Evidence Claim to versioned v0.4 candidate profile projection."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from typing import Any

from campus_job_agent.ontology import CapabilityOntology
from campus_job_agent.schemas import (
    CandidateProfile,
    CapabilityAssessment,
    EducationRecord,
    EvidenceClaim,
    EvidenceCoverage,
    ExperienceRecord,
    ProfileSnapshot,
    Provenance,
    ResponsibilityBoundary,
)
from campus_job_agent.storage.base import ProfileRepository


_EXPERIENCE_PATTERN = re.compile(
    r"^(?:experience:([A-Za-z0-9_-]+)[.:]|experiences\[([^\]]+)\]\.)"
    r"(title|kind|description|responsibilities|technologies|outputs|results)$"
)


class CandidateProfileProjector:
    def __init__(
        self, repository: ProfileRepository, ontology: CapabilityOntology | None = None
    ) -> None:
        self.repository = repository
        self.ontology = ontology or CapabilityOntology.load_default()

    def project(
        self,
        subject_id: str,
        claims: list[EvidenceClaim],
        *,
        completion_reason: str | None = None,
        unknowns: list[str] | None = None,
    ) -> ProfileSnapshot:
        active = sorted(
            (claim for claim in claims if claim.status == "active"),
            key=lambda item: (item.created_at, item.claim_id),
        )
        grouped: dict[str, list[EvidenceClaim]] = defaultdict(list)
        for claim in active:
            grouped[claim.predicate].append(claim)

        conflicts = _find_conflicts(grouped)
        derived_unknowns = [
            predicate
            for predicate, values in grouped.items()
            if values and values[-1].value is None
        ]
        conflicting_predicates = {item["predicate"] for item in conflicts}
        capabilities = self._project_capabilities(grouped, conflicting_predicates)
        education = _project_education(grouped, conflicting_predicates)
        experiences = _project_experiences(grouped)
        boundaries = _project_boundaries(grouped, conflicting_predicates)
        used_claims = _stable_unique(
            claim.claim_id
            for values in grouped.values()
            for claim in values
            if _is_candidate_predicate(claim.predicate)
        )
        supported = sum(
            1
            for values in grouped.values()
            if values
            and _is_candidate_predicate(values[0].predicate)
            and values[-1].value is not None
            and values[0].predicate not in conflicting_predicates
        )
        inferred = sum(
            1
            for values in grouped.values()
            if values
            and _is_candidate_predicate(values[0].predicate)
            and values[0].claim_type == "model_inference"
        )
        latest = self.repository.get_latest_profile(subject_id, "candidate")
        profile = CandidateProfile(
            candidate_id=subject_id,
            schema_version="v0.4",
            education=education,
            capabilities=capabilities,
            experiences=experiences,
            responsibility_boundaries=boundaries,
            unknowns=_stable_unique([*(unknowns or []), *derived_unknowns]),
            conflicts=conflicts,
            evidence_coverage=EvidenceCoverage(
                supported_field_count=supported,
                inferred_field_count=inferred,
                unknown_field_count=len(
                    _stable_unique([*(unknowns or []), *derived_unknowns])
                ),
                conflicted_field_count=len(conflicts),
            ),
            supporting_claim_ids=used_claims,
            previous_snapshot_id=None if latest is None else latest.snapshot_id,
            completion_reason=completion_reason,
        )
        profile_payload = profile.model_dump(mode="json")
        if latest is not None and _canonical_profile(latest.profile_data) == _canonical_profile(
            profile_payload
        ):
            return latest
        snapshot = ProfileSnapshot(
            subject_id=subject_id,
            profile_type="candidate",
            version=1 if latest is None else latest.version + 1,
            schema_version="v0.4",
            profile_data=profile_payload,
            supporting_claim_ids=used_claims,
            provenance=Provenance(
                provider="deterministic",
                model="candidate-profile-projector-v0.4",
                schema_version="v0.4",
            ),
        )
        return self.repository.save_profile(snapshot)

    def _project_capabilities(
        self,
        grouped: dict[str, list[EvidenceClaim]],
        conflicting_predicates: set[str],
    ) -> list[CapabilityAssessment]:
        result: list[CapabilityAssessment] = []
        for predicate, claims in sorted(grouped.items()):
            if not predicate.startswith("capability:"):
                continue
            raw_label = predicate.split(":", 1)[1]
            resolution = self.ontology.resolve(raw_label)
            latest = claims[-1]
            if latest.value is None:
                continue
            level = "unknown"
            if isinstance(latest.value, dict):
                level = str(latest.value.get("level", "unknown"))
            if level not in {"unknown", "beginner", "intermediate", "advanced", "expert"}:
                level = "unknown"
            status = (
                "conflicted"
                if predicate in conflicting_predicates
                else "inferred"
                if latest.claim_type == "model_inference"
                else "confirmed"
            )
            result.append(
                CapabilityAssessment(
                    capability_id=resolution.capability_id,
                    raw_label=resolution.canonical_name or raw_label,
                    level=level,
                    confidence=max(item.confidence for item in claims),
                    status=status,
                    evidence_summary=f"{len(claims)} persisted claim(s)",
                    supporting_claim_ids=[item.claim_id for item in claims],
                )
            )
        return result


def _project_education(
    grouped: dict[str, list[EvidenceClaim]], conflicting_predicates: set[str]
) -> list[EducationRecord]:
    aliases = {
        "education.institution": "institution",
        "education:institution": "institution",
        "education.degree": "degree",
        "education:degree": "degree",
        "education.major": "major",
        "education:major": "major",
        "education.graduation_year": "graduation_year",
        "education:graduation_year": "graduation_year",
    }
    values: dict[str, Any] = {}
    claim_ids: list[str] = []
    field_claim_ids: dict[str, list[str]] = {}
    for predicate, field in aliases.items():
        claims = grouped.get(predicate, [])
        if (
            not claims
            or claims[-1].value is None
            or predicate in conflicting_predicates
        ):
            continue
        values[field] = claims[-1].value
        field_claim_ids[field] = [item.claim_id for item in claims]
        claim_ids.extend(field_claim_ids[field])
    if "institution" not in values:
        return []
    return [
        EducationRecord(
            **values,
            supporting_claim_ids=_stable_unique(claim_ids),
            field_supporting_claim_ids=field_claim_ids,
        )
    ]


def _project_experiences(
    grouped: dict[str, list[EvidenceClaim]],
) -> list[ExperienceRecord]:
    values: dict[str, dict[str, Any]] = defaultdict(dict)
    claim_ids: dict[str, list[str]] = defaultdict(list)
    field_claim_ids: dict[str, dict[str, list[str]]] = defaultdict(dict)
    for predicate, claims in grouped.items():
        match = _EXPERIENCE_PATTERN.match(predicate)
        if match is None:
            continue
        experience_id = match.group(1) or match.group(2)
        field = match.group(3)
        value = claims[-1].value
        if value is None:
            continue
        if field in {"responsibilities", "technologies", "outputs", "results"}:
            if not isinstance(value, list):
                value = [value]
        values[experience_id][field] = value
        claim_ids[experience_id].extend(item.claim_id for item in claims)
        field_claim_ids[experience_id][field] = [
            item.claim_id for item in claims
        ]
    result: list[ExperienceRecord] = []
    for experience_id, data in sorted(values.items()):
        result.append(
            ExperienceRecord(
                experience_id=experience_id,
                kind=data.get("kind", "project"),
                title=str(data.get("title", experience_id)),
                description=data.get("description"),
                responsibilities=[str(value) for value in data.get("responsibilities", [])],
                technologies=[str(value) for value in data.get("technologies", [])],
                outputs=[str(value) for value in data.get("outputs", [])],
                results=[str(value) for value in data.get("results", [])],
                supporting_claim_ids=_stable_unique(claim_ids[experience_id]),
                field_supporting_claim_ids=field_claim_ids[experience_id],
            )
        )
    return result


def _project_boundaries(
    grouped: dict[str, list[EvidenceClaim]], conflicting_predicates: set[str]
) -> list[ResponsibilityBoundary]:
    result: list[ResponsibilityBoundary] = []
    for predicate, claims in sorted(grouped.items()):
        match = _EXPERIENCE_PATTERN.match(predicate)
        if match is None or match.group(3) != "responsibilities":
            continue
        experience_id = match.group(1) or match.group(2)
        values = claims[-1].value
        if values is None:
            continue
        if not isinstance(values, list):
            values = [values]
        status = "conflicted" if predicate in conflicting_predicates else "confirmed"
        for value in values:
            result.append(
                ResponsibilityBoundary(
                    experience_id=experience_id,
                    scope=str(value),
                    status=status,
                    confidence=max(item.confidence for item in claims),
                    supporting_claim_ids=[item.claim_id for item in claims],
                )
            )
    return result


def _find_conflicts(
    grouped: dict[str, list[EvidenceClaim]],
) -> list[dict[str, Any]]:
    conflicts: list[dict[str, Any]] = []
    for predicate, claims in sorted(grouped.items()):
        values: dict[str, list[EvidenceClaim]] = defaultdict(list)
        for claim in claims:
            canonical = json.dumps(
                claim.value, ensure_ascii=False, sort_keys=True, default=str
            )
            values[canonical].append(claim)
        if len(values) <= 1:
            continue
        conflicts.append(
            {
                "conflict_id": f"conflict:{predicate}",
                "predicate": predicate,
                "claim_ids": [claim.claim_id for claim in claims],
                "values": [claim.value for claim in claims],
                "status": "open",
            }
        )
    return conflicts


def _is_candidate_predicate(predicate: str) -> bool:
    return (
        predicate.startswith("capability:")
        or predicate.startswith("education.")
        or predicate.startswith("education:")
        or _EXPERIENCE_PATTERN.match(predicate) is not None
    )


def _canonical_profile(profile_data: dict[str, Any]) -> str:
    semantic = dict(profile_data)
    semantic.pop("generated_at", None)
    semantic.pop("previous_snapshot_id", None)
    return json.dumps(
        semantic, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    )


def _stable_unique(values: Any) -> list[Any]:
    result: list[Any] = []
    for value in values:
        if value not in result:
            result.append(value)
    return result
