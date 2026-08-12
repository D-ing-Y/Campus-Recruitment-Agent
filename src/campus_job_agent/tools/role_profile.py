"""ToolRegistry boundary for v0.5 source collection and role projection."""

from __future__ import annotations

from typing import Any

from campus_job_agent.llm import StructuredOutputError
from campus_job_agent.schemas import (
    CommunityContentCluster, CommunityEvidenceDocument, CommunityEvidenceSegment,
    CommunityExtractionBatch,
    CommunityPostCandidate, CommunitySearchDecisionReceipt,
    CommunitySearchDiagnostic, CommunitySearchPlan, CommunitySourceEvaluation,
    CompanyRoleGroup,
    ExperienceEvidenceRecord, ExperienceScopeLink, FieldResolution, JobDemandProfile,
    JobDetailCandidate, JobIdentityLink, JobReputationProfile,
    JobPostingCluster, NormalizedJobPosting, OfficialVerificationPlan,
    OfficialEscalationReceipt, RoleDetailEvidenceReceipt, RoleFamilyDemandProfile,
    RoleFamilyMembership, RoleIntelligenceBundle, SearchScope, SourceDetailRequest,
    SourceDocument, SourceQuery, SourceRunReceipt, ToolResult,
)
from campus_job_agent.schemas.evidence import utc_now
from campus_job_agent.sources.adapters import SourceAdapterRegistry
from campus_job_agent.sources.credential_store import LocalCredentialStore
from campus_job_agent.sources.processing import (
    diagnose_community_search, discover_community_post_candidates, discover_job_detail_candidates,
    deduplicate_experience, deduplicate_jobs, extract_archived_document,
    link_job_identity, normalize_experience_document, normalize_job_document,
    parse_official_document, plan_official_verification,
)
from campus_job_agent.sources.role_intelligence import (
    CommunityEvidenceExtractor, build_community_search_plan,
    CommunitySearchEvaluator,
    build_company_role_groups, ensure_community_body_fragment,
    materialize_community_evidence,
)
from campus_job_agent.sources.role_intelligence_projection import (
    DemandReputationProjector, official_escalation_for_job,
)
from campus_job_agent.sources.repository import SQLiteRoleRepository
from campus_job_agent.sources.role_gates import (
    assess_role_detail_evidence, classify_role_family, experience_link_applies,
    link_experience_scope,
)
from campus_job_agent.sources.role_pipeline import (
    RoleProfileProjector, extract_experience_claims, extract_recruitment_claims,
    resolve_fields,
)
from campus_job_agent.storage.base import BlobStore, EvidenceRepository, ProfileRepository
from campus_job_agent.tools.registry import ToolRegistry


def _ok(name: str, records: list[dict[str, Any]] | None = None, evidence_ids: list[str] | None = None, **metadata: Any) -> ToolResult:
    return ToolResult(tool_name=name, status="success", records=records or [], evidence_ids=evidence_ids or [], error=None,
                      metadata={"error_type": None, "retryable": False, "needs_user_action": False, **metadata})


def _fail(name: str, message: str, error_type: str, *, retryable: bool = False, needs_user_action: bool = False,
          records: list[dict[str, Any]] | None = None, evidence_ids: list[str] | None = None) -> ToolResult:
    return ToolResult(tool_name=name, status="failed", records=records or [], evidence_ids=evidence_ids or [], error=message,
                      metadata={"error_type": error_type, "retryable": retryable, "needs_user_action": needs_user_action})


class CollectSourceTool:
    def __init__(self, name: str, adapters: SourceAdapterRegistry, role_repository: SQLiteRoleRepository) -> None:
        self.name, self.adapters, self.role_repository = name, adapters, role_repository

    def run(self, args: dict[str, Any]) -> ToolResult:
        try:
            query = SourceQuery.model_validate(args["query"])
            adapter = self.adapters.get(query.source_id)
            if adapter is None:
                return _fail(self.name, f"source adapter not registered: {query.source_id}", "source_changed")
            started_at = utc_now()
            batch = adapter.collect(query, args.get("credential_ref"))
            completed_at = utc_now()
            receipt = _save_receipt(
                self.role_repository, str(args["run_id"]), adapter, batch,
                bool(args.get("credential_ref")), started_at=started_at,
                completed_at=completed_at,
            )
            if batch.status not in {"success", "empty"}:
                return _fail(self.name, batch.error_type or batch.status, batch.error_type or batch.status,
                             retryable=batch.retryable, needs_user_action=batch.needs_user_action,
                             records=[{"batch": batch.model_dump(mode="json"), "receipt": receipt.model_dump(mode="json")}],
                             evidence_ids=[str(item.raw_artifact_id) for item in batch.documents if item.raw_artifact_id])
            return _ok(self.name, [{"batch": batch.model_dump(mode="json"), "receipt": receipt.model_dump(mode="json")}],
                       [str(item.raw_artifact_id) for item in batch.documents if item.raw_artifact_id], idempotency_key=batch.idempotency_key)
        except Exception as exc:
            return _fail(self.name, str(exc), "storage_error", retryable=True)


class FetchSourceDetailTool:
    name = "source.fetch_detail"

    def __init__(self, adapters: SourceAdapterRegistry, role_repository: SQLiteRoleRepository) -> None:
        self.adapters, self.role_repository = adapters, role_repository

    def run(self, args: dict[str, Any]) -> ToolResult:
        try:
            request = SourceDetailRequest.model_validate(args["request"])
            adapter = self.adapters.get(request.source_id)
            if adapter is None or not callable(getattr(adapter, "fetch_detail", None)):
                return _fail(self.name, "detail adapter is unavailable", "unsupported_input")
            batch = adapter.fetch_detail(request, args.get("credential_ref"))
            receipt = _save_receipt(
                self.role_repository, str(args["run_id"]), adapter, batch,
                bool(args.get("credential_ref")),
            )
            self.role_repository.save(
                "source_detail_request", request,
                idempotency_key=f"source-detail-request:{request.idempotency_key}",
            )
            for document in batch.documents:
                self.role_repository.save(
                    "source_document", document,
                    idempotency_key=f"source-document:{document.source_document_id}",
                )
            if batch.status not in {"success", "empty"}:
                return _fail(
                    self.name, batch.error_type or batch.status,
                    batch.error_type or batch.status, retryable=batch.retryable,
                    needs_user_action=batch.needs_user_action,
                    records=[{"batch": batch.model_dump(mode="json"), "receipt": receipt.model_dump(mode="json")}],
                    evidence_ids=[str(item.raw_artifact_id) for item in batch.documents if item.raw_artifact_id],
                )
            return _ok(
                self.name,
                [{"batch": batch.model_dump(mode="json"), "receipt": receipt.model_dump(mode="json")}],
                [str(item.raw_artifact_id) for item in batch.documents if item.raw_artifact_id],
                idempotency_key=batch.idempotency_key,
            )
        except ValueError as exc:
            return _fail(self.name, str(exc), "unsupported_input")
        except Exception as exc:
            return _fail(self.name, str(exc), "storage_error", retryable=True)


