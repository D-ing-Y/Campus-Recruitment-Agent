"""Convert evidence-bound structured model output into runtime claims."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections import defaultdict
from typing import Any

from campus_job_agent.evidence.candidate_predicates import (
    CandidatePredicate,
    CandidatePredicateError,
    parse_candidate_predicate,
)
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
    ExtractedClaim,
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
        self,
        subject_id: str,
        fragments: list[EvidenceFragment],
        *,
        max_attempts: int | None = None,
    ) -> tuple[list[EvidenceClaim], list[LLMCallRecord]]:
        def retry(previous: str, error: str) -> list[dict[str, str]]:
            return build_claim_retry_messages(
                fragments, subject_id, previous, error
            )

        config = self.config
        if max_attempts is not None:
            config = self.config.model_copy(
                update={
                    "max_retries": min(
                        self.config.max_retries, max(0, max_attempts - 1)
                    )
                }
            )
        batch, records = parse_structured_output(
            messages=build_claim_extractor_messages(fragments, subject_id),
            output_model=ClaimExtractionBatch,
            config=config,
            provider=self.provider,
            cache=self.cache,
            prompt_name=CLAIM_PROMPT_NAME,
            prompt_version=CLAIM_PROMPT_VERSION,
            schema_version=CLAIM_SCHEMA_VERSION,
            retry_builder=retry,
        )
        normalized_claims = _normalize_candidate_records(batch.claims)
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
                schema_version=CLAIM_SCHEMA_VERSION,
            )
            for item in normalized_claims
        ]
        return claims, records


def _normalize_candidate_records(claims: list[ExtractedClaim]) -> list[ExtractedClaim]:
    """Replace model-local record labels with deterministic persistence IDs."""

    grouped: dict[tuple[str, str], dict[str, Any]] = defaultdict(dict)
    parsed_claims: list[tuple[ExtractedClaim, CandidatePredicate | None]] = []
    for claim in claims:
        try:
            parsed = parse_candidate_predicate(claim.predicate)
        except CandidatePredicateError:
            parsed = None
        value = _normalize_candidate_value(parsed, claim.value)
        normalized = claim.model_copy(update={"value": value})
        parsed_claims.append((normalized, parsed))
        if (
            parsed is not None
            and parsed.kind in {"education", "experience"}
            and parsed.record_id is not None
            and parsed.field is not None
        ):
            grouped[(parsed.kind, parsed.record_id)][parsed.field] = value

    stable_ids = {
        key: _stable_record_id(key[0], fields)
        for key, fields in grouped.items()
    }
    result: list[ExtractedClaim] = []
    for claim, parsed in parsed_claims:
        if (
            parsed is None
            or parsed.kind not in {"education", "experience"}
            or parsed.record_id is None
            or parsed.field is None
        ):
            result.append(claim)
            continue
        record_id = stable_ids[(parsed.kind, parsed.record_id)]
        result.append(
            claim.model_copy(
                update={"predicate": f"{parsed.kind}:{record_id}.{parsed.field}"}
            )
        )
    return result


def _normalize_candidate_value(
    parsed: CandidatePredicate | None, value: Any
) -> Any:
    if (
        parsed is not None
        and parsed.kind == "education"
        and parsed.field == "graduation_year"
        and isinstance(value, str)
    ):
        match = re.search(r"(?<!\d)(?:19|20)\d{2}(?!\d)", value)
        if match:
            return match.group(0)
    return value


def _stable_record_id(kind: str, fields: dict[str, Any]) -> str:
    if kind == "education":
        identity = {
            field: fields[field]
            for field in ("institution", "degree", "major")
            if field in fields
        }
        prefix = "edu"
    else:
        identity = {"title": fields["title"]} if "title" in fields else {}
        prefix = "exp"
    material = identity or fields
    canonical = json.dumps(
        _canonical_identity(material),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]
    return f"{prefix}-{digest}"


def _canonical_identity(value: Any) -> Any:
    if isinstance(value, str):
        normalized = unicodedata.normalize("NFKC", value)
        return " ".join(normalized.casefold().split())
    if isinstance(value, dict):
        return {
            str(key): _canonical_identity(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, list):
        normalized = [_canonical_identity(item) for item in value]
        return sorted(normalized, key=lambda item: json.dumps(item, ensure_ascii=False))
    return value
