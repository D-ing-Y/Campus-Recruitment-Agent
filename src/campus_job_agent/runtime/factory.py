"""Production composition root for the local v0.7.1 CLI runtime."""

from __future__ import annotations

import os
import sqlite3
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from campus_job_agent.evidence import ClaimExtractorService
from campus_job_agent.llm import (
    LLMCache, LLMConfigError, LLMProviderError, build_llm_provider,
    infer_model_integration,
    load_llm_config,
)
from campus_job_agent.schemas import LLMConfig
from campus_job_agent.integrations import (
    MediaCrawlerSidecarClient,
    MediaCrawlerSidecarConfig,
    SocialBridgeError,
)
from campus_job_agent.sources import (
    CommunityEvidenceExtractor, LocalCredentialStore, MeituanOfficialCareersAdapter, NowcoderExperienceAdapter,
    OfficialCareersAdapter, SourceAdapterRegistry, SQLiteRoleRepository,
    XiaohongshuExperienceAdapter, ZhaopinJobsAdapter,
)
from campus_job_agent.storage import LocalBlobStore, SQLiteRepository
from campus_job_agent.tools import build_candidate_profile_registry, build_role_profile_registry
from campus_job_agent.tools.registry import ToolRegistry
from campus_job_agent.workflows.candidate_profile import CandidateProfileGraphRuntime, open_sqlite_checkpointer
from campus_job_agent.workflows.career_intent import (
    CareerIntentGraphRuntime,
    IntentCandidateExtractor,
    IntentEvidenceIngestor,
    SQLiteIntentRepository,
)
from campus_job_agent.workflows.feedback import FeedbackGraphRuntime, SQLiteFeedbackRepository
from campus_job_agent.workflows.feedback.ingestion import FeedbackIngestor
from campus_job_agent.workflows.feedback.service import FeedbackService
from campus_job_agent.workflows.preparation_plan import PreparationPlanGraphRuntime, SQLitePreparationRepository
from campus_job_agent.workflows.preparation_plan.service import PreparationService
from campus_job_agent.workflows.profile_matching import ProfileMatchingGraphRuntime, SQLiteMatchingRepository
from campus_job_agent.workflows.profile_matching.service import MatchingService
from campus_job_agent.workflows.role_profile import RoleProfileGraphRuntime
from campus_job_agent.workflows.resume_evidence import (
    ResumeEvidenceExtractor,
    ResumeEvidenceGraphRuntime,
)

from campus_job_agent.runtime.artifacts import RunArtifactWriter
from campus_job_agent.runtime.candidate import CandidateApplicationService
from campus_job_agent.runtime.intent import IntentApplicationService
from campus_job_agent.runtime.resume import ResumeApplicationService
from campus_job_agent.runtime.role import RoleApplicationService
from campus_job_agent.runtime.model_profiles import (
    ModelProfileError,
    ModelProfileService,
    SQLiteModelProfileRepository,
)
from campus_job_agent.runtime.sessions import SQLiteSessionRepository, SessionService


@dataclass(frozen=True)
class RuntimePaths:
    data_root: Path
    run_root: Path
    blob_root: Path
    cache_root: Path
    database_root: Path
    checkpoint_root: Path
    credential_root: Path

    @classmethod
    def resolve(cls, data_root: str | Path | None = None) -> "RuntimePaths":
        if data_root is not None:
            root = Path(data_root).expanduser().resolve()
        elif os.getenv("CAMPUS_AGENT_DATA_ROOT"):
            root = Path(os.environ["CAMPUS_AGENT_DATA_ROOT"]).expanduser().resolve()
        else:
            root = (_locate_project_root() / "data").resolve()
        return cls(
            data_root=root,
            run_root=root / "runs",
            blob_root=root / "evidence" / "blobs",
            cache_root=root / "cache",
            database_root=root / "db",
            checkpoint_root=root / "checkpoints",
            credential_root=root / "cache" / "credentials",
        )

    def ensure(self) -> None:
        for path in (
            self.data_root, self.run_root, self.blob_root, self.cache_root,
            self.database_root, self.checkpoint_root, self.credential_root,
        ):
            path.mkdir(parents=True, exist_ok=True)


