"""Deterministic objective, priority, minimum-package and scheduling policies."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, time, timedelta
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from campus_job_agent.schemas import (
    GapAssessment,
    JobInstanceRoleProfile,
    MinimumPreparationPackage,
    PreparationActivity,
    PreparationConstraints,
    PreparationObjective,
    PriorityFactors,
    ScheduledSession,
    TargetPreparationSummary,
)
from campus_job_agent.schemas.matching import canonical_hash


BAND_RANK = {"P0_blocker": 0, "P1_core": 1, "P2_transferable": 2, "P3_bonus": 3, "P4_deferred": 4}
IMPROVABILITY_RANK = {"high": 0, "medium": 1, "low": 2, "unknown": 3, "unaddressable": 4}
UNADDRESSABLE_QUALIFICATIONS = {"degree", "major", "graduation_year", "recruitment_eligibility"}


class PreparationPolicyError(ValueError):
    pass


def derive_objectives(
    assessments: Iterable[GapAssessment],
    roles: dict[str, JobInstanceRoleProfile],
) -> list[PreparationObjective]:
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for assessment in assessments:
        job_id = str(assessment.job_instance_profile_snapshot_id)
        role = roles[job_id]
        requirements = {item["assessment_item_id"]: item for item in assessment.requirement_assessments}
        for qualification in assessment.qualification_assessments:
            if qualification.get("outcome") != "failed":
                continue
            qtype = str(qualification.get("qualification_type", "other"))
            addressability = "unaddressable" if qtype in UNADDRESSABLE_QUALIFICATIONS else "addressable"
            objective_type = "target_review" if addressability == "unaddressable" else "resolve_uncertainty"
            key = (objective_type, f"qualification:{qtype}")
            bucket = grouped.setdefault(key, _objective_bucket(objective_type, f"复核硬性资格：{qtype}", addressability,
                                                                "unaddressable_blocker" if addressability == "unaddressable" else "addressable_hard_blocker"))
            bucket["targets"].add(job_id)
            bucket["qualifications"].add(str(qualification["assessment_item_id"]))
            bucket["claims"].update(qualification.get("candidate_claim_ids", []))
            bucket["claims"].update(qualification.get("role_claim_ids", []))
        for gap in assessment.gaps:
            if gap.gap_type == "capability_gap":
                activity_type, addressability, reason = "develop_capability", "addressable", "selected_target_core_capability_gap"
            elif gap.gap_type == "evidence_gap":
                activity_type, addressability, reason = "strengthen_evidence", "addressable", "selected_target_core_evidence_gap"
            elif gap.gap_type == "epistemic_uncertainty":
                activity_type, addressability, reason = "resolve_uncertainty", "unknown", "high_value_unknown"
            else:
                activity_type, addressability, reason = "target_review", "partially_addressable", "preference_conflict_review"
            requirement = next((requirements.get(item_id) for item_id in gap.assessment_item_ids if item_id in requirements), None)
            importance = str((requirement or {}).get("importance", "core"))
            capability = gap.capability_id or str((requirement or {}).get("capability_id") or gap.gap_id)
            key = (activity_type, capability)
            label = str((requirement or {}).get("raw_label") or gap.summary)
            bucket = grouped.setdefault(key, _objective_bucket(activity_type, _objective_title(activity_type, label), addressability, reason))
            bucket["targets"].add(job_id)
            if gap.gap_id:
                bucket["gaps"].add(gap.gap_id)
            bucket["requirements"].update(gap.assessment_item_ids)
            bucket["claims"].update(gap.candidate_claim_ids)
            bucket["claims"].update(gap.role_claim_ids)
            bucket["importance"].add(importance)
        for signal in role.hiring_signals:
            if not signal.signal_id:
                continue
            activity_type = "written_exam_practice" if signal.signal_type == "written_exam" else "interview_practice"
            key = (activity_type, signal.signal_id)
            reason = "frequent_hiring_signal" if signal.frequency_label == "frequent_signal" else "observed_hiring_signal"
            bucket = grouped.setdefault(key, _objective_bucket(activity_type, _objective_title(activity_type, signal.summary), "addressable", reason))
            bucket["targets"].add(job_id)
            bucket["signals"].add(signal.signal_id)
            bucket["claims"].update(signal.supporting_claim_ids)
    # Every selected target needs a submission-ready material check. The role schema
    # does not yet enumerate individual form fields, so this objective explicitly
    # points at the job snapshot's application field instead of inventing
    # a résumé/portfolio requirement.
    for job_id, role in sorted(roles.items()):
        key = ("prepare_application_asset", job_id)
        bucket = grouped.setdefault(
            key,
            _objective_bucket(
                "prepare_application_asset", f"核对申请材料：{role.role_title}", "addressable",
                "addressable_hard_blocker",
            ),
        )
        bucket["reasons"].add("required_application_asset")
        bucket["targets"].add(job_id)
        bucket["assets"].add(f"{job_id}#/application_url")
    result: list[PreparationObjective] = []
    for key, bucket in sorted(grouped.items()):
        payload = [key, sorted(bucket["targets"]), sorted(bucket["gaps"]), sorted(bucket["requirements"]),
                   sorted(bucket["qualifications"]), sorted(bucket["signals"]), sorted(bucket["assets"])]
        digest = canonical_hash("preparation-objective", payload)
        reasons = list(bucket["reasons"])
        if len(bucket["targets"]) > 1:
            reasons.append("multi_target_transfer_value")
        if bucket["importance"] == {"bonus"}:
            reasons.append("bonus_gap")
        result.append(PreparationObjective(
            objective_id=f"objective:{digest[7:31]}", objective_type=key[0], title=bucket["title"],
            target_job_profile_ids=sorted(bucket["targets"]), gap_ids=sorted(bucket["gaps"]),
            requirement_assessment_ids=sorted(bucket["requirements"]),
            qualification_assessment_ids=sorted(bucket["qualifications"]),
            hiring_signal_ids=sorted(bucket["signals"]), application_asset_refs=sorted(bucket["assets"]),
            supporting_claim_ids=sorted(bucket["claims"]),
            addressability=bucket["addressability"], reason_codes=sorted(set(reasons)),
        ))
    return result


def generate_activities(objectives: Iterable[PreparationObjective],
                        roles: dict[str, JobInstanceRoleProfile]) -> list[PreparationActivity]:
    result: list[PreparationActivity] = []
    for objective in sorted(objectives, key=lambda item: item.objective_id):
        template = _activity_template(objective.objective_type)
        deadlines = [roles[target].application_deadline.date() for target in objective.target_job_profile_ids
                     if roles[target].application_deadline is not None]
        digest = canonical_hash("preparation-activity", [objective.objective_id, objective.objective_type, template])
        result.append(PreparationActivity(
            activity_id=f"activity:{digest[7:31]}", activity_type=objective.objective_type,
            objective_ids=[objective.objective_id], title=objective.title,
            description=template["description"], expected_outputs=template["outputs"],
            completion_criteria=template["criteria"], verification_method=template["verification"],
            estimated_hours=template["hours"], splittable=template["splittable"],
            minimum_session_minutes=template["minimum_session"], deadline=min(deadlines) if deadlines else None,
            target_job_profile_ids=objective.target_job_profile_ids, gap_ids=objective.gap_ids,
            requirement_assessment_ids=objective.requirement_assessment_ids,
            qualification_assessment_ids=objective.qualification_assessment_ids,
            hiring_signal_ids=objective.hiring_signal_ids, supporting_claim_ids=objective.supporting_claim_ids,
        ))
    return result


def compute_priority(activity: PreparationActivity, objectives: dict[str, PreparationObjective],
                     roles: dict[str, JobInstanceRoleProfile], constraints: PreparationConstraints) -> PriorityFactors:
    related = [objectives[item] for item in activity.objective_ids]
    reasons = sorted({reason for item in related for reason in item.reason_codes})
    addressabilities = {item.addressability for item in related}
    if "unaddressable" in addressabilities:
        band, improvability = "P4_deferred", "unaddressable"
    elif "addressable_hard_blocker" in reasons:
        band, improvability = "P0_blocker", "high"
    elif any("core_" in reason for reason in reasons) or "frequent_hiring_signal" in reasons:
        band, improvability = "P1_core", "high"
    elif len(activity.target_job_profile_ids) > 1 or "high_value_unknown" in reasons:
        band, improvability = "P2_transferable", "medium"
    elif "bonus_gap" in reasons or "observed_hiring_signal" in reasons:
        band, improvability = "P3_bonus", "medium"
    else:
        band, improvability = "P2_transferable", "medium"
    weights: list[float] = []
    strengths: list[float] = []
    for target in activity.target_job_profile_ids:
        role = roles[target]
        assessment_ids = set(activity.requirement_assessment_ids)
        for requirement in [*role.requirements, *role.bonus_items]:
            if requirement.requirement_id in assessment_ids:
                weights.append(requirement.weight * (1.5 if requirement.importance == "core" and requirement.obligation == "required" else 1.0))
        for signal in role.hiring_signals:
            if signal.signal_id in activity.hiring_signal_ids:
                frequency = 1.0 if signal.frequency_label == "frequent_signal" else 0.4
                scope = {"role_family": 1.0, "company_role": 0.8, "job_instance": 0.6}.get(signal.scope_level, 0.25)
                freshness = {"current_window": 1.0, "historical": 0.5, "unknown": 0.3}[signal.freshness]
                strengths.append(round(frequency * scope * freshness * min(signal.independent_source_count / 2, 1), 6))
    if not weights:
        weights = [1.5 if any("core_" in reason for reason in reasons) else 0.5 if "bonus_gap" in reasons else 1.0]
    deadline_urgency = 0.0
    if activity.deadline:
        horizon_days = max((constraints.horizon_end - constraints.horizon_start).days + 1, 1)
        remaining = max((activity.deadline - constraints.horizon_start).days, 0)
        deadline_urgency = round(max(0.0, 1 - remaining / horizon_days), 6)
    target_count = len(set(activity.target_job_profile_ids))
    sort_key = (
        BAND_RANK[band], -target_count, -max(weights), -max(strengths or [0.0]), -target_count,
        -deadline_urgency, IMPROVABILITY_RANK[improvability], activity.estimated_hours, activity.activity_id,
    )
    return PriorityFactors(
        priority_factor_id=f"priority:{activity.activity_id.split(':', 1)[-1]}", activity_id=activity.activity_id,
        priority_band=band, selected_target_count=target_count, role_importance_weight=round(max(weights), 6),
        hiring_signal_strength=round(max(strengths or [0.0]), 6), transfer_target_count=target_count,
        deadline_urgency=deadline_urgency, improvability=improvability,
        estimated_effort_hours=activity.estimated_hours, sort_key=sort_key,
        reason_codes=reasons or ["deterministic_default"],
    )


def stable_activity_order(activities: Iterable[PreparationActivity],
                          factors: dict[str, PriorityFactors]) -> list[PreparationActivity]:
    return sorted(activities, key=lambda item: factors[item.activity_id].sort_key)


def validate_dependency_dag(activities: Iterable[PreparationActivity]) -> list[str]:
    items = {item.activity_id: item for item in activities}
    for item in items.values():
        unknown = set(item.dependencies) - set(items)
        if unknown:
            raise PreparationPolicyError(f"invalid_activity_reference: {sorted(unknown)}")
    visiting: set[str] = set()
    visited: set[str] = set()
    order: list[str] = []

    def visit(activity_id: str) -> None:
        if activity_id in visiting:
            raise PreparationPolicyError("dependency_cycle")
        if activity_id in visited:
            return
        visiting.add(activity_id)
        for dependency in sorted(items[activity_id].dependencies):
            visit(dependency)
        visiting.remove(activity_id)
        visited.add(activity_id)
        order.append(activity_id)

    for activity_id in sorted(items):
        visit(activity_id)
    return order


def schedule_activities(activities: list[PreparationActivity], factors: dict[str, PriorityFactors],
                        constraints: PreparationConstraints) -> tuple[list[ScheduledSession], dict[str, str], str]:
    validate_dependency_dag(activities)
    ordered = stable_activity_order(activities, factors)
    by_id = {item.activity_id: item for item in activities}
    remaining = {item.activity_id for item in activities}
    completed_at: dict[str, datetime] = {}
    sessions: list[ScheduledSession] = []
    deferred: dict[str, str] = {}
    daily_used: dict[date, int] = defaultdict(int)
    weekly_used: dict[tuple[int, int], int] = defaultdict(int)
    timezone = ZoneInfo(constraints.timezone)
    guard = 0
    while remaining:
        guard += 1
        if guard > len(activities) * 2:
            raise PreparationPolicyError("dependency_cycle")
        ready = [item for item in ordered if item.activity_id in remaining and set(item.dependencies).isdisjoint(remaining)]
        if not ready:
            raise PreparationPolicyError("dependency_cycle")
        for activity in ready:
            if activity.activity_type in constraints.excluded_activity_types:
                deferred[activity.activity_id] = "activity_type_excluded"
                remaining.remove(activity.activity_id)
                continue
            dependency_failed = next((item for item in activity.dependencies if item in deferred), None)
            if dependency_failed:
                deferred[activity.activity_id] = "dependency_deferred"
                remaining.remove(activity.activity_id)
                continue
            minutes = int(round(activity.estimated_hours * 60))
            chunks = _activity_chunks(activity, minutes, constraints)
            allocated: list[tuple[datetime, datetime, int]] = []
            search_start = constraints.horizon_start
            if activity.dependencies:
                latest = max(completed_at[item] for item in activity.dependencies)
                search_start = max(search_start, latest.date())
            for chunk in chunks:
                slot = _find_slot(chunk, search_start, activity.deadline, constraints, daily_used, weekly_used, timezone)
                if slot is None:
                    break
                start_at, end_at = slot
                allocated.append((start_at, end_at, chunk))
                daily_used[start_at.date()] += chunk
                weekly_used[start_at.date().isocalendar()[:2]] += chunk
                search_start = start_at.date()
            if len(allocated) != len(chunks):
                # Roll back partially allocated chunks; partial activities are never disguised as scheduled.
                for start_at, _, chunk in allocated:
                    daily_used[start_at.date()] -= chunk
                    weekly_used[start_at.date().isocalendar()[:2]] -= chunk
                allocated = []
                deferred[activity.activity_id] = "deadline_infeasible" if activity.deadline and activity.deadline <= constraints.horizon_end else "capacity_shortage"
            else:
                for index, (start_at, end_at, chunk) in enumerate(allocated, 1):
                    digest = canonical_hash("scheduled-session", [activity.activity_id, index, start_at.isoformat(), chunk])
                    sessions.append(ScheduledSession(
                        session_id=f"session:{digest[7:31]}", activity_id=activity.activity_id,
                        session_index=index, start_at=start_at, end_at=end_at, duration_minutes=chunk,
                    ))
                completed_at[activity.activity_id] = allocated[-1][1]
            remaining.remove(activity.activity_id)
    sessions.sort(key=lambda item: (item.start_at, item.activity_id, item.session_index))
    digest = canonical_hash("preparation-schedule", [item.model_dump(mode="json") for item in sessions])
    return sessions, deferred, digest


def build_package(objectives: list[PreparationObjective], activities: list[PreparationActivity],
                  scheduled: list[ScheduledSession], deferred: dict[str, str],
                  factors: dict[str, PriorityFactors]) -> MinimumPreparationPackage:
    scheduled_ids = {item.activity_id for item in scheduled}
    target_review_ids = {item.activity_id for item in activities if item.activity_type == "target_review"}
    included = sorted(scheduled_ids | (target_review_ids - set(deferred)))
    unaddressable = sorted(item.objective_id for item in objectives if item.addressability == "unaddressable")
    unscheduled = {item.activity_id for item in activities} - set(included)
    for activity_id in unscheduled:
        deferred.setdefault(activity_id, "policy_deferred")
    if unaddressable:
        status = "blocked"
    elif any(reason not in {"bonus_deprioritized", "policy_deferred_optional"}
             for reason in deferred.values()):
        status = "partial"
    elif not activities:
        status = "unknown"
    else:
        status = "complete"
    targets = sorted({target for item in activities for target in item.target_job_profile_ids})
    summaries = {
        target: TargetPreparationSummary(
            addressable_hard_blockers_included=sum(
                1 for item in activities if target in item.target_job_profile_ids
                and factors[item.activity_id].priority_band == "P0_blocker" and item.activity_id in included
            ),
            projected_core_coverage=None,
            required_application_assets_included=all(
                item.activity_id in included for item in activities
                if target in item.target_job_profile_ids and item.activity_type == "prepare_application_asset"
            ),
            practice_minimum_included=all(
                item.activity_id in included for item in activities
                if target in item.target_job_profile_ids and item.activity_type in {"written_exam_practice", "interview_practice"}
                and "frequent_hiring_signal" in factors[item.activity_id].reason_codes
            ),
        ) for target in targets
    }
    warnings = []
    if any(reason == "capacity_shortage" for reason in deferred.values()):
        warnings.append("capacity_prevents_full_policy_package")
    if any(reason == "max_activity_budget_reached" for reason in deferred.values()):
        warnings.append("activity_budget_prevents_full_policy_package")
    if unaddressable:
        warnings.append("unaddressable_blocker_requires_target_review")
    digest = canonical_hash("minimum-preparation-package", [included, sorted(deferred.items()), unaddressable, status])
    return MinimumPreparationPackage(
        package_id=f"package:{digest[7:31]}", status=status, included_activity_ids=included,
        deferred_activity_ids=sorted(deferred), unaddressable_objective_ids=unaddressable,
        deferred_reasons=dict(sorted(deferred.items())), target_summaries=summaries, warnings=warnings,
    )


def _objective_bucket(activity_type: str, title: str, addressability: str, reason: str) -> dict[str, Any]:
    return {"type": activity_type, "title": title, "addressability": addressability,
            "reasons": {reason}, "targets": set(), "gaps": set(), "requirements": set(),
            "qualifications": set(), "signals": set(), "assets": set(), "claims": set(), "importance": set()}


def _objective_title(activity_type: str, label: str) -> str:
    verbs = {
        "resolve_uncertainty": "核实", "strengthen_evidence": "补强证据", "develop_capability": "训练核心能力",
        "prepare_application_asset": "核对申请材料", "written_exam_practice": "完成笔试练习",
        "interview_practice": "完成面试练习", "target_review": "复核目标",
    }
    return f"{verbs.get(activity_type, '准备')}：{label[:80]}"


def _activity_template(activity_type: str) -> dict[str, Any]:
    templates = {
        "resolve_uncertainty": ("收集并归档能够解决该未知项的最小材料。", ["一份可归档材料或明确 unknown 结论"], ["材料进入 Evidence Store 并可定位"], "evidence_ingestion_required", 1.0, True, 30),
        "strengthen_evidence": ("整理现有经历中的个人职责、实现、评估和结果，形成可引用材料。", ["带引用的项目案例说明"], ["职责、实现和结果均有材料支撑"], "evidence_ingestion_required", 2.0, True, 60),
        "develop_capability": ("使用可重放的小任务练习该能力，不预设完成即等于掌握。", ["练习产物与结果记录"], ["产物可运行且结果已归档"], "practice_result_required", 4.0, True, 60),
        "prepare_application_asset": ("整理目标岗位要求的必需申请材料。", ["可提交的申请材料"], ["必填字段完整且引用可追溯"], "artifact_required", 1.5, True, 30),
        "written_exam_practice": ("按已归档的笔试信号完成一次限时练习。", ["答案、耗时与得分记录"], ["结果与错题已归档"], "practice_result_required", 2.0, False, 120),
        "interview_practice": ("按已归档的面试信号完成模拟问答并留存评价。", ["回答纲要与评价记录"], ["至少一次可归档的模拟评价"], "evaluator_feedback_required", 1.5, True, 30),
        "portfolio_revision": ("修订作品集中与岗位要求相关的证据展示。", ["新版作品集"], ["修订内容可回溯到原材料"], "artifact_required", 2.0, True, 60),
        "target_review": ("复核该目标的不可处理阻塞项和是否继续保留目标。", ["一条明确的目标去留决策"], ["不可处理资格已展示且用户已复核"], "self_report_only", 0.5, False, 30),
    }
    description, outputs, criteria, verification, hours, splittable, minimum = templates[activity_type]
    return {"description": description, "outputs": outputs, "criteria": criteria, "verification": verification,
            "hours": hours, "splittable": splittable, "minimum_session": minimum}


def _activity_chunks(activity: PreparationActivity, minutes: int, constraints: PreparationConstraints) -> list[int]:
    if not activity.splittable:
        if minutes > constraints.daily_max_hours * 60:
            return [minutes]
        return [minutes]
    unit = max(activity.minimum_session_minutes, constraints.session_minutes)
    chunks: list[int] = []
    while minutes > unit:
        chunks.append(unit)
        minutes -= unit
    if minutes:
        if chunks and minutes < activity.minimum_session_minutes:
            chunks[-1] += minutes
        else:
            chunks.append(minutes)
    return chunks


def _find_slot(minutes: int, start: date, deadline: date | None, constraints: PreparationConstraints,
               daily_used: dict[date, int], weekly_used: dict[tuple[int, int], int],
               timezone: ZoneInfo) -> tuple[datetime, datetime] | None:
    end = min(constraints.horizon_end, deadline) if deadline else constraints.horizon_end
    current = max(start, constraints.horizon_start)
    while current <= end:
        week = current.isocalendar()[:2]
        if (current not in constraints.unavailable_dates
                and daily_used[current] + minutes <= int(constraints.daily_max_hours * 60)
                and weekly_used[week] + minutes <= int(constraints.weekly_hours * 60)):
            start_at = datetime.combine(current, time(19, 0), tzinfo=timezone) + timedelta(minutes=daily_used[current])
            return start_at, start_at + timedelta(minutes=minutes)
        current += timedelta(days=1)
    return None
