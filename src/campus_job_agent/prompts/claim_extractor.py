"""Evidence-bound claim extraction prompt."""

import json

from campus_job_agent.ontology import CapabilityOntology
from campus_job_agent.schemas import EvidenceFragment

CLAIM_PROMPT_NAME = "candidate_claim_extractor"
CLAIM_PROMPT_VERSION = "candidate_claim_extractor_v5"
CLAIM_SCHEMA_VERSION = "candidate_claim_v0.7.1"


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
    capability_ids = [
        item.capability_id
        for item in CapabilityOntology.load_default().capabilities
    ]
    return [
        {
            "role": "system",
            "content": (
                "CLAIM_EXTRACTOR_V071\n"
                "You extract atomic candidate facts only from supplied evidence. "
                "Return one JSON object with key claims. Every claim must cite at "
                "least one supplied fragment_id. Never invent facts. Allowed "
                "claim_type: observed_fact, user_reported, model_inference, "
                "feedback_signal. Use only capability:<capability_id>, "
                "education:<stable_record_id>.<field>, or "
                "experience:<stable_record_id>.<field>. Education fields are "
                "institution, degree, major, graduation_year. Experience fields are "
                "kind, title, description, responsibilities, technologies, outputs, results. "
                "List fields must be JSON arrays of non-empty strings. Reuse the same stable "
                "record_id for fields from one record. A project title does not prove an "
                "individual responsibility. Do not output contact, award, or arbitrary paths. "
                "Never output generic predicates: education, skill, project_experience, "
                "research_experience, experience, project, or capability. Split every record "
                "into atomic field claims. Every claim object must also contain claim_type, "
                "evidence_fragment_ids, and confidence. confidence must be a JSON number from "
                '0.0 through 1.0, for example "confidence":0.9; never strings such as "high". '
                "Canonical predicate/value examples: "
                '{"predicate":"education:graduate.institution","value":"Example University","claim_type":"observed_fact","evidence_fragment_ids":["fragment-id"],"confidence":0.9}; '
                '{"predicate":"experience:depression-project.title","value":"Multimodal Project"}; '
                '{"predicate":"experience:depression-project.responsibilities","value":["Implemented model"]}; '
                '{"predicate":"capability:programming.python","value":{"level":"advanced"}}. '
                "Replace example values only with facts directly supported by the supplied fragments. "
                f"Allowed capability_id values: {', '.join(capability_ids)}."
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
