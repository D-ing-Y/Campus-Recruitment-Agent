from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
CLI = Path(os.environ.get(
    "CAMPUS_AGENT_TEST_CLI", str(REPO_ROOT / ".venv" / "bin" / "campus-agent")
))


def _run(tmp_path: Path, *args: str, stdin: str | None = None):
    env = dict(os.environ)
    env["CAMPUS_AGENT_DATA_ROOT"] = str(tmp_path / "data")
    env.pop("CAMPUS_AGENT_LLM_PROVIDER", None)
    env.pop("CAMPUS_AGENT_MODEL_PROFILE", None)
    env.pop("OPENAI_API_KEY", None)
    env.pop("OPENAI_BASE_URL", None)
    env.pop("OPENAI_MODEL", None)
    return subprocess.run(
        [str(CLI), *args], cwd=tmp_path, env=env, input=stdin,
        text=True, capture_output=True, check=False,
    )


def test_model_provider_crud_switch_doctor_and_secret_redaction(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text(
        "CAMPUS_AGENT_LLM_PROVIDER=openai_compatible\n"
        "OPENAI_API_KEY=must-not-be-loaded\n",
        encoding="utf-8",
    )
    added = _run(
        tmp_path, "--json", "model", "add",
        "--preset", "deepseek", "--id", "deepseek-main",
        "--name", "DeepSeek Main", "--model", "deepseek-v4-flash",
        "--api-key-stdin", "--activate", stdin="cli-secret-value\n",
    )
    assert added.returncode == 0, added.stderr
    assert "cli-secret-value" not in added.stdout + added.stderr
    payload = json.loads(added.stdout)
    assert payload["result"]["id"] == "deepseek-main"
    assert payload["result"]["isCurrent"] is True
    assert payload["result"]["api_key_present"] is True

    listed = _run(tmp_path, "--json", "model", "list")
    assert listed.returncode == 0
    profiles = json.loads(listed.stdout)["result"]["providers"]
    assert {item["id"] for item in profiles} == {"mock-default", "deepseek-main"}
    assert "cli-secret-value" not in listed.stdout

    edited = _run(
        tmp_path, "--json", "model", "edit", "deepseek-main",
        "--model", "deepseek-v4-pro", "--timeout-seconds", "120",
        "--api-key-stdin",
        stdin="rotated-cli-secret\n",
    )
    assert edited.returncode == 0, edited.stderr
    assert "rotated-cli-secret" not in edited.stdout + edited.stderr
    assert json.loads(edited.stdout)["result"]["settingsConfig"]["model"] == "deepseek-v4-pro"
    assert json.loads(edited.stdout)["result"]["settingsConfig"]["timeout_seconds"] == 120.0

    doctor = _run(tmp_path, "--json", "doctor")
    checks = json.loads(doctor.stdout)["checks"]
    assert checks["llm"]["profile_id"] == "deepseek-main"
    assert checks["llm"]["provider"] == "openai_compatible"
    assert checks["llm"]["model"] == "deepseek-v4-pro"
    assert checks["llm"]["api_key_present"] is True
    assert "cli-secret-value" not in doctor.stdout
    assert "rotated-cli-secret" not in doctor.stdout
    assert "must-not-be-loaded" not in doctor.stdout

    switched = _run(tmp_path, "--json", "model", "use", "mock-default")
    assert switched.returncode == 0
    assert json.loads(switched.stdout)["result"]["isCurrent"] is True
    tested = _run(tmp_path, "--json", "model", "test", "mock-default")
    assert tested.returncode == 0
    assert json.loads(tested.stdout)["result"]["status"] == "available"

    active_remove = _run(tmp_path, "--json", "model", "remove", "mock-default")
    assert active_remove.returncode == 3
    assert json.loads(active_remove.stdout)["errors"][0]["error_type"] == "contract_violation"
    removed = _run(tmp_path, "--json", "model", "remove", "deepseek-main")
    assert removed.returncode == 0
    assert "cli-secret-value" not in removed.stdout + removed.stderr


def test_explicit_cli_ui_opens_project_shell_with_only_model_available(tmp_path: Path) -> None:
    ui = _run(tmp_path, "ui", stdin="1\n1\n0\n2\n0\n")
    assert ui.returncode == 0, ui.stderr
    assert "Campus Job Agent" in ui.stdout
    assert "Model Configuration" in ui.stdout
    assert "mock-default" in ui.stdout
    assert "not available yet" in ui.stdout
    assert "API key" not in ui.stdout


def test_non_tty_without_command_does_not_block_and_key_argument_is_forbidden(tmp_path: Path) -> None:
    guide = _run(tmp_path)
    assert guide.returncode == 0
    assert "session start" in guide.stdout
    forbidden = _run(
        tmp_path, "--json", "model", "add", "--preset", "deepseek",
        "--id", "unsafe", "--name", "Unsafe", "--api-key", "must-not-work",
    )
    assert forbidden.returncode == 2
    assert "must-not-work" not in forbidden.stdout + forbidden.stderr