@dataclass
class Runtime:
    paths: RuntimePaths
    owner_id: str
    blob_store: LocalBlobStore
    evidence_repository: SQLiteRepository
    profile_repository: SQLiteRepository
    role_repository: SQLiteRoleRepository
    matching_repository: SQLiteMatchingRepository
    preparation_repository: SQLitePreparationRepository
    feedback_repository: SQLiteFeedbackRepository
    intent_repository: SQLiteIntentRepository
    session_repository: SQLiteSessionRepository
    session_service: SessionService
    llm_config: Any
    llm_config_error: str | None
    llm_provider: Any
    llm_cache: LLMCache
    structured_output: Any
    tool_registry: ToolRegistry
    source_adapter_registry: SourceAdapterRegistry
    credential_resolver: LocalCredentialStore
    model_profile_repository: SQLiteModelProfileRepository
    model_profile_service: ModelProfileService
    event_sink: RunArtifactWriter
    artifact_writer: RunArtifactWriter
    application_services: dict[str, Any]
    checkpoint_paths: dict[str, Path]

    @contextmanager
    def open_workflow(self, workflow: str) -> Iterator[Any]:
        if workflow not in self.checkpoint_paths:
            raise ValueError(f"unknown workflow: {workflow}")
        with open_sqlite_checkpointer(self.checkpoint_paths[workflow]) as checkpointer:
            if workflow == "candidate":
                yield CandidateProfileGraphRuntime(
                    registry=self.tool_registry,
                    evidence_repository=self.evidence_repository,
                    profile_repository=self.profile_repository,
                    checkpointer=checkpointer,
                )
            elif workflow == "resume":
                yield ResumeEvidenceGraphRuntime(
                    blob_store=self.blob_store,
                    repository=self.evidence_repository,
                    extractor=ResumeEvidenceExtractor(
                        self.llm_config, self.llm_provider, self.llm_cache
                    ),
                    checkpointer=checkpointer,
                )
            elif workflow == "intent":
                yield CareerIntentGraphRuntime(
                    ingestor=IntentEvidenceIngestor(
                        blob_store=self.blob_store,
                        evidence_repository=self.evidence_repository,
                    ),
                    extractor=IntentCandidateExtractor(
                        self.llm_config, self.llm_provider, self.llm_cache
                    ),
                    evidence_repository=self.evidence_repository,
                    profile_repository=self.profile_repository,
                    intent_repository=self.intent_repository,
                    session_repository=self.session_repository,
                    checkpointer=checkpointer,
                )
            elif workflow == "role":
                yield RoleProfileGraphRuntime(
                    registry=self.tool_registry,
                    evidence_repository=self.evidence_repository,
                    profile_repository=self.profile_repository,
                    role_repository=self.role_repository,
                    checkpointer=checkpointer,
                )
            elif workflow == "matching":
                yield ProfileMatchingGraphRuntime(
                    evidence_repository=self.evidence_repository,
                    profile_repository=self.profile_repository,
                    matching_repository=self.matching_repository,
                    checkpointer=checkpointer,
                )
            elif workflow == "preparation":
                yield PreparationPlanGraphRuntime(
                    profile_repository=self.profile_repository,
                    matching_repository=self.matching_repository,
                    preparation_repository=self.preparation_repository,
                    checkpointer=checkpointer,
                )
            else:
                yield FeedbackGraphRuntime(
                    blob_store=self.blob_store,
                    evidence_repository=self.evidence_repository,
                    profile_repository=self.profile_repository,
                    matching_repository=self.matching_repository,
                    preparation_repository=self.preparation_repository,
                    feedback_repository=self.feedback_repository,
                    checkpointer=checkpointer,
                )

    def doctor(self) -> dict[str, Any]:
        writable = {}
        for name, path in {
            "data_root": self.paths.data_root, "run_root": self.paths.run_root,
            "blob_root": self.paths.blob_root, "cache_root": self.paths.cache_root,
        }.items():
            writable[name] = os.access(path, os.W_OK)
        checkpoint_ok = True
        checkpoint_error = None
        try:
            for path in self.checkpoint_paths.values():
                with sqlite3.connect(path) as connection:
                    connection.execute("SELECT 1")
        except sqlite3.Error as exc:
            checkpoint_ok = False
            checkpoint_error = type(exc).__name__
        llm_complete = self.llm_config_error is None and (
            self.llm_config.provider == "mock" or bool(
            self.llm_config.base_url and self.llm_config.api_key and self.llm_config.model
            )
        )
        return {
            "python": sys.version.split()[0],
            "package_version": "0.7.0",
            "paths": {
                "data_root": str(self.paths.data_root), "run_root": str(self.paths.run_root),
                "blob_root": str(self.paths.blob_root), "cache_root": str(self.paths.cache_root),
            },
            "writable": writable,
            "sqlite_checkpointer": {"available": checkpoint_ok, "error_type": checkpoint_error},
            "llm": {
                "profile_id": (
                    self.model_profile_repository.get_current().profile_id
                    if self.model_profile_repository.get_current() else None
                ),
                "provider": self.llm_config.provider, "model": self.llm_config.model,
                "configuration_complete": llm_complete, "api_key_present": bool(self.llm_config.api_key),
                "error_type": "config_error" if self.llm_config_error else None,
            },
            "credential_store": {"exists": self.paths.credential_root.is_dir(), "payload_visible": False},
            "source_adapters": self.source_adapter_registry.capabilities(),
            "console_script": str(Path(sys.argv[0]).resolve()),
            "legacy_cli": "legacy-mini-runtime",
            "feature_stage": "v0.7.1-wp1.3.1",
        }


