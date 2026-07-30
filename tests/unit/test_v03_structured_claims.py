import json
from pathlib import Path

import pytest

from campus_job_agent.evidence.claim_extractor import ClaimExtractorService
from campus_job_agent.llm import (
    LLMCache,
    LLMProviderError,
    MockLLMProvider,
    StructuredOutputError,
)
from campus_job_agent.schemas import EvidenceFragment, LLMConfig, LLMResponse


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


def test_claim_extractor_preserves_retryable_network_timeout(tmp_path) -> None:
    class TimeoutProvider:
        name = "timeout"

        def generate(self, request: object):
            raise LLMProviderError(
                "read timed out", error_type="network_timeout", retryable=True
            )

    service = ClaimExtractorService(
        LLMConfig(model="timeout", cache_enabled=False),
        TimeoutProvider(),
        LLMCache(str(tmp_path / "cache")),
    )
    with pytest.raises(StructuredOutputError) as error:
        service.extract("candidate", [_fragment()])
    assert error.value.error_type == "network_timeout"
    assert error.value.retryable is True


def test_claim_extractor_derives_stable_record_ids_and_normalizes_year(tmp_path) -> None:
    class ChangingRecordIdProvider:
        name = "fixture"

        def __init__(self) -> None:
            self.call_count = 0

        def generate(self, request: object) -> LLMResponse:
            self.call_count += 1
            education_id, experience_id, year = (
                ("edu1", "exp1", "2024")
                if self.call_count == 1
                else ("school1", "proj1", "2024-07")
            )
            claims = [
                (f"education:{education_id}.institution", "Example University"),
                (f"education:{education_id}.degree", "Master"),
                (f"education:{education_id}.major", "Computer Science"),
                (f"education:{education_id}.graduation_year", year),
                (f"experience:{experience_id}.kind", "project"),
                (f"experience:{experience_id}.title", "Campus Job Agent"),
                (f"experience:{experience_id}.technologies", ["Python"]),
            ]
            return LLMResponse(
                provider=self.name,
                model="changing-record-ids",
                usage=None,
                text=json.dumps(
                    {
                        "claims": [
                            {
                                "predicate": predicate,
                                "value": value,
                                "claim_type": "observed_fact",
                                "evidence_fragment_ids": ["fragment-1"],
                                "confidence": 0.9,
                            }
                            for predicate, value in claims
                        ]
                    }
                ),
            )

    provider = ChangingRecordIdProvider()
    service = ClaimExtractorService(
        LLMConfig(model="changing-record-ids", cache_enabled=False),
        provider,
        LLMCache(str(tmp_path / "cache")),
    )

    first, _ = service.extract("candidate", [_fragment()])
    second, _ = service.extract("candidate", [_fragment()])

    assert [claim.predicate for claim in first] == [
        claim.predicate for claim in second
    ]
    assert [claim.value for claim in first] == [claim.value for claim in second]
    assert next(
        claim.value
        for claim in second
        if claim.predicate.endswith(".graduation_year")
    ) == "2024"
    assert all("edu1" not in claim.predicate for claim in first)
    assert all("school1" not in claim.predicate for claim in second)
    assert all("exp1" not in claim.predicate for claim in first)
    assert all("proj1" not in claim.predicate for claim in second)


def test_claim_extractor_normalizes_common_confidence_label(tmp_path) -> None:
    class LabelConfidenceProvider:
        name = "fixture"

        def generate(self, request: object) -> LLMResponse:
            return LLMResponse(
                provider=self.name,
                model="label-confidence",
                usage=None,
                text=json.dumps(
                    {
                        "claims": [
                            {
                                "predicate": "capability:programming.python",
                                "value": {"level": "advanced"},
                                "claim_type": "observed_fact",
                                "evidence_fragment_ids": ["fragment-1"],
                                "confidence": "high",
                            }
                        ]
                    }
                ),
            )

    service = ClaimExtractorService(
        LLMConfig(model="label-confidence", cache_enabled=False),
        LabelConfidenceProvider(),
        LLMCache(str(tmp_path / "cache")),
    )

    claims, _ = service.extract("candidate", [_fragment()])

    assert claims[0].confidence == 0.9