class FetchCommunityDetailsTool:
    """Fetch a bounded homogeneous community batch through one domain Tool call."""

    name = "source.fetch_community_details"

    def __init__(
        self, adapters: SourceAdapterRegistry,
        role_repository: SQLiteRoleRepository,
    ) -> None:
        self.adapters, self.role_repository = adapters, role_repository

    def run(self, args: dict[str, Any]) -> ToolResult:
        try:
            requests = [
                SourceDetailRequest.model_validate(item)
                for item in args.get("requests", [])
            ]
            if not 1 <= len(requests) <= 10:
                return _fail(
                    self.name, "community detail batch size must be 1..10",
                    "unsupported_input",
                )
            source_ids = {item.source_id for item in requests}
            if len(source_ids) != 1 or any(
                item.channel != "experience" for item in requests
            ):
                return _fail(
                    self.name, "community detail batch must use one experience source",
                    "unsupported_input",
                )
            adapter = self.adapters.get(next(iter(source_ids)))
            if adapter is None or not callable(getattr(adapter, "fetch_detail", None)):
                return _fail(
                    self.name, "community detail adapter is unavailable",
                    "unsupported_input",
                )
            for request in requests:
                self.role_repository.save(
                    "source_detail_request", request,
                    idempotency_key=f"source-detail-request:{request.idempotency_key}",
                )
            fetch_many = getattr(adapter, "fetch_details", None)
            started_at = utc_now()
            if callable(fetch_many):
                batches = fetch_many(
                    requests, credential_ref=args.get("credential_ref"),
                    max_concurrency=min(int(args.get("max_concurrency", 2)), 2),
                )
            else:
                batches = [
                    adapter.fetch_detail(request, args.get("credential_ref"))
                    for request in requests
                ]
            completed_at = utc_now()
            if len(batches) != len(requests):
                return _fail(
                    self.name, "community detail adapter returned an incomplete batch",
                    "source_changed",
                )
            records: list[dict[str, Any]] = []
            evidence_ids: list[str] = []
            failures = []
            for request, batch in zip(requests, batches, strict=True):
                receipt = _save_receipt(
                    self.role_repository, str(args["run_id"]), adapter, batch,
                    bool(args.get("credential_ref")),
                    started_at=started_at, completed_at=completed_at,
                )
                for document in batch.documents:
                    self.role_repository.save(
                        "source_document", document,
                        idempotency_key=(
                            f"source-document:{document.source_document_id}"
                        ),
                    )
                    if document.raw_artifact_id:
                        evidence_ids.append(str(document.raw_artifact_id))
                records.append({
                    "detail_request_id": request.detail_request_id,
                    "batch": batch.model_dump(mode="json"),
                    "receipt": receipt.model_dump(mode="json"),
                })
                if batch.status not in {"success", "empty"}:
                    failures.append(batch)
            if failures:
                first = failures[0]
                return _fail(
                    self.name, first.error_type or first.status,
                    first.error_type or first.status,
                    retryable=any(item.retryable for item in failures),
                    needs_user_action=any(
                        item.needs_user_action for item in failures
                    ),
                    records=records, evidence_ids=evidence_ids,
                )
            return _ok(
                self.name, records, evidence_ids,
                request_count=len(requests),
                success_count=sum(
                    item.status == "success" for item in batches
                ),
            )
        except ValueError as exc:
            return _fail(self.name, str(exc), "unsupported_input")
        except Exception as exc:
            return _fail(self.name, str(exc), "storage_error", retryable=True)


class ValidateExternalSessionTool:
    name = "source.validate_external_session"

    def __init__(self, adapters: SourceAdapterRegistry) -> None:
        self.adapters = adapters

    def run(self, args: dict[str, Any]) -> ToolResult:
        source_id = str(args.get("source_id") or "")
        adapter = self.adapters.get(source_id)
        if adapter is None or adapter.capabilities.authorization_mode != "external_session":
            return _fail(self.name, "external session adapter is unavailable", "unsupported_input")
        status = str(adapter.authorization_status())
        if status == "external_session_available":
            return _ok(self.name, [], [], authorization_status=status)
        return _fail(
            self.name, status, status,
            needs_user_action=status in {"authentication_required", "risk_controlled"},
        )


class DiscoverJobDetailCandidatesTool:
    name = "source.discover_job_detail_candidates"

    def __init__(self, evidence_repository: EvidenceRepository, role_repository: SQLiteRoleRepository) -> None:
        self.evidence_repository, self.role_repository = evidence_repository, role_repository

    def run(self, args: dict[str, Any]) -> ToolResult:
        try:
            candidates: list[JobDetailCandidate] = []
            scope_value = args.get("search_scope")
            preferred_locations = (
                SearchScope.model_validate(scope_value).locations
                if scope_value is not None else []
            )
            for document_id in args.get("source_document_ids", []):
                document = self.role_repository.get(str(document_id), SourceDocument)
                if document is None or not document.raw_artifact_id:
                    continue
                fragments = self.evidence_repository.list_fragments(document.raw_artifact_id)
                for item in discover_job_detail_candidates(
                    document, fragments,
                    preferred_locations=preferred_locations,
                ):
                    candidates.append(self.role_repository.save(
                        "job_detail_candidate", item,
                        idempotency_key=f"job-detail-candidate:{item.candidate_id}",
                    ))
            return _ok(
                self.name, [item.model_dump(mode="json") for item in candidates],
                [item.candidate_id for item in candidates],
            )
        except Exception as exc:
            return _fail(self.name, str(exc), "normalization_error")


class DiscoverCommunityPostCandidatesTool:
    name = "source.discover_community_post_candidates"

    def __init__(self, evidence_repository: EvidenceRepository, role_repository: SQLiteRoleRepository) -> None:
        self.evidence_repository, self.role_repository = evidence_repository, role_repository

    def run(self, args: dict[str, Any]) -> ToolResult:
        try:
            candidates: list[CommunityPostCandidate] = []
            diagnostics: list[CommunitySearchDiagnostic] = []
            intended_by_query = dict(args.get("intended_by_query", {}))
            group_by_query = dict(args.get("group_by_query", {}))
            for document_id in args.get("source_document_ids", []):
                document = self.role_repository.get(str(document_id), SourceDocument)
                if document is None or not document.raw_artifact_id:
                    continue
                group = self.role_repository.get(str(group_by_query.get(document.query_id, "")), CompanyRoleGroup)
                fragments = self.evidence_repository.list_fragments(document.raw_artifact_id)
                values = discover_community_post_candidates(
                    document, fragments,
                    intended_document_types=list(intended_by_query.get(document.query_id, [])),
                    company_hint=group.company_display_name if group else None,
                    role_family_hint=group.role_family_id if group else None,
                )
                diagnostic = diagnose_community_search(document, fragments, values)
                diagnostics.append(self.role_repository.save(
                    "community_search_diagnostic", diagnostic,
                    idempotency_key=f"community-search-diagnostic:{diagnostic.diagnostic_id}",
                ))
                for item in values:
                    candidates.append(self.role_repository.save(
                        "community_post_candidate", item,
                        idempotency_key=f"community-post-candidate:{item.candidate_id}",
                    ))
            return _ok(
                self.name, [item.model_dump(mode="json") for item in candidates],
                [item.candidate_id for item in candidates],
                diagnostic_ids=[item.diagnostic_id for item in diagnostics],
                diagnostic_outcomes={item.diagnostic_id: item.outcome for item in diagnostics},
            )
        except Exception as exc:
            return _fail(self.name, str(exc), "normalization_error")


