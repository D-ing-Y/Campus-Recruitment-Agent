from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from campus_job_agent.llm import LLMCache
from campus_job_agent.integrations.community_retrieval import (
    BRAVE_SEARCH_ENDPOINT,
    BraveSearchClient,
    CommunityFetchResult,
    Crawl4AICommunityFetcher,
    build_brave_nowcoder_query,
    canonical_nowcoder_detail_url,
    classify_crawl4ai_result,
    extract_nowcoder_main_body,
)
from campus_job_agent.schemas import (
    CommunityContentCluster, CommunityEvidenceDocument,
    CommunityEvidenceCoverage, CommunityEvidenceSegment,
    CommunitySearchDecisionReceipt,
    CommunitySourceEvaluation, EvidenceArtifact, EvidenceFragment,
    LLMConfig, LLMResponse, SourceDetailRequest, SourceDocument, SourceQuery,
    SourceBatch, SourceCapabilities,
)
from campus_job_agent.sources import (
    BraveNowcoderExperienceAdapter,
    SQLiteRoleRepository,
    SourceAdapterRegistry,
)
from campus_job_agent.sources.role_intelligence import (
    CommunitySearchEvaluator, cluster_community_documents,
)
from campus_job_agent.sources.processing import (
    discover_community_post_candidates,
    extract_archived_document,
)
from campus_job_agent.storage import LocalBlobStore, SQLiteRepository
from campus_job_agent.tools.role_profile import (
    EvaluateCommunitySearchTool, FetchCommunityDetailsTool,
)
from campus_job_agent.workflows.role_profile.graph import (
    _representative_community_segment_ids,
)


def _stores(tmp_path: Path):
    return (
        LocalBlobStore(tmp_path / "blobs"),
        SQLiteRepository(tmp_path / "evidence.sqlite3"),
        SQLiteRoleRepository(tmp_path / "role.sqlite3"),
    )


def _query() -> SourceQuery:
    return SourceQuery(
        query_id="brave-query-1", channel="experience",
        source_id="nowcoder_experience",
        keywords=["甲公司", "AI Agent 开发", "面经"], company="甲公司",
        role_family="ai_agent_engineering", graduation_year="2027",
        recruitment_type="autumn_campus", page_size=5,
    )


class _ReadyNowcoderProfile:
    def resolve_cdp(self, value, *, source_id):
        assert value == "local-browser-profile://nowcoder_experience/default"
        assert source_id == "nowcoder_experience"
        return "http://127.0.0.1:9223"

    def mark_authenticated_verified(self, value, *, verified_at):
        return None


def test_brave_query_is_domain_constrained_and_rejects_operator_injection() -> None:
    assert build_brave_nowcoder_query(["甲公司", "AI Agent", "面经"]) == (
        "甲公司 AI Agent 面经 site:nowcoder.com"
    )
    for value in (
        "site:example.com 面经", "https://example.com", "inurl:/search",
    ):
        with pytest.raises(ValueError, match="forbidden"):
            build_brave_nowcoder_query([value])


@pytest.mark.parametrize("value,expected", [
    ("https://www.nowcoder.com/feed/main/detail/abc-123?from=search", "https://www.nowcoder.com/feed/main/detail/abc-123"),
    ("https://nowcoder.com/discuss/12345#reply", "https://www.nowcoder.com/discuss/12345"),
    ("https://www.nowcoder.com/search/all?query=x", None),
    ("https://evil.example/discuss/12345", None),
    ("http://www.nowcoder.com/discuss/12345", None),
])
def test_nowcoder_detail_allowlist(value: str, expected: str | None) -> None:
    assert canonical_nowcoder_detail_url(value) == expected


def test_brave_client_never_places_api_key_in_archived_response() -> None:
    captured = {}

    def request(url, *, params, headers, timeout):
        captured.update({"url": url, "params": params, "headers": headers})
        return httpx.Response(
            200,
            json={"web": {"results": [{
                "url": "https://www.nowcoder.com/discuss/12345",
                "title": "甲公司面经", "description": "search snippet",
            }]}},
            request=httpx.Request("GET", url),
        )

    raw, metadata = BraveSearchClient(request=request).search_nowcoder(
        keywords=["甲公司", "面经"], limit=5, api_key="brave-secret",
    )
    assert captured["url"] == BRAVE_SEARCH_ENDPOINT
    assert captured["headers"]["X-Subscription-Token"] == "brave-secret"
    assert "brave-secret" not in raw.decode()
    assert "brave-secret" not in json.dumps(metadata)
    assert metadata["query"].endswith("site:nowcoder.com")


