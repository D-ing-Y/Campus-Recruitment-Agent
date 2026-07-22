"""Deterministic raw replay, normalization, deduplication and official verification."""

from __future__ import annotations

import hashlib
import html
import json
import re
from collections import defaultdict
from datetime import UTC, datetime
from difflib import SequenceMatcher
from html.parser import HTMLParser
from typing import Any, Callable
from urllib.parse import urljoin, urlparse
from uuid import NAMESPACE_URL, uuid5

from campus_job_agent.schemas import (
    DocumentExtraction,
    EvidenceFragment,
    ExperienceEvidenceRecord,
    ExtractionUnit,
    JobIdentityLink,
    JobPostingCluster,
    NormalizedJobPosting,
    OfficialSiteAdapterSpec,
    OfficialVerificationPlan,
    SearchScope,
    SourceDocument,
)
from campus_job_agent.schemas.source import canonical_hash, normalize_text
from campus_job_agent.storage.base import BlobStore, EvidenceRepository


WEB_PARSER_VERSION = "web_document_v1"


def extract_archived_document(
    document: SourceDocument,
    *,
    blob_store: BlobStore,
    repository: EvidenceRepository,
) -> tuple[DocumentExtraction, list[EvidenceFragment]]:
    """Replay an archived response. Parsing is impossible without its Artifact."""

    if document.access_status != "success" or not document.raw_artifact_id:
        raise ValueError("raw-before-parse: source document is not an archived success")
    artifact = repository.get_artifact(document.raw_artifact_id)
    if artifact is None or not blob_store.exists(artifact.raw_uri):
        raise ValueError("raw-before-parse: archived artifact or blob is missing")
    existing = repository.get_extraction(artifact.artifact_id)
    if existing is not None:
        return existing, repository.list_fragments(artifact.artifact_id)
    raw = blob_store.get(artifact.raw_uri)
    content_type = document.content_type.casefold()
    if "json" in content_type:
        data = json.loads(raw.decode("utf-8"))
        text = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True)
        locator_type = "jsonpath_and_char_range"
        units = [ExtractionUnit(index=1, start=0, end=len(text), locator={"jsonpath": "$"})]
    else:
        decoded = raw.decode("utf-8", errors="replace")
        # Preserve HTML so JSON-LD and selectors remain replayable. The
        # deterministic fallback strips markup only when it needs plain text.
        text = decoded
        locator_type = "css_selector_and_char_range"
        units = [ExtractionUnit(index=1, start=0, end=len(text), locator={"selector": "body"})]
    text_hash = hashlib.sha256(text.encode()).hexdigest()
    text_uri = blob_store.put(f"derived/{artifact.artifact_id}/{WEB_PARSER_VERSION}.txt", text.encode())
    extraction = repository.save_extraction(DocumentExtraction(
        artifact_id=artifact.artifact_id, parser_name="json_document" if "json" in content_type else "deterministic_html_text",
        parser_version=WEB_PARSER_VERSION, text_uri=text_uri, text_hash=text_hash,
        locator_type=locator_type, units=units,
    ))
    fragment = EvidenceFragment(
        fragment_id=str(uuid5(NAMESPACE_URL, f"{artifact.artifact_id}:{WEB_PARSER_VERSION}:0:{len(text)}")),
        artifact_id=artifact.artifact_id, locator_type=locator_type,
        locator={**units[0].locator, "start": 0, "end": len(text)}, text=text,
        text_hash=text_hash,
        metadata={"source_id": document.source_id, "channel": document.channel, "query_id": document.query_id,
                  "source_url": document.source_url, "parser_version": WEB_PARSER_VERSION},
    )
    repository.save_fragment(fragment)
    return extraction, [fragment]


