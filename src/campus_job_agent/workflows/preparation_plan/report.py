"""Privacy-minimal preparation report projection."""

from campus_job_agent.schemas import LearningPlan, MinimumPreparationPackage, PreparationActivity, PriorityFactors


def build_plan_report(plan: LearningPlan, package: MinimumPreparationPackage,
                      factors: list[PriorityFactors], activities: list[PreparationActivity]) -> dict:
    return {
        "learning_plan_id": plan.learning_plan_id,
        "status": plan.status,
        "package_id": package.package_id,
        "package_status": package.status,
        "activity_count": len(plan.activity_ids),
        "session_count": len(plan.schedule),
        "input_refs": {"input_set_id": plan.input_set_id, "constraints_id": plan.constraints_id,
                       "objective_ids": plan.objective_ids},
        "priority_factors": {item.activity_id: item.model_dump(mode="json") for item in factors},
        "schedule": [item.model_dump(mode="json") for item in plan.schedule],
        "activity_refs": {
            item.activity_id: {
                "target_job_profile_ids": item.target_job_profile_ids,
                "gap_ids": item.gap_ids,
                "requirement_assessment_ids": item.requirement_assessment_ids,
                "hiring_signal_ids": item.hiring_signal_ids,
                "supporting_claim_ids": item.supporting_claim_ids,
                "dependencies": item.dependencies,
            }
            for item in activities
        },
        "deferred_activity_ids": package.deferred_activity_ids,
        "blocker_activity_ids": [item.activity_id for item in factors if item.priority_band == "P0_blocker"],
        "unaddressable_objective_ids": package.unaddressable_objective_ids,
        "warnings": [*package.warnings, "priority_is_not_success_probability"],
    }
