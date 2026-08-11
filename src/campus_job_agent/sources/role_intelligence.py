"""Deterministic WP3.1 grouping, community extraction validation and scope policy."""

from __future__ import annotations

import hashlib
import html
import json
import re
from collections import defaultdict
from html.parser import HTMLParser
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from campus_job_agent.llm import LLMCache, LLMProvider, parse_structured_output
from campus_job_agent.prompts.role_intelligence import (
    COMMUNITY_EVIDENCE_PROMPT_VERSION,
    COMMUNITY_EVIDENCE_SYSTEM,
)
from campus_job_agent.schemas import (
    CommunityDocumentClassificationReceipt,
    CommunityEvidenceDocument,
    CommunityEvidenceSegment,
    CommunityExtractionBatch,
    CommunityPostCandidate,
    CommunitySearchPlan,
    CommunitySearchQuery,
    CompanyRoleGroup,
    EvidenceFragment,
    JobPostingCluster,
    LLMConfig,
    NormalizedJobPosting,
    SearchScope,
    SourceDocument,
)
from campus_job_agent.schemas.role_intelligence import (
    INTERVIEW_SEGMENT_TYPES,
    REPUTATION_SEGMENT_TYPES,
    quote_hash,
    stable_role_id,
)
from campus_job_agent.schemas.source import normalize_text
from campus_job_agent.storage.base import EvidenceRepository


ROLE_FAMILY_DISPLAY_NAMES: dict[str, str] = {
    "ai_agent_engineering": "AI Agent 开发",
    "backend_engineering": "后端开发",
    "frontend_engineering": "前端开发",
    "algorithm_engineering": "算法工程",
    "machine_learning_engineering": "机器学习工程",
    "data_engineering": "数据开发",
    "data_analysis": "数据分析",
    "software_engineering": "软件开发",
    "test_engineering": "测试开发",
    "product_management": "产品经理",
}

COMMUNITY_SOURCE_CASCADES: dict[str, tuple[str, str]] = {
    "interview_experience": ("nowcoder_experience", "xiaohongshu_experience"),
    "employment_experience": ("xiaohongshu_experience", "nowcoder_experience"),
}


def role_family_display_name(role_family_id: str) -> str:
    """Return a user-facing search phrase, never an internal snake-case ID."""

    known = ROLE_FAMILY_DISPLAY_NAMES.get(role_family_id)
    if known:
        return known
    tokens = [item for item in re.split(r"[_\-\s]+", role_family_id) if item]
    if not tokens:
        return "目标岗位"
    return " ".join(item.upper() if len(item) <= 3 else item.title() for item in tokens)


def build_community_search_query(
    group: CompanyRoleGroup, *, evidence_purpose: str, source_id: str,
    round_index: int, source_priority: int, detail_budget: int = 3,
    parent_query_id: str | None = None,
) -> CommunitySearchQuery:
    if evidence_purpose not in COMMUNITY_SOURCE_CASCADES:
        raise ValueError("unsupported community evidence purpose")
    if round_index not in {1, 2, 3}:
        raise ValueError("community search round must be 1..3")
    expected_source = COMMUNITY_SOURCE_CASCADES[evidence_purpose][source_priority - 1]
    if source_id != expected_source and not source_id.startswith("fixture"):
        raise ValueError("community source does not match deterministic cascade")
    suffix = "面经" if evidence_purpose == "interview_experience" else "工作体验"
    exact = group.exact_role_terms[0] if group.exact_role_terms else role_family_display_name(group.role_family_id)
    level = {1: "exact_role", 2: "role_family", 3: "company_only"}[round_index]
    middle = {1: exact, 2: role_family_display_name(group.role_family_id), 3: ""}[round_index]
    text = " ".join(item for item in (group.company_display_name, middle, suffix) if item)
    # query_kind remains a legacy compatibility label. New routing reads the
    # explicit purpose and relaxation_level fields instead.
    kind = (
        "company_reputation"
        if evidence_purpose == "employment_experience"
        else {
            "exact_role": "company_exact_role",
            "role_family": "company_role_family",
            "company_only": "generic_family_interview",
        }[level]
    )
    intended = [evidence_purpose, "mixed"]
    query_id = stable_role_id(
        "community-query",
        [group.group_id, evidence_purpose, source_id, source_priority, round_index, text],
    )
    return CommunitySearchQuery(
        query_id=query_id, query_kind=kind, query_text=text,
        intended_document_types=intended, source_ids=[source_id], source_id=source_id,
        evidence_purpose=evidence_purpose, round_index=round_index,
        relaxation_level=level, parent_query_id=parent_query_id,
        source_priority=source_priority, search_budget=1, detail_budget=detail_budget,
        expansion_reason=f"bounded_{evidence_purpose}_{level}",
    )


