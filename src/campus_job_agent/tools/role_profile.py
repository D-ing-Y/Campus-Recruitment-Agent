"""ToolRegistry boundary for v0.5 source collection and role projection."""

from __future__ import annotations

from typing import Any

from campus_job_agent.schemas import (
    ExperienceEvidenceRecord, FieldResolution, JobIdentityLink, JobPostingCluster,
    NormalizedJobPosting, OfficialVerificationPlan, SearchScope, SourceDocument,
    SourceQuery, SourceRunReceipt, ToolResult,
)
from campus_job_agent.sources.adapters import SourceAdapterRegistry
from campus_job_agent.sources.credential_store import LocalCredentialStore
from campus_job_agent.sources.processing import (
    deduplicate_experience, deduplicate_jobs, extract_archived_document,
    link_job_identity, normalize_experience_document, normalize_job_document,
    parse_official_document, plan_official_verification,
)
from campus_job_agent.sources.repository import SQLiteRoleRepository
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
            batch = adapter.collect(query, args.get("credential_ref"))
            receipt = _save_receipt(self.role_repository, str(args["run_id"]), adapter, batch, bool(args.get("credential_ref")))
            if batch.status not in {"success", "empty"}:
                return _fail(self.name, batch.error_type or batch.status, batch.error_type or batch.status,
                             retryable=batch.retryable, needs_user_action=batch.needs_user_action,
                             records=[{"batch": batch.model_dump(mode="json"), "receipt": receipt.model_dump(mode="json")}],
                             evidence_ids=[str(item.raw_artifact_id) for item in batch.documents if item.raw_artifact_id])
            return _ok(self.name, [{"batch": batch.model_dump(mode="json"), "receipt": receipt.model_dump(mode="json")}],
                       [str(item.raw_artifact_id) for item in batch.documents if item.raw_artifact_id], idempotency_key=batch.idempotency_key)
        except Exception as exc:
            return _fail(self.name, str(exc), "storage_error", retryable=True)


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
        experience_claims = []
        for item in claims:
            if item is None or not item.predicate.startswith("hiring_signal."):
                continue
            value = item.value if isinstance(item.value, dict) else {}
            scope_level = value.get("scope_level", "unknown")
            if scope_level == "unknown":
                continue
            if scope_level in {"company_only", "company_role", "job_instance"} and value.get("company") and canonical_job and value.get("company") != canonical_job.company:
                continue
            if scope_level == "job_instance" and value.get("role_title") and canonical_job and value.get("role_title") != canonical_job.role_title:
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


def _save_receipt(repository: SQLiteRoleRepository, run_id: str, adapter: Any, batch: Any, auth_used: bool) -> SourceRunReceipt:
    receipt = SourceRunReceipt(
        run_id=run_id, source_id=batch.source_id, channel=batch.channel, adapter_version=adapter.capabilities.adapter_version,
        query_ids=[batch.query_id], received_count=len(batch.documents), archived_count=sum(bool(item.raw_artifact_id) for item in batch.documents),
        artifact_ids=[str(item.raw_artifact_id) for item in batch.documents if item.raw_artifact_id],
        public_source_urls=[item.source_url for item in batch.documents if item.source_url.startswith("http")], auth_used=auth_used,
        status="completed" if batch.status in {"success", "empty", "official_not_found"} else "interrupted" if batch.needs_user_action else "failed",
        warnings=[value for value in [batch.error_type] if value],
    )
    # An auth-required batch can be replaced by a successful batch after the user
    # resumes with a credential ref. Preserve both state transitions while keeping
    # repeated runs of the same terminal status idempotent.
    return repository.save(
        "source_run_receipt", receipt,
        idempotency_key=f"receipt:{batch.idempotency_key}:{batch.status}",
    )


def build_role_profile_registry(*, blob_store: BlobStore, evidence_repository: EvidenceRepository,
                                profile_repository: ProfileRepository, role_repository: SQLiteRoleRepository,
                                adapters: SourceAdapterRegistry, credential_store: LocalCredentialStore) -> ToolRegistry:
    registry = ToolRegistry()
    for tool in [
        CollectSourceTool("source.discover_jobs", adapters, role_repository), CollectSourceTool("source.collect_experience", adapters, role_repository),
        VerifyOfficialTool(adapters, role_repository), ExtractSourceDocumentTool(blob_store, evidence_repository),
        NormalizeJobTool(evidence_repository, role_repository), NormalizeExperienceTool(evidence_repository, role_repository),
        DeduplicateJobsTool(role_repository), DeduplicateExperienceTool(role_repository), PlanOfficialVerificationTool(role_repository),
        LinkJobIdentityTool(role_repository), ExtractRoleClaimsTool(evidence_repository, role_repository), ResolveJobFieldsTool(evidence_repository, role_repository),
        ProjectJobInstanceTool(evidence_repository, profile_repository, role_repository), AggregateRoleFamilyTool(profile_repository),
        ImportCredentialTool(credential_store), ValidateCredentialRefTool(credential_store),
    ]: registry.register(tool)
    return registry