class VerifyOfficialTool:
    name = "source.verify_official_career"

    def __init__(self, adapters: SourceAdapterRegistry, role_repository: SQLiteRoleRepository) -> None:
        self.adapters, self.role_repository = adapters, role_repository

    def run(self, args: dict[str, Any]) -> ToolResult:
        try:
            plan = OfficialVerificationPlan.model_validate(args["plan"])
            source_id = str(args.get("source_id", "official_careers"))
            adapter = self.adapters.get(source_id)
            if adapter is None:
                return _fail(self.name, f"source adapter not registered: {source_id}", "adapter_required")
            batch = adapter.verify(plan, args.get("credential_ref"))
            receipt = _save_receipt(self.role_repository, str(args["run_id"]), adapter, batch, bool(args.get("credential_ref")))
            allowed = {"success", "empty", "official_not_found", "official_unavailable", "adapter_required"}
            if batch.status not in allowed:
                return _fail(self.name, batch.error_type or batch.status, batch.error_type or batch.status,
                             retryable=batch.retryable, needs_user_action=batch.needs_user_action,
                             records=[{"batch": batch.model_dump(mode="json"), "receipt": receipt.model_dump(mode="json")}],
                             evidence_ids=[str(item.raw_artifact_id) for item in batch.documents if item.raw_artifact_id])
            return _ok(self.name, [{"batch": batch.model_dump(mode="json"), "receipt": receipt.model_dump(mode="json")}],
                       [str(item.raw_artifact_id) for item in batch.documents if item.raw_artifact_id], verification_status=batch.status)
        except Exception as exc:
            return _fail(self.name, str(exc), "storage_error", retryable=True)


class ExtractSourceDocumentTool:
    name = "source.extract_document"

    def __init__(self, blob_store: BlobStore, evidence_repository: EvidenceRepository) -> None:
        self.blob_store, self.evidence_repository = blob_store, evidence_repository

    def run(self, args: dict[str, Any]) -> ToolResult:
        try:
            document = SourceDocument.model_validate(args["document"])
            extraction, fragments = extract_archived_document(document, blob_store=self.blob_store, repository=self.evidence_repository)
            return _ok(self.name, [{"extraction": extraction.model_dump(mode="json"), "fragments": [item.model_dump(mode="json") for item in fragments]}],
                       [item.fragment_id for item in fragments], parser_version=extraction.parser_version)
        except ValueError as exc:
            return _fail(self.name, str(exc), "parse_error")
        except Exception as exc:
            return _fail(self.name, str(exc), "storage_error", retryable=True)


class NormalizeJobTool:
    name = "source.normalize_job_posting"

    def __init__(self, evidence_repository: EvidenceRepository, role_repository: SQLiteRoleRepository) -> None:
        self.evidence_repository, self.role_repository = evidence_repository, role_repository

    def run(self, args: dict[str, Any]) -> ToolResult:
        try:
            document = SourceDocument.model_validate(args["document"])
            scope = SearchScope.model_validate(args["search_scope"])
            fragments = [self.evidence_repository.get_fragment(value) for value in args["fragment_ids"]]
            if any(item is None for item in fragments):
                raise ValueError("unknown source fragment")
            if document.channel == "employer_official":
                jobs, method, spec = parse_official_document(document, fragments, scope)  # type: ignore[arg-type]
                if spec is not None:
                    self.role_repository.save("official_adapter_spec", spec, idempotency_key=f"official-spec:{spec.spec_id}")
                if not jobs:
                    return _fail(self.name, method, method)
            else:
                jobs = normalize_job_document(document, fragments, scope)  # type: ignore[arg-type]
            saved = [self.role_repository.save("normalized_job", item, idempotency_key=f"normalized-job:{item.job_posting_id}") for item in jobs]
            return _ok(self.name, [item.model_dump(mode="json") for item in saved], [item.job_posting_id for item in saved])
        except Exception as exc:
            return _fail(self.name, str(exc), "normalization_error")


class NormalizeExperienceTool:
    name = "source.normalize_experience"

    def __init__(self, evidence_repository: EvidenceRepository, role_repository: SQLiteRoleRepository) -> None:
        self.evidence_repository, self.role_repository = evidence_repository, role_repository

    def run(self, args: dict[str, Any]) -> ToolResult:
        try:
            document = SourceDocument.model_validate(args["document"])
            fragments = [self.evidence_repository.get_fragment(value) for value in args["fragment_ids"]]
            if any(item is None for item in fragments):
                raise ValueError("unknown source fragment")
            records = normalize_experience_document(document, fragments, str(args["role_family"]))  # type: ignore[arg-type]
            saved = [self.role_repository.save("experience", item, idempotency_key=f"experience:{item.experience_record_id}") for item in records]
            return _ok(self.name, [item.model_dump(mode="json") for item in saved], [item.experience_record_id for item in saved])
        except Exception as exc:
            return _fail(self.name, str(exc), "normalization_error")


class ClassifyRoleFamilyTool:
    name = "source.classify_role_family"

    def __init__(self, role_repository: SQLiteRoleRepository) -> None:
        self.role_repository = role_repository

    def run(self, args: dict[str, Any]) -> ToolResult:
        try:
            scope = SearchScope.model_validate(args["search_scope"])
            memberships = []
            for job_id in args.get("job_ids", []):
                job = self.role_repository.get(str(job_id), NormalizedJobPosting)
                if job is None or job.source_type == "employer_official":
                    continue
                item = classify_role_family(job, scope)
                memberships.append(self.role_repository.save(
                    "role_family_membership", item,
                    idempotency_key=f"role-family-membership:{item.membership_id}",
                ))
            return _ok(
                self.name,
                [item.model_dump(mode="json") for item in memberships],
                [item.membership_id for item in memberships],
            )
        except Exception as exc:
            return _fail(self.name, str(exc), "role_family_classification_error")


class DeduplicateJobsTool:
    name = "source.deduplicate_jobs"
    def __init__(self, role_repository: SQLiteRoleRepository) -> None: self.role_repository = role_repository
    def run(self, args: dict[str, Any]) -> ToolResult:
        jobs = [self.role_repository.get(value, NormalizedJobPosting) for value in args.get("job_ids", [])]
        clusters, fuzzy = deduplicate_jobs([item for item in jobs if item is not None])
        saved = [self.role_repository.save("job_cluster", item, idempotency_key=f"cluster:{item.cluster_id}") for item in clusters]
        return _ok(self.name, [{"clusters": [item.model_dump(mode="json") for item in saved], "fuzzy_candidates": fuzzy}], [item.cluster_id for item in saved])


class DeduplicateExperienceTool:
    name = "source.deduplicate_experience"
    def __init__(self, role_repository: SQLiteRoleRepository) -> None: self.role_repository = role_repository
    def run(self, args: dict[str, Any]) -> ToolResult:
        records = [self.role_repository.get(value, ExperienceEvidenceRecord) for value in args.get("experience_ids", [])]
        unique = deduplicate_experience([item for item in records if item is not None])
        return _ok(self.name, [item.model_dump(mode="json") for item in unique], [item.experience_record_id for item in unique])