def normalize_job_document(
    document: SourceDocument,
    fragments: list[EvidenceFragment],
    scope: SearchScope,
) -> list[NormalizedJobPosting]:
    if document.source_id == "zhaopin_jobs":
        payload = _zhaopin_jobs_payload(fragments[0].text, document)
    else:
        payload = _json_from_fragments(fragments)
    records = payload.get("jobs") if isinstance(payload, dict) else None
    if records is None:
        records = [payload]
    results: list[NormalizedJobPosting] = []
    for index, raw in enumerate(records or []):
        if not isinstance(raw, dict):
            continue
        source_url = str(raw.get("source_url") or document.source_url)
        job = NormalizedJobPosting(
            job_posting_id=str(uuid5(NAMESPACE_URL, f"normalized-job:{document.source_id}:{source_url}:{document.content_hash}:{index}")),
            job_id=_optional_string(raw.get("job_id")), company=str(raw.get("company") or "unknown"),
            company_type=str(raw.get("company_type") or "unknown"), role_title=str(raw.get("role_title") or raw.get("title") or "unknown"),
            role_family=str(raw.get("role_family") or scope.target_role_family), city=str(raw.get("city") or "unknown"),
            work_location_detail=_optional_string(raw.get("work_location_detail")), salary_min=_optional_number(raw.get("salary_min")),
            salary_max=_optional_number(raw.get("salary_max")), salary_unit=_optional_string(raw.get("salary_unit")),
            salary_source=str(raw.get("salary_source") or ("official" if document.channel == "employer_official" else "third_party_only" if raw.get("salary_min") is not None else "unknown")),
            job_description=str(raw.get("job_description") or raw.get("description") or ""),
            requirements_raw=str(raw.get("requirements_raw") or raw.get("requirements") or ""),
            requirements_normalized=[str(value) for value in raw.get("requirements_normalized", [])],
            degree_requirement=_optional_string(raw.get("degree_requirement")), major_requirement=_optional_string(raw.get("major_requirement")),
            graduation_year=str(raw.get("graduation_year") or "unknown"), recruitment_type=str(raw.get("recruitment_type") or "unknown"),
            application_deadline=_optional_datetime(raw.get("application_deadline")), application_url=_optional_string(raw.get("application_url")),
            source_url=source_url, source_id=document.source_id,
            source_type="employer_official" if document.channel == "employer_official" else "recruitment_platform",
            source_date=_optional_datetime(raw.get("source_date")), retrieved_at=document.retrieved_at,
            confidence=float(raw.get("confidence", 0.85)), raw_artifact_ids=[str(document.raw_artifact_id)],
            supporting_fragment_ids=[item.fragment_id for item in fragments], notes=[str(value) for value in raw.get("notes", [])],
        )
        job = apply_hard_scope(job, scope)
        results.append(job)
    return results


def normalize_experience_document(
    document: SourceDocument,
    fragments: list[EvidenceFragment],
    role_family: str,
) -> list[ExperienceEvidenceRecord]:
    try:
        payload = _json_from_fragments(fragments)
    except ValueError:
        if document.source_id != "nowcoder_experience":
            raise
        payload = {"experiences": _nowcoder_experiences_from_html(fragments[0].text)}
    records = payload.get("experiences") if isinstance(payload, dict) else None
    if records is None:
        records = [payload]
    results: list[ExperienceEvidenceRecord] = []
    for index, raw in enumerate(records or []):
        if not isinstance(raw, dict):
            continue
        signals = raw.get("signals", {}) if isinstance(raw.get("signals", {}), dict) else {}
        quote_texts = [str(value) for values in signals.values() if isinstance(values, list) for value in values]
        fragment_id = fragments[0].fragment_id
        results.append(ExperienceEvidenceRecord(
            experience_record_id=str(uuid5(NAMESPACE_URL, f"experience:{document.source_id}:{document.source_url}:{document.content_hash}:{index}")),
            platform=str(raw.get("platform") or document.source_id), query_id=document.query_id,
            content_type=str(raw.get("content_type") or "interview_post"), source_url=str(raw.get("source_url") or document.source_url),
            title=str(raw.get("title") or "unknown"), author_ref=str(raw.get("author_ref") or "anonymous"),
            published_at=_optional_datetime(raw.get("published_at")), retrieved_at=document.retrieved_at,
            company=_optional_string(raw.get("company")), role_title=_optional_string(raw.get("role_title")),
            role_family=_optional_string(raw.get("role_family")) or role_family, city=_optional_string(raw.get("city")),
            stage=_optional_string(raw.get("stage")), scope_level=str(raw.get("scope_level") or "unknown"),
            signals=signals, summary=str(raw.get("summary") or ""),
            evidence_quotes=[{"text": value, "fragment_id": fragment_id} for value in quote_texts],
            confidence=float(raw.get("confidence", 0.7)), tags=[str(value) for value in raw.get("tags", [])],
            raw_artifact_id=str(document.raw_artifact_id), supporting_fragment_ids=[item.fragment_id for item in fragments],
            notes=[str(value) for value in raw.get("notes", [])],
        ))
    return results


