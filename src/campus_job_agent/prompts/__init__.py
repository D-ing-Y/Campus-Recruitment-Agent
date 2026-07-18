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
from campus_job_agent.prompts.candidate_sufficiency import (
    PROMPT_NAME as SUFFICIENCY_PROMPT_NAME,
    PROMPT_VERSION as SUFFICIENCY_PROMPT_VERSION,
    SCHEMA_VERSION as SUFFICIENCY_SCHEMA_VERSION,
    build_candidate_sufficiency_messages,
    build_candidate_sufficiency_retry_messages,
)
from campus_job_agent.prompts.question_planner import (
    PROMPT_NAME as QUESTION_PROMPT_NAME,
    PROMPT_VERSION as QUESTION_PROMPT_VERSION,
    SCHEMA_VERSION as QUESTION_SCHEMA_VERSION,
    build_question_planner_messages,
    build_question_planner_retry_messages,
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
    "SUFFICIENCY_PROMPT_NAME",
    "SUFFICIENCY_PROMPT_VERSION",
    "SUFFICIENCY_SCHEMA_VERSION",
    "build_candidate_sufficiency_messages",
    "build_candidate_sufficiency_retry_messages",
    "QUESTION_PROMPT_NAME",
    "QUESTION_PROMPT_VERSION",
    "QUESTION_SCHEMA_VERSION",
    "build_question_planner_messages",
    "build_question_planner_retry_messages",
]