class LinkExperienceScopesTool:
    name = "source.link_experience_scopes"

    def __init__(self, role_repository: SQLiteRoleRepository) -> None:
        self.role_repository = role_repository

    def run(self, args: dict[str, Any]) -> ToolResult:
        try:
            scope = SearchScope.model_validate(args["search_scope"])
            records = [
                self.role_repository.get(str(value), ExperienceEvidenceRecord)
                for value in args.get("experience_ids", [])
            ]
            clusters = [
                self.role_repository.get(str(value), JobPostingCluster)
                for value in args.get("cluster_ids", [])
            ]
            jobs = {
                item.job_posting_id: item
                for item in self.role_repository.list("normalized_job", NormalizedJobPosting)
            }
            links = []
            for record in records:
                if record is None:
                    continue
                link = link_experience_scope(
                    record, scope, [item for item in clusters if item is not None], jobs,
                )
                links.append(self.role_repository.save(
                    "experience_scope_link", link,
                    idempotency_key=f"experience-scope-link:{link.experience_scope_link_id}",
                ))
            return _ok(
                self.name,
                [item.model_dump(mode="json") for item in links],
                [item.experience_scope_link_id for item in links],
            )
        except Exception as exc:
            return _fail(self.name, str(exc), "experience_scope_link_error")


class PlanOfficialVerificationTool:
    name = "source.plan_official_verification"
    def __init__(self, role_repository: SQLiteRoleRepository) -> None: self.role_repository = role_repository
    def run(self, args: dict[str, Any]) -> ToolResult:
        try:
            cluster = self.role_repository.get(str(args["cluster_id"]), JobPostingCluster)
            if cluster is None: raise ValueError("job cluster not found")
            jobs = {job.job_posting_id: job for job in self.role_repository.list("normalized_job", NormalizedJobPosting)}
            plan = plan_official_verification(cluster, jobs, company_domains=args.get("company_domains"))
            saved = self.role_repository.save("official_plan", plan, idempotency_key=f"official-plan:{plan.job_cluster_id}")
            return _ok(self.name, [saved.model_dump(mode="json")], [saved.verification_plan_id])
        except Exception as exc: return _fail(self.name, str(exc), "validation_error")


class LinkJobIdentityTool:
    name = "source.link_job_identity"
    def __init__(self, role_repository: SQLiteRoleRepository) -> None: self.role_repository = role_repository
    def run(self, args: dict[str, Any]) -> ToolResult:
        try:
            cluster = self.role_repository.get(str(args["cluster_id"]), JobPostingCluster)
            if cluster is None: raise ValueError("job cluster not found")
            discovery = self.role_repository.get(cluster.canonical_job_posting_id, NormalizedJobPosting)
            if discovery is None: raise ValueError("canonical job not found")
            official = [self.role_repository.get(value, NormalizedJobPosting) for value in args.get("official_job_ids", [])]
            link = link_job_identity(cluster, discovery, [item for item in official if item is not None], verification_status=args.get("verification_status"))
            saved = self.role_repository.save("identity_link", link, idempotency_key=f"identity-link:{link.job_identity_link_id}")
            return _ok(self.name, [saved.model_dump(mode="json")], [saved.job_identity_link_id])
        except Exception as exc: return _fail(self.name, str(exc), "identity_ambiguous")


class AssessRoleDetailEvidenceTool:
    name = "source.assess_role_detail_evidence"

    def __init__(self, evidence_repository: EvidenceRepository, role_repository: SQLiteRoleRepository) -> None:
        self.evidence_repository = evidence_repository
        self.role_repository = role_repository

    def run(self, args: dict[str, Any]) -> ToolResult:
        try:
            cluster = self.role_repository.get(str(args["cluster_id"]), JobPostingCluster)
            if cluster is None:
                raise ValueError("job cluster not found")
            relevant_job_ids = set(cluster.member_job_posting_ids)
            links = [
                self.role_repository.get(str(value), JobIdentityLink)
                for value in args.get("identity_link_ids", [])
            ]
            relevant_job_ids.update(
                item.official_job_posting_id
                for item in links
                if item is not None
                and item.job_cluster_id == cluster.cluster_id
                and item.status == "confirmed"
                and item.official_job_posting_id
            )
            jobs = [
                self.role_repository.get(value, NormalizedJobPosting)
                for value in relevant_job_ids
            ]
            receipt = assess_role_detail_evidence(
                scope_id=str(args["scope_id"]),
                cluster=cluster,
                jobs=[item for item in jobs if item is not None],
                documents=self.role_repository.list("source_document", SourceDocument),
                repository=self.evidence_repository,
            )
            saved = self.role_repository.save(
                "role_detail_evidence", receipt,
                idempotency_key=f"role-detail-evidence:{receipt.receipt_id}",
            )
            return _ok(self.name, [saved.model_dump(mode="json")], [saved.receipt_id])
        except Exception as exc:
            return _fail(self.name, str(exc), "detail_evidence_gate_error")


class ExtractRoleClaimsTool:
    name = "evidence.extract_role_claims"
    def __init__(self, evidence_repository: EvidenceRepository, role_repository: SQLiteRoleRepository) -> None:
        self.evidence_repository, self.role_repository = evidence_repository, role_repository
    def run(self, args: dict[str, Any]) -> ToolResult:
        saved, rejected = [], []
        for job_id in args.get("job_ids", []):
            job = self.role_repository.get(job_id, NormalizedJobPosting)
            if job is None: continue
            try: saved.extend(extract_recruitment_claims(job, owner_id=str(args["owner_id"]), repository=self.evidence_repository, subject_id=f"job:{job_id}"))
            except Exception as exc: rejected.append(str(exc))
        for record_id in args.get("experience_ids", []):
            record = self.role_repository.get(record_id, ExperienceEvidenceRecord)
            if record is None: continue
            try: saved.extend(extract_experience_claims(record, owner_id=str(args["owner_id"]), repository=self.evidence_repository, subject_id=str(args.get("experience_subject_id") or f"role_family:{record.role_family or 'unknown'}")))
            except Exception as exc: rejected.append(str(exc))
        if rejected and not saved: return _fail(self.name, "; ".join(rejected), "authority_violation")
        return _ok(self.name, [item.model_dump(mode="json") for item in saved], [item.claim_id for item in saved], rejected=rejected)


class ResolveJobFieldsTool:
    name = "source.resolve_job_fields"
    def __init__(self, evidence_repository: EvidenceRepository, role_repository: SQLiteRoleRepository) -> None:
        self.evidence_repository, self.role_repository = evidence_repository, role_repository
    def run(self, args: dict[str, Any]) -> ToolResult:
        link = self.role_repository.get(str(args["identity_link_id"]), JobIdentityLink)
        if link is None: return _fail(self.name, "identity link not found", "identity_ambiguous")
        claims = [self.evidence_repository.get_claim(value) for value in args.get("claim_ids", [])]
        resolutions = resolve_fields(link, [item for item in claims if item is not None], repository=self.evidence_repository)
        saved = [self.role_repository.save("field_resolution", item, idempotency_key=f"resolution:{item.field_resolution_id}") for item in resolutions]
        return _ok(self.name, [item.model_dump(mode="json") for item in saved], [item.field_resolution_id for item in saved])