def apply_hard_scope(job: NormalizedJobPosting, scope: SearchScope) -> NormalizedJobPosting:
    reasons: list[str] = []
    if scope.locations and job.city != "unknown" and normalize_text(job.city) not in {normalize_text(value) for value in scope.locations}:
        reasons.append("location_mismatch")
    if scope.graduation_year != "unknown" and job.graduation_year != "unknown" and job.graduation_year != scope.graduation_year:
        reasons.append("graduation_year_mismatch")
    if scope.recruitment_type != "unknown" and job.recruitment_type != "unknown" and job.recruitment_type != scope.recruitment_type:
        reasons.append("recruitment_type_mismatch")
    if scope.companies and normalize_text(job.company) not in {normalize_text(value) for value in scope.companies}:
        reasons.append("company_mismatch")
    if not reasons:
        if job.application_deadline and job.application_deadline < datetime.now(UTC):
            return job.model_copy(update={"status": "expired", "notes": [*job.notes, "application_deadline_passed"]})
        return job
    return job.model_copy(update={
        "status": "excluded_hard_scope", "exclusion_code": reasons[0],
        "exclusion_evidence_fragment_ids": list(job.supporting_fragment_ids),
        "notes": [*job.notes, *reasons],
    })


def deduplicate_jobs(jobs: list[NormalizedJobPosting]) -> tuple[list[JobPostingCluster], list[tuple[str, str, float]]]:
    groups: dict[str, list[NormalizedJobPosting]] = defaultdict(list)
    unkeyed: list[NormalizedJobPosting] = []
    for job in jobs:
        key = job.exact_identity_key()
        if key:
            groups[key].append(job)
        else:
            unkeyed.append(job)
    clusters: list[JobPostingCluster] = []
    for key, members in sorted(groups.items()):
        canonical = sorted(members, key=lambda item: (item.source_type != "employer_official", item.job_posting_id))[0]
        clusters.append(JobPostingCluster(
            cluster_id=str(uuid5(NAMESPACE_URL, f"job-cluster:{key}")), canonical_job_posting_id=canonical.job_posting_id,
            member_job_posting_ids=[item.job_posting_id for item in members], exact_key=key,
            merge_method="exact_normalized_key" if len(members) > 1 else "not_merged", confidence=1.0,
            source_ids=sorted({item.source_id for item in members}),
        ))
    for job in unkeyed:
        key = canonical_hash("unmerged-job", job.job_posting_id)
        clusters.append(JobPostingCluster(
            cluster_id=str(uuid5(NAMESPACE_URL, f"job-cluster:{key}")), canonical_job_posting_id=job.job_posting_id,
            member_job_posting_ids=[job.job_posting_id], merge_method="not_merged", confidence=1.0,
            source_ids=[job.source_id],
        ))
    fuzzy: list[tuple[str, str, float]] = []
    for left_index, left in enumerate(jobs):
        for right in jobs[left_index + 1:]:
            if left.exact_identity_key() == right.exact_identity_key() and left.exact_identity_key():
                continue
            score = _fuzzy_job_score(left, right)
            if score >= 0.72:
                fuzzy.append((left.job_posting_id, right.job_posting_id, round(score, 4)))
    return clusters, fuzzy


def deduplicate_experience(records: list[ExperienceEvidenceRecord]) -> list[ExperienceEvidenceRecord]:
    seen: set[str] = set()
    result: list[ExperienceEvidenceRecord] = []
    for record in records:
        key = canonical_hash("experience-dedup", [record.source_url, _canonical_signal_text(record)])
        if key in seen:
            continue
        seen.add(key)
        result.append(record)
    return result


def plan_official_verification(
    cluster: JobPostingCluster,
    jobs_by_id: dict[str, NormalizedJobPosting],
    *,
    company_domains: dict[str, list[str]] | None = None,
) -> OfficialVerificationPlan:
    job = jobs_by_id[cluster.canonical_job_posting_id]
    domains = list((company_domains or {}).get(normalize_text(job.company), []))
    entry_urls: list[str] = []
    if job.application_url:
        parsed = urlparse(job.application_url)
        if parsed.hostname and job.source_type == "employer_official":
            domains.append(parsed.hostname)
            entry_urls.append(job.application_url)
    domains = sorted(set(domains))
    return OfficialVerificationPlan(
        verification_plan_id=str(uuid5(NAMESPACE_URL, f"official-plan:{cluster.cluster_id}")),
        job_cluster_id=cluster.cluster_id, canonical_company=job.company,
        candidate_role_title=job.role_title, candidate_location=job.city,
        candidate_recruitment_cycle=f"{job.graduation_year}_{job.recruitment_type}",
        candidate_application_ids=[value for value in [job.job_id] if value],
        official_domain_candidates=domains, official_entry_url_candidates=entry_urls,
        allowed_domains=domains,
    )