class CommunityEvidenceExtractor:
    def __init__(
        self, config: LLMConfig, provider: LLMProvider, cache: LLMCache,
    ) -> None:
        self.config, self.provider, self.cache = config, provider, cache

    def extract(
        self, *, text: str, company: str | None, role_family: str | None,
        intended_document_types: list[str],
    ) -> tuple[CommunityExtractionBatch, list[Any]]:
        fixture = _fixture_extraction(text)
        if fixture is not None:
            return fixture, []
        payload = {
            "scope_hints": {
                "company": company,
                "role_family": role_family,
                "intended_document_types": intended_document_types,
            },
            "POST_TEXT": text[:30000],
        }
        messages = [
            {"role": "system", "content": COMMUNITY_EVIDENCE_SYSTEM},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ]

        def retry(previous: str, error: str) -> list[dict[str, str]]:
            return [messages[0], {"role": "user", "content": (
                messages[1]["content"]
                + f"\nPrevious output failed validation: {error}. Return the complete schema again."
            )}]

        return parse_structured_output(
            messages=messages, output_model=CommunityExtractionBatch,
            config=self.config, provider=self.provider, cache=self.cache,
            prompt_name="community_evidence",
            prompt_version=COMMUNITY_EVIDENCE_PROMPT_VERSION,
            schema_version="v0.7.1", retry_builder=retry,
        )


def build_company_role_groups(
    scope: SearchScope,
    clusters: list[JobPostingCluster],
    jobs_by_id: dict[str, NormalizedJobPosting],
) -> list[CompanyRoleGroup]:
    grouped: dict[tuple[str, str], list[tuple[JobPostingCluster, NormalizedJobPosting]]] = defaultdict(list)
    for cluster in clusters:
        job = jobs_by_id.get(cluster.canonical_job_posting_id)
        if job is None or job.role_family != scope.target_role_family:
            continue
        company_key = normalize_text(job.company)
        if not company_key or company_key == "unknown":
            continue
        grouped[(company_key, job.role_family)].append((cluster, job))
    results: list[CompanyRoleGroup] = []
    for (company_key, family), values in sorted(grouped.items()):
        display = sorted({job.company for _, job in values}, key=lambda item: (len(item), item))[0]
        payload = [scope.scope_id, company_key, family, sorted(cluster.cluster_id for cluster, _ in values)]
        results.append(CompanyRoleGroup(
            group_id=stable_role_id("company-role-group", payload),
            search_scope_id=scope.scope_id, company_key=company_key,
            company_display_name=display,
            company_aliases=sorted({job.company for _, job in values}), role_family_id=family,
            job_instance_ids=sorted(cluster.cluster_id for cluster, _ in values),
            exact_role_terms=sorted({job.role_title for _, job in values}),
        ))
    return results


def build_community_search_plan(
    group: CompanyRoleGroup, *, source_id: str = "nowcoder_experience",
    detail_budget: int = 3,
) -> CommunitySearchPlan:
    # Compatibility helper for WP3.1 callers: create the first bounded attempt
    # for each purpose. WP3.1.1 Graph calls build_community_search_query one
    # attempt at a time and evaluates coverage before planning another round.
    queries = []
    for purpose in ("interview_experience", "employment_experience"):
        cascade = COMMUNITY_SOURCE_CASCADES[purpose]
        priority = cascade.index(source_id) + 1 if source_id in cascade else 1
        selected_source = cascade[priority - 1]
        queries.append(build_community_search_query(
            group, evidence_purpose=purpose, source_id=selected_source,
            round_index=1, source_priority=priority, detail_budget=detail_budget,
        ))
    return CommunitySearchPlan(
        plan_id=stable_role_id("community-plan", [group.group_id, [item.query_id for item in queries]]),
        company_role_group_id=group.group_id, queries=queries,
    )


def ensure_community_body_fragment(
    document: SourceDocument,
    source_fragment: EvidenceFragment,
    repository: EvidenceRepository,
) -> EvidenceFragment:
    body = _visible_text(source_fragment.text)
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
    fragment = EvidenceFragment(
        fragment_id=str(uuid5(NAMESPACE_URL, f"community-body:{document.raw_artifact_id}:{digest}")),
        artifact_id=source_fragment.artifact_id, locator_type="css_selector_and_char_range",
        locator={"selector": "body", "start": 0, "end": len(body)}, text=body,
        text_hash=digest,
        metadata={
            "source_id": document.source_id, "source_document_id": document.source_document_id,
            "document_kind": document.document_kind, "parser_version": "community_body_v1",
        },
    )
    return repository.save_fragment(fragment)