class ProjectJobInstanceTool:
    name = "profile.project_job_instance"
    def __init__(self, evidence_repository: EvidenceRepository, profile_repository: ProfileRepository, role_repository: SQLiteRoleRepository) -> None:
        self.evidence_repository, self.role_repository = evidence_repository, role_repository
        self.projector = RoleProfileProjector(profile_repository)
    def run(self, args: dict[str, Any]) -> ToolResult:
        cluster = self.role_repository.get(str(args["cluster_id"]), JobPostingCluster)
        if cluster is None: return _fail(self.name, "job cluster not found", "validation_error")
        detail_receipts = [
            self.role_repository.get(value, RoleDetailEvidenceReceipt)
            for value in args.get("detail_evidence_receipt_ids", [])
        ]
        if not any(
            item is not None and item.job_cluster_id == cluster.cluster_id and item.status == "eligible"
            for item in detail_receipts
        ):
            return _fail(self.name, "detail_evidence_missing", "detail_evidence_missing")
        jobs = [self.role_repository.get(value, NormalizedJobPosting) for value in cluster.member_job_posting_ids]
        claims = [self.evidence_repository.get_claim(value) for value in args.get("claim_ids", [])]
        links = [self.role_repository.get(value, JobIdentityLink) for value in args.get("identity_link_ids", [])]
        resolutions = [self.role_repository.get(value, FieldResolution) for value in args.get("field_resolution_ids", [])]
        relevant_job_ids = set(cluster.member_job_posting_ids)
        relevant_links = [item for item in links if item is not None and item.job_cluster_id == cluster.cluster_id]
        relevant_link_ids = {item.job_identity_link_id for item in relevant_links}
        relevant_job_ids.update(item.official_job_posting_id for item in relevant_links if item.official_job_posting_id)
        jobs = [self.role_repository.get(value, NormalizedJobPosting) for value in relevant_job_ids]
        canonical_job = next((item for item in jobs if item is not None and item.job_posting_id == cluster.canonical_job_posting_id), None)
        scope_links = [
            self.role_repository.get(value, ExperienceScopeLink)
            for value in args.get("experience_scope_link_ids", [])
        ]
        links_by_record = {
            item.experience_record_id: item for item in scope_links if item is not None
        }
        experience_claims = []
        for item in claims:
            if item is None or not item.predicate.startswith("hiring_signal."):
                continue
            value = item.value if isinstance(item.value, dict) else {}
            scope_link = links_by_record.get(str(value.get("experience_record_id", "")))
            if canonical_job is None or scope_link is None or not experience_link_applies(
                scope_link, cluster_id=cluster.cluster_id, job=canonical_job,
            ):
                continue
            experience_claims.append(item)
        snapshot = self.projector.project_job_instance(
            cluster, [item for item in jobs if item is not None],
            [item for item in claims if item is not None and item.subject_id in {f"job:{job_id}" for job_id in relevant_job_ids}],
            relevant_links,
            [item for item in resolutions if item is not None and item.job_identity_link_id in relevant_link_ids],
            experience_claims,
        )
        return _ok(self.name, [{"snapshot_id": snapshot.snapshot_id, "supporting_claim_ids": snapshot.supporting_claim_ids}], [snapshot.snapshot_id])


class AggregateRoleFamilyTool:
    name = "profile.aggregate_role_family"
    def __init__(self, profile_repository: ProfileRepository) -> None: self.profile_repository, self.projector = profile_repository, RoleProfileProjector(profile_repository)
    def run(self, args: dict[str, Any]) -> ToolResult:
        scope = SearchScope.model_validate(args["search_scope"])
        snapshots = [self.profile_repository.get_profile(value) for value in args.get("snapshot_ids", [])]
        snapshot = self.projector.aggregate_role_family(scope, [item for item in snapshots if item is not None], thresholds=args.get("thresholds"))
        return _ok(self.name, [{"snapshot_id": snapshot.snapshot_id, "profile": snapshot.profile_data}], [snapshot.snapshot_id])


class BuildCompanyRoleGroupsTool:
    name = "role.build_company_role_groups"

    def __init__(self, role_repository: SQLiteRoleRepository) -> None:
        self.role_repository = role_repository

    def run(self, args: dict[str, Any]) -> ToolResult:
        try:
            scope = SearchScope.model_validate(args["search_scope"])
            clusters = [
                self.role_repository.get(str(value), JobPostingCluster)
                for value in args.get("cluster_ids", [])
            ]
            jobs = {
                item.job_posting_id: item
                for item in self.role_repository.list("normalized_job", NormalizedJobPosting)
            }
            groups = build_company_role_groups(
                scope, [item for item in clusters if item is not None], jobs
            )
            saved = [
                self.role_repository.save(
                    "company_role_group", item,
                    idempotency_key=f"company-role-group:{item.group_id}",
                )
                for item in groups
            ]
            return _ok(
                self.name, [item.model_dump(mode="json") for item in saved],
                [item.group_id for item in saved],
            )
        except Exception as exc:
            return _fail(self.name, str(exc), "validation_error")


class PlanCommunitySearchTool:
    name = "role.plan_community_search"

    def __init__(self, role_repository: SQLiteRoleRepository) -> None:
        self.role_repository = role_repository

    def run(self, args: dict[str, Any]) -> ToolResult:
        try:
            plans: list[CommunitySearchPlan] = []
            for group_id in args.get("group_ids", []):
                group = self.role_repository.get(str(group_id), CompanyRoleGroup)
                if group is None or group.status != "active":
                    continue
                plan = build_community_search_plan(
                    group, source_id=str(args.get("source_id", "nowcoder_experience")),
                    detail_budget=int(args.get("detail_budget", 3)),
                )
                plans.append(self.role_repository.save(
                    "community_search_plan", plan,
                    idempotency_key=f"community-search-plan:{plan.plan_id}",
                ))
            return _ok(
                self.name, [item.model_dump(mode="json") for item in plans],
                [item.plan_id for item in plans],
            )
        except Exception as exc:
            return _fail(self.name, str(exc), "validation_error")


