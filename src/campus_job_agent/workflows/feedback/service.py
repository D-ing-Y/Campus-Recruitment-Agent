"""Evidence, Claim, progress, impact and directive services for v0.7 feedback."""

from __future__ import annotations

import json
from typing import Any

from campus_job_agent.evidence.claim_validator import ClaimValidator
from campus_job_agent.schemas import (
    ComparisonSet, EvidenceClaim, FeedbackAttribution, FeedbackDiagnosis, FeedbackDirective,
    FeedbackEvent, FeedbackImpactAssessment, FeedbackInput, FeedbackObservation, LearningPlan,
    PlanProgressEvent, ProfileSnapshot,
)
from campus_job_agent.schemas.evidence import ClaimExtractor
from campus_job_agent.schemas.matching import canonical_hash
from campus_job_agent.storage.base import EvidenceRepository, ProfileRepository
from campus_job_agent.workflows.feedback.ingestion import FeedbackIngestor
from campus_job_agent.workflows.feedback.policy import (
    assess_impact, build_attributions, extract_observations, propose_diagnoses, validate_diagnoses,
)
from campus_job_agent.workflows.feedback.repository import SQLiteFeedbackRepository
from campus_job_agent.workflows.preparation_plan.repository import SQLitePreparationRepository
from campus_job_agent.workflows.profile_matching.repository import SQLiteMatchingRepository


class FeedbackServiceError(RuntimeError):
    pass


