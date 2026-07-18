"""Artifact registration and safe text extraction."""

import hashlib
import mimetypes
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from uuid import uuid4

from campus_job_agent.schemas import EvidenceArtifact, Provenance
from campus_job_agent.storage.base import BlobStore, EvidenceRepository


@dataclass
class IngestionResult:
    artifact: EvidenceArtifact
    deduplicated: bool
    warnings: list[str] = field(default_factory=list)


class ArtifactIngestor:
    def __init__(self, blob_store: BlobStore, repository: EvidenceRepository) -> None:
        self.blob_store = blob_store
        self.repository = repository

    def ingest_file(
        self,
        path: str | Path,
        *,
        owner_id: str,
        source_type: str = "user_upload",
        source_url: str | None = None,
        extract_text: bool = True,
        parser_version: str = "v0.3.0",
    ) -> IngestionResult:
        file_path = Path(path)
        raw = file_path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        existing = self.repository.find_artifact_by_hash(digest, owner_id)
        if existing is not None:
            return IngestionResult(existing, deduplicated=True)

        suffix = file_path.suffix.lower()
        content_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        artifact_id = str(uuid4())
        owner_segment = hashlib.sha256(owner_id.encode("utf-8")).hexdigest()[:24]
        raw_key = f"raw/{owner_segment}/{artifact_id}/{file_path.name}"
        text_key = f"text/{owner_segment}/{artifact_id}.txt"
        created_uris: list[str] = []
        warnings: list[str] = []
        try:
            raw_uri = self.blob_store.put(raw_key, raw)
            created_uris.append(raw_uri)
            text, parser_name = (
                _extract_text(raw, suffix)
                if extract_text
                else (None, "registration_only")
            )
            text_uri = None
            if text is not None:
                text_uri = self.blob_store.put(text_key, text.encode("utf-8"))
                created_uris.append(text_uri)
            else:
                warnings.append("artifact registered without inline text extraction")
                parser_name = "registration_only"
            artifact = EvidenceArtifact(
                artifact_id=artifact_id,
                owner_id=owner_id,
                source_type=source_type,
                content_type=content_type,
                source_url=source_url,
                original_name=file_path.name,
                raw_uri=raw_uri,
                text_uri=text_uri,
                content_hash=digest,
                parser_name=parser_name,
                parser_version=parser_version,
                provenance=Provenance(
                    source_url=source_url,
                    parser_name=parser_name,
                    parser_version=parser_version,
                ),
            )
            saved = self.repository.save_artifact(artifact)
            if saved.artifact_id != artifact.artifact_id:
                for uri in created_uris:
                    self.blob_store.delete(uri)
                return IngestionResult(saved, deduplicated=True)
            return IngestionResult(saved, deduplicated=False, warnings=warnings)
        except Exception:
            for uri in reversed(created_uris):
                self.blob_store.delete(uri)
            raise


class _TextHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.ignored_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style"}:
            self.ignored_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"} and self.ignored_depth:
            self.ignored_depth -= 1

    def handle_data(self, data: str) -> None:
        if not self.ignored_depth and data.strip():
            self.parts.append(data.strip())


def _extract_text(raw: bytes, suffix: str) -> tuple[str | None, str]:
    if suffix in {".txt", ".md", ".markdown"}:
        return raw.decode("utf-8"), "utf8_text"
    if suffix in {".html", ".htm"}:
        parser = _TextHTMLParser()
        parser.feed(raw.decode("utf-8"))
        return "\n".join(parser.parts), "stdlib_html"
    return None, "registration_only"