class ClassifyCommunityDocumentsTool:
    name = "role.classify_community_documents"

    def __init__(
        self, evidence_repository: EvidenceRepository,
        role_repository: SQLiteRoleRepository,
        extractor: CommunityEvidenceExtractor | None,
    ) -> None:
        self.evidence_repository, self.role_repository = evidence_repository, role_repository
        self.extractor = extractor

    def run(self, args: dict[str, Any]) -> ToolResult:
        documents: list[CommunityEvidenceDocument] = []
        segments: list[CommunityEvidenceSegment] = []
        receipts = []
        llm_calls: list[dict[str, Any]] = []
        try:
            remaining_llm_calls = max(0, int(args.get("max_llm_calls", 10**6)))
            group_by_query = dict(args.get("group_by_query", {}))
            candidate_by_url = {
                item.detail_url: item
                for item in self.role_repository.list("community_post_candidate", CommunityPostCandidate)
            }
            for source_document_id in args.get("source_document_ids", []):
                source_document = self.role_repository.get(str(source_document_id), SourceDocument)
                if (
                    source_document is None
                    or source_document.document_kind != "experience_post"
                    or not source_document.raw_artifact_id
                ):
                    continue
                fragments = self.evidence_repository.list_fragments(source_document.raw_artifact_id)
                if not fragments:
                    continue
                source_fragment = next(
                    (
                        item for item in fragments
                        if item.metadata.get("parser_version")
                        == "nowcoder_main_body_v1"
                    ),
                    max(fragments, key=lambda item: len(item.text)),
                )
                body = ensure_community_body_fragment(
                    source_document, source_fragment, self.evidence_repository
                )
                group = self.role_repository.get(
                    str(group_by_query.get(source_document.query_id, "")), CompanyRoleGroup
                )
                candidate = candidate_by_url.get(source_document.source_url)
                intended = candidate.intended_document_types if candidate else []
                if self.extractor is not None:
                    extraction_text = source_fragment.text
                    try:
                        fixture_payload = __import__("json").loads(source_fragment.text)
                    except (ValueError, TypeError):
                        fixture_payload = None
                    is_fixture = (
                        isinstance(fixture_payload, dict)
                        and (
                            "community_extraction" in fixture_payload
                            or {"document_type", "segments"}.issubset(fixture_payload)
                        )
                    )
                    if not is_fixture:
                        extraction_text = body.text
                        if remaining_llm_calls <= 0:
                            continue
                    batch, calls = self.extractor.extract(
                        text=extraction_text,
                        company=group.company_display_name if group else None,
                        role_family=group.role_family_id if group else None,
                        intended_document_types=intended,
                        max_total_attempts=max(1, remaining_llm_calls),
                    )
                    llm_calls.extend(item.model_dump(mode="json") for item in calls)
                    remaining_llm_calls -= sum(
                        int(item.retry_count) + 1 for item in calls
                    )
                    provider = self.extractor.provider.name
                    model = self.extractor.config.model
                else:
                    payload = __import__("json").loads(source_fragment.text)
                    raw = payload.get("community_extraction") or _legacy_fixture_community_extraction(payload)
                    batch = CommunityExtractionBatch.model_validate(raw)
                    provider, model = "deterministic", "fixture-community-v1"
                evidence_document, receipt, extracted_segments = materialize_community_evidence(
                    document=source_document, body_fragment=body, extraction=batch,
                    repository=self.evidence_repository, group=group,
                    provider=provider, model=model,
                )
                documents.append(self.role_repository.save(
                    "community_evidence_document", evidence_document,
                    idempotency_key=f"community-evidence-document:{evidence_document.document_id}",
                ))
                receipts.append(self.role_repository.save(
                    "community_classification_receipt", receipt,
                    idempotency_key=f"community-classification-receipt:{receipt.receipt_id}",
                ))
                for item in extracted_segments:
                    segments.append(self.role_repository.save(
                        "community_evidence_segment", item,
                        idempotency_key=f"community-evidence-segment:{item.segment_id}",
                    ))
            return _ok(
                self.name,
                [{
                    "documents": [item.model_dump(mode="json") for item in documents],
                    "segments": [item.model_dump(mode="json") for item in segments],
                    "receipts": [item.model_dump(mode="json") for item in receipts],
                    "llm_calls": llm_calls,
                }],
                [
                    *[item.document_id for item in documents],
                    *[item.segment_id for item in segments],
                    *[item.receipt_id for item in receipts],
                ],
            )
        except StructuredOutputError as exc:
            failed_calls = [
                *llm_calls,
                *(item.model_dump(mode="json") for item in exc.call_records),
            ]
            return _fail(
                self.name, str(exc),
                exc.error_type if exc.error_type in {
                    "network_timeout", "rate_limited", "auth_required",
                    "provider_error",
                } else "llm_output_error",
                retryable=exc.retryable,
                records=[{
                    "documents": [], "segments": [], "receipts": [],
                    "llm_calls": failed_calls,
                }],
            )
        except Exception as exc:
            return _fail(
                self.name, str(exc), "llm_output_error",
                records=[{
                    "documents": [], "segments": [], "receipts": [],
                    "llm_calls": llm_calls,
                }],
            )


class EvaluateCommunitySearchTool:
    name = "role.evaluate_community_search"

    def __init__(
        self, evidence_repository: EvidenceRepository,
        role_repository: SQLiteRoleRepository,
        evaluator: CommunitySearchEvaluator | None,
    ) -> None:
        self.evidence_repository = evidence_repository
        self.role_repository = role_repository
        self.evaluator = evaluator

    def run(self, args: dict[str, Any]) -> ToolResult:
        calls: list[Any] = []
        try:
            evaluations = [
                item for value in args.get("source_evaluation_ids", [])
                if (item := self.role_repository.get(
                    str(value), CommunitySourceEvaluation
                )) is not None
            ]
            clusters = [
                item for value in args.get("cluster_ids", [])
                if (item := self.role_repository.get(
                    str(value), CommunityContentCluster
                )) is not None
            ]
            if not evaluations:
                return _fail(
                    self.name, "community source calibration is unavailable",
                    "validation_error",
                )
            segment_summaries: list[dict[str, str]] = []
            for cluster in clusters[:12]:
                for segment_id in cluster.member_segment_ids[:6]:
                    segment = self.role_repository.get(
                        segment_id, CommunityEvidenceSegment
                    )
                    if segment is None or segment.validation_status != "accepted":
                        continue
                    fragment = self.evidence_repository.get_fragment(
                        segment.fragment_id
                    )
                    if fragment is None:
                        continue
                    segment_summaries.append({
                        "segment_id": segment.segment_id,
                        "quote": fragment.text,
                        "limited_summary": segment.limited_summary,
                    })
            hard_floor_met = len(clusters) >= 3
            if self.evaluator is None or self.evaluator.provider.name == "mock":
                ranked = sorted(
                    evaluations,
                    key=lambda item: (
                        item.valid_body_rate + item.relevance_rate
                        + item.scope_hit_rate
                        + min(item.accepted_segment_count, 3) / 3
                        - item.duplicate_rate - item.failure_rate
                        - min(item.latency_ms / 30_000, 1.0) * 0.1
                        - min(item.search_cost_units, 1.0) * 0.05,
                        item.source_id,
                    ),
                    reverse=True,
                )
                available = [
                    item.source_id for item in ranked
                    if item.sampled_detail_count > 0
                ]
                allocation = (
                    {available[0]: 1.0} if len(available) == 1
                    else {available[0]: 0.7, available[1]: 0.3}
                    if len(available) >= 2 else {}
                )
                decision = CommunitySearchDecisionReceipt(
                    decision_id=(
                        "community-search-decision:deterministic:"
                        + __import__("hashlib").sha256(
                            ":".join(item.evaluation_id for item in ranked).encode()
                        ).hexdigest()[:24]
                    ),
                    run_id=str(args["run_id"]),
                    evidence_purpose=str(args["evidence_purpose"]),
                    source_evaluation_ids=[item.evaluation_id for item in ranked],
                    ranked_source_ids=[item.source_id for item in ranked],
                    budget_allocation=allocation,
                    proposed_keywords=[], cluster_ids=[item.cluster_id for item in clusters],
                    verdict="sufficient" if hard_floor_met else "insufficient",
                    hard_floor_met=hard_floor_met, provider="deterministic",
                    model="community-metric-fallback-v1",
                    reason_codes=[
                        "mock_or_unavailable_evaluator_fallback",
                        "hard_floor_met" if hard_floor_met else "hard_floor_not_met",
                    ],
                )
            else:
                decision, calls = self.evaluator.evaluate(
                    run_id=str(args["run_id"]),
                    evidence_purpose=str(args["evidence_purpose"]),
                    evaluations=evaluations, clusters=clusters,
                    segment_summaries=segment_summaries,
                    allowed_keywords=[
                        str(value) for value in args.get("allowed_keywords", [])
                    ],
                    hard_floor_met=hard_floor_met,
                    max_total_attempts=max(
                        1, int(args.get("max_llm_calls", 3))
                    ),
                )
            saved = self.role_repository.save(
                "community_search_decision_receipt", decision,
                idempotency_key=f"community-search-decision:{decision.decision_id}",
            )
            return _ok(
                self.name, [{
                    "decision": saved.model_dump(mode="json"),
                    "llm_calls": [item.model_dump(mode="json") for item in calls],
                }], [saved.decision_id],
            )
        except StructuredOutputError as exc:
            return _fail(
                self.name, str(exc),
                exc.error_type if exc.error_type in {
                    "network_timeout", "rate_limited", "auth_required",
                    "provider_error",
                } else "llm_output_error",
                retryable=exc.retryable,
                records=[{
                    "decision": None,
                    "llm_calls": [
                        item.model_dump(mode="json")
                        for item in exc.call_records
                    ],
                }],
            )
        except ValueError as exc:
            return _fail(
                self.name, str(exc), "policy_blocked",
                records=[{
                    "decision": None,
                    "llm_calls": [
                        item.model_dump(mode="json") for item in calls
                    ],
                }],
            )
        except Exception as exc:
            return _fail(
                self.name, str(exc), "llm_output_error",
                records=[{
                    "decision": None,
                    "llm_calls": [
                        item.model_dump(mode="json") for item in calls
                    ],
                }],
            )


