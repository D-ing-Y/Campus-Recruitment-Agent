"""Versioned prompt for v0.4 evidence-gap questions."""

import json
from typing import Any


PROMPT_NAME = "candidate_question_planner"
PROMPT_VERSION = "candidate_question_planner_v1"
SCHEMA_VERSION = "v0.4"


def build_question_planner_messages(
    payload: dict[str, Any],
) -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": (
                "CANDIDATE_QUESTION_PLANNER_V1\n"
                "Return JSON only as QuestionPlan v0.4. Every question must bind one "
                "supplied open gap, be directly answerable, avoid asked/skipped targets, "
                "and stay within max_questions. Never prompt the user to invent work."
            ),
        },
        {
            "role": "user",
            "content": json.dumps(payload, ensure_ascii=False, sort_keys=True),
        },
    ]


def build_question_planner_retry_messages(
    payload: dict[str, Any], previous: str, error: str
) -> list[dict[str, str]]:
    messages = build_question_planner_messages(payload)
    messages.append(
        {
            "role": "user",
            "content": (
                "The previous output failed schema validation. Return complete "
                f"corrected JSON only. Error: {error}. Output: {previous}"
            ),
        }
    )
    return messages
