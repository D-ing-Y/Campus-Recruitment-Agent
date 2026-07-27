"""Privacy-minimal feedback report projection."""

from campus_job_agent.schemas import FeedbackAttribution, FeedbackDirective, FeedbackImpactAssessment


def build_feedback_report(*, event_id: str, observation_ids: list[str], diagnosis_ids: list[str],
                          attributions: list[FeedbackAttribution], claim_ids: list[str], progress_ids: list[str],
                          impact: FeedbackImpactAssessment, directives: list[FeedbackDirective]) -> dict:
    return {
        "feedback_event_id": event_id,
        "observation_ids": observation_ids,
        "diagnosis_ids": diagnosis_ids,
        "attributions": [{
            "attribution_id": item.attribution_id,
            "status": item.confirmation_status,
            "scope": item.subject_scope,
            "source_authority": item.authority,
        } for item in attributions],
        "feedback_claim_ids": claim_ids,
        "progress_event_ids": progress_ids,
        "impact_assessment_id": impact.impact_assessment_id,
        "impact": impact.model_dump(mode="json"),
        "directives": [{
            "directive_id": item.directive_id,
            "directive_type": item.directive_type,
            "status": item.status,
            "reason_codes": item.reason_codes,
            "affected_target_ids": item.affected_target_ids,
            "resolved_refs": item.resolved_refs,
        } for item in directives],
        "version_chain_refs": {item.directive_id: item.resolved_refs for item in directives},
        "warnings": ["diagnosis_is_not_certain_causality", "raw_feedback_omitted_from_report"],
    }
