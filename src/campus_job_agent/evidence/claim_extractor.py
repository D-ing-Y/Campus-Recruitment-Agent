"""Convert evidence-bound structured model output into runtime claims."""

from campus_job_agent.llm import LLMCache, LLMProvider, parse_structured_output
from campus_job_agent.prompts import (
    CLAIM_PROMPT_NAME,
    CLAIM_PROMPT_VERSION,
    CLAIM_SCHEMA_VERSION,
    build_claim_extractor_messages,
    build_claim_retry_messages,
)
from campus_job_agent.schemas import (
    ClaimExtractionBatch,
    ClaimExtractor,
    EvidenceClaim,
    EvidenceFragment,
    LLMCallRecord,
    LLMConfig,
)


class ClaimExtractorService:
    def __init__(
        self, config: LLMConfig, provider: LLMProvider, cache: LLMCache
    ) -> None:
        self.config = config
        self.provider = provider
        self.cache = cache

    def extract(
        self, subject_id: str, fragments: list[EvidenceFragment]
    ) -> tuple[list[EvidenceClaim], list[LLMCallRecord]]:
        def retry(previous: str, error: str) -> list[dict[str, str]]:
            return build_claim_retry_messages(
                fragments, subject_id, previous, error
            )

        batch, records = parse_structured_output(
            messages=build_claim_extractor_messages(fragments, subject_id),
            output_model=ClaimExtractionBatch,
            config=self.config,
            provider=self.provider,
            cache=self.cache,
            prompt_name=CLAIM_PROMPT_NAME,
            prompt_version=CLAIM_PROMPT_VERSION,
            schema_version=CLAIM_SCHEMA_VERSION,
            retry_builder=retry,
        )
        extractor = ClaimExtractor(provider=self.provider.name, model=self.config.model)
        claims = [
            EvidenceClaim(
                subject_id=subject_id,
                predicate=item.predicate,
                value=item.value,
                claim_type=item.claim_type,
                evidence_fragment_ids=item.evidence_fragment_ids,
                confidence=item.confidence,
                extractor=extractor,
                prompt_version=CLAIM_PROMPT_VERSION,
            )
            for item in batch.claims
        ]
        return claims, records