def parse_official_document(
    document: SourceDocument,
    fragments: list[EvidenceFragment],
    scope: SearchScope,
    *,
    registered_adapter: Callable[[str], list[dict[str, Any]]] | None = None,
    llm_extractor: Callable[[str], list[dict[str, Any]]] | None = None,
) -> tuple[list[NormalizedJobPosting], str, OfficialSiteAdapterSpec | None]:
    """Fixed chain: JSON-LD, registered adapter, deterministic text, LLM JSON, spec."""

    text = "\n".join(item.text for item in fragments)
    candidates = _json_ld_jobs(text)
    method = "json_ld"
    if not candidates:
        try:
            structured = json.loads(text)
        except json.JSONDecodeError:
            structured = None
        if isinstance(structured, dict) and (structured.get("role_title") or structured.get("title")) and structured.get("company"):
            candidates = [structured]
            method = "registered_adapter"
        elif isinstance(structured, dict) and isinstance(structured.get("jobs"), list):
            candidates = [item for item in structured["jobs"] if isinstance(item, dict)]
            method = "registered_adapter"
    if not candidates and registered_adapter is not None:
        candidates = registered_adapter(text)
        method = "registered_adapter"
    if not candidates:
        candidates = _deterministic_official_text(text)
        method = "deterministic_text"
    if not candidates and llm_extractor is not None:
        candidates = llm_extractor(text)
        method = "llm_strict_json"
    if candidates:
        wrapper = {"jobs": candidates}
        synthetic = EvidenceFragment(
            fragment_id=fragments[0].fragment_id, artifact_id=fragments[0].artifact_id,
            locator_type=fragments[0].locator_type, locator=fragments[0].locator,
            text=json.dumps(wrapper, ensure_ascii=False), text_hash=fragments[0].text_hash,
            metadata=fragments[0].metadata,
        )
        jobs = normalize_job_document(document, [synthetic], scope)
        return jobs, method, None
    domain = urlparse(document.source_url).hostname
    spec = None
    if domain:
        spec = OfficialSiteAdapterSpec(allowed_domains=[domain], entry_url_patterns=[document.source_url], status="candidate")
    return [], "adapter_required", spec


def link_job_identity(
    cluster: JobPostingCluster,
    discovery_job: NormalizedJobPosting,
    official_jobs: list[NormalizedJobPosting],
    *,
    verification_status: str | None = None,
) -> JobIdentityLink:
    if not official_jobs:
        status = verification_status if verification_status in {"official_not_found", "official_unavailable"} else "official_unavailable" if verification_status in {"adapter_required", "source_changed"} else "official_not_found"
        return JobIdentityLink(
            job_identity_link_id=str(uuid5(NAMESPACE_URL, f"identity:{cluster.cluster_id}:{status}")),
            job_cluster_id=cluster.cluster_id, status=status, match_confidence=0.0,
            match_signals={"verification_status": verification_status or status},
        )
    best = max(official_jobs, key=lambda item: _fuzzy_job_score(discovery_job, item))
    signals = {
        "company": "exact" if normalize_text(discovery_job.company) == normalize_text(best.company) else "different",
        "role_title": _match_label(discovery_job.role_title, best.role_title),
        "location": "exact" if normalize_text(discovery_job.city) == normalize_text(best.city) else "unknown" if "unknown" in {discovery_job.city, best.city} else "different",
        "recruitment_cycle": _recruitment_cycle_match(discovery_job, best),
        "application_id": "exact" if discovery_job.job_id and discovery_job.job_id == best.job_id else "unknown",
        "responsibility_signature": _responsibility_match_label(
            discovery_job.job_description + discovery_job.requirements_raw,
            best.job_description + best.requirements_raw,
        ),
    }
    score = _fuzzy_job_score(discovery_job, best)
    strong = sum(value in {"exact", "strong"} for value in signals.values())
    status = "confirmed" if strong >= 4 and signals["company"] == "exact" else "identity_ambiguous" if score >= 0.55 else "rejected"
    return JobIdentityLink(
        job_identity_link_id=str(uuid5(NAMESPACE_URL, f"identity:{cluster.cluster_id}:{best.job_posting_id}")),
        job_cluster_id=cluster.cluster_id, official_job_posting_id=best.job_posting_id,
        status=status, match_confidence=round(score, 4), match_signals=signals,
        supporting_fragment_ids=sorted(set(discovery_job.supporting_fragment_ids + best.supporting_fragment_ids)),
    )