def materialize_community_evidence(
    *,
    document: SourceDocument,
    body_fragment: EvidenceFragment,
    extraction: CommunityExtractionBatch,
    repository: EvidenceRepository,
    group: CompanyRoleGroup | None,
    provider: str,
    model: str,
) -> tuple[
    CommunityEvidenceDocument,
    CommunityDocumentClassificationReceipt,
    list[CommunityEvidenceSegment],
]:
    if document.document_kind != "experience_post" or not document.raw_artifact_id:
        raise ValueError("community detail evidence requires an archived experience_post")
    document_id = stable_role_id(
        "community-document", [document.source_document_id, document.content_hash]
    )
    provisional: list[CommunityEvidenceSegment] = []
    rejected_count = 0
    for index, candidate in enumerate(extraction.segments):
        occurrences = [match.start() for match in re.finditer(re.escape(candidate.quote), body_fragment.text)]
        if len(occurrences) != 1:
            rejected_count += 1
            continue
        start = occurrences[0]
        end = start + len(candidate.quote)
        fragment_id = str(uuid5(
            NAMESPACE_URL,
            f"community-quote:{body_fragment.fragment_id}:{start}:{end}:{quote_hash(candidate.quote)}",
        ))
        quote_fragment = repository.save_fragment(EvidenceFragment(
            fragment_id=fragment_id, artifact_id=body_fragment.artifact_id,
            locator_type="css_selector_and_char_range",
            locator={"selector": "body", "start": start, "end": end},
            text=candidate.quote, text_hash=quote_hash(candidate.quote),
            metadata={
                "source_document_id": document.source_document_id,
                "parent_fragment_id": body_fragment.fragment_id,
                "segment_index": index,
            },
        ))
        usage, status, reasons, company_key, family, job_id = _resolve_segment_usage(
            candidate=candidate, group=group, document_text=body_fragment.text,
        )
        provisional.append(CommunityEvidenceSegment(
            segment_id=stable_role_id(
                "community-segment", [document_id, fragment_id, candidate.segment_type, usage]
            ),
            document_id=document_id, fragment_id=quote_fragment.fragment_id,
            quote_start=start, quote_end=end, quote_hash=quote_fragment.text_hash,
            segment_type=candidate.segment_type, usage=usage,
            company_key=company_key, role_family_id=family, job_instance_id=job_id,
            polarity=candidate.polarity, limited_summary=candidate.limited_summary,
            scope_confidence=candidate.confidence if status == "accepted" else 0.0,
            classification_confidence=candidate.confidence,
            validation_status=status, reason_codes=reasons,
        ))
    accepted_types = {
        "interview" if item.segment_type in INTERVIEW_SEGMENT_TYPES else "reputation"
        for item in provisional if item.validation_status == "accepted"
    }
    reason_codes: list[str] = []
    if extraction.document_type == "mixed" and accepted_types != {"interview", "reputation"}:
        reason_codes.append("community_segment_mixed_requires_split")
        provisional = [
            item.model_copy(update={
                "usage": "excluded", "validation_status": "rejected",
                "reason_codes": [*item.reason_codes, "mixed_document_not_independently_split"],
            })
            for item in provisional
        ]
    accepted = [item for item in provisional if item.validation_status == "accepted"]
    rejected_count += len(provisional) - len(accepted)
    actual_types = {
        "interview" if item.segment_type in INTERVIEW_SEGMENT_TYPES else "reputation"
        for item in accepted
    }
    actual_document_type = (
        "mixed" if actual_types == {"interview", "reputation"}
        else "interview_experience" if actual_types == {"interview"}
        else "employment_experience" if actual_types == {"reputation"}
        else "unknown"
    )
    if actual_document_type != extraction.document_type:
        reason_codes.append("document_type_normalized_from_validated_segments")
    receipt_id = stable_role_id(
        "community-classification", [document.source_document_id, extraction.model_dump(mode="json")]
    )
    evidence_document = CommunityEvidenceDocument(
        document_id=document_id, artifact_id=str(document.raw_artifact_id),
        source_document_id=document.source_document_id, source_id=document.source_id,
        detail_url=document.source_url, retrieved_at=document.retrieved_at,
        published_at=document.published_at, document_type=actual_document_type,
        company_key=next((item.company_key for item in accepted if item.company_key), None),
        role_family_id=next((item.role_family_id for item in accepted if item.role_family_id), None),
        job_instance_id=next((item.job_instance_id for item in accepted if item.job_instance_id), None),
        classification_receipt_id=receipt_id,
    )
    receipt = CommunityDocumentClassificationReceipt(
        receipt_id=receipt_id, source_document_id=document.source_document_id,
        artifact_id=str(document.raw_artifact_id), document_type=actual_document_type,
        accepted_segment_ids=[item.segment_id for item in accepted],
        rejected_segment_count=rejected_count,
        reason_codes=reason_codes or (["segments_validated"] if accepted else ["no_projectable_segments"]),
        provider=provider, model=model, prompt_version=COMMUNITY_EVIDENCE_PROMPT_VERSION,
    )
    return evidence_document, receipt, provisional


