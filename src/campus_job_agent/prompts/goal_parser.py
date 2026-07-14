"""JSON-only prompt contract for v0.2 goal parsing."""

PROMPT_NAME = "goal_parser"
PROMPT_VERSION = "v1"
SCHEMA_VERSION = "v0.2"


SCHEMA_INSTRUCTIONS = """
Return exactly one JSON object with these fields:
- role_query: string, use "unknown" if not present.
- city: string, use "unknown" if not present.
- graduation_year: string, use "unknown" if not present.
- recruitment_type: one of "autumn_campus", "spring_campus", "internship", "unknown".
- keywords: array of strings, use [] if none are present.
- raw_text: string, exactly equal to the original user goal.
- companies: array of strings, use [] if none are present.
- industries: array of strings, use [] if none are present.
- locations: array of strings, use [] if none are present.
- constraints: array of strings, use [] if none are present.
- confidence: number or null.
- warnings: array of strings, use [] if none are present.
Do not infer cities, years, companies, or roles that the user did not provide.
Do not output Markdown, code fences, comments, or explanatory text.
"""


def build_goal_parser_messages(user_input: str) -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": (
                "You are a structured parser. Output only valid JSON. "
                "The JSON must satisfy the requested schema."
            ),
        },
        {
            "role": "user",
            "content": f"{SCHEMA_INSTRUCTIONS}\nOriginal user goal:\n{user_input}",
        },
    ]


def build_goal_parser_retry_messages(
    user_input: str,
    previous_output: str,
    error_summary: str,
) -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": (
                "You are repairing structured JSON. Output only a complete "
                "valid JSON object that satisfies the schema."
            ),
        },
        {
            "role": "user",
            "content": (
                f"{SCHEMA_INSTRUCTIONS}\nOriginal user goal:\n{user_input}\n"
                f"Previous output summary:\n{previous_output[:500]}\n"
                f"Validation error summary:\n{error_summary[:500]}\n"
                "Return the full corrected JSON object, not a patch."
            ),
        },
    ]
