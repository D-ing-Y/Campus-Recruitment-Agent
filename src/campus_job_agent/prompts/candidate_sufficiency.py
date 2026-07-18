"""Versioned prompt for v0.4 candidate sufficiency evaluation."""

import json
from typing import Any


PROMPT_NAME = "candidate_sufficiency"
PROMPT_VERSION = "candidate_sufficiency_v1"
SCHEMA_VERSION = "v0.4"


def build_candidate_sufficiency_messages(
    payload: dict[str, Any],
) -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": (
                "CANDIDATE_SUFFICIENCY_V1\n"
                "Return JSON only and validate it as SufficiencyAssessment v0.4. "
                "Evaluate whether the evidence-bounded profile is honest enough for "
                "later career exploration, not whether the candidate qualifies for a "
                "job. CareerIntent is out of scope. Only recommend one of read_more, "
                "ask_user, request_more_materials, finalize_with_unknowns, complete, "
                "fail. Do not invent evidence or raise budgets. The runtime recomputes "
                "information_value from its component values."
            ),
        },
        {
            "role": "user",
            "content": json.dumps(payload, ensure_ascii=False, sort_keys=True),
        },
    ]


def build_candidate_sufficiency_retry_messages(
    payload: dict[str, Any], previous: str, error: str
) -> list[dict[str, str]]:
    messages = build_candidate_sufficiency_messages(payload)
    messages.append(
        {
            "role": "user",
            "content": (
                "The previous output failed schema validation. Return the complete "
                f"corrected JSON object only. Error: {error}. Output: {previous}"
            ),
        }
    )
    return messages