def test_adapter_archives_brave_raw_before_discovering_allowlisted_url(
    tmp_path: Path,
) -> None:
    payload = {"web": {"results": [
        {"url": "https://www.nowcoder.com/discuss/12345", "title": "面经"},
        {"url": "https://www.nowcoder.com/search/all?q=x", "title": "搜索页"},
    ]}}
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()

    class SearchClient:
        def search_nowcoder(self, **kwargs):
            return raw, {"provider": "brave_search", "query": "safe"}

    blob, evidence, role = _stores(tmp_path)
    adapter = BraveNowcoderExperienceAdapter(
        blob_store=blob, evidence_repository=evidence, role_repository=role,
        owner_id="owner", live_enabled=True,
        credential_resolver=lambda ref, *, source_id: {"api_key": "secret"},
        search_client=SearchClient(),
    )
    batch = adapter.collect(
        _query(), "local-secret://nowcoder_experience/default"
    )
    assert batch.status == "success" and len(batch.documents) == 1
    document = batch.documents[0]
    artifact = evidence.get_artifact(str(document.raw_artifact_id))
    assert artifact is not None and artifact.source_url == BRAVE_SEARCH_ENDPOINT
    assert artifact.metadata["request"]["provider"] == "brave_search"
    _, fragments = extract_archived_document(
        document, blob_store=blob, repository=evidence,
    )
    assert blob.get(artifact.raw_uri) == raw
    assert json.loads(fragments[0].text) == payload
    candidates = discover_community_post_candidates(document, fragments)
    assert [item.detail_url for item in candidates] == [
        "https://www.nowcoder.com/discuss/12345"
    ]


def test_crawl4ai_fetcher_uses_one_bounded_batch_runner() -> None:
    calls = []

    def runner(urls, concurrency):
        calls.append((urls, concurrency))
        return [
            CommunityFetchResult(
                requested_url=value, final_url=value, success=True,
                status_code=200, html="<article>" + "正文" * 50 + "</article>",
            ) for value in urls
        ]

    fetcher = Crawl4AICommunityFetcher(runner=runner)
    urls = [
        "https://www.nowcoder.com/discuss/12345",
        "https://www.nowcoder.com/discuss/12346",
    ]
    assert len(fetcher.fetch_many(urls, max_concurrency=9)) == 2
    assert calls == [(urls, 2)]


def test_nowcoder_profile_requires_one_unique_main_post() -> None:
    body = "甲公司 AI Agent 面试流程，一面询问 RAG 召回与评测。" * 5
    extracted = extract_nowcoder_main_body(
        html=f'<div class="post-content">{body}</div>',
    )
    assert extracted == (body, ".post-content")
    ambiguous = extract_nowcoder_main_body(
        html=(
            f'<div class="post-content">{body}</div>'
            f'<div class="post-content">{body}</div>'
        ),
    )
    assert ambiguous is None


@pytest.mark.parametrize("result,expected", [
    (CommunityFetchResult("u", "u", False, 429), "rate_limited"),
    (CommunityFetchResult("u", "u", False, 403, error_message="robots.txt denied"), "robots_disallowed"),
    (CommunityFetchResult("u", "u", False, None, error_message="Blocked by robots.txt"), "robots_disallowed"),
    (CommunityFetchResult("u", "u", True, 200, html="请完成验证码"), "risk_controlled"),
    (CommunityFetchResult("u", "u", True, 200, html="登录后查看"), "authentication_required"),
    (CommunityFetchResult("u", "u", True, 200, html='<script>const loginMode = true</script><div class="nc-post-content">' + "有效正文" * 30 + "</div>"), "success"),
])
def test_crawl4ai_failure_classification(
    result: CommunityFetchResult, expected: str,
) -> None:
    assert classify_crawl4ai_result(result) == expected


