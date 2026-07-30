from __future__ import annotations

from types import SimpleNamespace

from campus_job_agent.cli_ui import _read_default_keys, _read_yes_no, run_cli_ui
from campus_job_agent.runtime.model_profiles import (
    ModelProfileService,
    SQLiteModelProfileRepository,
)
from campus_job_agent.sources import LocalCredentialStore


def _service(tmp_path):
    return ModelProfileService(
        SQLiteModelProfileRepository(tmp_path / "model-profiles.sqlite3"),
        LocalCredentialStore(tmp_path / "secrets"),
    )


def test_deepseek_add_accepts_dim_defaults_until_secret_and_allows_overrides(
    tmp_path,
) -> None:
    service = _service(tmp_path)
    answers = iter([
        "1", "2", "1", "", "", "", "", "", "",  # DeepSeek + defaults
        "2", "1", "my-deepseek", "My DeepSeek", "https://llm.example/v1",
        "custom-model", "75", "n",  # second DeepSeek: user overrides
        "0", "0",
    ])
    secrets = iter(["first-secret", "second-secret"])
    prompts: list[str] = []
    output: list[str] = []

    def read(prompt: str) -> str:
        prompts.append(prompt)
        return next(answers)

    def read_secret(prompt: str) -> str:
        prompts.append(prompt)
        return next(secrets)

    assert run_cli_ui(
        SimpleNamespace(model_profile_service=service),
        read=read,
        write=output.append,
        read_secret=read_secret,
        style_defaults=True,
    ) == 0

    defaulted = service.repository.get("deepseek-main")
    assert defaulted is not None
    assert defaulted.name == "DeepSeek"
    assert defaulted.settings_config.base_url == "https://api.deepseek.com"
    assert defaulted.settings_config.model == "deepseek-v4-flash"
    assert defaulted.settings_config.timeout_seconds == 90.0
    assert defaulted.is_current is True

    overridden = service.repository.get("my-deepseek")
    assert overridden is not None
    assert overridden.name == "My DeepSeek"
    assert overridden.settings_config.base_url == "https://llm.example/v1"
    assert overridden.settings_config.model == "custom-model"
    assert overridden.settings_config.timeout_seconds == 75.0
    assert overridden.is_current is False

    prompt_text = "".join(prompts)
    assert "Preset: " in prompt_text
    assert "Preset : \033[2m1\033[0m" not in prompt_text
    assert "Provider ID : \033[2mdeepseek-main\033[0m" in prompt_text
    assert "Display name : \033[2mDeepSeek\033[0m" in prompt_text
    assert "Base URL : \033[2mhttps://api.deepseek.com\033[0m" in prompt_text
    assert "Model : \033[2mdeepseek-v4-flash\033[0m" in prompt_text
    assert "Request timeout (seconds) : \033[2m90\033[0m" in prompt_text
    assert "Activate now? [Y/n] " in prompt_text
    assert "first-secret" not in prompt_text + "".join(output)


def test_inline_default_disappears_on_first_input_and_enter_accepts_default() -> None:
    output: list[str] = []
    keys = iter(["m", "y", "-", "i", "d", "\r"])

    value = _read_default_keys(
        "Provider ID", "deepseek-main", read_key=lambda: next(keys),
        write_raw=output.append,
    )

    assert value == "my-id"
    rendered = "".join(output)
    assert "Provider ID : \033[2mdeepseek-main\033[0m" in rendered
    first_typed_frame = rendered.split("\033[2K")[-5]
    assert "Provider ID : m" in first_typed_frame
    assert "deepseek-main" not in first_typed_frame

    accepted: list[str] = []
    assert _read_default_keys(
        "Display name", "DeepSeek", read_key=lambda: "\r",
        write_raw=accepted.append,
    ) == "DeepSeek"
    assert "Display name : \033[2mDeepSeek\033[0m" in "".join(accepted)


def test_linux_yes_no_prompt_uses_uppercase_as_enter_default() -> None:
    prompts: list[str] = []

    assert _read_yes_no(
        lambda prompt: prompts.append(prompt) or "", "Activate now?",
        default=True,
    ) is True
    assert _read_yes_no(
        lambda prompt: prompts.append(prompt) or "", "Rotate API key?",
        default=False,
    ) is False
    assert _read_yes_no(lambda _: "N", "Activate now?", default=True) is False
    assert _read_yes_no(lambda _: "Y", "Remove?", default=False) is True
    assert prompts == ["Activate now? [Y/n] ", "Rotate API key? [y/N] "]


def test_generated_provider_id_avoids_existing_ids(tmp_path) -> None:
    service = _service(tmp_path)
    service.add(
        profile_id="deepseek-main", name="First", preset="deepseek",
        api_key="first-secret",
    )

    assert service.suggest_profile_id("deepseek") == "deepseek-main-2"
    service.add(
        profile_id="deepseek-main-2", name="Second", preset="deepseek",
        api_key="second-secret",
    )
    assert service.suggest_profile_id("deepseek") == "deepseek-main-3"
