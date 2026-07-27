"""Prompt boundary for the optional v0.6 MatchExplanation provider."""

import json
from typing import Any


PROMPT_NAME = "match_explanation"
PROMPT_VERSION = "match_explanation_v1"
SCHEMA_VERSION = "v0.6"


def build_match_explanation_messages(payload: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": (
                "MATCH_EXPLANATION_V1. Return JSON only. Explain only supplied fact IDs. "
                "Never alter numbers, status, weights, gap types, ranking, or routes. "
                "Never predict Offer, admission, or interview success probability."
            ),
        },
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False, sort_keys=True)},
    ]
