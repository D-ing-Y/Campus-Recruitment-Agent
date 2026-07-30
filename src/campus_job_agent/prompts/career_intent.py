"""Evidence-bound CareerIntent candidate extraction prompt."""

from __future__ import annotations

import json

from campus_job_agent.schemas import EvidenceFragment


INTENT_PROMPT_NAME = "career_intent_extractor"
INTENT_PROMPT_VERSION = "career_intent_extractor_v1"
INTENT_SCHEMA_VERSION = "career_intent_candidate_v0.7.1"


def build_career_intent_messages(fragment: EvidenceFragment) -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": (
                "CAREER_INTENT_EXTRACTOR_V071\n"
                "Extract only the user's job-search intent from the supplied evidence. "
                "Return target_roles and typed constraints with exact evidence_fragment_ids. "
                "Allowed constraint keys: location, industry, company, company_type, work_mode, "
                "recruitment_type, graduation_year, other. Values must always be arrays of strings. "
                "Use kind=hard only for explicit must/only/required language. Prefer/priority/would-like "
                "language is negotiable. The Chinese word 校招 alone means recruitment_type value "
                "campus_unspecified and unresolved_fields must contain recruitment_type; never infer "
                "autumn_campus or spring_campus. Explicit 2027 graduation is graduation_year=2027. "
                "Keep the user's raw target role wording, for example Agent 开发. Do not invent salary, "
                "company, industry, season, capability, or candidate facts. confidence is 0..1."
            ),
        },
        {
            "role": "user",
            "content": json.dumps(
                {
                    "fragment": {
                        "fragment_id": fragment.fragment_id,
                        "artifact_id": fragment.artifact_id,
                        "locator": fragment.locator,
                        "text": fragment.text,
                    }
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
        },
    ]


def build_career_intent_retry_messages(
    fragment: EvidenceFragment, previous: str, error: str
) -> list[dict[str, str]]:
    messages = build_career_intent_messages(fragment)
    messages.append({
        "role": "user",
        "content": (
            "The previous structured candidate failed validation. Correct only the schema or evidence "
            f"mapping and return the complete object again. Error: {error}. Previous: {previous}"
        ),
    })
    return messages


__all__ = [
    "INTENT_PROMPT_NAME", "INTENT_PROMPT_VERSION", "INTENT_SCHEMA_VERSION",
    "build_career_intent_messages", "build_career_intent_retry_messages",
]
