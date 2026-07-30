from __future__ import annotations

import pytest
from pydantic import ValidationError

from campus_job_agent.schemas import (
    CareerIntent,
    CareerIntentCandidate,
    IntentConstraintCandidate,
    IntentRevisionPatch,
    IntentValidationReceipt,
    IntentValueCandidate,
)
from campus_job_agent.workflows.career_intent.policy import (
    apply_revision,
    publish_intent,
    validate_candidate,
)
from campus_job_agent.workflows.career_intent.repository import SQLiteIntentRepository


FRAGMENT_ID = "fragment-intent-1"


def _candidate(*, recruitment: str = "campus_unspecified") -> CareerIntentCandidate:
    return CareerIntentCandidate(
        target_roles=[IntentValueCandidate(
            value="Agent 开发",
            evidence_fragment_ids=[FRAGMENT_ID],
            confidence=0.96,
        )],
        constraints=[
            IntentConstraintCandidate(
                key="location", values=["成都"], kind="hard",
                evidence_fragment_ids=[FRAGMENT_ID], confidence=0.98,
            ),
            IntentConstraintCandidate(
                key="graduation_year", values=["2027"], kind="hard",
                evidence_fragment_ids=[FRAGMENT_ID], confidence=0.98,
            ),
            IntentConstraintCandidate(
                key="recruitment_type", values=[recruitment], kind="hard",
                evidence_fragment_ids=[FRAGMENT_ID], confidence=0.94,
            ),
            IntentConstraintCandidate(
                key="company_type", values=["大型企业"], kind="negotiable",
                evidence_fragment_ids=[FRAGMENT_ID], confidence=0.91,
            ),
            IntentConstraintCandidate(
                key="company_type", values=["互联网科技公司"], kind="negotiable",
                evidence_fragment_ids=[FRAGMENT_ID], confidence=0.90,
            ),
        ],
        unresolved_fields=["recruitment_type"] if recruitment == "campus_unspecified" else [],
    )


def _draft() -> object:
    return validate_candidate(
        candidate=_candidate(), user_id="intent-owner", artifact_id="artifact-intent-1",
        fragment_id=FRAGMENT_ID,
        raw_text="想找 Agent 开发，工作地点必须成都，2027 年毕业，参加校招，优先大型企业和互联网科技公司",
    )


def test_intent_validator_preserves_hard_preference_and_unresolved_boundary() -> None:
    draft = _draft()
    assert draft.target_roles == ["Agent 开发"]
    assert draft.target_role_families == ["ai_agent_engineering"]
    assert draft.unresolved_fields == ["recruitment_type"]
    assert draft.validation_issues == []

    by_key: dict[str, list] = {}
    for item in draft.constraints:
        by_key.setdefault(item.key, []).append(item)
        assert item.source_ref and FRAGMENT_ID in item.source_ref
        assert item.status == "unknown"
    assert by_key["location"][0].kind == "hard"
    assert by_key["location"][0].affects_search_scope is True
    assert all(item.kind == "negotiable" for item in by_key["company_type"])
    assert all(item.affects_search_scope is False for item in by_key["company_type"])


def test_revision_then_publish_uses_constraints_as_canonical_source() -> None:
    revised = apply_revision(
        _draft(), IntentRevisionPatch(recruitment_type="autumn_campus"),
        response_fragment_id="fragment-response-1",
    )
    assert revised.unresolved_fields == []
    assert revised.validation_issues == []
    company_constraint = next(item for item in revised.constraints if item.key == "company_type")
    assert company_constraint.value == ["大型企业", "互联网科技公司"]
    location = next(item for item in revised.constraints if item.key == "location")
    recruitment = next(item for item in revised.constraints if item.key == "recruitment_type")
    assert location.source_ref == f"{FRAGMENT_ID}#/intent/location"
    assert recruitment.source_ref == "fragment-response-1#/patch/recruitment_type"

    intent = publish_intent(revised, response_id="response-confirm-1")
    assert intent.confirmed is True
    assert intent.locations == ["成都"]
    assert intent.graduation_year == "2027"
    assert intent.recruitment_type == "autumn_campus"
    assert intent.company_types == []
    assert any(
        item.key == "company_type" and item.kind == "negotiable" and item.status == "confirmed"
        for item in intent.constraints
    )

    with pytest.raises(ValidationError, match="flat fields drift"):
        CareerIntent.model_validate({
            **intent.model_dump(mode="json"),
            "locations": ["上海"],
        })


def test_domain_validator_blocks_wrong_fragment_and_model_classification() -> None:
    candidate = _candidate(recruitment="autumn_campus")
    candidate.target_roles[0].evidence_fragment_ids = ["fragment-forged"]
    candidate.constraints[-1].kind = "hard"
    draft = validate_candidate(
        candidate=candidate, user_id="intent-owner", artifact_id="artifact-intent-1",
        fragment_id=FRAGMENT_ID,
        raw_text="想找 Agent 开发，必须成都，2027 年毕业，参加秋招，优先大型企业和互联网科技公司",
    )
    assert "invalid_target_role_fragment_ref" in draft.validation_issues
    assert "target_roles_missing" in draft.validation_issues
    assert "classification_mismatch:company_type:negotiable" in draft.validation_issues
    assert "target_roles" in draft.unresolved_fields
    with pytest.raises(ValueError, match="still requires confirmation"):
        publish_intent(draft, response_id="response-must-not-publish")


def test_domain_validator_normalizes_company_type_mislabeled_as_industry() -> None:
    candidate = _candidate()
    candidate.constraints[-1].key = "industry"
    draft = validate_candidate(
        candidate=candidate, user_id="intent-owner", artifact_id="artifact-intent-1",
        fragment_id=FRAGMENT_ID,
        raw_text="想找 Agent 开发，工作地点必须成都，2027 年毕业，参加校招，优先大型企业和互联网科技公司",
    )
    assert not any(item.key == "industry" for item in draft.constraints)
    assert [
        item.value for item in draft.constraints if item.key == "company_type"
    ] == ["大型企业", "互联网科技公司"]


def test_intent_repository_uses_each_record_type_primary_identifier(tmp_path) -> None:
    repository = SQLiteIntentRepository(tmp_path / "intent.sqlite3")
    draft = _draft()
    receipt = IntentValidationReceipt(
        receipt_id="intent-validation-receipt-1", run_id="run-1",
        draft_id=draft.draft_id, status="needs_confirmation",
    )
    repository.save("intent_draft", draft, owner_id="intent-owner")
    repository.save("validation_receipt", receipt, owner_id="intent-owner")
    assert repository.get(draft.draft_id, type(draft)) == draft
    assert repository.get(receipt.receipt_id, IntentValidationReceipt) == receipt
