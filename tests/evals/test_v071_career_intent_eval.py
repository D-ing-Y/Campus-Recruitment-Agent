from __future__ import annotations

import json
from pathlib import Path

import pytest

from campus_job_agent.schemas import (
    CareerIntentCandidate,
    IntentConstraintCandidate,
    IntentValueCandidate,
)
from campus_job_agent.workflows.career_intent.policy import validate_candidate


GOLD = json.loads(
    (Path(__file__).parents[1] / "fixtures" / "v071" / "career_intent_gold.json")
    .read_text(encoding="utf-8")
)["cases"]


@pytest.mark.parametrize("case", GOLD, ids=lambda item: item["case_id"])
def test_wp2_domain_classification_gold_is_deterministic(case: dict) -> None:
    fragment_id = f"fragment-{case['case_id']}"
    candidate = CareerIntentCandidate(
        target_roles=[IntentValueCandidate(
            value="Agent 开发", evidence_fragment_ids=[fragment_id], confidence=0.95,
        )],
        constraints=[IntentConstraintCandidate(
            key=case["model_key"], values=[case["model_value"]],
            kind=case["model_kind"], evidence_fragment_ids=[fragment_id], confidence=0.9,
        )],
    )
    draft = validate_candidate(
        candidate=candidate, user_id="eval-owner", artifact_id="eval-artifact",
        fragment_id=fragment_id, raw_text=case["raw_text"],
    )
    assert len(draft.constraints) == 1
    actual = draft.constraints[0]
    assert actual.key == case["expected_key"]
    assert actual.kind == case["expected_kind"]
    assert actual.affects_search_scope is case["expected_affects_search_scope"]
    assert actual.source_ref and fragment_id in actual.source_ref
