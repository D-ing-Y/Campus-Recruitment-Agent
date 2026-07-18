"""Deterministic mock LLM provider for tests and local smoke runs."""

import json

from campus_job_agent.llm.base import LLMProviderError
from campus_job_agent.schemas import LLMRequest, LLMResponse


class MockLLMProvider:
    name = "mock"

    def __init__(self, mode: str = "valid_json") -> None:
        self.mode = mode
        self.call_count = 0

    def generate(self, request: LLMRequest) -> LLMResponse:
        self.call_count += 1
        if self.mode == "provider_error":
            raise LLMProviderError("Mock provider error")
        if self.mode == "invalid_json_then_valid" and self.call_count == 1:
            return self._response("{not valid json", request.model)
        if self.mode == "schema_error_then_valid" and self.call_count == 1:
            return self._response(json.dumps({"role_query": "AI Agent"}), request.model)
        if self.mode == "always_invalid_json":
            return self._response("{not valid json", request.model)
        if request.messages and (
            "CLAIM_EXTRACTOR_V03" in request.messages[0]["content"]
            or "CLAIM_EXTRACTOR_V04" in request.messages[0]["content"]
        ):
            if self.mode == "claim_schema_error_then_valid" and self.call_count == 1:
                return self._response(
                    json.dumps({"claims": [{"predicate": "skill"}]}),
                    request.model,
                )
            return self._response(
                json.dumps(_valid_claims(request.messages), ensure_ascii=False),
                request.model,
            )
        if request.messages and "CANDIDATE_SUFFICIENCY_V1" in request.messages[0]["content"]:
            return self._response(
                json.dumps(_valid_sufficiency(request.messages), ensure_ascii=False),
                request.model,
            )
        if request.messages and "CANDIDATE_QUESTION_PLANNER_V1" in request.messages[0]["content"]:
            return self._response(
                json.dumps(_valid_question_plan(request.messages), ensure_ascii=False),
                request.model,
            )
        return self._response(json.dumps(_valid_goal(), ensure_ascii=False), request.model)

    def _response(self, text: str, model: str) -> LLMResponse:
        return LLMResponse(
            text=text,
            provider=self.name,
            model=model,
            usage=None,
            raw_metadata={"mock_mode": self.mode, "call_count": self.call_count},
        )


def _valid_goal() -> dict:
    return {
        "role_query": "AI Agent",
        "city": "成都",
        "graduation_year": "2027",
        "recruitment_type": "autumn_campus",
        "keywords": ["AI Agent", "LLM", "智能体"],
        "raw_text": "成都 AI Agent 2027 秋招",
        "companies": [],
        "industries": [],
        "locations": ["成都"],
        "constraints": [],
        "confidence": 0.95,
        "warnings": [],
    }


def _valid_claims(messages: list[dict[str, str]]) -> dict:
    payload = json.loads(messages[1]["content"])
    fragments = payload.get("fragments", [])
    if not fragments:
        return {"claims": []}
    claims = []
    for fragment in fragments:
        text = str(fragment.get("text", ""))
        labels = [
            name
            for name in ["Python", "LangGraph", "RAG", "LLM"]
            if name.lower() in text.lower()
        ]
        claims.extend(
            [
                {
                    "predicate": f"capability:{label}",
                    "value": {"label": label, "level": "unknown"},
                    "claim_type": "observed_fact",
                    "evidence_fragment_ids": [fragment["fragment_id"]],
                    "confidence": 0.8,
                }
                for label in labels
            ]
        )
        lower = text.lower()
        if "education:" in lower or "university" in lower or "大学" in text:
            claims.append(
                {
                    "predicate": "education.institution",
                    "value": _line_value(text, ["university", "大学", "education:"]),
                    "claim_type": "observed_fact",
                    "evidence_fragment_ids": [fragment["fragment_id"]],
                    "confidence": 0.9,
                }
            )
        year = next((value for value in ["2026", "2027", "2028"] if value in text), None)
        if year and ("graduat" in lower or "毕业" in text):
            claims.append(
                {
                    "predicate": "education.graduation_year",
                    "value": year,
                    "claim_type": "observed_fact",
                    "evidence_fragment_ids": [fragment["fragment_id"]],
                    "confidence": 0.95,
                }
            )
        if "project" in lower or "项目" in text:
            claims.extend(
                [
                    {
                        "predicate": "experiences[project].kind",
                        "value": "project",
                        "claim_type": "observed_fact",
                        "evidence_fragment_ids": [fragment["fragment_id"]],
                        "confidence": 0.95,
                    },
                    {
                        "predicate": "experiences[project].title",
                        "value": "Documented project",
                        "claim_type": "observed_fact",
                        "evidence_fragment_ids": [fragment["fragment_id"]],
                        "confidence": 0.8,
                    },
                ]
            )
        if "responsibilit" in lower or "负责" in text or "implemented" in lower:
            claims.append(
                {
                    "predicate": "experiences[project].responsibilities",
                    "value": _responsibility_value(text),
                    "claim_type": "observed_fact",
                    "evidence_fragment_ids": [fragment["fragment_id"]],
                    "confidence": 0.85,
                }
            )
    return {"claims": claims}