class FeedbackService:
    def __init__(self, *, ingestor: FeedbackIngestor, evidence_repository: EvidenceRepository,
                 profile_repository: ProfileRepository, feedback_repository: SQLiteFeedbackRepository,
                 preparation_repository: SQLitePreparationRepository,
                 matching_repository: SQLiteMatchingRepository) -> None:
        self.ingestor = ingestor
        self.evidence = evidence_repository
        self.profiles = profile_repository
        self.repository = feedback_repository
        self.preparation = preparation_repository
        self.matching = matching_repository
        self.claim_validator = ClaimValidator(evidence_repository)

    def ingest(self, **kwargs: Any):
        return self.ingestor.ingest(**kwargs)

    def interpret(self, *, event: FeedbackEvent,
                  candidate_snapshot_id: str | None) -> tuple[list[FeedbackObservation], list[FeedbackDiagnosis], list[FeedbackAttribution]]:
        fragments = [self.evidence.get_fragment(item) for item in event.fragment_ids]
        if any(item is None for item in fragments):
            raise FeedbackServiceError("feedback_raw_archive_failed: fragment missing")
        feedback_input = self._load_archived_input(event)
        observations = extract_observations(event, feedback_input, "\n".join(item.text for item in fragments if item))
        diagnoses = validate_diagnoses(event, observations, propose_diagnoses(event, feedback_input, observations))
        candidate_subject = None
        if candidate_snapshot_id:
            snapshot = self.profiles.get_profile(candidate_snapshot_id)
            if snapshot is None:
                raise FeedbackServiceError("snapshot_not_found: candidate")
            candidate_subject = snapshot.subject_id
        attributions = build_attributions(event, observations, diagnoses, candidate_subject_ref=candidate_subject)
        for item in observations:
            self.repository.save("feedback_observation", item, owner_id=event.user_id)
        for item in diagnoses:
            self.repository.save("feedback_diagnosis", item, owner_id=event.user_id)
        for item in attributions:
            self.repository.save("feedback_attribution", item, owner_id=event.user_id)
        self.repository.replace_lifecycle(event.feedback_event_id, FeedbackEvent,
                                          "awaiting_confirmation" if any(item.requires_confirmation for item in attributions) else "interpreted")
        return observations, diagnoses, attributions

    def apply_attribution_response(self, *, event: FeedbackEvent, response_id: str, action: str,
                                   attribution_ids: list[str], diagnosis_ids: list[str],
                                   relabels: list[dict[str, Any]]) -> list[FeedbackAttribution]:
        attributions = [self.repository.get(item, FeedbackAttribution, owner_id=event.user_id) for item in attribution_ids]
        if any(item is None for item in attributions):
            raise FeedbackServiceError("feedback_scope_invalid: attribution")
        updates: list[FeedbackAttribution] = []
        relabel_map = {item["attribution_id"]: item for item in relabels}
        all_event_attributions = self.repository.list("feedback_attribution", FeedbackAttribution, owner_id=event.user_id)
        if action in {"reject_diagnoses", "mark_unknown"}:
            targets = [item for item in all_event_attributions if item.feedback_event_id == event.feedback_event_id
                       and (not diagnosis_ids or set(item.diagnosis_ids) & set(diagnosis_ids))]
        else:
            targets = [item for item in attributions if item is not None]
        for item in targets:
            if action == "confirm_attributions":
                status, scope, subject = "confirmed", item.subject_scope, item.subject_ref
            elif action == "relabel_scope":
                patch = relabel_map.get(item.attribution_id)
                if patch is None:
                    raise FeedbackServiceError("feedback_scope_invalid: missing relabel")
                status, scope, subject = "relabeled", patch["subject_scope"], patch.get("subject_ref")
            elif action == "reject_diagnoses":
                status, scope, subject = "rejected", item.subject_scope, item.subject_ref
            else:
                status, scope, subject = "unknown", "unknown", None
            updated = item.model_copy(update={"confirmation_status": status, "subject_scope": scope,
                                              "subject_ref": subject, "confirmed_by_response_id": response_id})
            self.repository.replace_lifecycle(item.attribution_id, FeedbackAttribution, status,
                                              extra_updates={"confirmation_status": status,
                                                             "subject_scope": scope, "subject_ref": subject,
                                                             "confirmed_by_response_id": response_id})
            updates.append(updated)
            for diagnosis_id in item.diagnosis_ids:
                diagnosis = self.repository.get(diagnosis_id, FeedbackDiagnosis, owner_id=event.user_id)
                if diagnosis is not None:
                    diagnosis_status = "accepted" if status in {"confirmed", "relabeled"} else "rejected" if status == "rejected" else "unknown"
                    self.repository.replace_lifecycle(diagnosis_id, FeedbackDiagnosis, diagnosis_status)
        return updates

    def persist_claims_and_progress(self, *, event: FeedbackEvent,
                                    attributions: list[FeedbackAttribution]) -> tuple[list[EvidenceClaim], list[PlanProgressEvent]]:
        claims: list[EvidenceClaim] = []
        progress: list[PlanProgressEvent] = []
        accepted = [item for item in attributions if item.confirmation_status in {"confirmed", "relabeled", "not_required"}]
        structured = self._load_archived_input(event).structured or {}
        for attribution in accepted:
            if attribution.subject_scope == "plan_task":
                if not event.plan_id or not event.activity_id:
                    continue
                plan = self.preparation.get(event.plan_id, LearningPlan, owner_id=event.user_id)
                if plan is None or event.activity_id not in plan.activity_ids:
                    raise FeedbackServiceError("invalid_activity_reference")
                status = str(structured.get("status", "completed")).lower()
                mapped = status if status in {"not_started", "active", "completed", "blocked", "skipped"} else "completed_self_reported"
                percent = int(structured.get("progress_percent", 100 if "complete" in mapped else 0))
                digest = canonical_hash("plan-progress", [event.feedback_event_id, event.plan_id, event.activity_id, mapped, percent])
                progress_event = PlanProgressEvent(
                    progress_event_id=f"progress:{digest[7:31]}", learning_plan_id=event.plan_id,
                    activity_id=event.activity_id, status=mapped, progress_percent=percent,
                    feedback_event_id=event.feedback_event_id, evidence_artifact_ids=event.raw_artifact_ids,
                    reason_codes=["progress_only_not_capability_mastery"], occurred_at=event.occurred_at,
                )
                progress.append(self.preparation.save("progress_event", progress_event, owner_id=event.user_id))
                continue
            if attribution.subject_scope == "unknown" or not attribution.diagnosis_ids:
                continue
            diagnosis = self.repository.get(attribution.diagnosis_ids[0], FeedbackDiagnosis, owner_id=event.user_id)
            if diagnosis is None or diagnosis.status != "accepted":
                continue
            if attribution.subject_scope == "candidate_capability" and attribution.capability_id and structured.get("capability_level"):
                predicate = f"capability:{attribution.capability_id}"
                value: Any = {"level": structured["capability_level"], "feedback_event_id": event.feedback_event_id}
            else:
                predicate = f"feedback.{diagnosis.diagnosis_type}"
                value = {"diagnosis_id": diagnosis.diagnosis_id, "scope": attribution.subject_scope,
                         "summary": diagnosis.summary, "confirmation_status": attribution.confirmation_status,
                         "authority": attribution.authority}
            subject = attribution.subject_ref or f"feedback-subject:{event.feedback_event_id}"
            digest = canonical_hash("feedback-claim", [subject, predicate, value, event.fragment_ids])
            claim = EvidenceClaim(
                claim_id=f"feedback-claim:{digest[7:31]}", subject_id=subject, predicate=predicate,
                value=value, claim_type="feedback_signal", evidence_fragment_ids=event.fragment_ids,
                confidence=diagnosis.confidence, extractor=ClaimExtractor(provider="deterministic", model="feedback-policy-v1"),
                prompt_version="feedback_claim_v1", schema_version="v0.7",
                origin_kind="feedback_event", origin_ref=event.feedback_event_id,
                effective_at=event.occurred_at,
            )
            claims.append(self.claim_validator.validate_and_save(
                claim, allowed_artifact_ids=set(event.raw_artifact_ids), expected_owner_id=event.user_id
            ))
        return claims, progress

    def _load_archived_input(self, event: FeedbackEvent) -> FeedbackInput:
        artifact = self.evidence.get_artifact(event.raw_artifact_ids[0])
        fragments = [self.evidence.get_fragment(item) for item in event.fragment_ids]
        if artifact is None or any(item is None for item in fragments):
            raise FeedbackServiceError("feedback_raw_archive_failed: archived input missing")
        text = "\n".join(item.text for item in fragments if item)
        common = {
            "feedback_type": event.feedback_type,
            "source_kind": event.source_kind,
            "occurred_at": event.occurred_at,
            "stage": event.stage,
            "capability_id": event.capability_id,
            "suggested_scope": event.suggested_scope,
        }
        if artifact.content_type == "application/json":
            try:
                return FeedbackInput(**common, structured=json.loads(text))
            except (json.JSONDecodeError, TypeError, ValueError) as exc:
                raise FeedbackServiceError("feedback_raw_archive_failed: structured fragment invalid") from exc
        return FeedbackInput(**common, text=text)

    def assess_and_save_impact(self, event: FeedbackEvent, attributions: list[FeedbackAttribution],
                               progress: list[PlanProgressEvent]) -> FeedbackImpactAssessment:
        impact = assess_impact(event, attributions, [item.progress_event_id for item in progress])
        return self.repository.save("feedback_impact", impact, owner_id=event.user_id)

    def create_directives(self, event: FeedbackEvent, impact: FeedbackImpactAssessment,
                          claim_ids: list[str]) -> list[FeedbackDirective]:
        types: list[tuple[str, str]] = []
        if impact.candidate_rebuild_required:
            types.append(("candidate_profile_rebuild_required", "new_candidate_feedback_claim"))
        if impact.role_instance_refresh_required:
            types.append(("role_instance_refresh_required", "new_job_or_company_feedback_signal"))
        if impact.role_family_aggregation_candidate:
            types.append(("role_family_aggregation_candidate", "single_event_aggregation_candidate"))
        if impact.intent_review_required:
            types.append(("intent_review_required", "feedback_may_change_intent"))
        if impact.rematch_required_after_rebuild:
            types.append(("rematch_required", "upstream_snapshot_change_requires_rematch"))
        if impact.replan_required:
            types.append(("replan_required", "feedback_changes_relevant_plan_input"))
        directives: list[FeedbackDirective] = []
        for directive_type, reason in types:
            digest = canonical_hash("feedback-directive", [event.feedback_event_id, directive_type,
                                                           sorted(claim_ids), event.target_job_profile_ids])
            directive = FeedbackDirective(
                directive_id=f"feedback-directive:{digest[7:31]}", directive_type=directive_type,
                originating_feedback_event_id=event.feedback_event_id, originating_plan_id=event.plan_id,
                reason_codes=[reason], required_input_refs=sorted(claim_ids),
                affected_target_ids=event.target_job_profile_ids,
            )
            directives.append(self.repository.save("feedback_directive", directive, owner_id=event.user_id))
        return directives

    def resolve_directive(self, *, user_id: str, directive_id: str, response_id: str,
                          resolved_refs: list[str], old_snapshot_ref: str | None = None,
                          no_change: bool = False) -> FeedbackDirective:
        directive = self.repository.get(directive_id, FeedbackDirective, owner_id=user_id)
        if directive is None:
            raise FeedbackServiceError("directive_resolution_invalid: directive")
        if directive.status == "resolved":
            payload = {"directive_id": directive_id, "resolved_refs": resolved_refs, "old_snapshot_ref": old_snapshot_ref,
                       "no_change": no_change, "user_id": user_id}
            self.repository.save_resolution(directive_id, response_id, payload)
            return directive
        if directive.directive_type in {"candidate_profile_rebuild_required", "intent_review_required", "role_instance_refresh_required"}:
            if no_change and directive.directive_type == "role_instance_refresh_required":
                if not resolved_refs or not resolved_refs[0].startswith("no-change:"):
                    raise FeedbackServiceError("directive_resolution_invalid: no-change receipt")
            else:
                if len(resolved_refs) != 1:
                    raise FeedbackServiceError("directive_resolution_invalid: snapshot count")
                new = self.profiles.get_profile(resolved_refs[0])
                old = self.profiles.get_profile(str(old_snapshot_ref)) if old_snapshot_ref else None
                expected = {"candidate_profile_rebuild_required": "candidate", "intent_review_required": "career_intent",
                            "role_instance_refresh_required": "role"}[directive.directive_type]
                if new is None or old is None or new.profile_type != expected or old.profile_type != expected \
                        or new.subject_id != old.subject_id or new.version <= old.version:
                    raise FeedbackServiceError("directive_resolution_invalid: snapshot successor")
                self._assert_snapshot_owner(new, user_id)
                self._assert_snapshot_owner(old, user_id)
        elif directive.directive_type == "role_family_aggregation_candidate":
            if not no_change:
                if len(resolved_refs) != 1 or self.profiles.get_profile(resolved_refs[0]) is None:
                    raise FeedbackServiceError("directive_resolution_invalid: family aggregation")
        elif directive.directive_type == "rematch_required":
            if len(resolved_refs) != 1:
                raise FeedbackServiceError("directive_resolution_invalid: comparison")
            comparison = self.matching.get(resolved_refs[0], ComparisonSet, owner_id=user_id)
            if comparison is None or comparison.status != "current":
                raise FeedbackServiceError("directive_resolution_invalid: comparison")
        elif directive.directive_type == "replan_required":
            if len(resolved_refs) != 1:
                raise FeedbackServiceError("directive_resolution_invalid: plan")
            plan = self.preparation.get(resolved_refs[0], LearningPlan, owner_id=user_id)
            if plan is None or (directive.originating_plan_id and plan.previous_plan_id != directive.originating_plan_id
                                and plan.supersedes_plan_id != directive.originating_plan_id):
                raise FeedbackServiceError("directive_resolution_invalid: plan successor")
        payload = {"directive_id": directive_id, "resolved_refs": resolved_refs, "old_snapshot_ref": old_snapshot_ref,
                   "no_change": no_change, "user_id": user_id}
        self.repository.save_resolution(directive_id, response_id, payload)
        return self.repository.replace_lifecycle(directive_id, FeedbackDirective, "resolved",
                                                 extra_updates={"resolved_refs": resolved_refs})

    def _assert_snapshot_owner(self, snapshot: ProfileSnapshot, user_id: str) -> None:
        declared = snapshot.profile_data.get("owner_id") or snapshot.profile_data.get("user_id")
        if declared is not None:
            if str(declared) != user_id:
                raise FeedbackServiceError("directive_resolution_invalid: snapshot owner")
            return
        if not snapshot.supporting_claim_ids:
            raise FeedbackServiceError("directive_resolution_invalid: snapshot owner unverifiable")
        for claim_id in snapshot.supporting_claim_ids:
            claim = self.evidence.get_claim(claim_id)
            if claim is None:
                raise FeedbackServiceError("directive_resolution_invalid: snapshot claim")
            for fragment_id in claim.evidence_fragment_ids:
                fragment = self.evidence.get_fragment(fragment_id)
                artifact = self.evidence.get_artifact(fragment.artifact_id) if fragment else None
                if artifact is None or artifact.owner_id != user_id:
                    raise FeedbackServiceError("directive_resolution_invalid: snapshot owner")


__all__ = ["FeedbackService", "FeedbackServiceError"]