def validate_official_redirect(source_url: str, redirect_url: str, plan: OfficialVerificationPlan) -> None:
    source = urlparse(source_url)
    target = urlparse(redirect_url)
    if target.scheme != "https" or target.hostname not in set(plan.allowed_domains):
        raise ValueError("official redirect leaves the approved domain allowlist")
    if source.hostname not in set(plan.allowed_domains):
        raise ValueError("official source URL is outside the approved domain allowlist")


def _recruitment_cycle_match(left: NormalizedJobPosting, right: NormalizedJobPosting) -> str:
    values = (left.graduation_year, left.recruitment_type, right.graduation_year, right.recruitment_type)
    if "unknown" in values:
        return "unknown"
    return "exact" if values[:2] == values[2:] else "different"


def _json_from_fragments(fragments: list[EvidenceFragment]) -> Any:
    if not fragments:
        raise ValueError("normalizer requires archived fragments")
    for fragment in fragments:
        try:
            return json.loads(fragment.text)
        except json.JSONDecodeError:
            continue
    raise ValueError("strict JSON normalization input is invalid")


def _zhaopin_jobs_payload(text: str, document: SourceDocument) -> dict[str, Any]:
    """Parse archived Zhaopin HTML after raw evidence has been persisted."""
    jobs: list[dict[str, Any]] = []
    for raw in _json_ld_jobs(text):
        application_url = _canonical_zhaopin_job_url(str(raw.get("application_url") or ""))
        if application_url is None:
            continue
        salary_min, salary_max, salary_unit = _zhaopin_salary(raw.get("salary_source"))
        jobs.append({
            **raw,
            "job_id": raw.get("job_id") or _zhaopin_job_id(str(application_url)),
            "salary_min": salary_min,
            "salary_max": salary_max,
            "salary_unit": salary_unit,
            "salary_source": "third_party_only" if salary_min is not None else "unknown",
            "source_url": str(application_url),
            "application_url": str(application_url),
            "confidence": 0.85,
            "notes": ["zhaopin_json_ld_search_result"],
        })

    card_pattern = re.compile(
        r'<div[^>]+class=["\'][^"\']*(?:joblist-box__item|job-card|positionlist|jobinfo)[^"\']*["\'][^>]*>.*?</div>\s*</div>',
        re.I | re.S,
    )
    for index, card in enumerate(card_pattern.findall(text)[:100]):
        detail_url = _first_html_match([r'<a[^>]+href=["\']([^"\']+)["\']'], card)
        title = _first_html_match([
            r'class=["\'][^"\']*jobinfo__top[^"\']*["\'][^>]*>.*?<a[^>]*>(.*?)</a>',
            r'class=["\'][^"\']*(?:job-title|position-name|iteminfo__line1__jobname)[^"\']*["\'][^>]*>(.*?)</',
            r'<a[^>]+href=["\'][^"\']+["\'][^>]*>(.*?)</a>',
        ], card)
        company = _first_html_match([
            r'class=["\'][^"\']*companyinfo__top[^"\']*["\'][^>]*>.*?<a[^>]*>(.*?)</a>',
            r'class=["\'][^"\']*(?:company-name|companyinfo)[^"\']*["\'][^>]*>(.*?)</',
        ], card)
        salary = _first_html_match([r'class=["\'][^"\']*(?:salary|job-salary)[^"\']*["\'][^>]*>(.*?)</'], card)
        location = _first_html_match([r'class=["\'][^"\']*(?:job-area|location|iteminfo__line2__jobdesc)[^"\']*["\'][^>]*>(.*?)</'], card)
        tags = [
            _clean_html_text(value, limit=100)
            for value in re.findall(
                r'class=["\'][^"\']*jobinfo__tag[^"\']*["\'][^>]*>(.*?)</',
                card, re.I | re.S,
            )
        ]
        tags = [value for value in tags if value]
        if not title or not detail_url:
            continue
        application_url = _canonical_zhaopin_job_url(urljoin(document.source_url, detail_url))
        if application_url is None:
            continue
        salary_min, salary_max, salary_unit = _zhaopin_salary(salary)
        jobs.append({
            "job_id": _zhaopin_job_id(application_url),
            "company": company or "unknown",
            "role_title": title,
            "city": re.split(r"[-·]", location, maxsplit=1)[0].strip() if location else "unknown",
            "work_location_detail": location or None,
            "salary_min": salary_min,
            "salary_max": salary_max,
            "salary_unit": salary_unit,
            "salary_source": "third_party_only" if salary_min is not None else "unknown",
            # Search cards may contain recruiter names. Keep only job fields and tags
            # in normalized data; the complete card remains available in raw evidence.
            "job_description": " | ".join(value for value in (title, salary, location) if value),
            "requirements_raw": "；".join(tags),
            "requirements_normalized": tags,
            "source_url": application_url,
            "application_url": application_url,
            "confidence": 0.75 if company else 0.6,
            "notes": ["zhaopin_search_card_without_detail"],
        })

    # The public search response embeds the complete server-side result set in
    # ``__INITIAL_STATE__``.  Prefer it over the visible cards: it contains the
    # canonical company, city and job description while still being part of the
    # archived response (raw-before-parse remains intact).
    jobs.extend(_zhaopin_initial_state_jobs(text))
    unique = {str(job.get("application_url")): job for job in jobs if job.get("application_url")}
    return {"jobs": list(unique.values())}


