from pathlib import Path

from campus_job_agent.evidence.claim_extractor import ClaimExtractorService
from campus_job_agent.evidence.pipeline import EvidencePipeline
from campus_job_agent.llm import LLMCache, MockLLMProvider
from campus_job_agent.schemas import CandidateProfile, LLMConfig
from campus_job_agent.storage import LocalBlobStore, SQLiteRepository


FIXTURES = Path(__file__).parents[1] / "fixtures" / "v03"


def test_artifact_to_fragment_to_claim_to_candidate_profile(tmp_path) -> None:
    repository = SQLiteRepository(tmp_path / "evidence.sqlite3")
    pipeline = EvidencePipeline(
        blob_store=LocalBlobStore(tmp_path / "blobs"),
        evidence_repository=repository,
        profile_repository=repository,
        claim_extractor=ClaimExtractorService(
            LLMConfig(model="mock-claims", cache_enabled=False),
            MockLLMProvider(),
            LLMCache(str(tmp_path / "cache")),
        ),
    )
    resume = FIXTURES / "anonymous_resume.md"
    result = pipeline.run(
        [
            resume,
            resume,
            FIXTURES / "anonymous_project.txt",
            FIXTURES / "anonymous_job.html",
        ],
        owner_id="owner-anonymous",
        subject_id="candidate-anonymous",
    )
    assert len(result.artifacts) == 3
    assert result.evaluation.duplicate_artifact_count == 1
    assert result.evaluation.evidence_trace_rate == 1.0
    assert result.evaluation.unsupported_claim_count == 0
    assert all("text" not in event.model_dump() for event in result.trace)
    profile = CandidateProfile.model_validate(result.profile.profile_data)
    assert profile.capabilities
    assert set(profile.supporting_claim_ids) <= {claim.claim_id for claim in result.claims}
    assert "Evidence Pipeline Report" in result.markdown_report()
    report_path = result.write_report(tmp_path / "reports" / "v03.md")
    assert report_path.read_text().startswith("# v0.3 Evidence Pipeline Report")
