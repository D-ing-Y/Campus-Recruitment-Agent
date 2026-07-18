"""Real local tools used by the v0.4 candidate-profile workflow."""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from pypdf import PdfReader

from campus_job_agent.evidence import ArtifactIngestor, ClaimValidator
from campus_job_agent.evidence.claim_extractor import ClaimExtractorService
from campus_job_agent.evidence.projector import CandidateProfileProjector
from campus_job_agent.schemas import (
    ClaimExtractor,
    DocumentExtraction,
    EvidenceArtifact,
    EvidenceClaim,
    EvidenceFragment,
    ExtractionUnit,
    HumanInteractionRequest,
    HumanInteractionResponse,
    ProfileVersionDiff,
    ToolResult,
)
from campus_job_agent.storage.base import BlobStore, EvidenceRepository, ProfileRepository
from campus_job_agent.tools.registry import ToolRegistry


PARSER_VERSION = "v0.4.0"
SUPPORTED_PLAIN_SUFFIXES = {".md", ".markdown", ".txt"}


def _success(
    name: str,
    *,
    records: list[dict[str, Any]] | None = None,
    evidence_ids: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> ToolResult:
    values = {
        "error_type": None,
        "retryable": False,
        "needs_user_action": False,
    }
    values.update(metadata or {})
    return ToolResult(
        tool_name=name,
        status="success",
        records=records or [],
        evidence_ids=evidence_ids or [],
        metadata=values,
    )


def _failure(
    name: str,
    error: str,
    error_type: str,
    *,
    retryable: bool = False,
    needs_user_action: bool = False,
    evidence_ids: list[str] | None = None,
) -> ToolResult:
    return ToolResult(
        tool_name=name,
        status="failed",
        records=[],
        evidence_ids=evidence_ids or [],
        error=error,
        metadata={
            "error_type": error_type,
            "retryable": retryable,
            "needs_user_action": needs_user_action,
        },
    )


class CandidateIngestMaterialTool:
    name = "candidate.ingest_material"

    def __init__(self, blob_store: BlobStore, repository: EvidenceRepository) -> None:
        self.ingestor = ArtifactIngestor(blob_store, repository)

    def run(self, args: dict[str, Any]) -> ToolResult:
        path = Path(str(args.get("path", "")))
        owner_id = str(args.get("owner_id", "")).strip()
        if not owner_id or not path.is_file():
            return _failure(
                self.name,
                "owner_id and an existing file path are required",
                "validation_error",
            )
        if not _is_supported_path(path):
            return _failure(
                self.name,
                "supported inputs are text PDF, Markdown, TXT and README files",
                "unsupported_input",
                needs_user_action=True,
            )
        try:
            result = self.ingestor.ingest_file(
                path,
                owner_id=owner_id,
                extract_text=False,
                parser_version=PARSER_VERSION,
            )
        except PermissionError as exc:
            return _failure(self.name, str(exc), "permission_denied")
        except (OSError, UnicodeError) as exc:
            return _failure(
                self.name, str(exc), "storage_error", retryable=isinstance(exc, OSError)
            )
        artifact = result.artifact
        return _success(
            self.name,
            records=[
                {
                    "artifact_id": artifact.artifact_id,
                    "original_name": artifact.original_name,
                    "content_type": artifact.content_type,
                    "deduplicated": result.deduplicated,
                }
            ],
            evidence_ids=[artifact.artifact_id],
            metadata={
                "idempotency_key": artifact.content_hash,
                "deduplicated": result.deduplicated,
                "content_hash": artifact.content_hash,
            },
        )


class ExtractPlainTextTool:
    name = "evidence.extract_plain_text"

    def __init__(self, blob_store: BlobStore, repository: EvidenceRepository) -> None:
        self.blob_store = blob_store
        self.repository = repository

    def run(self, args: dict[str, Any]) -> ToolResult:
        artifact = self.repository.get_artifact(str(args.get("artifact_id", "")))
        owner_id = str(args.get("owner_id", ""))
        if artifact is None:
            return _failure(self.name, "artifact not found", "validation_error")
        if owner_id and artifact.owner_id != owner_id:
            return _failure(self.name, "artifact owner mismatch", "permission_denied")
        if not _is_plain_name(artifact.original_name):
            return _failure(
                self.name,
                "artifact is not Markdown, TXT or README",
                "unsupported_input",
                needs_user_action=True,
                evidence_ids=[artifact.artifact_id],
            )
        existing = self.repository.get_extraction(artifact.artifact_id)
        if existing is not None:
            return _extraction_result(self.name, existing, deduplicated=True)
        try:
            text = self.blob_store.get(artifact.raw_uri).decode("utf-8-sig")
            if not text.strip():
                return _failure(
                    self.name,
                    "plain-text input is empty",
                    "unsupported_input",
                    needs_user_action=True,
                    evidence_ids=[artifact.artifact_id],
                )
            extraction = _save_plain_extraction(
                artifact, text, self.blob_store, self.repository
            )
            return _extraction_result(self.name, extraction, deduplicated=False)
        except UnicodeDecodeError as exc:
            return _failure(
                self.name,
                f"input is not valid UTF-8 text: {exc}",
                "unsupported_input",
                needs_user_action=True,
                evidence_ids=[artifact.artifact_id],
            )
        except OSError as exc:
            return _failure(
                self.name,
                str(exc),
                "storage_error",
                retryable=True,
                evidence_ids=[artifact.artifact_id],
            )


class ExtractPdfTextTool:
    name = "evidence.extract_pdf_text"

    def __init__(self, blob_store: BlobStore, repository: EvidenceRepository) -> None:
        self.blob_store = blob_store
        self.repository = repository

    def run(self, args: dict[str, Any]) -> ToolResult:
        artifact = self.repository.get_artifact(str(args.get("artifact_id", "")))
        owner_id = str(args.get("owner_id", ""))
        if artifact is None:
            return _failure(self.name, "artifact not found", "validation_error")
        if owner_id and artifact.owner_id != owner_id:
            return _failure(self.name, "artifact owner mismatch", "permission_denied")
        if Path(artifact.original_name).suffix.lower() != ".pdf":
            return _failure(
                self.name,
                "artifact is not a PDF",
                "unsupported_input",
                needs_user_action=True,
                evidence_ids=[artifact.artifact_id],
            )
        existing = self.repository.get_extraction(artifact.artifact_id)
        if existing is not None:
            return _extraction_result(self.name, existing, deduplicated=True)
        try:
            reader = PdfReader(io.BytesIO(self.blob_store.get(artifact.raw_uri)))
            pages = [(page.extract_text() or "").replace("\x00", "") for page in reader.pages]
        except Exception as exc:
            return _failure(
                self.name,
                f"PDF parser could not read this document: {exc}",
                "unsupported_input",
                needs_user_action=True,
                evidence_ids=[artifact.artifact_id],
            )
        if not pages or not any(page.strip() for page in pages):
            return _failure(
                self.name,
                "PDF has no extractable text layer; OCR is outside v0.4",
                "unsupported_input",
                needs_user_action=True,
                evidence_ids=[artifact.artifact_id],
            )
        try:
            extraction = _save_pdf_extraction(
                artifact, pages, self.blob_store, self.repository
            )
            return _extraction_result(self.name, extraction, deduplicated=False)
        except OSError as exc:
            return _failure(
                self.name,
                str(exc),
                "storage_error",
                retryable=True,
                evidence_ids=[artifact.artifact_id],
            )


class CreateFragmentsTool:
    name = "evidence.create_fragments"

    def __init__(
        self,
        blob_store: BlobStore,
        repository: EvidenceRepository,
        *,
        max_chars: int = 1200,
    ) -> None:
        self.blob_store = blob_store
        self.repository = repository
        self.max_chars = max_chars

    def run(self, args: dict[str, Any]) -> ToolResult:
        artifact_id = str(args.get("artifact_id", ""))
        artifact = self.repository.get_artifact(artifact_id)
        extraction = self.repository.get_extraction(artifact_id)
        owner_id = str(args.get("owner_id", ""))
        if artifact is None or extraction is None:
            return _failure(
                self.name, "artifact must have a persisted extraction", "validation_error"
            )
        if owner_id and artifact.owner_id != owner_id:
            return _failure(self.name, "artifact owner mismatch", "permission_denied")
        try:
            text = self.blob_store.get(extraction.text_uri).decode("utf-8")
            fragments = _fragments_from_extraction(
                artifact, extraction, text, self.max_chars
            )
            saved = [self.repository.save_fragment(item) for item in fragments]
        except OSError as exc:
            return _failure(self.name, str(exc), "storage_error", retryable=True)
        return _success(
            self.name,
            records=[
                {
                    "artifact_id": artifact_id,
                    "fragment_count": len(saved),
                    "locator_type": extraction.locator_type,
                }
            ],
            evidence_ids=[item.fragment_id for item in saved],
            metadata={
                "parser_name": extraction.parser_name,
                "parser_version": extraction.parser_version,
                "record_count": len(saved),
            },
        )


class ExtractCandidateClaimsTool:
    name = "evidence.extract_candidate_claims"

    def __init__(
        self,
        repository: EvidenceRepository,
        extractor: ClaimExtractorService,
    ) -> None:
        self.repository = repository
        self.extractor = extractor
        self.validator = ClaimValidator(repository)

    def run(self, args: dict[str, Any]) -> ToolResult:
        subject_id = str(args.get("subject_id", "")).strip()
        owner_id = str(args.get("owner_id", "")).strip()
        fragment_ids = [str(value) for value in args.get("fragment_ids", [])]
        if not subject_id or not owner_id or not fragment_ids:
            return _failure(
                self.name,
                "subject_id, owner_id and fragment_ids are required",
                "validation_error",
            )
        fragments: list[EvidenceFragment] = []
        allowed_artifacts: set[str] = set()
        for fragment_id in fragment_ids:
            fragment = self.repository.get_fragment(fragment_id)
            if fragment is None:
                return _failure(
                    self.name, f"unknown fragment: {fragment_id}", "validation_error"
                )
            artifact = self.repository.get_artifact(fragment.artifact_id)
            if artifact is None or artifact.owner_id != owner_id:
                return _failure(
                    self.name, "fragment owner mismatch", "permission_denied"
                )
            fragments.append(fragment)
            allowed_artifacts.add(fragment.artifact_id)
        try:
            claims, calls = self.extractor.extract(
                subject_id,
                fragments,
                max_attempts=(
                    int(args["remaining_llm_calls"])
                    if "remaining_llm_calls" in args
                    else None
                ),
            )
            saved = [
                self.validator.validate_and_save(
                    claim,
                    allowed_artifacts,
                    expected_owner_id=owner_id,
                )
                for claim in claims
            ]
        except Exception as exc:
            error_type = (
                "llm_output_error"
                if exc.__class__.__name__ == "StructuredOutputError"
                else "validation_error"
            )
            calls = [
                item.model_dump(mode="json")
                for item in getattr(exc, "call_records", [])
            ]
            failed = _failure(self.name, str(exc), error_type)
            if calls:
                failed.records = [{"claim_ids": [], "llm_calls": calls}]
            return failed
        return _success(
            self.name,
            records=[
                {
                    "claim_ids": [item.claim_id for item in saved],
                    "llm_calls": [item.model_dump(mode="json") for item in calls],
                }
            ],
            evidence_ids=[item.claim_id for item in saved],
            metadata={"record_count": len(saved)},
        )


class ArchiveUserResponseTool:
    name = "evidence.archive_user_response"

    def __init__(self, blob_store: BlobStore, repository: EvidenceRepository) -> None:
        self.blob_store = blob_store
        self.repository = repository
        self.validator = ClaimValidator(repository)

    def run(self, args: dict[str, Any]) -> ToolResult:
        try:
            request = HumanInteractionRequest.model_validate(args.get("request"))
            response = HumanInteractionResponse.model_validate(args.get("response"))
            candidate_id = str(args.get("candidate_id", "")).strip()
            if not candidate_id:
                raise ValueError("candidate_id is required")
            if any(
                correction.candidate_id != candidate_id
                for correction in response.corrections
            ):
                raise ValueError(
                    "correction candidate_id does not match the active candidate"
                )
            roots = [str(value) for value in args.get("allowed_path_roots", [])]
            if response.file_paths and (
                not roots
                or any(
                    not _path_is_allowed(path, roots)
                    for path in response.file_paths
                )
            ):
                raise ValueError(
                    "uploaded file path is outside the caller's allowed roots"
                )
            _validate_response_against_request(request, response)
        except Exception as exc:
            error_type = (
                "idempotency_conflict"
                if "idempotency_conflict" in str(exc)
                else "validation_error"
            )
            return _failure(self.name, str(exc), error_type)
        canonical = canonical_response_payload(response)
        payload_hash = hashlib.sha256(canonical).hexdigest()
        idempotency_key = hashlib.sha256(
            (
                response.thread_id
                + response.request_id
                + response.response_id
                + canonical.decode("utf-8")
                + response.schema_version
            ).encode("utf-8")
        ).hexdigest()
        existing = self.repository.get_response_receipt(response.response_id)
        if existing is not None:
            if existing.get("payload_hash") != payload_hash:
                return _failure(
                    self.name,
                    "idempotency_conflict: response_id has a different payload",
                    "idempotency_conflict",
                )
            return _success(
                self.name,
                records=[existing],
                evidence_ids=_receipt_evidence_ids(existing),
                metadata={"idempotency_key": idempotency_key, "deduplicated": True},
            )
        try:
            artifact = _archive_response_artifact(
                self.blob_store, self.repository, request, response, canonical
            )
            fragments = _response_fragments(artifact, response)
            fragments = [self.repository.save_fragment(item) for item in fragments]
            claims = self._claims_from_response(
                request, response, candidate_id, artifact, fragments
            )
            result = {
                "payload_hash": payload_hash,
                "artifact_id": artifact.artifact_id,
                "fragment_ids": [item.fragment_id for item in fragments],
                "claim_ids": [item.claim_id for item in claims],
                "action": response.action,
            }
            receipt = self.repository.save_response_receipt(
                response_id=response.response_id,
                idempotency_key=idempotency_key,
                payload_hash=payload_hash,
                result=result,
            )
        except ValueError as exc:
            error_type = (
                "idempotency_conflict"
                if "idempotency_conflict" in str(exc)
                else "validation_error"
            )
            return _failure(self.name, str(exc), error_type)
        except FileExistsError:
            return _failure(
                self.name,
                "idempotency_conflict: response_id has a different payload",
                "idempotency_conflict",
            )
        except Exception as exc:
            return _failure(self.name, str(exc), "storage_error", retryable=True)
        return _success(
            self.name,
            records=[receipt],
            evidence_ids=_receipt_evidence_ids(receipt),
            metadata={"idempotency_key": idempotency_key, "deduplicated": False},
        )

    def _claims_from_response(
        self,
        request: HumanInteractionRequest,
        response: HumanInteractionResponse,
        candidate_id: str,
        artifact: EvidenceArtifact,
        fragments: list[EvidenceFragment],
    ) -> list[EvidenceClaim]:
        fragment_by_pointer = {
            str(item.locator["pointer"]): item for item in fragments
        }
        questions = {item.question_id: item for item in request.questions}
        saved: list[EvidenceClaim] = []
        for index, answer in enumerate(response.answers):
            if answer.declined:
                continue
            question = questions[answer.question_id]
            fragment = fragment_by_pointer[f"/answers/{index}/text"]
            claim = EvidenceClaim(
                subject_id=candidate_id,
                predicate=question.target_path,
                value=answer.text,
                claim_type="user_reported",
                evidence_fragment_ids=[fragment.fragment_id],
                confidence=1.0,
                extractor=ClaimExtractor(provider="human", model="user_response"),
                prompt_version="human_interaction_v0.4",
                schema_version="v0.4",
            )
            saved.append(
                self.validator.validate_and_save(
                    claim,
                    {artifact.artifact_id},
                    expected_owner_id=request.user_id,
                )
            )
        for index, correction in enumerate(response.corrections):
            fragment = fragment_by_pointer[f"/corrections/{index}"]
            supersedes = correction.supersedes_claim_ids or [None]
            for previous_id in supersedes:
                claim = EvidenceClaim(
                    subject_id=correction.candidate_id,
                    predicate=correction.target_path,
                    value=correction.new_value,
                    claim_type="user_reported",
                    evidence_fragment_ids=[fragment.fragment_id],
                    confidence=1.0,
                    extractor=ClaimExtractor(provider="human", model="profile_correction"),
                    prompt_version="human_interaction_v0.4",
                    schema_version="v0.4",
                    supersedes_claim_id=previous_id,
                )
                existing_claim = next(
                    (
                        item
                        for item in self.repository.list_claims(
                            correction.candidate_id
                        )
                        if item.idempotency_key() == claim.idempotency_key()
                    ),
                    None,
                )
                if existing_claim is not None:
                    saved.append(existing_claim)
                    continue
                saved_claim = self.validator.validate_and_save(
                    claim,
                    {artifact.artifact_id},
                    expected_owner_id=request.user_id,
                )
                saved.append(saved_claim)
                if previous_id:
                    self.repository.mark_claim_superseded(previous_id)
        return saved


class ProjectCandidateTool:
    name = "profile.project_candidate"

    def __init__(
        self, evidence_repository: EvidenceRepository, profile_repository: ProfileRepository
    ) -> None:
        self.evidence_repository = evidence_repository
        self.projector = CandidateProfileProjector(profile_repository)

    def run(self, args: dict[str, Any]) -> ToolResult:
        candidate_id = str(args.get("candidate_id", "")).strip()
        if not candidate_id:
            return _failure(self.name, "candidate_id is required", "validation_error")
        try:
            claims = self.evidence_repository.list_active_claims(candidate_id)
            snapshot = self.projector.project(
                candidate_id,
                claims,
                completion_reason=args.get("completion_reason"),
                unknowns=[str(value) for value in args.get("unknowns", [])],
            )
        except Exception as exc:
            return _failure(self.name, str(exc), "storage_error", retryable=True)
        return _success(
            self.name,
            records=[
                {
                    "snapshot_id": snapshot.snapshot_id,
                    "version": snapshot.version,
                    "schema_version": snapshot.schema_version,
                    "supporting_claim_ids": snapshot.supporting_claim_ids,
                }
            ],
            evidence_ids=[snapshot.snapshot_id],
        )


class LoadCandidateTool:
    name = "profile.load_candidate"

    def __init__(self, repository: ProfileRepository) -> None:
        self.repository = repository

    def run(self, args: dict[str, Any]) -> ToolResult:
        snapshot_id = str(args.get("snapshot_id", "")).strip()
        candidate_id = str(args.get("candidate_id", "")).strip()
        snapshot = (
            self.repository.get_profile(snapshot_id)
            if snapshot_id
            else self.repository.get_latest_profile(candidate_id, "candidate")
            if candidate_id
            else None
        )
        if snapshot is None:
            return _failure(self.name, "candidate profile not found", "validation_error")
        profile = snapshot.profile_data
        return _success(
            self.name,
            records=[
                {
                    "snapshot_id": snapshot.snapshot_id,
                    "candidate_id": snapshot.subject_id,
                    "version": snapshot.version,
                    "schema_version": snapshot.schema_version,
                    "education_count": len(profile.get("education", [])),
                    "experience_count": len(profile.get("experiences", [])),
                    "capability_count": len(profile.get("capabilities", [])),
                    "responsibility_boundary_count": len(
                        profile.get("responsibility_boundaries", [])
                    ),
                    "unknowns": profile.get("unknowns", []),
                    "conflicts": profile.get("conflicts", []),
                    "evidence_coverage": profile.get("evidence_coverage", {}),
                    "supporting_claim_ids": snapshot.supporting_claim_ids,
                }
            ],
            evidence_ids=[snapshot.snapshot_id],
        )


class DiffCandidateVersionsTool:
    name = "profile.diff_candidate_versions"

    def __init__(self, repository: ProfileRepository) -> None:
        self.repository = repository

    def run(self, args: dict[str, Any]) -> ToolResult:
        first = self.repository.get_profile(str(args.get("from_snapshot_id", "")))
        second = self.repository.get_profile(str(args.get("to_snapshot_id", "")))
        if first is None or second is None:
            return _failure(self.name, "profile snapshot not found", "validation_error")
        if first.subject_id != second.subject_id:
            return _failure(
                self.name, "snapshots belong to different candidates", "validation_error"
            )
        diff = diff_profile_snapshots(first.snapshot_id, first.profile_data, second.snapshot_id, second.profile_data)
        return _success(
            self.name,
            records=[diff.model_dump(mode="json")],
            evidence_ids=[first.snapshot_id, second.snapshot_id],
        )


def build_candidate_profile_registry(
    *,
    blob_store: BlobStore,
    repository: EvidenceRepository,
    profile_repository: ProfileRepository,
    claim_extractor: ClaimExtractorService,
) -> ToolRegistry:
    registry = ToolRegistry()
    for tool in [
        CandidateIngestMaterialTool(blob_store, repository),
        ExtractPdfTextTool(blob_store, repository),
        ExtractPlainTextTool(blob_store, repository),
        CreateFragmentsTool(blob_store, repository),
        ExtractCandidateClaimsTool(repository, claim_extractor),
        ArchiveUserResponseTool(blob_store, repository),
        ProjectCandidateTool(repository, profile_repository),
        LoadCandidateTool(profile_repository),
        DiffCandidateVersionsTool(profile_repository),
    ]:
        registry.register(tool)
    return registry


def diff_profile_snapshots(
    from_snapshot_id: str,
    before: dict[str, Any],
    to_snapshot_id: str,
    after: dict[str, Any],
) -> ProfileVersionDiff:
    left = _flatten(before)
    right = _flatten(after)
    ignored = {"generated_at", "previous_snapshot_id"}
    left = {key: value for key, value in left.items() if key.split(".")[-1] not in ignored}
    right = {key: value for key, value in right.items() if key.split(".")[-1] not in ignored}
    old_conflicts = _conflict_ids(before)
    new_conflicts = _conflict_ids(after)
    return ProfileVersionDiff(
        from_snapshot_id=from_snapshot_id,
        to_snapshot_id=to_snapshot_id,
        added_paths=sorted(right.keys() - left.keys()),
        removed_paths=sorted(left.keys() - right.keys()),
        changed_paths=sorted(
            key for key in left.keys() & right.keys() if left[key] != right[key]
        ),
        new_conflicts=sorted(new_conflicts - old_conflicts),
        resolved_conflicts=sorted(old_conflicts - new_conflicts),
    )


def _is_supported_path(path: Path) -> bool:
    return path.suffix.lower() == ".pdf" or _is_plain_name(path.name)


def _is_plain_name(name: str) -> bool:
    path = Path(name)
    return path.suffix.lower() in SUPPORTED_PLAIN_SUFFIXES or path.name.lower() == "readme"


def _save_plain_extraction(
    artifact: EvidenceArtifact,
    text: str,
    blob_store: BlobStore,
    repository: EvidenceRepository,
) -> DocumentExtraction:
    units: list[ExtractionUnit] = []
    start = 0
    for line_number, line in enumerate(text.splitlines(keepends=True), start=1):
        end = start + len(line)
        units.append(
            ExtractionUnit(
                index=line_number,
                start=start,
                end=end,
                locator={"line": line_number},
            )
        )
        start = end
    if start < len(text) or not units:
        units.append(
            ExtractionUnit(
                index=len(units) + 1,
                start=start,
                end=len(text),
                locator={"line": len(units) + 1},
            )
        )
    return _persist_extraction(
        artifact,
        text,
        "utf8_plain_text",
        "line_and_char_range",
        units,
        blob_store,
        repository,
    )


def _save_pdf_extraction(
    artifact: EvidenceArtifact,
    pages: list[str],
    blob_store: BlobStore,
    repository: EvidenceRepository,
) -> DocumentExtraction:
    parts: list[str] = []
    units: list[ExtractionUnit] = []
    cursor = 0
    for page_number, page in enumerate(pages, start=1):
        if parts:
            parts.append("\n")
            cursor += 1
        start = cursor
        parts.append(page)
        cursor += len(page)
        units.append(
            ExtractionUnit(
                index=page_number,
                start=start,
                end=cursor,
                locator={"page": page_number},
            )
        )
    return _persist_extraction(
        artifact,
        "".join(parts),
        "pypdf_text",
        "page_and_char_range",
        units,
        blob_store,
        repository,
    )


def _persist_extraction(
    artifact: EvidenceArtifact,
    text: str,
    parser_name: str,
    locator_type: str,
    units: list[ExtractionUnit],
    blob_store: BlobStore,
    repository: EvidenceRepository,
) -> DocumentExtraction:
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    owner_segment = hashlib.sha256(
        artifact.owner_id.encode("utf-8")
    ).hexdigest()[:24]
    uri = blob_store.put(
        f"derived/{owner_segment}/{artifact.artifact_id}/{parser_name}-{PARSER_VERSION}.txt",
        text.encode("utf-8"),
    )
    return repository.save_extraction(
        DocumentExtraction(
            artifact_id=artifact.artifact_id,
            parser_name=parser_name,
            parser_version=PARSER_VERSION,
            text_uri=uri,
            text_hash=digest,
            locator_type=locator_type,
            units=units,
        )
    )


def _extraction_result(
    name: str, extraction: DocumentExtraction, *, deduplicated: bool
) -> ToolResult:
    return _success(
        name,
        records=[
            {
                "artifact_id": extraction.artifact_id,
                "unit_count": len(extraction.units),
                "locator_type": extraction.locator_type,
            }
        ],
        evidence_ids=[extraction.artifact_id],
        metadata={
            "deduplicated": deduplicated,
            "parser_name": extraction.parser_name,
            "parser_version": extraction.parser_version,
            "text_hash": extraction.text_hash,
        },
    )


def _fragments_from_extraction(
    artifact: EvidenceArtifact,
    extraction: DocumentExtraction,
    text: str,
    max_chars: int,
) -> list[EvidenceFragment]:
    fragments: list[EvidenceFragment] = []
    for unit in extraction.units:
        cursor = unit.start
        while cursor < unit.end:
            end = min(cursor + max_chars, unit.end)
            if end < unit.end:
                split = text.rfind("\n", cursor, end)
                if split > cursor:
                    end = split + 1
            value = text[cursor:end]
            if not value:
                break
            if not value.strip():
                cursor = end
                continue
            locator = {
                **unit.locator,
                "start": cursor - unit.start,
                "end": end - unit.start,
                "document_start": cursor,
                "document_end": end,
            }
            if extraction.locator_type == "line_and_char_range":
                locator["start_line"] = text.count("\n", 0, cursor) + 1
                locator["end_line"] = text.count("\n", 0, max(cursor, end - 1)) + 1
            locator_key = json.dumps(locator, sort_keys=True, separators=(",", ":"))
            fragment_id = str(
                uuid5(
                    NAMESPACE_URL,
                    f"{artifact.artifact_id}:{extraction.parser_version}:{locator_key}",
                )
            )
            fragments.append(
                EvidenceFragment(
                    fragment_id=fragment_id,
                    artifact_id=artifact.artifact_id,
                    locator_type=extraction.locator_type,
                    locator=locator,
                    text=value,
                    text_hash=hashlib.sha256(value.encode("utf-8")).hexdigest(),
                    metadata={
                        "parser_name": extraction.parser_name,
                        "parser_version": extraction.parser_version,
                    },
                )
            )
            cursor = end
    return fragments


def _validate_response_against_request(
    request: HumanInteractionRequest, response: HumanInteractionResponse
) -> None:
    if response.request_id != request.request_id:
        raise ValueError("request_id does not match the pending interaction")
    if response.thread_id != request.thread_id:
        raise ValueError("thread_id does not match the pending interaction")
    if response.user_id != request.user_id:
        raise ValueError("user_id does not match the pending interaction")
    if response.action not in request.allowed_actions:
        raise ValueError("response action is not allowed for this interaction")
    if request.expires_at and response.submitted_at > request.expires_at:
        raise ValueError("interaction request has expired")
    question_ids = {item.question_id for item in request.questions}
    if any(item.question_id not in question_ids for item in response.answers):
        raise ValueError("response contains an unknown question_id")
    targets = set(request.target_paths)
    if any(item.target_path not in targets for item in response.corrections):
        raise ValueError("correction target is outside the pending interaction")


def _archive_response_artifact(
    blob_store: BlobStore,
    repository: EvidenceRepository,
    request: HumanInteractionRequest,
    response: HumanInteractionResponse,
    canonical: bytes,
) -> EvidenceArtifact:
    digest = hashlib.sha256(canonical).hexdigest()
    existing = repository.find_artifact_by_hash(digest, request.user_id)
    if existing is not None:
        return existing
    artifact_id = str(
        uuid5(NAMESPACE_URL, f"human-response:{request.user_id}:{digest}")
    )
    owner_segment = hashlib.sha256(
        request.user_id.encode("utf-8")
    ).hexdigest()[:24]
    request_segment = hashlib.sha256(
        request.request_id.encode("utf-8")
    ).hexdigest()[:24]
    response_segment = hashlib.sha256(
        response.response_id.encode("utf-8")
    ).hexdigest()[:24]
    raw_uri = blob_store.put(
        f"responses/{owner_segment}/{request_segment}/{response_segment}.json",
        canonical,
    )
    return repository.save_artifact(
        EvidenceArtifact(
            artifact_id=artifact_id,
            owner_id=request.user_id,
            source_type="human_interaction",
            content_type="conversation_response",
            original_name=f"response-{response.response_id}.json",
            raw_uri=raw_uri,
            content_hash=digest,
            parser_name="human_response_json",
            parser_version=PARSER_VERSION,
            metadata={
                "thread_id": request.thread_id,
                "request_id": request.request_id,
                "response_id": response.response_id,
                "question_ids": [item.question_id for item in response.answers],
            },
        )
    )


def _response_fragments(
    artifact: EvidenceArtifact, response: HumanInteractionResponse
) -> list[EvidenceFragment]:
    values: list[tuple[str, str]] = []
    values.extend(
        (f"/answers/{index}/text", answer.text)
        for index, answer in enumerate(response.answers)
        if not answer.declined
    )
    values.extend(
        (
            f"/corrections/{index}",
            json.dumps(correction.model_dump(mode="json"), ensure_ascii=False, sort_keys=True),
        )
        for index, correction in enumerate(response.corrections)
    )
    fragments: list[EvidenceFragment] = []
    for pointer, value in values:
        fragments.append(
            EvidenceFragment(
                fragment_id=str(
                    uuid5(NAMESPACE_URL, f"{artifact.artifact_id}:{pointer}")
                ),
                artifact_id=artifact.artifact_id,
                locator_type="json_pointer",
                locator={"pointer": pointer},
                text=value,
                text_hash=hashlib.sha256(value.encode("utf-8")).hexdigest(),
                metadata={"schema_version": "v0.4"},
            )
        )
    return fragments


def _receipt_evidence_ids(receipt: dict[str, Any]) -> list[str]:
    return [
        str(value)
        for value in [
            receipt.get("artifact_id"),
            *receipt.get("fragment_ids", []),
            *receipt.get("claim_ids", []),
        ]
        if value
    ]


def canonical_response_payload(response: HumanInteractionResponse) -> bytes:
    return json.dumps(
        response.model_dump(mode="json", exclude_none=True),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _flatten(value: Any, prefix: str = "") -> dict[str, Any]:
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            child = f"{prefix}.{key}" if prefix else str(key)
            result.update(_flatten(item, child))
        return result
    if isinstance(value, list):
        result = {}
        for index, item in enumerate(value):
            result.update(_flatten(item, f"{prefix}[{index}]"))
        return result
    return {prefix: value}


def _conflict_ids(profile: dict[str, Any]) -> set[str]:
    return {
        str(value.get("conflict_id"))
        for value in profile.get("conflicts", [])
        if value.get("conflict_id")
    }


def _path_is_allowed(path: str, roots: list[str]) -> bool:
    candidate = Path(path).resolve()
    if not candidate.is_file():
        return False
    return any(
        candidate == Path(value).resolve()
        or Path(value).resolve() in candidate.parents
        for value in roots
    )
