"""Deterministic policies for v0.6 qualification, coverage, gaps and routing."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from campus_job_agent.schemas import (
    CandidateProfile,
    CareerIntent,
    ComparisonEntry,
    CoverageBreakdown,
    CoverageContribution,
    GapAssessment,
    GapItem,
    IntentConstraint,
    JobInstanceRoleProfile,
    MatchingBudget,
    PreferenceAssessment,
    Qualification,
    QualificationAssessment,
    RequirementAssessment,
    RoleRequirement,
)
from campus_job_agent.schemas.matching import canonical_hash


LEVELS = {"beginner": 0, "intermediate": 1, "advanced": 2, "expert": 3}
HARD_RANK = {"passed": 0, "unknown": 1, "failed": 2}


@dataclass(frozen=True)
class CapabilityTransfer:
    relation_id: str
    source_capability_id: str
    target_capability_id: str
    weight_factor: float = 0.75


class QualificationPolicy:
    version = "qualification_v1"

    def evaluate(self, candidate: CandidateProfile, qualification: Qualification) -> QualificationAssessment:
        candidate_value, candidate_claims, candidate_conflicted = _candidate_qualification(candidate, qualification.qualification_type)
        role_claims = list(qualification.supporting_claim_ids)
        if qualification.importance != "hard":
            outcome, reason = "not_applicable", "qualification_not_hard"
        elif qualification.status == "conflicted" or candidate_conflicted:
            outcome, reason = "conflicted", "qualification_fact_conflicted"
        elif qualification.status == "unknown":
            outcome, reason = "unknown", "role_value_unknown"
        elif candidate_value is None or not candidate_claims or not role_claims:
            outcome, reason = "unknown", "missing_comparable_evidence"
        else:
            result = compare_values(qualification.operator, qualification.value, candidate_value)
            if result is None:
                outcome, reason = "unknown", "unsupported_or_incomparable_operator"
            else:
                outcome, reason = ("passed", "comparator_passed") if result else ("failed", "comparator_failed")
        return QualificationAssessment(
            assessment_item_id=_id("qualification-item", [qualification.qualification_id, outcome, reason]),
            qualification_id=qualification.qualification_id,
            qualification_type=qualification.qualification_type,
            operator=qualification.operator,
            required_value=qualification.value,
            candidate_value=candidate_value,
            outcome=outcome,
            reason_code=reason,
            candidate_claim_ids=candidate_claims,
            role_claim_ids=role_claims,
            comparator_version=self.version,
        )

    @staticmethod
    def overall(items: list[QualificationAssessment]) -> str:
        applicable = [item for item in items if item.outcome != "not_applicable"]
        if any(item.outcome == "failed" for item in applicable):
            return "failed"
        if not applicable or any(item.outcome in {"unknown", "conflicted"} for item in applicable):
            return "unknown"
        return "passed"


def compare_values(operator: str, required: Any, candidate: Any) -> bool | None:
    if required is None or candidate is None:
        return None
    req_list = _as_list(required)
    cand_list = _as_list(candidate)
    req_norm = [_normalize(item) for item in req_list]
    cand_norm = [_normalize(item) for item in cand_list]
    if operator == "equals":
        return any(left == right for left in req_norm for right in cand_norm)
    if operator == "in":
        return any(value in req_norm for value in cand_norm)
    if operator == "contains_any":
        return bool(set(req_norm) & set(cand_norm))
    if operator == "contains_all":
        return set(req_norm).issubset(set(cand_norm))
    if operator in {"gte", "lte"}:
        try:
            req_number, cand_number = float(req_list[0]), float(cand_list[0])
        except (ValueError, TypeError, IndexError):
            return None
        return cand_number >= req_number if operator == "gte" else cand_number <= req_number
    if operator == "range":
        try:
            low, high = float(req_list[0]), float(req_list[1])
            value = float(cand_list[0])
        except (ValueError, TypeError, IndexError):
            return None
        return low <= value <= high
    return None


class CapabilityPolicy:
    version = "matching_weight_v1"

    def __init__(self, transfers: list[CapabilityTransfer] | None = None) -> None:
        self.transfers = transfers or []

    def evaluate(self, candidate: CandidateProfile, requirement: RoleRequirement) -> RequirementAssessment:
        requirement_id = requirement.requirement_id or _id("requirement", [requirement.raw_label, requirement.capability_id])
        importance = "bonus" if requirement.category == "bonus_capability" or requirement.importance == "bonus" else "core"
        base_weight = weight_for(importance, requirement.obligation)
        candidate_capability = None
        mapping_type = "unmapped"
        relation = None
        if requirement.capability_id:
            candidate_capability = next(
                (item for item in [*candidate.capabilities, *candidate.transferable_skills] if item.capability_id == requirement.capability_id), None
            )
            if candidate_capability is not None:
                mapping_type = "exact"
            else:
                relation = next(
                    (
                        edge for edge in self.transfers
                        if edge.target_capability_id == requirement.capability_id
                        and any(item.capability_id == edge.source_capability_id for item in [*candidate.capabilities, *candidate.transferable_skills])
                    ),
                    None,
                )
                if relation:
                    candidate_capability = next(
                        item for item in [*candidate.capabilities, *candidate.transferable_skills]
                        if item.capability_id == relation.source_capability_id
                    )
                    mapping_type = "transfer"
                else:
                    mapping_type = "exact"
        factor = relation.weight_factor if relation else 1.0
        effective_weight = round(base_weight * factor, 6)
        if requirement.authority == "forbidden":
            outcome, reason = "not_applicable", "requirement_authority_forbidden"
        elif not requirement.supporting_claim_ids:
            outcome, reason = "unknown", "role_requirement_evidence_missing"
        elif not requirement.capability_id:
            outcome, reason, mapping_type = "unmapped", "raw_label_requires_confirmed_mapping", "unmapped"
        elif candidate_capability is None:
            outcome, reason = "unknown", "candidate_capability_missing"
        elif candidate_capability.status in {"unknown", "conflicted"}:
            outcome, reason = "unknown", f"candidate_capability_{candidate_capability.status}"
        elif candidate_capability.status == "inferred" or not candidate_capability.supporting_claim_ids:
            outcome, reason = "evidence_insufficient", "candidate_evidence_not_confirmed"
        elif requirement.required_level == "unknown" or candidate_capability.level == "unknown":
            outcome, reason = "evidence_insufficient", "capability_level_unverified"
        elif requirement.required_level not in LEVELS or candidate_capability.level not in LEVELS:
            outcome, reason = "unknown", "capability_level_not_comparable"
        elif LEVELS[candidate_capability.level] >= LEVELS[requirement.required_level]:
            outcome, reason = "satisfied", "candidate_level_meets_requirement"
        else:
            outcome, reason = "insufficient", "confirmed_candidate_level_below_requirement"
        return RequirementAssessment(
            assessment_item_id=_id("requirement-item", [requirement_id, outcome, mapping_type, relation.relation_id if relation else None]),
            requirement_id=requirement_id,
            capability_id=requirement.capability_id,
            raw_label=requirement.raw_label,
            mapping_type=mapping_type,
            ontology_relation_id=relation.relation_id if relation else None,
            required_level=requirement.required_level,
            candidate_level=candidate_capability.level if candidate_capability else "unknown",
            outcome=outcome,
            importance=importance,
            obligation=requirement.obligation,
            base_weight=base_weight,
            effective_weight=effective_weight,
            reason_code=reason,
            candidate_claim_ids=list(candidate_capability.supporting_claim_ids) if candidate_capability else [],
            role_claim_ids=list(requirement.supporting_claim_ids),
            policy_version=self.version,
        )


def weight_for(importance: str, obligation: str) -> float:
    if importance == "core" and obligation == "required":
        return 1.5
    if importance == "core":
        return 1.0
    return 0.5


def compute_coverage(items: list[RequirementAssessment], dimension: str) -> CoverageBreakdown:
    selected = [item for item in items if item.importance == ("core" if dimension == "core_capability" else "bonus")]
    active = [item for item in selected if item.outcome != "not_applicable"]
    eligible_outcomes = {"satisfied", "insufficient", "evidence_insufficient"}
    uncertain_outcomes = {"unknown", "unmapped"}
    total = round(sum(item.effective_weight for item in active), 6)
    eligible = round(sum(item.effective_weight for item in active if item.outcome in eligible_outcomes), 6)
    covered = round(sum(item.effective_weight for item in active if item.outcome == "satisfied"), 6)
    uncertain = round(sum(item.effective_weight for item in active if item.outcome in uncertain_outcomes), 6)
    return CoverageBreakdown(
        dimension=dimension,
        total_weight=total,
        eligible_weight=eligible,
        covered_weight=covered,
        uncertain_weight=uncertain,
        coverage=None if eligible == 0 else round(covered / eligible, 6),
        covered_item_ids=[item.assessment_item_id for item in active if item.outcome == "satisfied"],
        uncovered_item_ids=[item.assessment_item_id for item in active if item.outcome in {"insufficient", "evidence_insufficient"}],
        uncertain_item_ids=[item.assessment_item_id for item in active if item.outcome in uncertain_outcomes],
        excluded_item_ids=[item.assessment_item_id for item in selected if item.outcome == "not_applicable"],
        contributions=[
            CoverageContribution(
                assessment_item_id=item.assessment_item_id,
                outcome=item.outcome,
                effective_weight=item.effective_weight,
                eligible=item.outcome in eligible_outcomes,
                covered=item.outcome == "satisfied",
                uncertain=item.outcome in uncertain_outcomes,
            )
            for item in selected
        ],
    )


class PreferencePolicy:
    version = "preference_v1"

    def evaluate(self, intent: CareerIntent, role: JobInstanceRoleProfile) -> list[PreferenceAssessment]:
        return [self._one(constraint, role) for constraint in intent.constraints]

    def _one(self, constraint: IntentConstraint, role: JobInstanceRoleProfile) -> PreferenceAssessment:
        role_value, role_claims = _role_preference(role, constraint.key)
        if constraint.status == "conflicted":
            outcome, reason = "unknown", "intent_constraint_conflicted"
        elif constraint.status != "confirmed":
            outcome, reason = "unknown", "intent_constraint_unconfirmed"
        elif role_value is None:
            outcome, reason = "unknown", "role_preference_value_unknown"
        elif not role_claims:
            outcome, reason = "unknown", "role_preference_evidence_missing"
        elif constraint.key == "salary" and (role.salary_unit is None or not isinstance(constraint.value, dict) or constraint.value.get("unit") != role.salary_unit):
            outcome, reason = "unknown", "salary_unit_not_comparable"
        else:
            comparable_value = role_value
            requested_value = constraint.value
            operator = constraint.operator
            if constraint.key == "salary" and isinstance(requested_value, dict):
                requested_value = requested_value.get("min")
                comparable_value = role.salary_max
                operator = "gte"
            result = compare_values(operator, requested_value, comparable_value)
            if result is None:
                outcome, reason = "unknown", "preference_not_comparable"
            else:
                outcome, reason = ("aligned", "preference_aligned") if result else ("conflict", "preference_conflict")
        return PreferenceAssessment(
            assessment_item_id=_id("preference-item", [constraint.constraint_id, role.role_profile_id, outcome]),
            preference_key=constraint.key,
            constraint_kind=constraint.kind,
            intent_value=constraint.value,
            role_value=role_value,
            outcome=outcome,
            reason_code=reason,
            intent_source_ref=constraint.source_ref,
            role_claim_ids=role_claims,
        )


def build_gaps(
    requirements: list[RequirementAssessment],
    preferences: list[PreferenceAssessment],
    *,
    role_stale_reason: str | None = None,
) -> list[GapItem]:
    gaps: list[GapItem] = []
    for item in requirements:
        if item.outcome == "insufficient":
            gap_type, severity, actions = "capability_gap", "high" if item.importance == "core" else "medium", ["revise_candidate", "defer_target"]
        elif item.outcome == "evidence_insufficient":
            gap_type, severity, actions = "evidence_gap", "medium", ["provide_candidate_evidence", "keep_unknown"]
        elif item.outcome in {"unknown", "unmapped"}:
            gap_type, severity, actions = "epistemic_uncertainty", "medium" if item.importance == "core" else "low", ["revise_candidate", "refresh_role", "keep_unknown"]
        else:
            continue
        gaps.append(_gap(gap_type, severity, item.reason_code, item.assessment_item_id, item.capability_id, item.candidate_claim_ids, item.role_claim_ids, actions))
    for item in preferences:
        if item.outcome == "conflict":
            severity = "blocking" if item.constraint_kind == "hard" else "medium"
            gaps.append(_gap("preference_conflict", severity, item.reason_code, item.assessment_item_id, None, [], item.role_claim_ids, ["revise_intent", "reject_target", "defer_target"]))
        elif item.outcome == "unknown":
            gaps.append(_gap("epistemic_uncertainty", "low", item.reason_code, item.assessment_item_id, None, [], item.role_claim_ids, ["revise_intent", "keep_unknown"]))
    if role_stale_reason:
        gaps.append(_gap("epistemic_uncertainty", "high", role_stale_reason, f"role:{role_stale_reason}", None, [], [], ["refresh_role"]))
    return gaps


def build_fact_index(assessment: GapAssessment) -> dict[str, Any]:
    facts: dict[str, Any] = {
        f"fact:{assessment.assessment_id}:hard": {"kind": "hard_status", "value": assessment.hard_constraint_status},
        f"fact:{assessment.assessment_id}:core": {"kind": "coverage", "value": assessment.core_coverage},
        f"fact:{assessment.assessment_id}:bonus": {"kind": "coverage", "value": assessment.bonus_coverage},
    }
    for item in assessment.qualification_assessments + assessment.requirement_assessments + assessment.preference_assessments:
        facts[f"fact:{item['assessment_item_id']}"] = item
    for item in assessment.gaps:
        facts[f"fact:{item.gap_id}"] = item.model_dump(mode="json")
    return facts


def comparison_entry(assessment: GapAssessment) -> ComparisonEntry:
    hard_status = assessment.hard_constraint_status or "unknown"
    blocking = sum(
        1 for item in assessment.preference_assessments
        if item.get("outcome") == "conflict" and item.get("constraint_kind") == "hard"
    )
    core = assessment.core_coverage or {}
    uncertainty = float(core.get("uncertain_weight", 0)) + float((assessment.bonus_coverage or {}).get("uncertain_weight", 0))
    if hard_status == "failed" or blocking:
        tier = "blocked"
    elif hard_status == "unknown" or uncertainty > 0:
        tier = "needs_clarification"
    else:
        tier = "review_first"
    job_id = assessment.job_instance_profile_snapshot_id or assessment.role_profile_snapshot_id or "unknown"
    return ComparisonEntry(
        job_instance_profile_snapshot_id=job_id,
        gap_assessment_id=assessment.assessment_id,
        recommended_tier=tier,
        hard_rank=HARD_RANK[hard_status],
        blocking_preference_conflict_count=blocking,
        core_coverage=core.get("coverage"),
        uncertainty_weight=round(uncertainty, 6),
        stable_tie_breaker=job_id,
    )


def stable_sort(entries: list[ComparisonEntry]) -> list[ComparisonEntry]:
    return sorted(
        entries,
        key=lambda item: (
            item.hard_rank,
            item.blocking_preference_conflict_count,
            item.core_coverage is None,
            -(item.core_coverage or 0),
            item.uncertainty_weight,
            item.stable_tie_breaker,
        ),
    )


class MatchingRoutePolicy:
    def decide(self, *, budgets: MatchingBudget, counters: Any, has_fatal_error: bool, has_refresh_directive: bool) -> str:
        if has_fatal_error:
            return "fail"
        if has_refresh_directive:
            return "role_refresh_required"
        if counters.decision_interrupts >= budgets.max_decision_interrupts:
            return "complete_with_unknowns"
        return "review_user"


def _candidate_qualification(candidate: CandidateProfile, key: str) -> tuple[Any, list[str], bool]:
    if key in candidate.qualifications:
        return candidate.qualifications[key], candidate.qualification_claim_ids.get(key, []), False
    if key in {"degree", "major", "graduation_year"}:
        values, claims = [], []
        for record in candidate.education:
            value = getattr(record, key, None)
            if value:
                values.append(value)
                claims.extend(record.field_supporting_claim_ids.get(key, record.supporting_claim_ids))
        return (values if len(values) > 1 else values[0] if values else None), list(dict.fromkeys(claims)), False
    conflict = any(item.get("field") == key or item.get("target_path") == key for item in candidate.conflicts)
    return None, [], conflict


def _role_preference(role: JobInstanceRoleProfile, key: str) -> tuple[Any, list[str]]:
    mapping = {
        "location": role.locations,
        "industry": role.industry,
        "company": role.company,
        "company_type": role.company_type,
        "work_mode": role.work_mode,
        "recruitment_type": role.recruitment_type,
        "graduation_year": role.graduation_year,
        "salary": {"min": role.salary_min, "max": role.salary_max, "unit": role.salary_unit} if role.salary_min is not None or role.salary_max is not None else None,
    }
    return mapping.get(key), list(role.supporting_claim_ids)


def _gap(gap_type: str, severity: str, reason: str, item_id: str, capability_id: str | None, candidate_claims: list[str], role_claims: list[str], actions: list[str]) -> GapItem:
    gap_id = _id("gap", [gap_type, item_id, reason])
    return GapItem(
        gap_id=gap_id,
        gap_type=gap_type,
        capability_id=capability_id,
        summary=reason.replace("_", " "),
        severity=severity,
        reason_code=reason,
        assessment_item_ids=[item_id],
        candidate_claim_ids=candidate_claims,
        role_claim_ids=role_claims,
        supporting_claim_ids=list(dict.fromkeys([*candidate_claims, *role_claims])),
        allowed_actions=actions,
        confidence=1.0 if candidate_claims or role_claims else 0.5,
    )


def _id(prefix: str, payload: Any) -> str:
    return f"{prefix}:{canonical_hash(prefix, payload)[7:31]}"


def _as_list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, (list, tuple, set)) else [value]


def _normalize(value: Any) -> str:
    return "".join(str(value).casefold().split())


__all__ = [
    "CapabilityPolicy", "CapabilityTransfer", "MatchingRoutePolicy", "PreferencePolicy",
    "QualificationPolicy", "build_fact_index", "build_gaps", "compare_values",
    "comparison_entry", "compute_coverage", "stable_sort", "weight_for",
]
