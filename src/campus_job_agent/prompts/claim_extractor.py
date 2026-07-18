"""Evidence-bound claim extraction prompt."""

import json

from campus_job_agent.schemas import EvidenceFragment

CLAIM_PROMPT_NAME = "candidate_claim_extractor"
CLAIM_PROMPT_VERSION = "candidate_claim_extractor_v2"
CLAIM_SCHEMA_VERSION = "v0.4"


def build_claim_extractor_messages(
    fragments: list[EvidenceFragment], subject_id: str
) -> list[dict[str, str]]:
    evidence = [
        {
            "fragment_id": fragment.fragment_id,
            "artifact_id": fragment.artifact_id,
            "locator": fragment.locator,
            "text_hash": fragment.text_hash,
            "text": fragment.text,
        }
        for fragment in fragments
    ]
    return [
        {
            "role": "system",
            "content": (
                "CLAIM_EXTRACTOR_V04\n"
                "You extract atomic candidate facts only from supplied evidence. "
                "Return one JSON object with key claims. Every claim must cite at "
                "least one supplied fragment_id. Never invent facts. Allowed "
                "claim_type: observed_fact, user_reported, model_inference, "
                "feedback_signal. Use capability:<label> for explicit skills; "
                "education.<field> for explicit education; and "
                "experiences[<id>].<field> for project or work evidence. A project "
                "title does not prove an individual responsibility."
            ),
        },
        {
            "role": "user",
            "content": json.dumps(
                {"subject_id": subject_id, "fragments": evidence},
                ensure_ascii=False,
                sort_keys=True,
            ),
        },
    ]


def build_claim_retry_messages(
    fragments: list[EvidenceFragment], subject_id: str, previous: str, error: str
) -> list[dict[str, str]]:
    messages = build_claim_extractor_messages(fragments, subject_id)
    messages.append(
        {
            "role": "user",
            "content": (
                "Your previous output was invalid. Return corrected JSON only. "
                f"Validation error: {error}. Previous output: {previous}"
            ),
        }
    )
    return messages
