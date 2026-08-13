"""Safe lifecycle management for isolated, project-owned Chrome profiles."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import time
from pathlib import Path
from typing import Any, Callable

import httpx

from campus_job_agent.schemas import BrowserProfileRef, BrowserProfileStatus


PROFILE_SPECS: dict[str, dict[str, Any]] = {
    "nowcoder_experience": {
        "port": 9223,
        "login_url": "https://www.nowcoder.com/",
    },
    "xiaohongshu_experience": {
        "port": 9222,
        "login_url": "https://www.xiaohongshu.com/explore",
    },
}


class BrowserProfileError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class BrowserProfileManager:
    """Own profile paths and processes while exposing opaque refs only."""

    def __init__(
        self,
        root: str | Path,
        *,
        chrome_executable: Path | None = None,
        launcher: Callable[[list[str]], int] | None = None,
        cdp_probe: Callable[[str, int], bool] | None = None,
        process_command: Callable[[int], str | None] | None = None,
        terminate: Callable[[int], None] | None = None,
    ) -> None:
        raw_root = Path(root).expanduser()
        self.root = raw_root if raw_root.is_absolute() else raw_root.absolute()
        self.chrome_executable = chrome_executable or self._default_chrome_executable()
        self._launcher = launcher or self._launch
        self._injected_launcher = launcher is not None
        self._cdp_probe = cdp_probe or self._probe_cdp
        self._process_command = process_command or self._read_process_command
        self._terminate = terminate or self._terminate_process
        self._ensure_root()

    def init(self, *, source_id: str, name: str = "default") -> BrowserProfileRef:
        ref = self._make_ref(source_id, name)
        profile_dir = self._profile_dir(ref)
        if profile_dir.exists() and profile_dir.is_symlink():
            raise BrowserProfileError("unsafe_profile_path", "profile path is a symlink")
        profile_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(profile_dir, 0o700)
        chrome_data = self._chrome_data_dir(ref)
        chrome_data.mkdir(mode=0o700, exist_ok=True)
        os.chmod(chrome_data, 0o700)
        metadata_path = profile_dir / "profile.json"
        if not metadata_path.exists():
            self._write_metadata(ref, pid=None, last_verified_at=None)
        else:
            self._read_metadata(ref)
        return ref

    def open(self, browser_profile_ref: str) -> BrowserProfileStatus:
        ref = self._parse_ref(browser_profile_ref)
        metadata = self._read_metadata(ref)
        port = int(PROFILE_SPECS[ref.source_id]["port"])
        current_pid = metadata.get("pid")
        if current_pid and self._owns_process(ref, int(current_pid)):
            return self.status(ref.browser_profile_ref)
        chrome_data = self._chrome_data_dir(ref)
        singleton_lock = chrome_data / "SingletonLock"
        if singleton_lock.exists() or singleton_lock.is_symlink():
            lock_pid = self._singleton_lock_pid(singleton_lock)
            if lock_pid and self._owns_process(ref, lock_pid):
                self._write_metadata(
                    ref, pid=lock_pid,
                    last_verified_at=metadata.get("last_verified_at"),
                )
                return self.status(ref.browser_profile_ref)
            if (
                current_pid
                and self._process_command(int(current_pid)) is None
                and self._singleton_lock_matches_pid(
                    singleton_lock, int(current_pid)
                )
            ):
                self._clear_stale_chrome_locks(chrome_data)
            else:
                raise BrowserProfileError(
                    "profile_in_use", "Chrome profile is occupied by an unowned process"
                )
        if self._cdp_probe("127.0.0.1", port):
            raise BrowserProfileError(
                "port_conflict", "the configured loopback CDP port is already occupied"
            )
        if not self._injected_launcher and not (
            self.chrome_executable.is_file()
            and os.access(self.chrome_executable, os.X_OK)
        ):
            raise BrowserProfileError("chrome_not_found", "system Chrome executable was not found")
        command = self._chrome_command(ref)
        pid = int(self._launcher(command))
        self._write_metadata(
            ref, pid=pid, last_verified_at=metadata.get("last_verified_at")
        )
        reachable = False
        for _ in range(10):
            if self._cdp_probe("127.0.0.1", port):
                reachable = True
                break
            time.sleep(0.2)
        running = self._owns_process(ref, pid)
        return self._public_status(
            ref,
            configured=True,
            chrome_running=running,
            cdp_reachable=reachable,
            lifecycle_status="ready" if reachable else "cdp_unreachable",
            reason_codes=[] if reachable else ["cdp_unreachable"],
            last_verified_at=metadata.get("last_verified_at"),
        )

    def status(self, browser_profile_ref: str) -> BrowserProfileStatus:
        ref = self._parse_ref(browser_profile_ref)
        profile_dir = self._profile_dir(ref)
        if not profile_dir.exists():
            return self._public_status(
                ref, configured=False, chrome_running=False,
                cdp_reachable=False, lifecycle_status="not_initialized",
                reason_codes=["profile_not_initialized"], last_verified_at=None,
            )
        metadata = self._read_metadata(ref)
        port = int(PROFILE_SPECS[ref.source_id]["port"])
        pid = metadata.get("pid")
        running = bool(pid and self._owns_process(ref, int(pid)))
        reachable = self._cdp_probe("127.0.0.1", port)
        if reachable and not running:
            return self._public_status(
                ref, configured=True, chrome_running=False, cdp_reachable=True,
                lifecycle_status="port_conflict", reason_codes=["port_conflict"],
                last_verified_at=metadata.get("last_verified_at"),
            )
        if not running:
            return self._public_status(
                ref, configured=True, chrome_running=False, cdp_reachable=False,
                lifecycle_status="stopped", reason_codes=["chrome_not_running"],
                last_verified_at=metadata.get("last_verified_at"),
            )
        return self._public_status(
            ref, configured=True, chrome_running=True, cdp_reachable=reachable,
            lifecycle_status="ready" if reachable else "cdp_unreachable",
            reason_codes=[] if reachable else ["cdp_unreachable"],
            last_verified_at=metadata.get("last_verified_at"),
        )

    def stop(self, browser_profile_ref: str) -> BrowserProfileStatus:
        ref = self._parse_ref(browser_profile_ref)
        metadata = self._read_metadata(ref)
        pid = metadata.get("pid")
        if pid is None:
            return self.status(ref.browser_profile_ref)
        if not self._owns_process(ref, int(pid)):
            raise BrowserProfileError(
                "process_ownership_mismatch",
                "stored process does not match the managed Chrome command",
            )
        self._terminate(int(pid))
        self._write_metadata(
            ref, pid=None, last_verified_at=metadata.get("last_verified_at")
        )
        return self._public_status(
            ref, configured=True, chrome_running=False, cdp_reachable=False,
            lifecycle_status="stopped", reason_codes=["chrome_stopped"],
            last_verified_at=metadata.get("last_verified_at"),
        )

    def resolve_cdp(self, browser_profile_ref: str, *, source_id: str) -> str:
        ref = self._parse_ref(browser_profile_ref)
        if ref.source_id != source_id:
            raise BrowserProfileError(
                "profile_source_mismatch", "browser profile source does not match operation"
            )
        status = self.status(ref.browser_profile_ref)
        if not status.chrome_running or not status.cdp_reachable:
            raise BrowserProfileError(
                "authentication_required", "managed browser profile CDP is not ready"
            )
        return f"http://127.0.0.1:{PROFILE_SPECS[source_id]['port']}"

    def mark_authenticated_verified(
        self, browser_profile_ref: str, *, verified_at: str
    ) -> None:
        ref = self._parse_ref(browser_profile_ref)
        metadata = self._read_metadata(ref)
        self._write_metadata(ref, pid=metadata.get("pid"), last_verified_at=verified_at)

    def _ensure_root(self) -> None:
        if self.root.exists() and self.root.is_symlink():
            raise BrowserProfileError("unsafe_profile_path", "profile root is a symlink")
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.root, 0o700)

    def _make_ref(self, source_id: str, name: str) -> BrowserProfileRef:
        return BrowserProfileRef(
            browser_profile_ref=f"local-browser-profile://{source_id}/{name}",
            source_id=source_id,
            name=name,
        )

    def _parse_ref(self, value: str) -> BrowserProfileRef:
        prefix = "local-browser-profile://"
        if not value.startswith(prefix):
            raise BrowserProfileError("invalid_profile_ref", "invalid browser profile reference")
        parts = value[len(prefix):].split("/")
        if len(parts) != 2:
            raise BrowserProfileError("invalid_profile_ref", "invalid browser profile reference")
        try:
            return self._make_ref(parts[0], parts[1])
        except ValueError as exc:
            raise BrowserProfileError("invalid_profile_ref", str(exc)) from exc

    def _profile_dir(self, ref: BrowserProfileRef) -> Path:
        candidate = self.root / ref.source_id / ref.name
        for path in (candidate.parent, candidate):
            if path.exists() and path.is_symlink():
                raise BrowserProfileError("unsafe_profile_path", "profile path is a symlink")
        resolved_root = self.root.resolve()
        resolved_candidate = candidate.resolve(strict=False)
        if resolved_candidate != resolved_root and resolved_root not in resolved_candidate.parents:
            raise BrowserProfileError("unsafe_profile_path", "profile path escapes managed root")
        return candidate

    def _read_metadata(self, ref: BrowserProfileRef) -> dict[str, Any]:
        self._chrome_data_dir(ref)
        path = self._profile_dir(ref) / "profile.json"
        if not path.is_file() or path.is_symlink():
            raise BrowserProfileError("profile_not_initialized", "profile is not initialized")
        try:
            metadata = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise BrowserProfileError("profile_metadata_invalid", "profile metadata is invalid") from exc
        expected = {
            "browser_profile_ref": ref.browser_profile_ref,
            "source_id": ref.source_id,
            "name": ref.name,
            "port": PROFILE_SPECS[ref.source_id]["port"],
        }
        if any(metadata.get(key) != value for key, value in expected.items()):
            raise BrowserProfileError("profile_metadata_invalid", "profile metadata identity mismatch")
        return metadata

    def _write_metadata(
        self, ref: BrowserProfileRef, *, pid: int | None, last_verified_at: str | None
    ) -> None:
        self._chrome_data_dir(ref)
        path = self._profile_dir(ref) / "profile.json"
        payload = {
            "schema_version": "v0.7.1",
            "browser_profile_ref": ref.browser_profile_ref,
            "source_id": ref.source_id,
            "name": ref.name,
            "port": PROFILE_SPECS[ref.source_id]["port"],
            "pid": pid,
            "last_verified_at": last_verified_at,
        }
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        os.chmod(temporary, 0o600)
        temporary.replace(path)

    def _chrome_command(self, ref: BrowserProfileRef) -> list[str]:
        chrome_data = self._chrome_data_dir(ref)
        spec = PROFILE_SPECS[ref.source_id]
        return [
            str(self.chrome_executable),
            "--remote-debugging-address=127.0.0.1",
            f"--remote-debugging-port={spec['port']}",
            f"--user-data-dir={chrome_data}",
            "--no-first-run",
            "--no-default-browser-check",
            str(spec["login_url"]),
        ]

    def _chrome_data_dir(self, ref: BrowserProfileRef) -> Path:
        profile_dir = self._profile_dir(ref)
        candidate = profile_dir / "chrome-data"
        if candidate.exists() and candidate.is_symlink():
            raise BrowserProfileError(
                "unsafe_profile_path", "Chrome data path is a symlink"
            )
        resolved_profile = profile_dir.resolve(strict=False)
        resolved_candidate = candidate.resolve(strict=False)
        if resolved_profile not in resolved_candidate.parents:
            raise BrowserProfileError(
                "unsafe_profile_path", "Chrome data path escapes managed profile"
            )
        return candidate

    def _owns_process(self, ref: BrowserProfileRef, pid: int) -> bool:
        command = self._process_command(pid)
        if not command:
            return False
        expected = self._chrome_command(ref)
        return all(
            marker in command
            for marker in (
                expected[0],
                f"--remote-debugging-port={PROFILE_SPECS[ref.source_id]['port']}",
                expected[3],
            )
        )

    @staticmethod
    def _singleton_lock_matches_pid(lock: Path, pid: int) -> bool:
        return BrowserProfileManager._singleton_lock_pid(lock) == pid

    @staticmethod
    def _singleton_lock_pid(lock: Path) -> int | None:
        if not lock.is_symlink():
            return None
        try:
            target = os.readlink(lock)
        except OSError:
            return None
        value = target.rsplit("-", 1)[-1]
        return int(value) if value.isdigit() else None

    @staticmethod
    def _clear_stale_chrome_locks(chrome_data: Path) -> None:
        for name in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
            path = chrome_data / name
            if path.is_symlink():
                path.unlink()

    def _public_status(
        self, ref: BrowserProfileRef, *, configured: bool,
        chrome_running: bool, cdp_reachable: bool, lifecycle_status: str,
        reason_codes: list[str], last_verified_at: str | None,
    ) -> BrowserProfileStatus:
        return BrowserProfileStatus(
            browser_profile_ref=ref.browser_profile_ref,
            source_id=ref.source_id,
            name=ref.name,
            configured=configured,
            chrome_running=chrome_running,
            cdp_reachable=cdp_reachable,
            authenticated_verified=last_verified_at is not None,
            last_verified_at=last_verified_at,
            lifecycle_status=lifecycle_status,
            reason_codes=reason_codes,
        )

    @staticmethod
    def _default_chrome_executable() -> Path:
        if os.name == "nt":
            return Path(os.environ.get("PROGRAMFILES", "C:/Program Files")) / "Google/Chrome/Application/chrome.exe"
        if os.uname().sysname == "Darwin":
            return Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
        return Path("/usr/bin/google-chrome")

    @staticmethod
    def _launch(command: list[str]) -> int:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return int(process.pid)

    @staticmethod
    def _probe_cdp(host: str, port: int) -> bool:
        if host != "127.0.0.1":
            return False
        try:
            response = httpx.get(f"http://{host}:{port}/json/version", timeout=0.5)
            payload = response.json() if response.status_code == 200 else {}
            return bool(payload.get("Browser") and payload.get("webSocketDebuggerUrl"))
        except (httpx.HTTPError, ValueError):
            return False

    @staticmethod
    def _read_process_command(pid: int) -> str | None:
        try:
            result = subprocess.run(
                ["ps", "-p", str(pid), "-o", "command="],
                text=True, capture_output=True, check=False, timeout=2,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        return result.stdout.strip() or None

    @staticmethod
    def _terminate_process(pid: int) -> None:
        os.kill(pid, signal.SIGTERM)
