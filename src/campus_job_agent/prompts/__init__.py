"""Prompt builders."""

from campus_job_agent.prompts.goal_parser import (
    PROMPT_NAME,
    PROMPT_VERSION,
    SCHEMA_VERSION,
    build_goal_parser_messages,
    build_goal_parser_retry_messages,
)
from campus_job_agent.prompts.claim_extractor import (
    CLAIM_PROMPT_NAME,
    CLAIM_PROMPT_VERSION,
    CLAIM_SCHEMA_VERSION,
    build_claim_extractor_messages,
    build_claim_retry_messages,
)

__all__ = [
    "PROMPT_NAME",
    "PROMPT_VERSION",
    "SCHEMA_VERSION",
    "build_goal_parser_messages",
    "build_goal_parser_retry_messages",
    "CLAIM_PROMPT_NAME",
    "CLAIM_PROMPT_VERSION",
    "CLAIM_SCHEMA_VERSION",
    "build_claim_extractor_messages",
    "build_claim_retry_messages",
]
