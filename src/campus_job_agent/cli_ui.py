"""Project CLI UI shell backed by the same application services as one-shot commands."""

from __future__ import annotations

import getpass
import json
import sys
from typing import Any, Callable


_DIM = "\033[2m"
_RESET = "\033[0m"


def run_cli_ui(
    runtime: Any,
    *,
    read: Callable[[str], str] = input,
    write: Callable[[str], None] = print,
    read_secret: Callable[[str], str] = getpass.getpass,
    style_defaults: bool | None = None,
) -> int:
    if style_defaults is None:
        style_defaults = sys.stdout.isatty()
    while True:
        write("")
        write("Campus Job Agent")
        write("================")
        write("1. Model Configuration")
        write("2. Workflow Workspace [not available yet]")
        write("3. Data & Privacy [not available yet]")
        write("4. Diagnostics [not available yet]")
        write("0. Exit")
        choice = read("Select: ").strip()
        if choice == "0":
            write("Goodbye.")
            return 0
        if choice == "1":
            _model_menu(
                runtime.model_profile_service,
                read,
                write,
                read_secret,
                style_defaults,
            )
            continue
        if choice in {"2", "3", "4"}:
            write("This area is not available yet.")
            continue
        write("Invalid selection.")


def _model_menu(
    service: Any,
    read: Callable[[str], str],
    write: Callable[[str], None],
    read_secret: Callable[[str], str],
    style_defaults: bool,
) -> None:
    while True:
        write("")
        write("Model Configuration")
        write("-------------------")
        write("1. List providers")
        write("2. Add provider")
        write("3. Edit provider")
        write("4. Switch provider")
        write("5. Show provider")
        write("6. Test provider")
        write("7. Remove provider")
        write("0. Back")
        choice = read("Select: ").strip()
        if choice == "0":
            return
        try:
            if choice == "1":
                _write_profiles(service.list_safe(), write)
            elif choice == "2":
                _interactive_add(
                    service, read, write, read_secret, style_defaults
                )
            elif choice == "3":
                _interactive_edit(
                    service, read, write, read_secret, style_defaults
                )
            elif choice == "4":
                profile = service.use(read("Provider ID: ").strip())
                write(f"Active provider: {profile.profile_id}")
            elif choice == "5":
                value = service.show_safe(read("Provider ID: ").strip())
                write(json.dumps(value, ensure_ascii=False, indent=2))
            elif choice == "6":
                value = service.test(read("Provider ID: ").strip())
                write(json.dumps(value, ensure_ascii=False, indent=2))
            elif choice == "7":
                profile_id = read("Provider ID: ").strip()
                if _read_yes_no(
                    read, f"Remove {profile_id}?", default=False
                ):
                    service.remove(profile_id)
                    write(f"Removed provider: {profile_id}")
                else:
                    write("Cancelled.")
            else:
                write("Invalid selection.")
        except Exception as exc:
            write(f"Operation failed: {exc}")


def _interactive_add(
    service: Any,
    read: Callable[[str], str],
    write: Callable[[str], None],
    read_secret: Callable[[str], str],
    style_defaults: bool,
) -> None:
    write("Presets: 1. DeepSeek  2. OpenAI-compatible  3. Mock")
    preset_choice = read("Preset: ").strip()
    preset = {
        "1": "deepseek", "2": "openai-compatible", "3": "mock",
    }.get(preset_choice)
    if preset is None:
        write("Invalid preset.")
        return
    write("Press Enter to accept each dim field default; API key is required.")
    defaults = {
        "deepseek": {
            "name": "DeepSeek",
            "base_url": "https://api.deepseek.com",
            "model": "deepseek-v4-flash",
            "timeout": "90",
        },
        "openai-compatible": {"name": "OpenAI-compatible", "timeout": "30"},
        "mock": {
            "name": "Mock (Local)", "model": "mock-goal-parser", "timeout": "30",
        },
    }[preset]
    profile_id = _read_default(
        read, "Provider ID", service.suggest_profile_id(preset), style_defaults
    )
    name = _read_default(read, "Display name", defaults["name"], style_defaults)
    base_url = None
    model = None
    api_key = None
    if preset == "deepseek":
        base_url = _read_default(
            read, "Base URL", defaults["base_url"], style_defaults
        )
        model = _read_default(read, "Model", defaults["model"], style_defaults)
        api_key = read_secret("API key: ")
    elif preset == "openai-compatible":
        base_url = read("Base URL: ").strip()
        model = read("Model: ").strip()
        api_key = read_secret("API key: ")
    else:
        model = _read_default(read, "Model", defaults["model"], style_defaults)
    timeout_seconds = float(_read_default(
        read, "Request timeout (seconds)", defaults["timeout"], style_defaults
    ))
    activate = _read_yes_no(read, "Activate now?", default=True)
    profile = service.add(
        profile_id=profile_id, name=name, preset=preset,
        base_url=base_url, model=model, api_key=api_key, activate=activate,
        timeout_seconds=timeout_seconds,
    )
    write(f"Saved provider: {profile.profile_id}")


