"""CC Switch-shaped model provider profiles and local secret resolution."""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from campus_job_agent.llm import (
    build_llm_provider,
    infer_model_capabilities,
    infer_model_integration,
)
from campus_job_agent.schemas import (
    LLMConfig,
    LLMRequest,
    ModelCapabilities,
    ModelIntegration,
    StructuredOutputStrategy,
)
from campus_job_agent.sources import LocalCredentialStore


_PROFILE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_APP_TYPE = "campus_job_agent"


class ModelProfileError(RuntimeError):
    error_type = "contract_violation"


class ModelProviderSettings(BaseModel):
    provider: Literal["mock", "openai_compatible"]
    integration: ModelIntegration | None = None
    base_url: str | None = None
    model: str
    credential_ref: str | None = None
    timeout_seconds: float = Field(default=30.0, gt=0)
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    max_retries: int = Field(default=1, ge=0, le=5)
    structured_output_strategy: StructuredOutputStrategy = "auto"
    model_capabilities: ModelCapabilities | None = None


class ModelProviderProfile(BaseModel):
    """External shape follows CC Switch while secrets remain references."""

    model_config = ConfigDict(populate_by_name=True)

    profile_id: str = Field(alias="id")
    app_type: str = Field(default=_APP_TYPE, alias="appType")
    name: str
    settings_config: ModelProviderSettings = Field(alias="settingsConfig")
    website_url: str | None = Field(default=None, alias="websiteUrl")
    category: str = "custom"
    created_at: int = Field(alias="createdAt")
    sort_index: int = Field(default=0, alias="sortIndex")
    notes: str | None = None
    icon: str | None = None
    icon_color: str | None = Field(default=None, alias="iconColor")
    is_current: bool = Field(default=False, alias="isCurrent")