class BuildOfficialEscalationReceiptsTool:
    name = "role.build_official_escalation_receipts"

    def __init__(self, role_repository: SQLiteRoleRepository) -> None:
        self.role_repository = role_repository

    def run(self, args: dict[str, Any]) -> ToolResult:
        try:
            requested = set(args.get("user_requested_job_ids", []))
            receipts: list[OfficialEscalationReceipt] = []
            plan_ids: list[str] = []
            jobs = {
                item.job_posting_id: item
                for item in self.role_repository.list("normalized_job", NormalizedJobPosting)
            }
            for cluster_id in args.get("cluster_ids", []):
                cluster = self.role_repository.get(str(cluster_id), JobPostingCluster)
                if cluster is None:
                    continue
                job = self.role_repository.get(
                    cluster.canonical_job_posting_id, NormalizedJobPosting
                )
                if job is None:
                    continue
                item = official_escalation_for_job(
                    cluster, job, user_requested=cluster.cluster_id in requested
                )
                receipts.append(self.role_repository.save(
                    "official_escalation_receipt", item,
                    idempotency_key=f"official-escalation-receipt:{item.receipt_id}",
                ))
                if item.trigger != "not_required":
                    plan = plan_official_verification(
                        cluster, jobs, company_domains=args.get("company_domains")
                    )
                    saved_plan = self.role_repository.save(
                        "official_plan", plan,
                        idempotency_key=f"official-plan:{plan.job_cluster_id}",
                    )
                    plan_ids.append(saved_plan.verification_plan_id)
            return _ok(
                self.name, [item.model_dump(mode="json") for item in receipts],
                [item.receipt_id for item in receipts],
                mandatory_official_verification_count=0,
                conditional_official_escalation_count=len(plan_ids),
                official_verification_plan_ids=plan_ids,
            )
        except Exception as exc:
            return _fail(self.name, str(exc), "validation_error")


class ProjectRoleIntelligenceTool:
    name = "profile.project_role_intelligence"

    def __init__(
        self, evidence_repository: EvidenceRepository,
        role_repository: SQLiteRoleRepository,
    ) -> None:
        self.evidence_repository, self.role_repository = evidence_repository, role_repository
        self.projector = DemandReputationProjector(role_repository, evidence_repository)

    def run(self, args: dict[str, Any]) -> ToolResult:
        try:
            scope = SearchScope.model_validate(args["search_scope"])
            clusters = [
                self.role_repository.get(str(value), JobPostingCluster)
                for value in args.get("eligible_cluster_ids", [])
            ]
            clusters = [item for item in clusters if item is not None]
            if not clusters:
                return _fail(self.name, "detail_evidence_missing", "detail_evidence_missing")
            jobs_by_id = {
                item.job_posting_id: item
                for item in self.role_repository.list("normalized_job", NormalizedJobPosting)
            }
            claims = [
                self.evidence_repository.get_claim(str(value))
                for value in args.get("claim_ids", [])
            ]
            detail_receipts = {
                item.job_cluster_id: item
                for value in args.get("detail_receipt_ids", [])
                if (item := self.role_repository.get(str(value), RoleDetailEvidenceReceipt)) is not None
            }
            escalation = {
                item.job_instance_id: item
                for value in args.get("official_escalation_receipt_ids", [])
                if (item := self.role_repository.get(str(value), OfficialEscalationReceipt)) is not None
            }
            segments = [
                item
                for value in args.get("segment_ids", [])
                if (item := self.role_repository.get(str(value), CommunityEvidenceSegment)) is not None
            ]
            documents = {
                item.document_id: item
                for item in self.role_repository.list(
                    "community_evidence_document", CommunityEvidenceDocument
                )
            }
            job_profiles: list[JobDemandProfile] = []
            for cluster in clusters:
                receipt = detail_receipts.get(cluster.cluster_id)
                if receipt is None:
                    continue
                job_profiles.append(self.projector.project_job_demand(
                    scope=scope, cluster=cluster, jobs_by_id=jobs_by_id,
                    claims=[item for item in claims if item is not None],
                    detail_receipt=receipt, segments=segments,
                    documents_by_id=documents,
                    escalation_receipt=escalation.get(cluster.cluster_id),
                ))
            if not job_profiles:
                return _fail(self.name, "detail_evidence_missing", "detail_evidence_missing")
            family = self.projector.project_family_demand(
                scope=scope, jobs=job_profiles, all_segments=segments,
                documents_by_id=documents,
            )
            jobs_by_company_family: dict[tuple[str, str], list[str]] = {}
            for item in job_profiles:
                jobs_by_company_family.setdefault(
                    (item.company_key, item.role_family_id), []
                ).append(item.job_instance_id)
            job_reputation, company_reputation = self.projector.project_reputation(
                segments=segments, documents_by_id=documents,
                jobs_by_company_family=jobs_by_company_family,
            )
            bundle = self.projector.build_bundle(
                scope=scope, family=family, jobs=job_profiles,
                job_reputation=job_reputation,
                company_reputation=company_reputation,
                raw_evidence_refs=list(args.get("raw_evidence_refs", [])),
                source_receipt_ids=list(args.get("source_receipt_ids", [])),
                segments=segments,
            )
            return _ok(self.name, [{
                "job_demand_profile_ids": [item.profile_id for item in job_profiles],
                "role_family_demand_profile_id": family.profile_id,
                "job_reputation_profile_ids": [item.profile_id for item in job_reputation],
                "company_reputation_profile_ids": [item.profile_id for item in company_reputation],
                "role_intelligence_bundle_id": bundle.bundle_id,
                "missing_sections": bundle.missing_sections,
            }], [
                *[item.profile_id for item in job_profiles], family.profile_id,
                *[item.profile_id for item in job_reputation],
                *[item.profile_id for item in company_reputation], bundle.bundle_id,
            ])
        except Exception as exc:
            return _fail(self.name, str(exc), "projection_error")