def test_adapter_batches_two_details_and_archives_before_body_parse(
    tmp_path: Path,
) -> None:
    body = "甲公司 AI Agent 面试询问 RAG 召回、评测和工程化落地。" * 6
    urls = [
        "https://www.nowcoder.com/discuss/12345",
        "https://www.nowcoder.com/discuss/12346",
    ]
    calls = []

    def runner(values, concurrency, cdp_url):
        assert cdp_url == "http://127.0.0.1:9223"
        calls.append((values, concurrency))
        return [
            CommunityFetchResult(
                requested_url=value, final_url=value, success=True,
                status_code=200,
                html=f'<article data-post-content>{body}{value[-1]}</article>',
                metadata={"title": "面经"},
            ) for value in values
        ]

    blob, evidence, role = _stores(tmp_path)
    adapter = BraveNowcoderExperienceAdapter(
        blob_store=blob, evidence_repository=evidence, role_repository=role,
        owner_id="owner", live_enabled=True,
        detail_fetcher=Crawl4AICommunityFetcher(runner=runner),
        browser_profile_manager=_ReadyNowcoderProfile(),
    )
    requests = [
        SourceDetailRequest(
            source_id="nowcoder_experience", channel="experience",
            query_id="q", candidate_id=f"c-{index}",
            parent_document_id="search", detail_url=value,
            expected_document_kind="experience_post",
        ) for index, value in enumerate(urls)
    ]
    batches = adapter.fetch_details(
        requests, max_concurrency=2,
        browser_profile_ref=(
            "local-browser-profile://nowcoder_experience/default"
        ),
    )
    assert calls == [(urls, 2)]
    assert [item.status for item in batches] == ["success", "success"]
    for batch in batches:
        document = batch.documents[0]
        assert evidence.get_artifact(str(document.raw_artifact_id)) is not None
        fragments = evidence.list_fragments(str(document.raw_artifact_id))
        assert any(
            item.metadata.get("parser_version") == "nowcoder_main_body_v1"
            for item in fragments
        )


def test_rejected_redirect_is_still_archived(tmp_path: Path) -> None:
    requested = "https://www.nowcoder.com/discuss/12345"

    def runner(values, concurrency, cdp_url):
        assert cdp_url == "http://127.0.0.1:9223"
        return [CommunityFetchResult(
            requested_url=requested, final_url="https://evil.example/post/1",
            success=True, status_code=200, html="private redirect",
        )]

    blob, evidence, role = _stores(tmp_path)
    adapter = BraveNowcoderExperienceAdapter(
        blob_store=blob, evidence_repository=evidence, role_repository=role,
        owner_id="owner", live_enabled=True,
        detail_fetcher=Crawl4AICommunityFetcher(runner=runner),
        browser_profile_manager=_ReadyNowcoderProfile(),
    )
    batch = adapter.fetch_detail(SourceDetailRequest(
        source_id="nowcoder_experience", channel="experience", query_id="q",
        candidate_id="c", parent_document_id="search", detail_url=requested,
        expected_document_kind="experience_post",
    ), browser_profile_ref=(
        "local-browser-profile://nowcoder_experience/default"
    ))
    assert batch.status == "policy_blocked"
    assert batch.documents and batch.documents[0].raw_artifact_id
    artifact = evidence.get_artifact(str(batch.documents[0].raw_artifact_id))
    assert artifact is not None
    assert artifact.metadata["redirect_validation"] == "rejected"