def _line_value(text: str, markers: list[str]) -> str:
    for line in text.splitlines():
        if any(marker in line.lower() or marker in line for marker in markers):
            return line.strip()[:240]
    return text.strip()[:240]


def _responsibility_value(text: str) -> str:
    for line in text.splitlines():
        lower = line.lower()
        if "responsibilit" in lower or "负责" in line or "implemented" in lower:
            return line.strip()[:400]
    return text.strip()[:400]


def _valid_sufficiency(messages: list[dict[str, str]]) -> dict:
    payload = json.loads(messages[1]["content"])
    profile = payload.get("profile", {})
    has_education = bool(profile.get("education"))
    has_experience = bool(profile.get("experiences"))
    has_capability = bool(profile.get("capabilities"))
    has_responsibility = bool(profile.get("responsibility_boundaries"))
    gaps = []
    if has_experience and not has_responsibility:
        gaps.append(
            {
                "gap_id": "gap:experience.project.responsibility",
                "target_path": "experiences[project].responsibilities",
                "category": "responsibility_boundary",
                "description": "The candidate's individual project responsibilities are unclear.",
                "importance": 0.95,
                "uncertainty": 0.9,
                "answerability": 0.95,
                "evidence_cost": 0.05,
                "information_value": 0,
                "preferred_action": "ask_user",
                "related_claim_ids": profile.get("supporting_claim_ids", []),
                "related_artifact_ids": payload.get("active_artifact_ids", []),
                "status": "open",
            }
        )
    complete = has_education and has_experience and has_capability and has_responsibility
    action = "complete" if complete else "ask_user" if gaps else "request_more_materials"
    return {
        "assessment_id": payload["assessment_id"],
        "schema_version": "v0.4",
        "candidate_id": payload["candidate_id"],
        "profile_snapshot_id": payload.get("profile_snapshot_id"),
        "is_sufficient": complete,
        "dimension_results": {
            "education": "sufficient" if has_education else "insufficient",
            "experience": "sufficient" if has_experience else "insufficient",
            "capability": "sufficient" if has_capability else "insufficient",
            "responsibility_boundary": "sufficient" if has_responsibility else "insufficient",
            "evidence_quality": "sufficient"
            if profile.get("supporting_claim_ids")
            else "insufficient",
        },
        "information_gaps": gaps,
        "blocking_conflict_ids": [
            value.get("conflict_id") for value in profile.get("conflicts", [])
        ],
        "recommended_action": action,
        "reason": "Deterministic mock assessment over the supplied profile summary.",
        "confidence": 0.9,
        "evaluator": {"provider": "mock", "model": "mock-sufficiency"},
        "prompt_version": "candidate_sufficiency_v1",
    }


def _valid_question_plan(messages: list[dict[str, str]]) -> dict:
    payload = json.loads(messages[1]["content"])
    gaps = payload.get("information_gaps", [])[: payload.get("max_questions", 3)]
    return {
        "plan_id": payload["plan_id"],
        "schema_version": "v0.4",
        "assessment_id": payload["assessment_id"],
        "questions": [
            {
                "question_id": f"question:{gap['gap_id']}",
                "gap_id": gap["gap_id"],
                "target_path": gap["target_path"],
                "prompt": (
                    "What did you personally own or implement for this item? "
                    "Please distinguish your contribution from the team's output."
                ),
                "reason": gap["description"],
                "answer_type": "free_text",
                "required": False,
                "related_claim_ids": gap.get("related_claim_ids", []),
            }
            for gap in gaps
        ],
    }
