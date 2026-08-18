from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from campus_job_agent.runtime import RuntimeFactory
from campus_job_agent.schemas import EvidenceFragment, LLMResponse
from campus_job_agent.workflows.resume_evidence import ResumeEvidenceExtractor


class _RecordingStructuredProvider:
    name = "langchain_deepseek"

    def __init__(self) -> None:
        self.strategies: list[str] = []
        self.output_models: list[Any] = []

    def generate_structured(
        self,
        request: Any,
        output_model: Any,
        *,
        requested_strategy: str = "auto",
    ) -> LLMResponse:
        self.strategies.append(requested_strategy)
        self.output_models.append(output_model)
        payload = {
            "personal_advantage": {
                "text": None,
                "evidence_fragment_ids": [],
            },
            "career_expectations": [],
            "work_experiences": [],
            "project_experiences": [{
                "name": "Campus Agent",
                "role": "Developer",
                "start_date": None,
                "end_date": None,
                "raw_subtype": None,
                "content": "Built a structured resume workflow.",
                "evidence_fragment_ids": ["fragment-runtime-resume"],
            }],
            "education_experiences": [],
            "professional_skills": {
                "text": "Python and LangGraph",
                "evidence_fragment_ids": ["fragment-runtime-resume"],
            },
            "custom_sections": [],
        }
        return LLMResponse(
            text=json.dumps(payload),
            parsed_json=payload,
            provider=self.name,
            model="deepseek-chat",
            requested_strategy=requested_strategy,
            effective_strategy=requested_strategy,
            raw_metadata={"tool_call_ids": ["call-runtime-resume"]},
        )


def test_runtime_factory_routes_resume_extraction_through_explicit_tool_calling(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("CAMPUS_AGENT_LLM_PROVIDER", "openai_compatible")
    monkeypatch.setenv("CAMPUS_AGENT_LLM_CACHE_ENABLED", "false")
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-secret")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.deepseek.com")
    monkeypatch.setenv("OPENAI_MODEL", "deepseek-chat")
    provider = _RecordingStructuredProvider()
    constructed: list[bool] = []

    def build_provider(config):
        constructed.append(True)
        return provider

    monkeypatch.setattr(
        "campus_job_agent.runtime.factory.build_llm_provider", build_provider
    )
    runtime = RuntimeFactory(data_root=tmp_path / "data").build(
        owner_id="runtime-resume-owner"
    )

    assert constructed == []
    assert runtime.llm_provider.integration == "deepseek"
    assert runtime.llm_provider.capabilities.tool_calling is True

    text = (
        "PROJECT EXPERIENCE Campus Agent Developer built a structured resume "
        "workflow. PROFESSIONAL SKILLS Python and LangGraph."
    )
    fragment = EvidenceFragment(
        fragment_id="fragment-runtime-resume",
        artifact_id="artifact-runtime-resume",
        locator_type="pdf_page",
        locator={"page": 1},
        text=text,
        text_hash=hashlib.sha256(text.encode()).hexdigest(),
    )
    extractor = ResumeEvidenceExtractor(
        runtime.llm_config, runtime.llm_provider, runtime.llm_cache
    )
    _, batch, calls, _ = extractor.extract(
        candidate_id="candidate-runtime-resume",
        fragments=[fragment],
    )

    assert constructed == [True]
    assert provider.strategies == ["tool_calling"]
    assert provider.output_models[0].__name__ == "ResumeExtractionBatch"
    assert calls[0].effective_strategy == "tool_calling"
    assert calls[0].capabilities is not None
    assert calls[0].capabilities["tool_calling"] is True
    assert batch.project_experiences[0].name == "Campus Agent"