def _cluster_document(
    *, index: int, body: str, tmp_path: Path,
    evidence: SQLiteRepository, role: SQLiteRoleRepository,
) -> tuple[str, str]:
    raw = body.encode()
    digest = __import__("hashlib").sha256(raw).hexdigest()
    blob = LocalBlobStore(tmp_path / "cluster-blobs")
    artifact = evidence.save_artifact(EvidenceArtifact(
        owner_id="owner", source_type="community_experience",
        content_type="text/html",
        source_url=f"https://www.nowcoder.com/discuss/{20000 + index}",
        original_name=f"post-{index}",
        raw_uri=blob.put(f"post-{index}/raw", raw), content_hash=digest,
    ))
    body_fragment = evidence.save_fragment(EvidenceFragment(
        artifact_id=artifact.artifact_id,
        locator_type="css_selector_and_char_range",
        locator={"selector": "article", "start": 0, "end": len(body)},
        text=body, text_hash=digest,
        metadata={"parser_version": "nowcoder_main_body_v1"},
    ))
    source_document = role.save("source_document", SourceDocument(
        source_document_id=f"source-document-{index}",
        source_id="nowcoder_experience", channel="experience", query_id="q",
        source_url=f"https://www.nowcoder.com/discuss/{20000 + index}",
        document_kind="experience_post", raw_artifact_id=artifact.artifact_id,
        content_hash=digest,
    ))
    document = role.save(
        "community_evidence_document", CommunityEvidenceDocument(
            document_id=f"community-document-{index}",
            artifact_id=artifact.artifact_id,
            source_document_id=source_document.source_document_id,
            source_id="nowcoder_experience",
            detail_url=source_document.source_url,
            retrieved_at=source_document.retrieved_at,
            document_type="interview_experience", company_key="甲公司",
            role_family_id="ai_agent_engineering",
            classification_receipt_id=f"receipt-{index}",
        ),
    )
    quote = body[:20]
    segment = role.save(
        "community_evidence_segment", CommunityEvidenceSegment(
            segment_id=f"segment-{index}", document_id=document.document_id,
            fragment_id=body_fragment.fragment_id, quote_start=0,
            quote_end=len(quote),
            quote_hash=__import__("hashlib").sha256(quote.encode()).hexdigest(),
            segment_type="interview_question", usage="demand_assessment",
            company_key="甲公司", role_family_id="ai_agent_engineering",
            validation_status="accepted",
        ),
    )
    return document.document_id, segment.segment_id


def test_three_layer_clustering_merges_near_duplicate_bodies(
    tmp_path: Path,
) -> None:
    evidence = SQLiteRepository(tmp_path / "cluster-evidence.sqlite3")
    role = SQLiteRoleRepository(tmp_path / "cluster-role.sqlite3")
    shared = "甲公司 AI Agent 一面询问 RAG 召回评测、工程化落地与项目难点。" * 12
    first, first_segment = _cluster_document(
        index=1, body=shared, tmp_path=tmp_path, evidence=evidence, role=role,
    )
    second, second_segment = _cluster_document(
        index=2, body=shared + "补充", tmp_path=tmp_path,
        evidence=evidence, role=role,
    )
    third, third_segment = _cluster_document(
        index=3, body="甲公司笔试考察 Python 并发编程和 SQL 窗口函数。" * 12,
        tmp_path=tmp_path, evidence=evidence, role=role,
    )
    clusters = cluster_community_documents(
        company_role_group_id="group", evidence_purpose="interview_experience",
        document_ids=[first, second, third], role_repository=role,
        evidence_repository=evidence,
    )
    assert len(clusters) == 2
    merged = next(item for item in clusters if len(item.member_document_ids) == 2)
    assert "shingle_jaccard" in merged.merge_methods

    semantic = cluster_community_documents(
        company_role_group_id="group", evidence_purpose="interview_experience",
        document_ids=[first, second, third], role_repository=role,
        evidence_repository=evidence,
        semantic_duplicate_segment_groups=[[first_segment, third_segment]],
        semantic_decision_receipt_id="decision-1",
    )
    assert len(semantic) == 1
    assert "semantic_segment_receipt" in semantic[0].merge_methods
    assert second_segment in semantic[0].member_segment_ids