def _zhaopin_initial_state_jobs(text: str) -> list[dict[str, Any]]:
    marker = "__INITIAL_STATE__="
    start = text.find(marker)
    if start < 0:
        return []
    try:
        state, _ = json.JSONDecoder().raw_decode(text[start + len(marker):])
    except json.JSONDecodeError:
        return []
    records = state.get("positionList", []) if isinstance(state, dict) else []
    results: list[dict[str, Any]] = []
    for record in records[:100] if isinstance(records, list) else []:
        if not isinstance(record, dict):
            continue
        application_url = _canonical_zhaopin_job_url(
            str(record.get("positionUrl") or record.get("positionURL") or "")
        )
        if application_url is None:
            continue
        detail = record.get("jobDetailData")
        detail = detail if isinstance(detail, dict) else {}
        position = detail.get("position")
        position = position if isinstance(position, dict) else {}
        desc = position.get("desc")
        desc = desc if isinstance(desc, dict) else {}
        base = position.get("base")
        base = base if isinstance(base, dict) else {}
        location = position.get("workLocation")
        location = location if isinstance(location, dict) else {}
        description = _clean_html_text(desc.get("description"), limit=12000)
        salary_min, salary_max, salary_unit = _zhaopin_salary(
            record.get("salary60") or record.get("salaryReal") or base.get("salary")
        )
        tags = [
            _clean_html_text(value, limit=100)
            for value in record.get("showSkillTags", [])
            if isinstance(value, str)
        ]
        results.append({
            "job_id": str(record.get("number") or base.get("positionNumber") or _zhaopin_job_id(application_url)),
            "company": _clean_html_text(record.get("companyName") or "unknown", limit=300),
            "company_type": _clean_html_text(record.get("propertyName") or record.get("property") or "unknown", limit=100),
            "role_title": _clean_html_text(record.get("name") or base.get("positionName") or "unknown", limit=500),
            "city": _clean_html_text(record.get("workCity") or "unknown", limit=100),
            "work_location_detail": _clean_html_text(
                location.get("workAddress") or location.get("address") or record.get("cityDistrict"), limit=1000
            ) or None,
            "salary_min": salary_min,
            "salary_max": salary_max,
            "salary_unit": salary_unit,
            "salary_source": "third_party_only" if salary_min is not None else "unknown",
            "job_description": description,
            "requirements_raw": description,
            "requirements_normalized": [value for value in tags if value],
            "degree_requirement": _clean_html_text(record.get("education"), limit=100) or None,
            "source_date": record.get("publishTime") or None,
            "source_url": application_url,
            "application_url": application_url,
            "confidence": 0.9,
            "notes": ["zhaopin_embedded_initial_state_v1"],
        })
    return results


def _first_html_match(patterns: list[str], text: str) -> str:
    for pattern in patterns:
        match = re.search(pattern, text, re.I | re.S)
        if match:
            return _clean_html_text(match.group(1), limit=1000)
    return ""


def _canonical_zhaopin_job_url(value: str) -> str | None:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or parsed.hostname not in {"www.zhaopin.com", "jobs.zhaopin.com"}:
        return None
    path = parsed.path.casefold()
    if "/jobdetail/" not in path and not (parsed.hostname == "jobs.zhaopin.com" and path.endswith(".htm")):
        return None
    return parsed._replace(scheme="https").geturl()


