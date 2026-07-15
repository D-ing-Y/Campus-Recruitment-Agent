from pathlib import Path

import pytest

from campus_job_agent.evidence import ArtifactIngestor, DeterministicFragmenter
from campus_job_agent.storage import LocalBlobStore, SQLiteRepository


FIXTURES = Path(__file__).parents[1] / "fixtures" / "v03"


def test_ingestion_dedup_html_and_deterministic_fragments(tmp_path) -> None:
    blobs = LocalBlobStore(tmp_path / "blobs")
    repository = SQLiteRepository(tmp_path / "evidence.sqlite3")
    ingestor = ArtifactIngestor(blobs, repository)
    first = ingestor.ingest_file(FIXTURES / "anonymous_job.html", owner_id="owner")
    second = ingestor.ingest_file(FIXTURES / "anonymous_job.html", owner_id="owner")
    assert not first.deduplicated
    assert second.deduplicated
    text = blobs.get(first.artifact.text_uri).decode()  # type: ignore[arg-type]
    assert "AI Agent Engineer" in text
    assert "secretTrackingValue" not in text
    fragmenter = DeterministicFragmenter(max_chars=40)
    first_fragments = fragmenter.fragment(first.artifact, text)
    second_fragments = fragmenter.fragment(first.artifact, text)
    assert first_fragments == second_fragments
    assert "".join(item.text for item in first_fragments) == text


def test_failed_repository_write_cleans_new_blobs(tmp_path) -> None:
    blobs = LocalBlobStore(tmp_path / "blobs")

    class BrokenRepository:
        def find_artifact_by_hash(self, content_hash, owner_id=None):
            return None

        def save_artifact(self, artifact):
            raise RuntimeError("database unavailable")

    with pytest.raises(RuntimeError):
        ArtifactIngestor(blobs, BrokenRepository()).ingest_file(  # type: ignore[arg-type]
            FIXTURES / "anonymous_resume.md", owner_id="owner"
        )
    assert not list((tmp_path / "blobs").rglob("*.*"))