class RuntimeFactory:
    def __init__(self, *, data_root: str | Path | None = None) -> None:
        self.paths = RuntimePaths.resolve(data_root)

    def build(self, *, owner_id: str = "local-user") -> Runtime:
        self.paths.ensure()
        evidence = SQLiteRepository(self.paths.database_root / "evidence.sqlite3")
        role = SQLiteRoleRepository(self.paths.database_root / "role.sqlite3")
        matching = SQLiteMatchingRepository(self.paths.database_root / "matching.sqlite3")
        preparation = SQLitePreparationRepository(self.paths.database_root / "preparation.sqlite3")
        feedback = SQLiteFeedbackRepository(self.paths.database_root / "feedback.sqlite3")
        intent = SQLiteIntentRepository(self.paths.database_root / "intent.sqlite3")
        sessions = SQLiteSessionRepository(self.paths.database_root / "sessions.sqlite3")
        blob = LocalBlobStore(self.paths.blob_root)
        credential_store = LocalCredentialStore(self.paths.credential_root)
        model_profiles = SQLiteModelProfileRepository(
            self.paths.database_root / "model_profiles.sqlite3"
        )
        model_profile_service = ModelProfileService(model_profiles, credential_store)

        llm_config_error = None
        try:
            explicit_profile = os.getenv("CAMPUS_AGENT_MODEL_PROFILE")
            if explicit_profile:
                llm_config = model_profile_service.resolve_llm_config(explicit_profile)
            elif "CAMPUS_AGENT_LLM_PROVIDER" in os.environ:
                llm_config = load_llm_config()
            else:
                llm_config = model_profile_service.resolve_llm_config()
        except (LLMConfigError, ModelProfileError, ValueError):
            llm_config_error = "configured provider is incomplete"
            current = model_profiles.get_current()
            llm_config = LLMConfig(
                provider=(
                    current.settings_config.provider if current
                    else "openai_compatible"
                ),
                base_url=(
                    current.settings_config.base_url if current
                    else os.getenv("OPENAI_BASE_URL") or None
                ),
                api_key=None,
                model=(
                    current.settings_config.model if current
                    else os.getenv("OPENAI_MODEL") or "unconfigured"
                ),
            )
        llm_config = llm_config.model_copy(
            update={"cache_dir": str(self.paths.cache_root / "llm")}
        )
        llm_cache = LLMCache(str(self.paths.cache_root / "llm"))
        if llm_config_error:
            provider = _UnavailableLLMProvider()
        elif infer_model_integration(llm_config) == "mock":
            provider = build_llm_provider(llm_config)
        else:
            # Configuration, session and object-inspection commands must not
            # import and construct a network SDK.  The concrete provider is
            # created only when a workflow actually sends an LLM request.
            provider = _LazyLLMProvider(llm_config)
        extractor = ClaimExtractorService(llm_config, provider, llm_cache)

        live_enabled = os.getenv("CAMPUS_AGENT_ENABLE_LIVE_SOURCES", "").lower() in {"1", "true", "yes"}
        adapter_kwargs = {
            "blob_store": blob, "evidence_repository": evidence, "role_repository": role,
            "owner_id": owner_id, "live_enabled": live_enabled,
            "credential_resolver": credential_store.resolve,
        }
        adapters = SourceAdapterRegistry()
        social_bridge = None
        if os.getenv("CAMPUS_AGENT_MEDIACRAWLER_ROOT"):
            try:
                social_bridge = MediaCrawlerSidecarClient(
                    MediaCrawlerSidecarConfig.from_env()
                )
            except SocialBridgeError:
                # Optional sidecar configuration remains observable through the
                # registered adapter's adapter_required status.
                social_bridge = None
        for adapter in (
            ZhaopinJobsAdapter(**adapter_kwargs),
            NowcoderExperienceAdapter(**adapter_kwargs),
            XiaohongshuExperienceAdapter(
                bridge_client=social_bridge, **adapter_kwargs,
            ),
            OfficialCareersAdapter(**adapter_kwargs),
            MeituanOfficialCareersAdapter(**adapter_kwargs),
        ):
            adapters.register(adapter)

        candidate_tools = build_candidate_profile_registry(
            blob_store=blob, repository=evidence, profile_repository=evidence,
            claim_extractor=extractor,
        )
        role_tools = build_role_profile_registry(
            blob_store=blob, evidence_repository=evidence, profile_repository=evidence,
            role_repository=role, adapters=adapters, credential_store=credential_store,
            community_extractor=CommunityEvidenceExtractor(llm_config, provider, llm_cache),
        )
        tools = ToolRegistry()
        for registry in (candidate_tools, role_tools):
            for tool in registry.values():
                tools.register(tool)

        ingestor = FeedbackIngestor(
            blob_store=blob, evidence_repository=evidence, feedback_repository=feedback
        )
        services = {
            "session": SessionService(sessions),
            "matching": MatchingService(
                profile_repository=evidence, evidence_repository=evidence,
                matching_repository=matching,
            ),
            "preparation": PreparationService(
                profile_repository=evidence, matching_repository=matching,
                preparation_repository=preparation,
            ),
            "feedback": FeedbackService(
                ingestor=ingestor, evidence_repository=evidence, profile_repository=evidence,
                feedback_repository=feedback, preparation_repository=preparation,
                matching_repository=matching,
            ),
        }
        writer = RunArtifactWriter(self.paths.run_root, software_version="0.7.0")
        checkpoints = {
            name: self.paths.checkpoint_root / f"{name}.sqlite3"
            for name in ("resume", "candidate", "intent", "role", "matching", "preparation", "feedback")
        }
        runtime = Runtime(
            paths=self.paths, owner_id=owner_id, blob_store=blob,
            evidence_repository=evidence, profile_repository=evidence,
            role_repository=role, matching_repository=matching,
            preparation_repository=preparation, feedback_repository=feedback,
            intent_repository=intent,
            session_repository=sessions, session_service=services["session"],
            llm_config=llm_config, llm_config_error=llm_config_error,
            llm_provider=provider, llm_cache=llm_cache,
            structured_output=extractor, tool_registry=tools,
            source_adapter_registry=adapters, credential_resolver=credential_store,
            model_profile_repository=model_profiles,
            model_profile_service=model_profile_service,
            event_sink=writer, artifact_writer=writer, application_services=services,
            checkpoint_paths=checkpoints,
        )
        runtime.application_services["candidate"] = CandidateApplicationService(runtime)
        runtime.application_services["resume"] = ResumeApplicationService(runtime)
        runtime.application_services["intent"] = IntentApplicationService(runtime)
        runtime.application_services["role"] = RoleApplicationService(runtime)
        runtime.application_services["model"] = model_profile_service
        return runtime


def _locate_project_root() -> Path:
    candidates = [Path(__file__).resolve(), Path(sys.prefix).resolve()]
    for candidate in candidates:
        for parent in candidate.parents:
            pyproject = parent / "pyproject.toml"
            if pyproject.is_file() and "campus-job-agent" in pyproject.read_text(encoding="utf-8"):
                return parent
    fallback = os.getenv("XDG_DATA_HOME")
    return Path(fallback).expanduser().resolve() / "campus-job-agent" if fallback else Path.home() / ".local" / "share" / "campus-job-agent"


class _UnavailableLLMProvider:
    name = "openai_compatible"

    def generate(self, request: Any) -> Any:
        raise LLMProviderError("LLM provider configuration is incomplete")


class _LazyLLMProvider:
    """Delay optional network SDK construction until the model boundary."""

    def __init__(self, config: LLMConfig) -> None:
        self.config = config
        self.integration = infer_model_integration(config)
        self.name = f"langchain_{self.integration}"
        self._provider: Any | None = None

    def generate(self, request: Any) -> Any:
        if self._provider is None:
            self._provider = build_llm_provider(self.config)
        return self._provider.generate(request)
