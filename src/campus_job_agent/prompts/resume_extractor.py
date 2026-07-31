"""PII-redacted structured resume extraction prompt."""

from __future__ import annotations

import json

from campus_job_agent.schemas import EvidenceFragment


RESUME_PROMPT_NAME = "resume_evidence_extractor"
RESUME_PROMPT_VERSION = "resume_evidence_extractor_v2"
RESUME_SCHEMA_VERSION = "resume_evidence_v0.7.1"


def build_resume_extractor_messages(
    fragments: list[EvidenceFragment], candidate_id: str
) -> list[dict[str, str]]:
    evidence = [
        {
            "fragment_id": item.fragment_id,
            "page": item.locator.get("page"),
            "text_hash": item.text_hash,
            "text": item.text,
        }
        for item in fragments
    ]
    return [
        {
            "role": "system",
            "content": (
                "RESUME_EVIDENCE_EXTRACTOR_V071\n"
                "Transcribe the supplied redacted resume into the typed Tool schema. "
                "This is source transcription, not candidate profiling. Preserve wording and "
                "record boundaries. Do not summarize, infer proficiency, invent missing values, "
                "or reconstruct redacted personal information. Use null or an empty list when a "
                "section is absent. Every non-empty text block or record must cite one or more "
                "supplied fragment_id values. personal_advantage and professional_skills remain "
                "separate. Employment and internship positions go to work_experiences. Projects, "
                "research, coursework, capstone and thesis records go to project_experiences and "
                "retain the source subtype in raw_subtype. Awards, certificates, campus activities, "
                "organizations and volunteering go to custom_sections. Bind education research "
                "directions or courses only to the visually adjacent education record, stopping "
                "at the next institution. Preserve source date words such as '至今' in end_date; "
                "null means that the source contains no end value. Emit each dated award or "
                "certificate bullet as its own custom_sections record; do not merge multiple "
                "items into one content block. Career expectations do not "
                "become candidate capabilities. Return only the structured Tool result."
            ),
        },
        {
            "role": "user",
            "content": json.dumps(
                {"candidate_id": candidate_id, "fragments": evidence},
                ensure_ascii=False,
                sort_keys=True,
            ),
        },
    ]


def build_resume_retry_messages(
    fragments: list[EvidenceFragment], candidate_id: str,
    previous: str, error: str,
) -> list[dict[str, str]]:
    messages = build_resume_extractor_messages(fragments, candidate_id)
    messages.append({
        "role": "user",
        "content": (
            "The previous Tool result failed schema validation. Correct only its structure and "
            f"source references. Error: {error}. Previous result: {previous}"
        ),
    })
    return messages


__all__ = [
    "RESUME_PROMPT_NAME", "RESUME_PROMPT_VERSION", "RESUME_SCHEMA_VERSION",
    "build_resume_extractor_messages", "build_resume_retry_messages",
]