def test_projection_uses_only_the_representative_post_from_each_cluster(
    tmp_path: Path,
) -> None:
    evidence = SQLiteRepository(tmp_path / "projection-evidence.sqlite3")
    role = SQLiteRoleRepository(tmp_path / "projection-role.sqlite3")
    shared = "甲公司 AI Agent 一面询问 RAG 召回评测、工程化落地与项目难点。" * 12
    first, first_segment = _cluster_document(
        index=11, body=shared, tmp_path=tmp_path, evidence=evidence, role=role,
    )
    second, second_segment = _cluster_document(
        index=12, body=shared + "补充", tmp_path=tmp_path,
        evidence=evidence, role=role,
    )
    cluster = cluster_community_documents(
        company_role_group_id="group", evidence_purpose="interview_experience",
        document_ids=[first, second], role_repository=role,
        evidence_repository=evidence,
    )[0]
    role.save("community_content_cluster", cluster)
    coverage = role.save(
        "community_evidence_coverage",
        CommunityEvidenceCoverage(
            coverage_id="coverage", company_role_group_id="group",
            evidence_purpose="interview_experience",
            accepted_document_ids=[first, second], independent_document_count=2,
            accepted_cluster_ids=[cluster.cluster_id], independent_cluster_count=1,
            status="insufficient", next_action="finalize_partial",
        ),
    )
    selected = _representative_community_segment_ids({
        "community_coverage_ids": [coverage.coverage_id],
        "community_evidence_segment_ids": [first_segment, second_segment],
    }, role)
    expected = (
        first_segment if cluster.representative_document_id == first
        else second_segment
    )
    assert selected == [expected]


class _DecisionProvider:
    name = "decision-test"

    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def generate(self, request):
        return LLMResponse(
            text=json.dumps(self.payload, ensure_ascii=False),
            provider=self.name, model=request.model,
        )


class _InvalidDecisionProvider:
    name = "invalid-decision-test"

    def generate(self, request):
        return LLMResponse(
            text="not-json", provider=self.name, model=request.model,
        )


def _source_evaluations() -> list[CommunitySourceEvaluation]:
    return [
        CommunitySourceEvaluation(
            evaluation_id="eval-nowcoder", run_id="run",
            source_id="nowcoder_experience",
            evidence_purpose="interview_experience", sampled_detail_count=2,
            relevant_detail_count=2, valid_body_count=2, scope_hit_count=2,
            accepted_segment_count=3, relevance_rate=1, valid_body_rate=1,
            scope_hit_rate=1,
        ),
        CommunitySourceEvaluation(
            evaluation_id="eval-xhs", run_id="run",
            source_id="xiaohongshu_experience",
            evidence_purpose="interview_experience", sampled_detail_count=1,
            relevant_detail_count=1, valid_body_count=1, scope_hit_count=1,
            accepted_segment_count=1, relevance_rate=1, valid_body_rate=1,
            scope_hit_rate=1,
        ),
    ]


def test_llm_evaluator_is_bounded_to_sources_keywords_and_segment_ids(
    tmp_path: Path,
) -> None:
    clusters = [
        CommunityContentCluster(
            cluster_id=f"cluster-{index}", company_role_group_id="group",
            evidence_purpose="interview_experience",
            representative_document_id=f"document-{index}",
            member_document_ids=[f"document-{index}"],
            member_segment_ids=[f"segment-{index}"],
            source_ids=["nowcoder_experience"],
        ) for index in range(3)
    ]
    provider = _DecisionProvider({
        "ranked_source_ids": [
            "nowcoder_experience", "xiaohongshu_experience",
        ],
        "missing_topics": ["笔试"], "proposed_keywords": ["笔试"],
        "semantic_duplicate_segment_groups": [["segment-0", "segment-1"]],
        "verdict": "sufficient",
    })
    evaluator = CommunitySearchEvaluator(
        LLMConfig(
            provider="openai_compatible", model="decision-model",
            cache_enabled=False, max_retries=2,
        ), provider, LLMCache(tmp_path / "llm-cache"),
    )
    decision, calls = evaluator.evaluate(
        run_id="run", evidence_purpose="interview_experience",
        evaluations=_source_evaluations(), clusters=clusters,
        segment_summaries=[
            {"segment_id": f"segment-{index}", "quote": f"问题 {index}"}
            for index in range(3)
        ],
        allowed_keywords=["面经", "笔试"], hard_floor_met=True,
    )
    assert decision.verdict == "sufficient"
    assert decision.budget_allocation == {
        "nowcoder_experience": 0.7, "xiaohongshu_experience": 0.3,
    }
    assert decision.semantic_duplicate_segment_groups == [["segment-0", "segment-1"]]
    assert sum(item.retry_count + 1 for item in calls) == 1