def _resolve_segment_usage(
    *, candidate: Any, group: CompanyRoleGroup | None, document_text: str,
):
    if candidate.segment_type == "unknown":
        return "excluded", "rejected", ["community_segment_unknown"], None, None, None
    if group is None:
        return "excluded", "ambiguous", ["community_scope_ambiguous"], None, None, None
    if candidate.company and normalize_text(candidate.company) != group.company_key:
        return "excluded", "ambiguous", ["community_company_scope_mismatch"], None, None, None
    normalized_document = normalize_text(document_text)
    company_terms = {
        normalize_text(item)
        for item in [group.company_display_name, *group.company_aliases]
        if item
    }
    if not any(term and term in normalized_document for term in company_terms):
        return "excluded", "ambiguous", ["community_company_scope_unconfirmed"], None, None, None
    exact_role_terms = {
        normalize_text(item) for item in group.exact_role_terms if item
    }
    exact_role_confirmed = any(
        term and term in normalized_document for term in exact_role_terms
    )
    family_display = normalize_text(role_family_display_name(group.role_family_id))
    family_tokens = {
        item for item in re.split(r"[^0-9a-z\u4e00-\u9fff+#]+", family_display)
        if len(item) >= 2 and item not in {"开发", "工程", "岗位", "应用"}
    }
    family_confirmed = bool(
        family_display and family_display in normalized_document
    ) or any(token in normalized_document for token in family_tokens)
    if candidate.role_title:
        suggested_role = normalize_text(candidate.role_title)
        allowed_roles = exact_role_terms | ({family_display} if family_display else set())
        if suggested_role not in allowed_roles:
            return "excluded", "ambiguous", ["community_role_scope_mismatch"], group.company_key, None, None
    if candidate.segment_type in INTERVIEW_SEGMENT_TYPES:
        if candidate.scope_level == "job_instance" and exact_role_confirmed and len(group.job_instance_ids) == 1:
            return "demand_assessment", "accepted", ["exact_role_scope_confirmed"], group.company_key, group.role_family_id, group.job_instance_ids[0]
        if candidate.scope_level in {"company_role", "role_family"} and (
            exact_role_confirmed or family_confirmed
        ):
            return "demand_assessment", "accepted", ["role_family_scope_confirmed"], group.company_key, group.role_family_id, None
        return "excluded", "ambiguous", ["community_role_scope_unconfirmed"], group.company_key, None, None
    if candidate.segment_type in REPUTATION_SEGMENT_TYPES:
        if candidate.scope_level == "company_only":
            return "reputation_company", "accepted", ["company_reputation_scope_confirmed"], group.company_key, None, None
        if candidate.scope_level == "job_instance" and exact_role_confirmed and len(group.job_instance_ids) == 1:
            return "reputation_job", "accepted", ["exact_role_scope_confirmed"], group.company_key, group.role_family_id, group.job_instance_ids[0]
        if candidate.scope_level in {"company_role", "role_family"} and (
            exact_role_confirmed or family_confirmed
        ):
            return "reputation_job", "accepted", ["role_family_scope_confirmed"], group.company_key, group.role_family_id, None
        return "excluded", "ambiguous", ["community_scope_ambiguous"], group.company_key, None, None
    return "excluded", "rejected", ["evidence_usage_violation"], None, None, None


def _fixture_extraction(text: str) -> CommunityExtractionBatch | None:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    raw = payload.get("community_extraction")
    if raw is None and "segments" in payload and "document_type" in payload:
        raw = payload
    return CommunityExtractionBatch.model_validate(raw) if isinstance(raw, dict) else None


class _VisibleTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript"}:
            self.skip += 1
        if tag in {"p", "div", "li", "br", "h1", "h2", "h3", "tr"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript"} and self.skip:
            self.skip -= 1

    def handle_data(self, data: str) -> None:
        if not self.skip and data.strip():
            self.parts.append(data)


def _visible_text(value: str) -> str:
    if value.lstrip().startswith(("{", "[")):
        try:
            payload = json.loads(value)
            if isinstance(payload, dict):
                explicit = payload.get("body_text") or payload.get("raw_text") or payload.get("body")
                if explicit:
                    title = str(payload.get("title") or "").strip()
                    return "\n".join(item for item in (title, str(explicit)) if item)
        except json.JSONDecodeError:
            pass
    parser = _VisibleTextParser()
    parser.feed(html.unescape(value))
    return re.sub(r"[ \t]+", " ", "".join(parser.parts)).strip()


__all__ = [
    "CommunityEvidenceExtractor", "build_company_role_groups",
    "COMMUNITY_SOURCE_CASCADES", "ROLE_FAMILY_DISPLAY_NAMES",
    "build_community_search_plan", "build_community_search_query",
    "ensure_community_body_fragment", "role_family_display_name",
    "materialize_community_evidence",
]
