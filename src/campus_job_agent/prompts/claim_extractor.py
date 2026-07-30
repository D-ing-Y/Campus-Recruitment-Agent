"""Evidence-bound claim extraction prompt."""

import json

from campus_job_agent.ontology import CapabilityOntology
from campus_job_agent.schemas import EvidenceFragment

CLAIM_PROMPT_NAME = "candidate_claim_extractor"
CLAIM_PROMPT_VERSION = "candidate_claim_extractor_v7"
CLAIM_SCHEMA_VERSION = "candidate_claim_v0.7.1.2"


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
                "CLAIM_EXTRACTOR_V071_P2\n"
                "You extract atomic candidate facts only from supplied evidence. "
                "Return one object with key claims using the typed Tool schema. Every claim "
                "must cite at least one supplied fragment_id. Never invent facts. Allowed "
                "claim_type: observed_fact, user_reported, model_inference, "
                "feedback_signal. Select claim_kind capability, education, experience_kind, "
                "experience_text, experience_list, or unsupported. Reuse the same batch-local "
                "record_id for fields from one record. The application replaces it with a "
                "deterministic persistence ID. A project title does not prove an "
                "individual responsibility. Do not output contact, award, or arbitrary paths. "
                "Use unsupported only for a source fact outside the current Candidate contract. "
                "Split every record into atomic typed claims. Every claim must contain claim_type, "
                "evidence_fragment_ids, and confidence. confidence must be a JSON number from "
                '0.0 through 1.0, for example "confidence":0.9; never strings such as "high". '
                "For capability, choose only a supplied capability_id and canonical level "
                "unknown/beginner/intermediate/advanced/expert. Preserve the source skill wording "
                "in value.raw_label and any different proficiency wording in value.raw_level. "
                "Use a capability_id only when raw_label is the same capability or one of its "
                "known aliases. Never force an unlisted language, library, tool, or general skill "
                "into a merely related capability_id; emit unsupported with a source-faithful "
                "predicate such as skill_tool or skill_language instead. Emit at most one "
                "capability claim for each capability_id. "
                "For experience_kind, value.kind must be employment/internship/research/project/"
                "competition/leadership/campus_activity/volunteering/entrepreneurship/teaching/"
                "training/other. value.context must be employment/internship/coursework/capstone/"
                "thesis/academic_research/public_funded_research/industry_collaboration/personal/"
                "open_source/competition/campus/volunteering/entrepreneurship/training/community/"
                "other/unspecified. Preserve the resume wording in value.raw_label. "
                "A role at a company is employment or internship; a distinct deliverable inside "
                "that role is project with employment or internship context. Course design uses "
                "project+coursework, graduation design project+capstone, vertical research "
                "research+public_funded_research, horizontal/client development project or "
                "research+industry_collaboration, and unknown labels other+other with raw_label. "
                "Education values and experience title/description are non-empty strings; "
                "responsibilities/technologies/outputs/results are non-empty string arrays. "
                "Typed examples: "
                '{"claim_kind":"education","record_id":"graduate","field":"institution","value":"Example University"}; '
                '{"claim_kind":"experience_kind","record_id":"depression-project","value":{"kind":"research","context":"public_funded_research","raw_label":"纵向科研经历"}}; '
                '{"claim_kind":"experience_text","record_id":"depression-project","field":"title","value":"Multimodal Project"}; '
                '{"claim_kind":"experience_list","record_id":"depression-project","field":"responsibilities","value":["Implemented model"]}; '
                '{"claim_kind":"capability","capability_id":"programming.python","value":{"level":"advanced","raw_label":"Python","raw_level":"熟练"}}. '
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