class ImportCredentialTool:
    name = "source.import_credential"
    def __init__(self, store: LocalCredentialStore) -> None: self.store = store
    def run(self, args: dict[str, Any]) -> ToolResult:
        try:
            ref = self.store.import_curl(source_id=str(args["source_id"]), path=str(args["path"]), name=str(args.get("name", "default")),
                                         allowed_path_roots=[str(value) for value in args.get("allowed_path_roots", [])])
            return _ok(self.name, [ref.model_dump(mode="json")])
        except Exception as exc: return _fail(self.name, str(exc), "credential_invalid")


class ValidateCredentialRefTool:
    name = "source.validate_credential_ref"
    def __init__(self, store: LocalCredentialStore) -> None: self.store = store
    def run(self, args: dict[str, Any]) -> ToolResult:
        try:
            ref = self.store.validate_ref(str(args["credential_ref"]), source_id=str(args["source_id"]))
            return _ok(self.name, [ref.model_dump(mode="json")])
        except Exception as exc: return _fail(self.name, str(exc), "credential_invalid")


def _save_receipt(
    repository: SQLiteRoleRepository, run_id: str, adapter: Any, batch: Any,
    auth_used: bool, *, started_at: Any | None = None,
    completed_at: Any | None = None,
) -> SourceRunReceipt:
    receipt = SourceRunReceipt(
        run_id=run_id, source_id=batch.source_id, channel=batch.channel, adapter_version=adapter.capabilities.adapter_version,
        query_ids=[batch.query_id], received_count=len(batch.documents), archived_count=sum(bool(item.raw_artifact_id) for item in batch.documents),
        artifact_ids=[str(item.raw_artifact_id) for item in batch.documents if item.raw_artifact_id],
        public_source_urls=[item.source_url for item in batch.documents if item.source_url.startswith("http")], auth_used=auth_used,
        status="completed" if batch.status in {"success", "empty", "official_not_found"} else "interrupted" if batch.needs_user_action else "failed",
        warnings=[value for value in [batch.error_type] if value],
        started_at=started_at or utc_now(), completed_at=completed_at or utc_now(),
    )
    # An auth-required batch can be replaced by a successful batch after the user
    # resumes with a credential ref. Preserve both state transitions while keeping
    # repeated runs of the same terminal status idempotent.
    return repository.save(
        "source_run_receipt", receipt,
        idempotency_key=f"receipt:{batch.idempotency_key}:{batch.status}",
    )


def _legacy_fixture_community_extraction(payload: dict[str, Any]) -> dict[str, Any]:
    """Replay old anonymous fixtures without preserving their mixed profile semantics."""

    signals = payload.get("signals") if isinstance(payload.get("signals"), dict) else {}
    scope = str(payload.get("scope_level") or "unknown")
    company = payload.get("company")
    definitions = {
        "written_exam": "written_exam",
        "interview": "interview_question",
        "project_preference": "project_preference",
        "tech_stack": "project_preference",
        "salary": "compensation",
        "work_context": "work_content",
    }
    segments: list[dict[str, Any]] = []
    for key, segment_type in definitions.items():
        for value in signals.get(key, []) if isinstance(signals.get(key), list) else []:
            text = str(value).strip()
            if not text:
                continue
            segments.append({
                "quote": text, "segment_type": segment_type,
                "scope_level": scope, "company": company,
                "polarity": "unknown", "limited_summary": text[:200],
                "confidence": float(payload.get("confidence", 0.7)),
            })
    kinds = {
        "interview" if item["segment_type"] in {
            "written_exam", "interview_process", "interview_question",
            "recruiter_feedback", "project_preference",
        } else "employment"
        for item in segments
    }
    document_type = (
        "mixed" if kinds == {"interview", "employment"}
        else "interview_experience" if kinds == {"interview"}
        else "employment_experience" if kinds == {"employment"}
        else "unknown"
    )
    return {"document_type": document_type, "segments": segments}


def build_role_profile_registry(*, blob_store: BlobStore, evidence_repository: EvidenceRepository,
                                profile_repository: ProfileRepository, role_repository: SQLiteRoleRepository,
                                adapters: SourceAdapterRegistry, credential_store: LocalCredentialStore,
                                community_extractor: CommunityEvidenceExtractor | None = None,
                                community_evaluator: CommunitySearchEvaluator | None = None) -> ToolRegistry:
    registry = ToolRegistry()
    for tool in [
        CollectSourceTool("source.discover_jobs", adapters, role_repository), CollectSourceTool("source.collect_experience", adapters, role_repository),
        FetchSourceDetailTool(adapters, role_repository),
        FetchCommunityDetailsTool(adapters, role_repository),
        ValidateExternalSessionTool(adapters),
        DiscoverJobDetailCandidatesTool(evidence_repository, role_repository),
        DiscoverCommunityPostCandidatesTool(evidence_repository, role_repository),
        VerifyOfficialTool(adapters, role_repository), ExtractSourceDocumentTool(blob_store, evidence_repository),
        NormalizeJobTool(evidence_repository, role_repository), NormalizeExperienceTool(evidence_repository, role_repository),
        ClassifyRoleFamilyTool(role_repository), DeduplicateJobsTool(role_repository), DeduplicateExperienceTool(role_repository),
        LinkExperienceScopesTool(role_repository), PlanOfficialVerificationTool(role_repository),
        LinkJobIdentityTool(role_repository), AssessRoleDetailEvidenceTool(evidence_repository, role_repository),
        ExtractRoleClaimsTool(evidence_repository, role_repository), ResolveJobFieldsTool(evidence_repository, role_repository),
        ProjectJobInstanceTool(evidence_repository, profile_repository, role_repository), AggregateRoleFamilyTool(profile_repository),
        BuildCompanyRoleGroupsTool(role_repository), PlanCommunitySearchTool(role_repository),
        ClassifyCommunityDocumentsTool(evidence_repository, role_repository, community_extractor),
        EvaluateCommunitySearchTool(
            evidence_repository, role_repository, community_evaluator,
        ),
        BuildOfficialEscalationReceiptsTool(role_repository),
        ProjectRoleIntelligenceTool(evidence_repository, role_repository),
        ImportCredentialTool(credential_store), ValidateCredentialRefTool(credential_store),
    ]: registry.register(tool)
    return registry