def _zhaopin_job_id(value: str) -> str:
    match = re.search(r"/jobdetail/([^/?#]+)", value, re.I)
    return match.group(1) if match else hashlib.sha256(value.encode()).hexdigest()[:20]


def _zhaopin_salary(value: Any) -> tuple[float | None, float | None, str | None]:
    text = str(value or "").replace(",", "")
    match = re.search(r"(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)\s*[Kk]", text)
    if match:
        return float(match.group(1)), float(match.group(2)), "K/month"
    match = re.search(r"(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)\s*万", text)
    if match:
        return float(match.group(1)) * 10, float(match.group(2)) * 10, "K/month"
    match = re.search(r"(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)\s*元\s*/?\s*天", text)
    if match:
        return float(match.group(1)), float(match.group(2)), "CNY/day"
    match = re.search(r"(\d{4,})\s*-\s*(\d{4,})\s*元", text)
    if match:
        return float(match.group(1)) / 1000, float(match.group(2)) / 1000, "K/month"
    return None, None, None


def _nowcoder_experiences_from_html(text: str) -> list[dict[str, Any]]:
    marker = "window.__INITIAL_STATE__="
    start = text.find(marker)
    if start < 0:
        return []
    try:
        state, _ = json.JSONDecoder().raw_decode(text[start + len(marker):])
    except json.JSONDecodeError:
        return []
    app = state.get("app", {}) if isinstance(state, dict) else {}
    search = app.get("180", {}) if isinstance(app, dict) else {}
    records = search.get("records", []) if isinstance(search, dict) else []
    results: list[dict[str, Any]] = []
    for record in records[:50] if isinstance(records, list) else []:
        if not isinstance(record, dict):
            continue
        data = record.get("data", {})
        moment = data.get("momentData", {}) if isinstance(data, dict) else {}
        if not isinstance(moment, dict):
            continue
        identifier = moment.get("uuid") or moment.get("id") or data.get("contentId")
        title = _clean_html_text(
            moment.get("newTitle") or moment.get("title") or record.get("title") or "unknown",
            limit=300,
        )
        content = _clean_html_text(
            moment.get("newContent") or moment.get("content") or moment.get("desc") or "",
            limit=4000,
        )
        if not identifier or (title == "unknown" and not content):
            continue
        combined = f"{title}\n{content}"
        company = next((name for name in ("京东", "腾讯", "阿里", "字节跳动", "美团", "百度", "华为") if name in combined), None)
        tech_stack = [
            value for value in ("Python", "Java", "C++", "MySQL", "Redis", "Kafka", "RAG", "Agent", "LangGraph")
            if value.casefold() in combined.casefold()
        ]
        interview = [content[:1200]] if content and any(value in combined for value in ("面经", "面试", "一面", "二面", "三面")) else []
        project = [content[:1200]] if content and "项目" in content else []
        stage = next((value for value in ("一面", "二面", "三面", "HR面", "笔试") if value.casefold() in combined.casefold()), None)
        results.append({
            "platform": "nowcoder",
            "content_type": "discussion_post",
            "source_url": f"https://www.nowcoder.com/feed/main/detail/{identifier}",
            "title": title,
            "author_ref": "anonymous",
            "company": company,
            "role_title": title if "agent" in title.casefold() else None,
            "stage": stage,
            "scope_level": "company_role" if company and "agent" in combined.casefold() else "unknown",
            "signals": {"interview": interview, "tech_stack": tech_stack, "project_preference": project},
            "summary": content[:600],
            "confidence": 0.75 if content else 0.5,
            "tags": tech_stack,
            "notes": ["deterministic_nowcoder_embedded_state_v1", "author_minimized"],
        })
    return results


def _clean_html_text(value: Any, *, limit: int) -> str:
    parser = _TextHTMLParser()
    parser.feed(html.unescape(str(value or "")))
    return re.sub(r"\s+", " ", parser.text()).strip()[:limit]


def _salary_range(value: Any) -> tuple[float | None, float | None]:
    match = re.search(r"(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)\s*[Kk]", str(value or ""))
    return (float(match.group(1)), float(match.group(2))) if match else (None, None)