def _interactive_edit(
    service: Any,
    read: Callable[[str], str],
    write: Callable[[str], None],
    read_secret: Callable[[str], str],
    style_defaults: bool,
) -> None:
    profile_id = read("Provider ID: ").strip()
    current = service.show_safe(profile_id)
    settings = current["settingsConfig"]
    write("Leave a field empty to keep its current value.")
    name = _read_optional(
        read, "Display name", current["name"], style_defaults
    )
    base_url = _read_optional(
        read, "Base URL", settings.get("base_url") or "-", style_defaults
    )
    model = _read_optional(
        read, "Model", settings.get("model"), style_defaults
    )
    timeout = _read_optional(
        read,
        "Request timeout (seconds)",
        str(settings.get("timeout_seconds")),
        style_defaults,
    )
    rotate = _read_yes_no(read, "Rotate API key?", default=False)
    api_key = read_secret("New API key: ") if rotate else None
    updated = service.edit(
        profile_id, name=name, base_url=base_url, model=model, api_key=api_key,
        timeout_seconds=float(timeout) if timeout is not None else None,
    )
    write(f"Updated provider: {updated.profile_id}")


def _read_default(
    read: Callable[[str], str],
    label: str,
    default: str,
    style_defaults: bool,
) -> str:
    if (
        style_defaults
        and read is input
        and sys.stdin.isatty()
        and sys.stdout.isatty()
    ):
        try:
            return _read_terminal_default(label, default)
        except (ImportError, OSError):
            pass
    visible_default = (
        f"{_DIM}{default}{_RESET}" if style_defaults else default
    )
    value = read(f"{label} : {visible_default}").strip()
    return value or default


def _read_optional(
    read: Callable[[str], str],
    label: str,
    current: str,
    style_defaults: bool,
) -> str | None:
    value = _read_default(read, label, current, style_defaults)
    return None if value == current else value


def _read_yes_no(
    read: Callable[[str], str],
    label: str,
    *,
    default: bool,
) -> bool:
    choices = "Y/n" if default else "y/N"
    while True:
        value = read(f"{label} [{choices}] ").strip().lower()
        if not value:
            return default
        if value in {"y", "yes"}:
            return True
        if value in {"n", "no"}:
            return False


def _read_terminal_default(label: str, default: str) -> str:
    import termios
    import tty

    file_descriptor = sys.stdin.fileno()
    previous = termios.tcgetattr(file_descriptor)

    def write_raw(value: str) -> None:
        sys.stdout.write(value)
        sys.stdout.flush()

    try:
        tty.setraw(file_descriptor)
        return _read_default_keys(
            label,
            default,
            read_key=lambda: sys.stdin.read(1),
            write_raw=write_raw,
        )
    finally:
        termios.tcsetattr(file_descriptor, termios.TCSADRAIN, previous)


def _read_default_keys(
    label: str,
    default: str,
    *,
    read_key: Callable[[], str],
    write_raw: Callable[[str], None],
) -> str:
    typed: list[str] = []

    def render() -> None:
        value = "".join(typed)
        visible = value if value else f"{_DIM}{default}{_RESET}"
        write_raw(f"\r\033[2K{label} : {visible}")

    render()
    while True:
        key = read_key()
        if key in {"\r", "\n"}:
            write_raw("\r\n")
            return "".join(typed) or default
        if key == "\x03":
            write_raw("^C\r\n")
            raise KeyboardInterrupt
        if key == "\x04":
            if typed:
                write_raw("\r\n")
                return "".join(typed)
            raise EOFError
        if key in {"\x7f", "\b"}:
            if typed:
                typed.pop()
                render()
            continue
        if key == "\x15":
            typed.clear()
            render()
            continue
        if key == "\x1b" or not key.isprintable():
            continue
        typed.append(key)
        render()


def _write_profiles(profiles: list[dict[str, Any]], write: Callable[[str], None]) -> None:
    if not profiles:
        write("No providers configured.")
        return
    for item in profiles:
        marker = "*" if item.get("isCurrent") else " "
        settings = item.get("settingsConfig", {})
        write(
            f"{marker} {item['id']} | {item['name']} | "
            f"{settings.get('provider')} | {settings.get('model')} | "
            f"key={'yes' if item.get('api_key_present') else 'no'}"
        )