class SQLiteModelProfileRepository:
    """SQLite SSOT with an atomic, single-current provider invariant."""

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS providers (
                    id TEXT NOT NULL,
                    app_type TEXT NOT NULL,
                    name TEXT NOT NULL,
                    settings_config TEXT NOT NULL,
                    website_url TEXT,
                    category TEXT,
                    created_at INTEGER NOT NULL,
                    sort_index INTEGER NOT NULL DEFAULT 0,
                    notes TEXT,
                    icon TEXT,
                    icon_color TEXT,
                    is_current INTEGER NOT NULL DEFAULT 0 CHECK(is_current IN (0, 1)),
                    PRIMARY KEY (id, app_type)
                )
                """
            )
            connection.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_one_current_model_provider "
                "ON providers(app_type) WHERE is_current = 1"
            )
            count = connection.execute(
                "SELECT COUNT(*) FROM providers WHERE app_type = ?", (_APP_TYPE,)
            ).fetchone()[0]
            if count == 0:
                profile = _mock_profile()
                self._insert(connection, profile)

    def add(self, profile: ModelProviderProfile, *, activate: bool) -> ModelProviderProfile:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if connection.execute(
                "SELECT 1 FROM providers WHERE id = ? AND app_type = ?",
                (profile.profile_id, _APP_TYPE),
            ).fetchone():
                raise ModelProfileError(f"model provider already exists: {profile.profile_id}")
            if activate:
                connection.execute(
                    "UPDATE providers SET is_current = 0 WHERE app_type = ?",
                    (_APP_TYPE,),
                )
            stored = profile.model_copy(update={"is_current": activate})
            self._insert(connection, stored)
        return self.get(profile.profile_id)  # type: ignore[return-value]

    def list(self) -> list[ModelProviderProfile]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM providers WHERE app_type = ? "
                "ORDER BY sort_index, created_at, id",
                (_APP_TYPE,),
            ).fetchall()
        return [self._from_row(row) for row in rows]

    def get(self, profile_id: str) -> ModelProviderProfile | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM providers WHERE id = ? AND app_type = ?",
                (profile_id, _APP_TYPE),
            ).fetchone()
        return None if row is None else self._from_row(row)

    def get_current(self) -> ModelProviderProfile | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM providers WHERE app_type = ? AND is_current = 1",
                (_APP_TYPE,),
            ).fetchone()
        return None if row is None else self._from_row(row)

    def set_current(self, profile_id: str) -> ModelProviderProfile:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if connection.execute(
                "SELECT 1 FROM providers WHERE id = ? AND app_type = ?",
                (profile_id, _APP_TYPE),
            ).fetchone() is None:
                raise ModelProfileError(f"model provider not found: {profile_id}")
            connection.execute(
                "UPDATE providers SET is_current = 0 WHERE app_type = ?",
                (_APP_TYPE,),
            )
            connection.execute(
                "UPDATE providers SET is_current = 1 WHERE id = ? AND app_type = ?",
                (profile_id, _APP_TYPE),
            )
        return self.get(profile_id)  # type: ignore[return-value]

    def update(self, profile: ModelProviderProfile) -> ModelProviderProfile:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if connection.execute(
                "SELECT 1 FROM providers WHERE id = ? AND app_type = ?",
                (profile.profile_id, _APP_TYPE),
            ).fetchone() is None:
                raise ModelProfileError(
                    f"model provider not found: {profile.profile_id}"
                )
            connection.execute(
                """
                UPDATE providers SET name = ?, settings_config = ?, website_url = ?,
                    category = ?, notes = ?, icon = ?, icon_color = ?
                WHERE id = ? AND app_type = ?
                """,
                (
                    profile.name, profile.settings_config.model_dump_json(),
                    profile.website_url, profile.category, profile.notes,
                    profile.icon, profile.icon_color, profile.profile_id, _APP_TYPE,
                ),
            )
        return self.get(profile.profile_id)  # type: ignore[return-value]

    def delete(self, profile_id: str) -> ModelProviderProfile:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM providers WHERE id = ? AND app_type = ?",
                (profile_id, _APP_TYPE),
            ).fetchone()
            if row is None:
                raise ModelProfileError(f"model provider not found: {profile_id}")
            profile = self._from_row(row)
            if profile.is_current:
                raise ModelProfileError("current model provider cannot be removed")
            connection.execute(
                "DELETE FROM providers WHERE id = ? AND app_type = ?",
                (profile_id, _APP_TYPE),
            )
        return profile

    @staticmethod
    def _insert(connection: sqlite3.Connection, profile: ModelProviderProfile) -> None:
        connection.execute(
            "INSERT INTO providers VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                profile.profile_id, profile.app_type, profile.name,
                profile.settings_config.model_dump_json(), profile.website_url,
                profile.category, profile.created_at, profile.sort_index,
                profile.notes, profile.icon, profile.icon_color,
                int(profile.is_current),
            ),
        )

    @staticmethod
    def _from_row(row: sqlite3.Row) -> ModelProviderProfile:
        return ModelProviderProfile(
            id=row["id"], appType=row["app_type"], name=row["name"],
            settingsConfig=json.loads(row["settings_config"]),
            websiteUrl=row["website_url"], category=row["category"],
            createdAt=row["created_at"], sortIndex=row["sort_index"],
            notes=row["notes"], icon=row["icon"], iconColor=row["icon_color"],
            isCurrent=bool(row["is_current"]),
        )


class ModelProfileService:
    def __init__(
        self,
        repository: SQLiteModelProfileRepository,
        secret_store: LocalCredentialStore,
    ) -> None:
        self.repository = repository
        self.secret_store = secret_store

    def suggest_profile_id(self, preset: str) -> str:
        base_id = {
            "deepseek": "deepseek-main",
            "openai-compatible": "openai-compatible",
            "mock": "mock-local",
        }.get(preset.strip().lower())
        if base_id is None:
            raise ModelProfileError(f"unknown model provider preset: {preset}")
        candidate = base_id
        suffix = 2
        while self.repository.get(candidate) is not None:
            candidate = f"{base_id}-{suffix}"
            suffix += 1
        return candidate

    def add(
        self, *, profile_id: str, name: str, preset: str,
        base_url: str | None = None, model: str | None = None,
        api_key: str | None = None, activate: bool = False,
        timeout_seconds: float | None = None,
        category: str | None = None, website_url: str | None = None,
        notes: str | None = None, icon: str | None = None,
    ) -> ModelProviderProfile:
        _validate_identity(profile_id, name)
        if self.repository.get(profile_id) is not None:
            raise ModelProfileError(f"model provider already exists: {profile_id}")
        settings, defaults = _settings_for_preset(
            preset, profile_id=profile_id, base_url=base_url,
            model=model, api_key=api_key, timeout_seconds=timeout_seconds,
        )
        secret_written = False
        if settings.credential_ref:
            self.secret_store.save_api_key(profile_id=profile_id, api_key=str(api_key))
            secret_written = True
        profile = ModelProviderProfile(
            id=profile_id, name=name, settingsConfig=settings,
            websiteUrl=website_url or defaults.get("website_url"),
            category=category or str(defaults["category"]),
            createdAt=int(datetime.now(UTC).timestamp() * 1000),
            sortIndex=len(self.repository.list()), notes=notes,
            icon=icon or str(defaults["icon"]), isCurrent=False,
        )
        try:
            return self.repository.add(profile, activate=activate)
        except Exception:
            if secret_written:
                self.secret_store.delete_secret(settings.credential_ref)
            raise

    def list_safe(self) -> list[dict[str, Any]]:
        return [self.safe(item) for item in self.repository.list()]

    def show_safe(self, profile_id: str) -> dict[str, Any]:
        profile = self.repository.get(profile_id)
        if profile is None:
            raise ModelProfileError(f"model provider not found: {profile_id}")
        return self.safe(profile)

    def use(self, profile_id: str) -> ModelProviderProfile:
        profile = self.repository.get(profile_id)
        if profile is None:
            raise ModelProfileError(f"model provider not found: {profile_id}")
        if (
            profile.settings_config.provider != "mock"
            and not self.secret_store.secret_exists(
                str(profile.settings_config.credential_ref)
            )
        ):
            raise ModelProfileError("model provider API key reference is unavailable")
        return self.repository.set_current(profile_id)

    def edit(
        self, profile_id: str, *, name: str | None = None,
        base_url: str | None = None, model: str | None = None,
        api_key: str | None = None, category: str | None = None,
        timeout_seconds: float | None = None,
        website_url: str | None = None, notes: str | None = None,
        icon: str | None = None,
    ) -> ModelProviderProfile:
        profile = self.repository.get(profile_id)
        if profile is None:
            raise ModelProfileError(f"model provider not found: {profile_id}")
        settings_payload = profile.settings_config.model_dump()
        settings_payload.update({
            key: value for key, value in {
                "base_url": base_url, "model": model,
                "timeout_seconds": timeout_seconds,
            }.items() if value is not None
        })
        try:
            settings = ModelProviderSettings.model_validate(settings_payload)
        except ValueError as exc:
            raise ModelProfileError("model provider settings are invalid") from exc
        if settings.provider == "openai_compatible":
            if not settings.base_url or not settings.model:
                raise ModelProfileError(
                    "base URL and model are required for an OpenAI-compatible provider"
                )
            if not settings.credential_ref:
                settings = settings.model_copy(update={
                    "credential_ref": f"local-secret://llm/{profile_id}"
                })
        elif api_key is not None:
            raise ModelProfileError("mock provider does not accept an API key")
        updated = profile.model_copy(update={
            "name": name.strip() if name is not None else profile.name,
            "settings_config": settings,
            "category": category if category is not None else profile.category,
            "website_url": (
                website_url if website_url is not None else profile.website_url
            ),
            "notes": notes if notes is not None else profile.notes,
            "icon": icon if icon is not None else profile.icon,
        })
        _validate_identity(updated.profile_id, updated.name)
        old_api_key: str | None = None
        secret_rotated = api_key is not None
        if secret_rotated:
            credential_ref = str(settings.credential_ref)
            if self.secret_store.secret_exists(credential_ref):
                old_api_key = self.secret_store.resolve_api_key(credential_ref)
            self.secret_store.save_api_key(profile_id=profile_id, api_key=str(api_key))
        try:
            return self.repository.update(updated)
        except Exception:
            if secret_rotated:
                if old_api_key is None:
                    self.secret_store.delete_secret(str(settings.credential_ref))
                else:
                    self.secret_store.save_api_key(
                        profile_id=profile_id, api_key=old_api_key
                    )
            raise

    def remove(self, profile_id: str) -> ModelProviderProfile:
        profile = self.repository.delete(profile_id)
        if profile.settings_config.credential_ref:
            self.secret_store.delete_secret(profile.settings_config.credential_ref)
        return profile

    def resolve_llm_config(self, profile_id: str | None = None) -> LLMConfig:
        profile = (
            self.repository.get(profile_id)
            if profile_id is not None
            else self.repository.get_current()
        )
        if profile is None:
            raise ModelProfileError("no current model provider is configured")
        settings = profile.settings_config
        api_key = None
        if settings.provider != "mock":
            if not settings.credential_ref:
                raise ModelProfileError("model provider has no API key reference")
            api_key = self.secret_store.resolve_api_key(settings.credential_ref)
        return LLMConfig(
            provider=settings.provider, base_url=settings.base_url,
            api_key=api_key, model=settings.model,
            timeout_seconds=settings.timeout_seconds,
            temperature=settings.temperature, max_retries=settings.max_retries,
            integration=settings.integration,
            structured_output_strategy=settings.structured_output_strategy,
            model_capabilities=settings.model_capabilities,
        )

    def test(self, profile_id: str) -> dict[str, Any]:
        profile = self.repository.get(profile_id)
        if profile is None:
            raise ModelProfileError(f"model provider not found: {profile_id}")
        config = self.resolve_llm_config(profile_id)
        provider = build_llm_provider(config)
        provider.generate(LLMRequest(
            messages=[
                {"role": "system", "content": "Return one small JSON object."},
                {"role": "user", "content": "Provider health check; no business data."},
            ],
            model=config.model, temperature=0.0,
            timeout_seconds=min(config.timeout_seconds, 15.0),
        ))
        integration = infer_model_integration(config)
        capabilities = infer_model_capabilities(config, integration)
        return {
            "profile_id": profile.profile_id, "provider": config.provider,
            "integration": integration,
            "model": config.model, "status": "available",
            "structured_output_strategy": config.structured_output_strategy,
            "capabilities": capabilities.model_dump(mode="json"),
            "business_material_sent": False,
        }

    def safe(self, profile: ModelProviderProfile) -> dict[str, Any]:
        result = profile.model_dump(mode="json", by_alias=True)
        ref = profile.settings_config.credential_ref
        result["api_key_present"] = bool(ref and self.secret_store.secret_exists(ref))
        return result


def _mock_profile() -> ModelProviderProfile:
    return ModelProviderProfile(
        id="mock-default", name="Mock (Offline)",
        settingsConfig=ModelProviderSettings(
            provider="mock", integration="mock", model="mock-goal-parser"
        ),
        category="official", createdAt=0, sortIndex=0,
        notes="Built-in deterministic offline provider", icon="terminal",
        isCurrent=True,
    )


def _validate_identity(profile_id: str, name: str) -> None:
    if not _PROFILE_ID.fullmatch(profile_id):
        raise ModelProfileError(
            "profile id must contain only letters, digits, dot, underscore, or hyphen"
        )
    if not name.strip():
        raise ModelProfileError("model provider name is required")


def _settings_for_preset(
    preset: str, *, profile_id: str, base_url: str | None,
    model: str | None, api_key: str | None, timeout_seconds: float | None,
) -> tuple[ModelProviderSettings, dict[str, str]]:
    normalized = preset.strip().lower()
    if normalized == "mock":
        return ModelProviderSettings(
            provider="mock", integration="mock", model=model or "mock-goal-parser",
            timeout_seconds=timeout_seconds or 30.0,
        ), {"category": "official", "icon": "terminal", "website_url": ""}
    if not api_key or not api_key.strip():
        raise ModelProfileError("API key is required for a non-mock model provider")
    if normalized == "deepseek":
        return ModelProviderSettings(
            provider="openai_compatible",
            integration="deepseek",
            base_url=base_url or "https://api.deepseek.com",
            model=model or "deepseek-v4-flash",
            credential_ref=f"local-secret://llm/{profile_id}",
            timeout_seconds=timeout_seconds or 90.0,
        ), {
            "category": "cn_official", "icon": "deepseek",
            "website_url": "https://platform.deepseek.com/",
        }
    if normalized == "openai-compatible":
        if not base_url or not model:
            raise ModelProfileError(
                "base URL and model are required for a custom OpenAI-compatible provider"
            )
        return ModelProviderSettings(
            provider="openai_compatible", integration="openai_compatible", base_url=base_url,
            model=model, credential_ref=f"local-secret://llm/{profile_id}",
            timeout_seconds=timeout_seconds or 30.0,
        ), {"category": "custom", "icon": "server", "website_url": ""}
    raise ModelProfileError(f"unknown model provider preset: {preset}")