def _json_ld_jobs(text: str) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for raw in re.findall(r"<script[^>]+type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script>", text, flags=re.I | re.S):
        try:
            value = json.loads(html.unescape(raw))
        except json.JSONDecodeError:
            continue
        values = value if isinstance(value, list) else [value]
        for item in values:
            if not isinstance(item, dict) or item.get("@type") != "JobPosting":
                continue
            location = item.get("jobLocation", {})
            if isinstance(location, list):
                location = location[0] if location else {}
            address = location.get("address", {}) if isinstance(location, dict) else {}
            org = item.get("hiringOrganization", {})
            candidates.append({
                "job_id": item.get("identifier", {}).get("value") if isinstance(item.get("identifier"), dict) else item.get("identifier"),
                "company": org.get("name", "unknown") if isinstance(org, dict) else "unknown",
                "role_title": item.get("title", "unknown"), "city": address.get("addressLocality", "unknown") if isinstance(address, dict) else "unknown",
                "job_description": item.get("description", ""), "requirements_raw": item.get("qualifications", ""),
                "application_deadline": item.get("validThrough"), "application_url": item.get("url"),
            })
    return candidates


def _deterministic_official_text(text: str) -> list[dict[str, Any]]:
    parser = _TextHTMLParser()
    parser.feed(text)
    text = parser.text()
    title = re.search(r"(?:职位|岗位|title)[:：]\s*([^\n]{2,80})", text, re.I)
    company = re.search(r"(?:公司|company)[:：]\s*([^\n]{2,80})", text, re.I)
    if not title or not company:
        return []
    city = re.search(r"(?:城市|地点|location)[:：]\s*([^\n]{1,40})", text, re.I)
    return [{"role_title": title.group(1).strip(), "company": company.group(1).strip(), "city": city.group(1).strip() if city else "unknown", "job_description": text}]


def _fuzzy_job_score(left: NormalizedJobPosting, right: NormalizedJobPosting) -> float:
    company = SequenceMatcher(None, normalize_text(left.company), normalize_text(right.company)).ratio()
    title = SequenceMatcher(None, normalize_text(left.role_title), normalize_text(right.role_title)).ratio()
    location = 1.0 if normalize_text(left.city) == normalize_text(right.city) else 0.4 if "unknown" in {left.city, right.city} else 0.0
    cycle = 1.0 if _recruitment_cycle_match(left, right) == "exact" else 0.2
    content = _responsibility_similarity(
        left.job_description + left.requirements_raw,
        right.job_description + right.requirements_raw,
    )
    return 0.25 * company + 0.25 * title + 0.15 * location + 0.15 * cycle + 0.20 * content


def _match_label(left: str, right: str) -> str:
    score = SequenceMatcher(None, normalize_text(left), normalize_text(right)).ratio()
    return "exact" if score == 1 else "strong" if score >= 0.75 else "weak" if score >= 0.5 else "different"


def _responsibility_match_label(left: str, right: str) -> str:
    score = _responsibility_similarity(left, right)
    return "exact" if score == 1 and normalize_text(left) == normalize_text(right) else "strong" if score >= 0.8 else "weak" if score >= 0.5 else "different"


def _responsibility_similarity(left: str, right: str) -> float:
    normalized_left, normalized_right = normalize_text(left), normalize_text(right)
    if not normalized_left or not normalized_right:
        return 0.0
    matcher = SequenceMatcher(None, normalized_left, normalized_right, autojunk=False)
    ratio = matcher.ratio()
    shorter_length = min(len(normalized_left), len(normalized_right))
    # A long official duties section may be embedded verbatim in a third-party
    # page that also adds an introduction and benefits. Matching-block coverage
    # recognizes that structure without treating short generic phrases as proof.
    if shorter_length < 80:
        return ratio
    coverage = sum(block.size for block in matcher.get_matching_blocks()) / shorter_length
    return max(ratio, coverage)


def _canonical_signal_text(record: ExperienceEvidenceRecord) -> str:
    return normalize_text(json.dumps(record.signals.model_dump(mode="json"), ensure_ascii=False, sort_keys=True))


def _optional_string(value: Any) -> str | None:
    if value is None or str(value).strip() in {"", "unknown", "null"}:
        return None
    return str(value)


def _optional_number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _optional_datetime(value: Any) -> datetime | None:
    if value in {None, "", "unknown"}:
        return None
    if isinstance(value, datetime):
        return value
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    except ValueError:
        return None


class _TextHTMLParser(HTMLParser):
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
            self.parts.append(data.strip())

    def text(self) -> str:
        return "\n".join(line.strip() for line in " ".join(self.parts).splitlines() if line.strip())
