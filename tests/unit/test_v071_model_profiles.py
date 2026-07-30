from __future__ import annotations

import json
import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from campus_job_agent.runtime.model_profiles import (
    ModelProfileError,
    ModelProfileService,
    SQLiteModelProfileRepository,
)
from campus_job_agent.sources import LocalCredentialStore


def _service(tmp_path):
    repository = SQLiteModelProfileRepository(tmp_path / "model-profiles.sqlite3")
    secrets = LocalCredentialStore(tmp_path / "secrets")
    return ModelProfileService(repository, secrets), repository, secrets


def test_repository_seeds_cc_switch_shaped_mock_and_switches_atomically(tmp_path) -> None:
    service, repository, _ = _service(tmp_path)
    current = repository.get_current()
    assert current is not None
    assert current.profile_id == "mock-default"
    assert current.is_current is True
    assert current.model_dump(mode="json", by_alias=True) == {
        "id": "mock-default",
        "appType": "campus_job_agent",
        "name": "Mock (Offline)",
        "settingsConfig": {
                "provider": "mock",
                "integration": "mock",
                "base_url": None,
            "model": "mock-goal-parser",
            "credential_ref": None,
            "timeout_seconds": 30.0,
            "temperature": 0.0,
                "max_retries": 1,
                "structured_output_strategy": "auto",
                "model_capabilities": None,
        },
        "websiteUrl": None,
        "category": "official",
        "createdAt": current.created_at,
        "sortIndex": 0,
        "notes": "Built-in deterministic offline provider",
        "icon": "terminal",
        "iconColor": None,
        "isCurrent": True,
    }

    service.add(
        profile_id="deepseek-main", name="DeepSeek Main", preset="deepseek",
        model="deepseek-v4-flash", api_key="secret-never-in-profile", activate=True,
    )
    assert repository.get_current().profile_id == "deepseek-main"
    assert sum(item.is_current for item in repository.list()) == 1
    stored = repository.get("deepseek-main")
    assert stored is not None
    assert stored.settings_config.timeout_seconds == 90.0
    serialized = json.dumps(stored.model_dump(mode="json", by_alias=True))
    assert "secret-never-in-profile" not in serialized
    assert stored.settings_config.credential_ref == "local-secret://llm/deepseek-main"
    assert b"secret-never-in-profile" not in repository.database_path.read_bytes()

    updated = service.edit(
        "deepseek-main", model="deepseek-v4-pro", api_key="rotated-secret",
        timeout_seconds=120,
    )
    assert updated.settings_config.model == "deepseek-v4-pro"
    assert updated.settings_config.timeout_seconds == 120.0
    assert service.resolve_llm_config().api_key == "rotated-secret"
    assert b"rotated-secret" not in repository.database_path.read_bytes()

    with pytest.raises(ModelProfileError, match="current"):
        service.remove("deepseek-main")


def test_api_key_store_is_private_and_resolves_only_at_model_boundary(tmp_path) -> None:
    service, _, secrets = _service(tmp_path)
    profile = service.add(
        profile_id="custom", name="Custom", preset="openai-compatible",
        base_url="https://llm.example/v1", model="example-model",
        api_key="top-secret-value", activate=True,
    )
    assert profile.settings_config.credential_ref == "local-secret://llm/custom"
    assert secrets.resolve_api_key(profile.settings_config.credential_ref) == "top-secret-value"
    files = list((tmp_path / "secrets").glob("*.json"))
    assert len(files) == 1
    assert oct(os.stat(tmp_path / "secrets").st_mode & 0o777) == "0o700"
    assert oct(os.stat(files[0]).st_mode & 0o777) == "0o600"


def test_profile_validation_and_current_transaction_rollback(tmp_path) -> None:
    service, repository, _ = _service(tmp_path)
    with pytest.raises(ModelProfileError, match="(?i)api key"):
        service.add(
            profile_id="missing-key", name="Missing", preset="deepseek",
            model="deepseek-v4-flash", api_key=None, activate=True,
        )
    assert repository.get("missing-key") is None
    assert repository.get_current().profile_id == "mock-default"

    with sqlite3.connect(repository.database_path) as connection:
        connection.execute(
            "CREATE TRIGGER reject_switch BEFORE UPDATE OF is_current ON providers "
            "WHEN NEW.id = 'broken' AND NEW.is_current = 1 BEGIN "
            "SELECT RAISE(ABORT, 'injected switch failure'); END"
        )
    service.add(
        profile_id="broken", name="Broken", preset="mock", activate=False,
    )
    with pytest.raises(sqlite3.DatabaseError, match="injected switch failure"):
        repository.set_current("broken")
    assert repository.get_current().profile_id == "mock-default"


def test_edit_rolls_back_rotated_secret_when_repository_update_fails(tmp_path) -> None:
    service, repository, secrets = _service(tmp_path)
    profile = service.add(
        profile_id="deepseek-main", name="DeepSeek", preset="deepseek",
        api_key="old-secret", activate=True,
    )
    with sqlite3.connect(repository.database_path) as connection:
        connection.execute(
            "CREATE TRIGGER reject_edit BEFORE UPDATE OF settings_config ON providers "
            "WHEN NEW.id = 'deepseek-main' BEGIN "
            "SELECT RAISE(ABORT, 'injected edit failure'); END"
        )
    with pytest.raises(sqlite3.DatabaseError, match="injected edit failure"):
        service.edit(
            "deepseek-main", model="deepseek-v4-pro", api_key="new-secret"
        )
    assert secrets.resolve_api_key(str(profile.settings_config.credential_ref)) == "old-secret"
    assert repository.get("deepseek-main").settings_config.model == "deepseek-v4-flash"

def test_service_builds_llm_config_from_active_profile_without_env_file(tmp_path) -> None:
    service, _, _ = _service(tmp_path)
    service.add(
        profile_id="deepseek-active", name="DeepSeek", preset="deepseek",
        model="deepseek-v4-pro", api_key="runtime-secret", activate=True,
    )
    config = service.resolve_llm_config()
    assert config.provider == "openai_compatible"
    assert config.base_url == "https://api.deepseek.com"
    assert config.model == "deepseek-v4-pro"
    assert config.api_key == "runtime-secret"


def test_concurrent_switches_preserve_exactly_one_current_provider(tmp_path) -> None:
    service, repository, _ = _service(tmp_path)
    service.add(profile_id="mock-a", name="Mock A", preset="mock")
    service.add(profile_id="mock-b", name="Mock B", preset="mock")

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(repository.set_current, ("mock-a", "mock-b")))

    assert {item.profile_id for item in results} == {"mock-a", "mock-b"}
    profiles = repository.list()
    assert sum(item.is_current for item in profiles) == 1
    assert repository.get_current().profile_id in {"mock-a", "mock-b"}
