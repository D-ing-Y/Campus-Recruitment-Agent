"""Deterministic resume normalization, review and publication policy."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from typing import Any

from campus_job_agent.schemas import (
    LIST_SECTIONS,
    RESUME_SECTION_ORDER,
    CareerExpectationRecord,
    CustomSectionRecord,
    EducationExperienceRecord,
    EvidenceFragment,
    ExtractedCareerExpectation,
    ExtractedCustomSection,
    ExtractedEducationExperience,
    ExtractedProjectExperience,
    ExtractedWorkExperience,
    PdfExtractionDiagnostics,
    PersonalInformation,
    ProjectExperienceRecord,
    ResumeData,
    ResumeDraft,
    ResumeEvidenceSnapshot,
    ResumeExtractionBatch,
    ResumeReviewReceipt,
    ResumeReviewRequest,
    ResumeReviewResponse,
    ResumeSourceRef,
    ResumeTextBlock,
    WorkExperienceRecord,
    canonical_hash,
)
from campus_job_agent.schemas.evidence import utc_now


_RECORD_MODELS = {
    "career_expectations": CareerExpectationRecord,
    "work_experiences": WorkExperienceRecord,
    "project_experiences": ProjectExperienceRecord,
    "education_experiences": EducationExperienceRecord,
    "custom_sections": CustomSectionRecord,
}


def build_resume_draft(
    *, owner_id: str, candidate_id: str, artifact_id: str,
    personal: PersonalInformation, batch: ResumeExtractionBatch,
    fragments: list[EvidenceFragment], diagnostics: PdfExtractionDiagnostics,
    predecessor_draft_id: str | None = None,
    candidate_claim_count_at_import: int = 0,
) -> ResumeDraft:
    fragment_map = {item.fragment_id: item for item in fragments}
    sources: dict[str, list[ResumeSourceRef]] = {}
    data = ResumeData(
        personal_information=personal,
        personal_advantage=ResumeTextBlock(text=batch.personal_advantage.text),
        career_expectations=_records(
            "career_expectations", batch.career_expectations,
            CareerExpectationRecord, fragment_map, sources,
        ),
        work_experiences=_records(
            "work_experiences", batch.work_experiences,
            WorkExperienceRecord, fragment_map, sources,
        ),
        project_experiences=_records(
            "project_experiences", batch.project_experiences,
            ProjectExperienceRecord, fragment_map, sources,
        ),
        education_experiences=_records(
            "education_experiences", batch.education_experiences,
            EducationExperienceRecord, fragment_map, sources,
        ),
        professional_skills=ResumeTextBlock(text=batch.professional_skills.text),
        custom_sections=_records(
            "custom_sections", _expand_custom_sections(batch.custom_sections),
            CustomSectionRecord, fragment_map, sources,
        ),
    )
    _block_sources(
        "/personal_advantage", batch.personal_advantage.model_dump(),
        fragment_map, sources,
    )
    _block_sources(
        "/professional_skills", batch.professional_skills.model_dump(),
        fragment_map, sources,
    )
    for field, value in personal.model_dump().items():
        if value not in (None, ""):
            matches = _matching_refs(value, fragments)
            if matches:
                sources[f"/personal_information/{field}"] = matches
    sources = ensure_source_coverage(data, sources, fragments)
    return ResumeDraft(
        owner_id=owner_id, candidate_id=candidate_id, artifact_id=artifact_id,
        predecessor_draft_id=predecessor_draft_id,
        candidate_claim_count_at_import=candidate_claim_count_at_import,
        status="awaiting_review", data=data, field_sources=sources,
        extraction_diagnostics=diagnostics,
    )


def next_review_target(draft: ResumeDraft) -> tuple[str, str, str | None] | None:
    for section in RESUME_SECTION_ORDER:
        if draft.section_statuses[section] != "pending":
            continue
        if section not in LIST_SECTIONS:
            return section, "block", None
        reviewed = set(draft.reviewed_record_ids.get(section, []))
        for item in getattr(draft.data, section):
            if item.record_id not in reviewed:
                return section, "record", item.record_id
        return section, "section_complete", None
    return None


def review_target_value(draft: ResumeDraft, request: ResumeReviewRequest) -> Any:
    value = getattr(draft.data, request.section)
    if request.target_kind == "record":
        return next(
            item.model_dump(mode="json")
            for item in value
            if item.record_id == request.record_id
        )
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return [item.model_dump(mode="json") for item in value]


def apply_resume_review(
    *, draft: ResumeDraft, request: ResumeReviewRequest,
    response: ResumeReviewResponse, fragments: list[EvidenceFragment],
) -> tuple[ResumeDraft, ResumeReviewReceipt]:
    _validate_review(draft, request, response)
    if response.action == "correct":
        _assert_patch_supported_by_pdf(response.patch or {}, fragments)
    before_value = review_target_value(draft, request)
    before_hash = canonical_hash(before_value)
    data_payload = draft.data.model_dump(mode="json")
    statuses = dict(draft.section_statuses)
    reviewed = {key: list(value) for key, value in draft.reviewed_record_ids.items()}
    status = draft.status
    result_status = "reviewed"

    if response.action == "cancel":
        status = "cancelled"
        result_status = "cancelled"
    elif response.action == "retry":
        status = "extracting"
        statuses = {section: "pending" for section in RESUME_SECTION_ORDER}
        reviewed = {}
        result_status = "retried"
    elif request.target_kind == "record":
        items = list(data_payload[request.section])
        index = next(
            (i for i, item in enumerate(items) if item["record_id"] == request.record_id),
            None,
        )
        if index is None:
            raise ValueError("resume review record no longer exists")
        if response.action == "remove":
            items.pop(index)
        elif response.action == "correct":
            patched = _merge_patch(items[index], response.patch or {})
            patched["record_id"] = request.record_id
            items[index] = _RECORD_MODELS[request.section].model_validate(patched).model_dump(mode="json")
            reviewed.setdefault(request.section, []).append(str(request.record_id))
        elif response.action == "confirm":
            reviewed.setdefault(request.section, []).append(str(request.record_id))
        else:
            raise ValueError("action is not valid for a record review")
        data_payload[request.section] = items
        remaining_ids = {str(item["record_id"]) for item in items}
        reviewed_ids = set(reviewed.get(request.section, []))
        if remaining_ids <= reviewed_ids:
            statuses[request.section] = (
                "confirmed_empty"
                if not items else
                "corrected"
                if response.action in {"correct", "remove"} else
                "confirmed"
            )
    elif request.target_kind == "block":
        if response.action == "remove":
            raise ValueError("remove is only allowed for list records")
        if response.action == "correct":
            data_payload[request.section] = _merge_patch(
                data_payload[request.section], response.patch or {}
            )
            statuses[request.section] = (
                "confirmed_empty"
                if _empty(data_payload[request.section]) else "corrected"
            )
        elif response.action == "confirm":
            statuses[request.section] = (
                "confirmed_empty" if _empty(data_payload[request.section]) else "confirmed"
            )
        else:
            raise ValueError("action is not valid for a block review")
    else:
        if response.action == "remove":
            raise ValueError("remove is only allowed for list records")
        if response.action == "correct":
            patch = response.patch or {}
            if set(patch) != {"items"} or not isinstance(patch["items"], list):
                raise ValueError("section-complete correction requires {'items': [...]} patch")
            model = _RECORD_MODELS[request.section]
            items = []
            for index, item in enumerate(patch["items"]):
                payload = dict(item)
                payload.setdefault("record_id", _record_id(request.section, payload, index))
                items.append(model.model_validate(payload).model_dump(mode="json"))
            data_payload[request.section] = items
            reviewed[request.section] = [item["record_id"] for item in items]
            statuses[request.section] = "corrected" if items else "confirmed_empty"
        elif response.action == "confirm":
            statuses[request.section] = (
                "confirmed_empty" if not data_payload[request.section] else "confirmed"
            )
        else:
            raise ValueError("action is not valid for section completion")

    data = ResumeData.model_validate(data_payload)
    sources = ensure_source_coverage(
        data,
        _remap_sources(
            before=draft.data, after=data,
            existing=draft.field_sources, fragments=fragments,
        ),
        fragments,
    )
    revision = draft.revision + 1
    updated = draft.model_copy(update={
        "revision": revision, "status": status, "data": data,
        "field_sources": sources, "section_statuses": statuses,
        "reviewed_record_ids": {
            key: list(dict.fromkeys(value)) for key, value in reviewed.items()
        },
        "updated_at": utc_now(),
    })
    after_value = (
        review_target_value(updated, request)
        if response.action not in {"remove", "cancel", "retry"}
        else updated.data.model_dump(mode="json")
    )
    receipt = ResumeReviewReceipt(
        response_id=response.response_id, request_id=request.request_id,
        draft_id=draft.draft_id, user_id=response.user_id,
        section=request.section, target_kind=request.target_kind,
        record_id=request.record_id, action=response.action,
        before_hash=before_hash, after_hash=canonical_hash(after_value),
        previous_revision=draft.revision, result_revision=revision,
        result_status=result_status,
    )
    updated = updated.model_copy(update={
        "review_receipt_ids": [*updated.review_receipt_ids, receipt.receipt_id]
    })
    return updated, receipt


def publish_resume_evidence(
    draft: ResumeDraft, *, version: int
) -> ResumeEvidenceSnapshot:
    if draft.status != "awaiting_review":
        raise ValueError("only an awaiting-review draft can be finalized")
    incomplete = [
        section for section, status in draft.section_statuses.items()
        if status == "pending"
    ]
    if incomplete:
        raise ValueError("resume draft has unconfirmed sections: " + ", ".join(incomplete))
    missing = missing_source_paths(draft)
    if missing:
        raise ValueError("resume fields are missing source references: " + ", ".join(missing[:3]))
    material = {
        "draft_id": draft.draft_id, "owner_id": draft.owner_id,
        "candidate_id": draft.candidate_id, "artifact_id": draft.artifact_id,
        "version": version, "data": draft.data.model_dump(mode="json"),
        "receipt_ids": draft.review_receipt_ids,
    }
    evidence_id = "resume-evidence-" + hashlib.sha256(
        json.dumps(material, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:24]
    return ResumeEvidenceSnapshot(
        resume_evidence_id=evidence_id, draft_id=draft.draft_id,
        owner_id=draft.owner_id, candidate_id=draft.candidate_id,
        artifact_id=draft.artifact_id, version=version,
        data=draft.data, field_sources=draft.field_sources,
        review_receipt_ids=draft.review_receipt_ids,
        extraction_diagnostics=draft.extraction_diagnostics,
    )


def ensure_source_coverage(
    data: ResumeData, existing: dict[str, list[ResumeSourceRef]],
    fragments: list[EvidenceFragment],
) -> dict[str, list[ResumeSourceRef]]:
    leaves = leaf_values(data)
    valid = {path: refs for path, refs in existing.items() if path in leaves and refs}
    return valid


def missing_source_paths(draft: ResumeDraft) -> list[str]:
    return [
        path
        for path, value in leaf_values(draft.data).items()
        if value not in (None, "")
        and not any(
            ref.start_offset is not None
            and ref.end_offset is not None
            and ref.end_offset > ref.start_offset
            for ref in draft.field_sources.get(path, [])
        )
    ]


def _remap_sources(
    *, before: ResumeData, after: ResumeData,
    existing: dict[str, list[ResumeSourceRef]],
    fragments: list[EvidenceFragment],
) -> dict[str, list[ResumeSourceRef]]:
    """Keep JSON Pointer provenance aligned when list records move or change."""

    result: dict[str, list[ResumeSourceRef]] = {}
    before_payload = before.model_dump(mode="json")
    after_payload = after.model_dump(mode="json")

    for section in RESUME_SECTION_ORDER:
        if section in LIST_SECTIONS:
            old_items = before_payload[section]
            old_index = {
                str(item.get("record_id")): index
                for index, item in enumerate(old_items)
            }
            for new_index, item in enumerate(after_payload[section]):
                prior_index = old_index.get(str(item.get("record_id")))
                prior = old_items[prior_index] if prior_index is not None else {}
                for field, value in item.items():
                    if field == "record_id" or value in (None, ""):
                        continue
                    new_path = f"/{section}/{new_index}/{_escape(field)}"
                    matched = _matching_refs(value, fragments)
                    if matched:
                        result[new_path] = matched
                        continue
                    if prior_index is not None and prior.get(field) == value:
                        old_path = f"/{section}/{prior_index}/{_escape(field)}"
                        if existing.get(old_path):
                            result[new_path] = existing[old_path]
            continue

        value = after_payload[section]
        for path, leaf in _section_leaves(value, f"/{section}").items():
            if leaf in (None, ""):
                continue
            matched = _matching_refs(leaf, fragments)
            if matched:
                result[path] = matched
            elif existing.get(path):
                result[path] = existing[path]
    return result


def _section_leaves(value: Any, path: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    if isinstance(value, dict):
        for key, item in value.items():
            if key != "record_id":
                result.update(_section_leaves(item, f"{path}/{_escape(key)}"))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            result.update(_section_leaves(item, f"{path}/{index}"))
    else:
        result[path] = value
    return result


def _matching_refs(
    value: Any, fragments: list[EvidenceFragment]
) -> list[ResumeSourceRef]:
    needle = _searchable(str(value))
    if not needle:
        return []
    matches: list[ResumeSourceRef] = []
    for fragment in fragments:
        span = _normalized_span(fragment.text, needle)
        if span is not None:
            matches.append(_source_ref(fragment, *span))
    return matches


def leaf_values(data: ResumeData) -> dict[str, Any]:
    result: dict[str, Any] = {}

    def visit(value: Any, path: str) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if key == "record_id":
                    continue
                visit(item, f"{path}/{_escape(key)}")
        elif isinstance(value, list):
            for index, item in enumerate(value):
                visit(item, f"{path}/{index}")
        else:
            result[path] = value

    visit(data.model_dump(mode="json"), "")
    return result


def _records(
    section: str, extracted: list[Any], model: type[Any],
    fragment_map: dict[str, EvidenceFragment],
    sources: dict[str, list[ResumeSourceRef]],
) -> list[Any]:
    result = []
    for index, item in enumerate(extracted):
        payload = item.model_dump(mode="json", exclude={"evidence_fragment_ids"})
        payload["record_id"] = _record_id(section, payload, index)
        record = model.model_validate(payload)
        result.append(record)
        referenced_fragments = [
            fragment_map[value]
            for value in item.evidence_fragment_ids if value in fragment_map
        ]
        for field, value in record.model_dump(mode="json").items():
            if field == "record_id" or value in (None, ""):
                continue
            lookup_value = (
                record.model_dump(mode="json").get("section_title")
                if field == "section_type" else value
            )
            refs = _matching_refs(lookup_value, referenced_fragments)
            if refs:
                sources[f"/{section}/{index}/{field}"] = refs
    return result


def _expand_custom_sections(
    records: list[ExtractedCustomSection],
) -> list[ExtractedCustomSection]:
    """Split a model-grouped award/certificate bullet list into source records."""

    result: list[ExtractedCustomSection] = []
    for item in records:
        if item.name and item.content:
            compact = " ".join(
                value for value in (item.start_date, item.name) if value
            )
            if _searchable(item.content).lstrip("·•●・-") == _searchable(compact):
                item = item.model_copy(update={"content": None})
        if (
            item.section_type not in {"award", "certificate"}
            or item.name or not item.content
        ):
            result.append(item)
            continue
        expanded: list[ExtractedCustomSection] = []
        for line in item.content.splitlines():
            cleaned = re.sub(r"^[\s·•●・-]+", "", line).strip()
            match = re.fullmatch(
                r"((?:19|20)\d{2}[./-]\d{1,2})\s+(.+)", cleaned
            )
            if match is None:
                continue
            expanded.append(item.model_copy(update={
                "name": match.group(2).strip(),
                "start_date": match.group(1),
                "content": None,
            }))
        result.extend(expanded or [item])
    return result


def _block_sources(
    path: str, payload: dict[str, Any], fragment_map: dict[str, EvidenceFragment],
    sources: dict[str, list[ResumeSourceRef]],
) -> None:
    referenced_fragments = [
        fragment_map[value]
        for value in payload.get("evidence_fragment_ids", []) if value in fragment_map
    ]
    if payload.get("text"):
        refs = _matching_refs(payload["text"], referenced_fragments)
        if refs:
            sources[f"{path}/text"] = refs


def _source_ref(
    fragment: EvidenceFragment, start_offset: int, end_offset: int
) -> ResumeSourceRef:
    return ResumeSourceRef(
        artifact_id=fragment.artifact_id, fragment_id=fragment.fragment_id,
        page_number=int(fragment.locator.get("page") or 1),
        text_hash=fragment.text_hash,
        start_offset=start_offset,
        end_offset=end_offset,
    )


def _normalized_span(text: str, needle: str) -> tuple[int, int] | None:
    normalized: list[str] = []
    offsets: list[int] = []
    for offset, character in enumerate(text):
        for folded in _folded_characters(character):
            normalized.append(folded)
            offsets.append(offset)
    index = "".join(normalized).find(needle)
    if index < 0:
        return None
    return offsets[index], offsets[index + len(needle) - 1] + 1


def _record_id(section: str, payload: dict[str, Any], index: int) -> str:
    identity = {key: value for key, value in payload.items() if value not in (None, "", [])}
    canonical = json.dumps(identity, ensure_ascii=False, sort_keys=True)
    digest = hashlib.sha256(f"{section}:{index}:{canonical}".encode("utf-8")).hexdigest()[:12]
    return f"{section.rstrip('s')}-{digest}"


def _validate_review(
    draft: ResumeDraft, request: ResumeReviewRequest, response: ResumeReviewResponse
) -> None:
    if request.draft_id != draft.draft_id or request.draft_revision != draft.revision:
        raise ValueError("stale_resume_draft: review revision mismatch")
    if response.request_id != request.request_id or response.thread_id != request.thread_id:
        raise ValueError("resume review request or thread mismatch")
    if response.user_id != request.user_id or response.user_id != draft.owner_id:
        raise ValueError("resume review identity mismatch")
    if response.action not in request.allowed_actions:
        raise ValueError("resume review action is not allowed")


def _merge_patch(target: Any, patch: Any) -> Any:
    if not isinstance(patch, dict):
        return patch
    result = dict(target) if isinstance(target, dict) else {}
    for key, value in patch.items():
        if key in {"record_id", "draft_id", "owner_id", "candidate_id", "artifact_id"}:
            raise ValueError(f"resume correction cannot change identity field: {key}")
        if value is None:
            result[key] = None
        elif isinstance(value, dict):
            result[key] = _merge_patch(result.get(key), value)
        else:
            result[key] = value
    return result


def _assert_patch_supported_by_pdf(
    patch: dict[str, Any], fragments: list[EvidenceFragment]
) -> None:
    """Reject corrected facts that cannot be transcribed from the archived PDF."""

    corpus = _searchable("\n".join(item.text for item in fragments))

    def visit(value: Any, key: str | None = None) -> None:
        if isinstance(value, dict):
            for child_key, child in value.items():
                visit(child, child_key)
            return
        if isinstance(value, list):
            for child in value:
                visit(child, key)
            return
        if value in (None, "") or key in {"record_id", "section_type"}:
            return
        candidate = _searchable(str(value))
        if candidate and candidate not in corpus:
            raise ValueError(
                f"resume correction is not supported by PDF text: {key or 'value'}"
            )

    visit(patch)


def _searchable(value: str) -> str:
    return "".join(
        folded
        for character in value
        for folded in _folded_characters(character)
    )


def _folded_characters(character: str) -> list[str]:
    return [
        value
        for value in unicodedata.normalize("NFKC", character.casefold())
        if not value.isspace() and unicodedata.category(value) != "Cf"
    ]


def _empty(value: Any) -> bool:
    if isinstance(value, dict):
        return all(_empty(item) for key, item in value.items() if key != "record_id")
    if isinstance(value, list):
        return not value
    return value in (None, "")


def _escape(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


__all__ = [
    "apply_resume_review", "build_resume_draft", "ensure_source_coverage",
    "leaf_values", "missing_source_paths", "next_review_target", "publish_resume_evidence",
    "review_target_value",
]
