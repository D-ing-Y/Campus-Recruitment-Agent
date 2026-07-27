"""Versioned structured-output boundary for feedback candidates."""

FEEDBACK_OBSERVATION_PROMPT_VERSION = "feedback_observation_v1"
FEEDBACK_DIAGNOSIS_PROMPT_VERSION = "feedback_diagnosis_v1"

SYSTEM_PROMPT = """Return JSON only. Observations must cite supplied fragments and contain no inferred cause.
Diagnoses must cite observations, state inference, alternatives and limitations. Never infer capability from rejection,
mastery from task completion, or role-family frequency from one event. Never elevate self-report authority."""

