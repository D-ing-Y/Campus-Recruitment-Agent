from pathlib import Path

import pytest

from campus_job_agent.evidence.claim_extractor import ClaimExtractorService
from campus_job_agent.llm import LLMCache, MockLLMProvider, StructuredOutputError
from campus_job_agent.schemas import EvidenceFragment, LLMConfig


def _fragment() -> EvidenceFragment:
    return EvidenceFragment(
        fragment_id="fragment-1",
        artifact_id="artifact-1",
        locator_type="char_range",
        locator={"start": 0, "end": 18},
        text="Python and LangGraph",
        text_hash="c" * 64,
    )


def test_claim_extractor_retries_and_cache_key_tracks_fragment_hash(tmp_path) -> None:
    provider = MockLLMProvider("claim_schema_error_then_valid")
    config = LLMConfig(model="mock-claims", cache_enabled=True, max_retries=1)
    service = ClaimExtractorService(config, provider, LLMCache(str(tmp_path / "cache")))
    claims, calls = service.extract("candidate", [_fragment()])
    assert len(claims) == 2
    assert calls[0].retry_count == 1
    assert provider.call_count == 2

    other = _fragment().model_copy(update={"text_hash": "d" * 64})
    _, other_calls = service.extract("candidate", [other])
    assert other_calls[0].cache_key != calls[0].cache_key


def test_claim_extractor_provider_error(tmp_path) -> None:
    service = ClaimExtractorService(
        LLMConfig(model="mock-claims", cache_enabled=False),
        MockLLMProvider("provider_error"),
        LLMCache(str(tmp_path / "cache")),
    )
    with pytest.raises(StructuredOutputError) as error:
        service.extract("candidate", [_fragment()])
    assert error.value.error_type == "provider_error"