def test_llm_cannot_override_cluster_floor_or_propose_operator() -> None:
    with pytest.raises(ValueError, match="hard floor"):
        CommunitySearchDecisionReceipt(
            decision_id="decision", run_id="run",
            evidence_purpose="interview_experience",
            ranked_source_ids=["nowcoder_experience"],
            budget_allocation={"nowcoder_experience": 1.0},
            verdict="sufficient", hard_floor_met=False,
            provider="test", model="test",
        )
    with pytest.raises(ValueError, match="keyword"):
        CommunitySearchDecisionReceipt(
            decision_id="decision", run_id="run",
            evidence_purpose="interview_experience",
            ranked_source_ids=["nowcoder_experience"],
            budget_allocation={"nowcoder_experience": 1.0},
            proposed_keywords=["site:evil.example"],
            verdict="insufficient", hard_floor_met=False,
            provider="test", model="test",
        )


def test_failed_evaluator_call_is_preserved_for_global_budget_accounting(
    tmp_path: Path,
) -> None:
    evidence = SQLiteRepository(tmp_path / "failed-eval-evidence.sqlite3")
    role = SQLiteRoleRepository(tmp_path / "failed-eval-role.sqlite3")
    evaluations = [
        role.save("community_source_evaluation", item)
        for item in _source_evaluations()
    ]
    evaluator = CommunitySearchEvaluator(
        LLMConfig(
            provider="openai_compatible", model="decision-model",
            cache_enabled=False, max_retries=2,
        ),
        _InvalidDecisionProvider(), LLMCache(tmp_path / "failed-eval-cache"),
    )
    result = EvaluateCommunitySearchTool(
        evidence, role, evaluator,
    ).run({
        "run_id": "run", "evidence_purpose": "interview_experience",
        "source_evaluation_ids": [item.evaluation_id for item in evaluations],
        "cluster_ids": [], "allowed_keywords": ["面经"],
        "max_llm_calls": 1,
    })
    assert result.status == "failed"
    assert result.records[0]["decision"] is None
    assert sum(
        int(item.get("retry_count", 0)) + 1
        for item in result.records[0]["llm_calls"]
    ) == 1


def test_community_detail_tool_calls_batch_adapter_once(tmp_path: Path) -> None:
    class Adapter:
        source_id = "nowcoder_experience"
        capabilities = SourceCapabilities(
            source_id=source_id, channel="experience",
            source_type="community_experience", adapter_version="test",
            supports_detail_fetch=True,
        )

        def __init__(self) -> None:
            self.calls = 0
            self.kwargs = {}

        def fetch_details(self, requests, **kwargs):
            self.calls += 1
            self.kwargs = kwargs
            return [
                SourceBatch(
                    source_id=self.source_id, channel="experience",
                    query_id=item.query_id, status="success",
                    idempotency_key=f"batch-{item.detail_request_id}",
                ) for item in requests
            ]

        def fetch_detail(self, request, credential_ref=None):
            raise AssertionError("per-detail fallback must not be used")

    adapter = Adapter()
    adapters = SourceAdapterRegistry()
    adapters.register(adapter)
    role = SQLiteRoleRepository(tmp_path / "batch-tool-role.sqlite3")
    tool = FetchCommunityDetailsTool(adapters, role)
    requests = [
        SourceDetailRequest(
            source_id="nowcoder_experience", channel="experience", query_id="q",
            candidate_id=f"candidate-{index}", parent_document_id="search",
            detail_url=f"https://www.nowcoder.com/discuss/{30000 + index}",
            expected_document_kind="experience_post",
        ) for index in range(2)
    ]
    result = tool.run({
        "run_id": "run", "requests": [
            item.model_dump(mode="json") for item in requests
        ],
        "browser_profile_ref": (
            "local-browser-profile://nowcoder_experience/default"
        ),
    })
    assert result.status == "success"
    assert adapter.calls == 1
    assert adapter.kwargs["browser_profile_ref"] == (
        "local-browser-profile://nowcoder_experience/default"
    )
    assert len(result.records) == 2
