"""Local v0.7 application-service saga across existing v0.4-v0.7 boundaries."""

from __future__ import annotations

from typing import Any

from campus_job_agent.evidence.projector import CandidateProfileProjector
from campus_job_agent.schemas import (
    FeedbackDirective, LearningPlan, PreparationConstraints, PreparationInputSet, TargetDecision,
)
from campus_job_agent.schemas.matching import canonical_hash
from campus_job_agent.storage.base import EvidenceRepository, ProfileRepository
from campus_job_agent.workflows.feedback.repository import SQLiteFeedbackRepository
from campus_job_agent.workflows.feedback.service import FeedbackService
from campus_job_agent.workflows.preparation_plan.repository import SQLitePreparationRepository
from campus_job_agent.workflows.preparation_plan.service import PreparationService
from campus_job_agent.workflows.profile_matching.repository import SQLiteMatchingRepository
from campus_job_agent.workflows.profile_matching.service import MatchingService


class FeedbackReplanSaga:
    """Execute one bounded local chain; this is not the future v1.0 Parent Graph."""

    def __init__(self, *, evidence_repository: EvidenceRepository, profile_repository: ProfileRepository,
                 matching_repository: SQLiteMatchingRepository,
                 preparation_repository: SQLitePreparationRepository,
                 feedback_repository: SQLiteFeedbackRepository, feedback_service: FeedbackService) -> None:
        self.evidence = evidence_repository
        self.profiles = profile_repository
        self.matching_repository = matching_repository
        self.preparation_repository = preparation_repository
        self.feedback_repository = feedback_repository
        self.feedback_service = feedback_service
        self.matching = MatchingService(profile_repository=profile_repository,
                                        evidence_repository=evidence_repository,
                                        matching_repository=matching_repository)
        self.preparation = PreparationService(profile_repository=profile_repository,
                                              matching_repository=matching_repository,
                                              preparation_repository=preparation_repository)

    def run_candidate_feedback_replan(self, *, user_id: str, feedback_event_id: str,
                                      old_candidate_snapshot_id: str, old_plan_id: str) -> dict[str, Any]:
        old_plan = self.preparation_repository.get(old_plan_id, LearningPlan, owner_id=user_id)
        if old_plan is None:
            raise ValueError("plan_not_found")
        old_input = self.preparation_repository.get(old_plan.input_set_id, PreparationInputSet, owner_id=user_id)
        if old_input is None:
            raise ValueError("preparation_input_not_found")
        candidate_directive = self._directive(feedback_event_id, user_id, "candidate_profile_rebuild_required")
        rematch_directive = self._directive(feedback_event_id, user_id, "rematch_required")
        replan_directive = self._directive(feedback_event_id, user_id, "replan_required")
        old_candidate = self.profiles.get_profile(old_candidate_snapshot_id)
        if old_candidate is None or old_candidate.profile_type != "candidate":
            raise ValueError("candidate_snapshot_not_found")
        claims = self.evidence.list_active_claims(old_candidate.subject_id)
        new_candidate = CandidateProfileProjector(self.profiles).project(
            old_candidate.subject_id, claims, completion_reason="sufficient"
        )
        if new_candidate.snapshot_id == old_candidate_snapshot_id:
            raise ValueError("candidate_rebuild_produced_no_successor")
        self.feedback_service.resolve_directive(
            user_id=user_id, directive_id=candidate_directive.directive_id,
            response_id=f"saga-candidate:{feedback_event_id}", resolved_refs=[new_candidate.snapshot_id],
            old_snapshot_ref=old_candidate_snapshot_id,
        )
        self.matching.invalidate_input(old_candidate_snapshot_id, owner_id=user_id)
        matching_input, candidate, intent, jobs, _ = self.matching.load_input_set(
            user_id=user_id, candidate_snapshot_id=new_candidate.snapshot_id,
            intent_snapshot_id=old_input.career_intent_snapshot_id,
            job_snapshot_ids=old_input.job_instance_profile_snapshot_ids,
            family_snapshot_ids=old_input.role_family_profile_snapshot_ids,
        )
        assessments = [self.matching.assess_job(
            input_set=matching_input, candidate=candidate, intent=intent,
            job_snapshot_id=snapshot_id, role=role,
        ) for snapshot_id, role in jobs.items()]
        comparison = self.matching.build_comparison(matching_input, assessments)
        old_decisions = [self.matching_repository.get(item, TargetDecision, owner_id=user_id)
                         for item in old_input.target_decision_ids]
        new_decisions = []
        for old in old_decisions:
            if old is None or old.status != "selected":
                continue
            digest = canonical_hash("saga-target-decision", [comparison.comparison_set_id, old.decision_id, feedback_event_id])
            new_decisions.append(TargetDecision(
                decision_id=f"target-decision:{digest[7:31]}", user_id=user_id,
                comparison_set_id=comparison.comparison_set_id,
                job_instance_profile_snapshot_id=old.job_instance_profile_snapshot_id,
                status="selected", reason_codes=["selected_target_carried_forward_after_rematch"],
                created_from_response_id=f"saga:{feedback_event_id}", supersedes_decision_id=old.decision_id,
            ))
        response_id = f"saga-decisions:{feedback_event_id}"
        self.matching_repository.save_decision_batch(
            new_decisions, owner_id=user_id, response_id=response_id,
            payload_hash=canonical_hash("saga-decision-batch", [item.model_dump(mode="json") for item in new_decisions]),
        )
        self.feedback_service.resolve_directive(
            user_id=user_id, directive_id=rematch_directive.directive_id,
            response_id=f"saga-rematch:{feedback_event_id}", resolved_refs=[comparison.comparison_set_id],
        )
        self.preparation.mark_plan_stale(old_plan_id, owner_id=user_id)
        constraints = self.preparation_repository.get(old_plan.constraints_id, PreparationConstraints, owner_id=user_id)
        loaded = self.preparation.load_and_validate_input(
            user_id=user_id, target_decision_ids=[item.decision_id for item in new_decisions],
            candidate_snapshot_id=new_candidate.snapshot_id,
            intent_snapshot_id=old_input.career_intent_snapshot_id,
            comparison_set_id=comparison.comparison_set_id,
            gap_assessment_ids=[item.assessment_id for item in assessments],
            job_snapshot_ids=old_input.job_instance_profile_snapshot_ids,
            family_snapshot_ids=old_input.role_family_profile_snapshot_ids,
            constraints_id=constraints.constraints_id,
        )
        new_plan, _, _, _ = self.preparation.build_plan(
            input_set=loaded[0], assessments=loaded[1], roles=loaded[2], constraints=loaded[3],
            previous_plan_id=old_plan_id, change_reason_codes=["feedback_candidate_rebuild_rematch"],
        )
        self.feedback_service.resolve_directive(
            user_id=user_id, directive_id=replan_directive.directive_id,
            response_id=f"saga-replan:{feedback_event_id}", resolved_refs=[new_plan.learning_plan_id],
        )
        return {
            "feedback_event_id": feedback_event_id,
            "old_candidate_snapshot_id": old_candidate_snapshot_id,
            "new_candidate_snapshot_id": new_candidate.snapshot_id,
            "comparison_set_id": comparison.comparison_set_id,
            "gap_assessment_ids": [item.assessment_id for item in assessments],
            "old_plan_id": old_plan_id,
            "new_plan_id": new_plan.learning_plan_id,
            "resolved_directive_ids": [candidate_directive.directive_id, rematch_directive.directive_id,
                                       replan_directive.directive_id],
        }

    def _directive(self, event_id: str, owner_id: str, directive_type: str) -> FeedbackDirective:
        item = next((value for value in self.feedback_repository.list(
            "feedback_directive", FeedbackDirective, owner_id=owner_id
        ) if value.originating_feedback_event_id == event_id and value.directive_type == directive_type), None)
        if item is None:
            raise ValueError(f"missing_directive:{directive_type}")
        return item


__all__ = ["FeedbackReplanSaga"]
