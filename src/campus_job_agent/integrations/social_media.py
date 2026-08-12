"""Read-only bridge for an external localhost MediaCrawler sidecar.

The bridge deliberately owns the sensitive candidate cache. Callers only see
opaque refs and canonical public URLs; cookies, xsec_token values and sidecar
output paths never cross into graph state.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import quote, urlparse

import httpx


class SocialBridgeError(RuntimeError):
    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(message or code)
        self.code = code


@dataclass(frozen=True)
class MediaCrawlerSidecarConfig:
    base_url: str
    installation_path: Path
    pinned_commit: str
    license_accepted: bool
    candidate_cache_root: Path
    timeout_seconds: float = 10.0
    poll_interval_seconds: float = 0.25
    max_poll_seconds: float = 45.0
    max_notes_count: int = 3

    def validate(self) -> "MediaCrawlerSidecarConfig":
        parsed = urlparse(self.base_url)
        if parsed.scheme not in {"http", "https"} or parsed.hostname not in {
            "localhost", "127.0.0.1", "::1",
        }:
            raise SocialBridgeError("policy_blocked", "sidecar must use localhost")
        if parsed.path.rstrip("/") != "/api":
            raise SocialBridgeError("policy_blocked", "sidecar must expose the pinned /api contract")
        if not self.license_accepted:
            raise SocialBridgeError("license_not_accepted")
        if len(self.pinned_commit.strip()) < 7:
            raise SocialBridgeError("unpinned_sidecar")
        installation = self.installation_path.expanduser().resolve()
        if not installation.is_dir():
            raise SocialBridgeError("adapter_required", "MediaCrawler installation is unavailable")
        actual_commit = _git_head_commit(installation)
        if not actual_commit:
            raise SocialBridgeError("unpinned_sidecar", "MediaCrawler git revision is unavailable")
        if not actual_commit.startswith(self.pinned_commit) and not self.pinned_commit.startswith(actual_commit):
            raise SocialBridgeError("sidecar_version_mismatch")
        project_root = Path(__file__).resolve().parents[3]
        if (project_root / "pyproject.toml").is_file() and installation.is_relative_to(
            project_root
        ):
            raise SocialBridgeError(
                "policy_blocked", "MediaCrawler must remain outside the main repository"
            )
        if not self.candidate_cache_root.expanduser().resolve().is_relative_to(
            self.installation_path.expanduser().resolve()
        ):
            raise SocialBridgeError(
                "policy_blocked", "sensitive candidate cache must stay inside sidecar root"
            )
        if not 1 <= self.max_notes_count <= 10:
            raise SocialBridgeError("policy_blocked", "bounded note count must be 1..10")
        if self.timeout_seconds <= 0 or self.poll_interval_seconds < 0 or self.max_poll_seconds <= 0:
            raise SocialBridgeError("policy_blocked", "sidecar timeouts must be bounded and positive")
        return self

    @classmethod
    def from_env(cls) -> "MediaCrawlerSidecarConfig":
        root = Path(os.environ.get("CAMPUS_AGENT_MEDIACRAWLER_ROOT", "")).expanduser()
        cache = Path(os.environ.get(
            "CAMPUS_AGENT_SOCIAL_CANDIDATE_CACHE",
            str(root / ".campus-agent-bridge-cache"),
        )).expanduser()
        return cls(
            base_url=os.environ.get(
                "CAMPUS_AGENT_MEDIACRAWLER_URL", "http://127.0.0.1:8080/api"
            ),
            installation_path=root,
            pinned_commit=os.environ.get("CAMPUS_AGENT_MEDIACRAWLER_COMMIT", ""),
            license_accepted=os.environ.get(
                "CAMPUS_AGENT_MEDIACRAWLER_LICENSE_ACCEPTED", ""
            ).casefold() in {"1", "true", "yes"},
            candidate_cache_root=cache,
            timeout_seconds=float(os.environ.get(
                "CAMPUS_AGENT_MEDIACRAWLER_TIMEOUT_SECONDS", "10"
            )),
            poll_interval_seconds=float(os.environ.get(
                "CAMPUS_AGENT_MEDIACRAWLER_POLL_INTERVAL_SECONDS", "0.25"
            )),
            max_poll_seconds=float(os.environ.get(
                "CAMPUS_AGENT_MEDIACRAWLER_MAX_POLL_SECONDS", "180"
            )),
            max_notes_count=int(os.environ.get(
                "CAMPUS_AGENT_MEDIACRAWLER_MAX_NOTES", "3"
            )),
        )


class MediaCrawlerSidecarClient:
    """Bounded single-task client for MediaCrawler's crawler/data API."""

    _task_lock = threading.Lock()

    def __init__(
        self, config: MediaCrawlerSidecarConfig, *, transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.config = config.validate()
        self.config.candidate_cache_root.mkdir(parents=True, exist_ok=True)
        self.config.candidate_cache_root.chmod(0o700)
        self._client = httpx.Client(
            base_url=self.config.base_url.rstrip("/") + "/",
            timeout=self.config.timeout_seconds,
            transport=transport,
        )

    def health(self) -> dict[str, Any]:
        try:
            response = self._client.get("crawler/status")
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise SocialBridgeError("adapter_required", type(exc).__name__) from exc
        return {
            "status": str(payload.get("status", "unknown")),
            "platform": payload.get("platform"),
            "crawler_type": payload.get("crawler_type"),
            "sidecar_commit": self.config.pinned_commit,
        }

    def auth_status(self) -> dict[str, Any]:
        status = self.health()
        # MediaCrawler's public status schema does not expose cookies. A
        # reachable idle/running CDP sidecar is the only safe preflight; start
        # errors remain authoritative for login/risk control.
        return {
            "status": "external_session_available"
            if status["status"] in {"idle", "running"} else "authentication_required",
            "cookie_exposed": False,
        }

    def search_posts(self, *, keywords: str, limit: int = 3) -> dict[str, Any]:
        records = self._run_task(
            {
                "platform": "xhs", "login_type": "qrcode", "crawler_type": "search",
                "keywords": keywords, "specified_ids": "", "creator_ids": "",
                "start_page": 1, "enable_comments": False, "enable_sub_comments": False,
                "save_option": "json", "cookies": "", "headless": False,
                "max_notes_count": min(limit, self.config.max_notes_count),
            }
        )
        candidates: list[dict[str, Any]] = []
        for record in records:
            normalized = self._cache_candidate(record)
            if normalized is not None:
                candidates.append(normalized)
        unique = {item["candidate_ref"]: item for item in candidates}
        return {
            "platform": "xiaohongshu", "candidates": list(unique.values()),
            "result_count": len(unique), "sensitive_parameters_exposed": False,
        }

    def fetch_post_detail(self, *, candidate_ref: str) -> dict[str, Any]:
        record = self._load_candidate(candidate_ref)
        specified = str(
            record.get("note_url") or record.get("source_url")
            or record.get("url") or record.get("note_id") or record.get("id") or ""
        )
        if not specified:
            raise SocialBridgeError("unsupported_input", "candidate has no sidecar locator")
        records = self._run_task(
            {
                "platform": "xhs", "login_type": "qrcode", "crawler_type": "detail",
                "keywords": "", "specified_ids": specified, "creator_ids": "",
                "start_page": 1, "enable_comments": False, "enable_sub_comments": False,
                "save_option": "json", "cookies": "", "headless": False,
                "max_notes_count": 1,
            }
        )
        target_id = _post_id(record)
        selected = next((item for item in records if _post_id(item) == target_id), None)
        if selected is None and len(records) == 1:
            selected = records[0]
        if selected is None:
            raise SocialBridgeError("empty", "detail output did not contain requested post")
        body = str(
            selected.get("desc") or selected.get("content") or selected.get("note_content") or ""
        ).strip()
        if not body:
            raise SocialBridgeError("empty", "detail post has no body")
        post_id = _post_id(selected) or target_id
        return {
            "platform": "xiaohongshu", "candidate_ref": candidate_ref,
            "platform_post_id": post_id,
            "canonical_url": _canonical_xhs_url(post_id),
            "title": str(selected.get("title") or ""), "body": body,
            "published_at": selected.get("time") or selected.get("publish_time"),
            "sensitive_parameters_exposed": False,
        }

    def _run_task(self, request: dict[str, Any]) -> list[dict[str, Any]]:
        if request.get("platform") != "xhs" or request.get("crawler_type") not in {"search", "detail"}:
            raise SocialBridgeError("policy_blocked")
        if request.get("enable_comments") or request.get("enable_sub_comments") or request.get("creator_ids"):
            raise SocialBridgeError("policy_blocked")
        with self._task_lock:
            status = self.health()["status"]
            if status not in {"idle"}:
                raise SocialBridgeError("sidecar_busy")
            before = self._list_data_files()
            try:
                response = self._client.post("crawler/start", json=request)
                response.raise_for_status()
            except httpx.HTTPError as exc:
                raise SocialBridgeError("adapter_required", type(exc).__name__) from exc
            deadline = time.monotonic() + self.config.max_poll_seconds
            while True:
                try:
                    status_response = self._client.get("crawler/status")
                    status_response.raise_for_status()
                    payload = status_response.json()
                except (httpx.HTTPError, ValueError) as exc:
                    raise SocialBridgeError("adapter_required", type(exc).__name__) from exc
                status = str(payload.get("status", "error"))
                if status == "idle":
                    break
                if status == "error":
                    raise _classified_sidecar_error(str(payload.get("error_message") or "sidecar error"))
                if time.monotonic() >= deadline:
                    raise SocialBridgeError("network_timeout")
                time.sleep(self.config.poll_interval_seconds)
            after = self._list_data_files()
            changed = [
                value for path, value in after.items()
                if path not in before or any(
                    before[path].get(field) != value.get(field)
                    for field in ("modified_at", "size", "record_count")
                )
            ]
            if not changed:
                completion_error = self._completed_task_error()
                if completion_error is not None:
                    raise completion_error
                return []
            records: list[dict[str, Any]] = []
            for item in changed:
                records.extend(self._read_data_file(str(item.get("path") or item.get("name") or "")))
            return _filter_task_records(records, request)

    def _completed_task_error(self) -> SocialBridgeError | None:
        """Classify a subprocess failure without exposing sidecar log text."""

        try:
            response = self._client.get("crawler/logs", params={"limit": 50})
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError):
            # Logs are diagnostic-only. The archived output gate remains the
            # authority when an older compatible sidecar has no log endpoint.
            return None
        values = payload.get("logs", []) if isinstance(payload, dict) else []
        messages = [
            str(item.get("message") or "")
            for item in values
            if isinstance(item, dict)
            and str(item.get("level") or "") in {"warning", "error"}
        ]
        lowered = "\n".join(messages).casefold()
        if not lowered:
            return None
        if any(token in lowered for token in ("captcha", "验证", "风控", "risk")):
            return SocialBridgeError("risk_controlled")
        if any(token in lowered for token in ("login", "登录", "cookie", "unauthorized")):
            return SocialBridgeError("authentication_required")
        if any(token in lowered for token in (
            "err_timed_out", "timed out", "timeout", "network timeout",
        )):
            return SocialBridgeError("network_timeout")
        if any(token in lowered for token in (
            "crawler exited with code", "traceback", "failed", "error",
        )):
            return SocialBridgeError("failed")
        return None

    def _list_data_files(self) -> dict[str, dict[str, Any]]:
        try:
            response = self._client.get(
                "data/files", params={"platform": "xhs", "file_type": "json"}
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise SocialBridgeError("adapter_required", type(exc).__name__) from exc
        values = payload.get("files", []) if isinstance(payload, dict) else payload
        return {
            str(item.get("path") or item.get("name")): item
            for item in values
            if isinstance(item, dict)
            and _safe_sidecar_path(str(item.get("path") or item.get("name") or ""))
            and "contents" in str(item.get("path") or item.get("name") or "").casefold()
        }

    def _read_data_file(self, path: str) -> list[dict[str, Any]]:
        if not _safe_sidecar_path(path):
            raise SocialBridgeError("policy_blocked", "unsafe sidecar output path")
        try:
            response = self._client.get(
                f"data/files/{quote(path, safe='/')}", params={"preview": "false"}
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise SocialBridgeError("adapter_required", type(exc).__name__) from exc
        try:
            value = response.json()
        except json.JSONDecodeError as exc:
            raise SocialBridgeError("source_changed", "sidecar JSON output is invalid") from exc
        if isinstance(value, dict):
            return [value]
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        raise SocialBridgeError("source_changed", "sidecar JSON output has an unsupported shape")

    def _cache_candidate(self, record: dict[str, Any]) -> dict[str, Any] | None:
        post_id = _post_id(record)
        if not post_id:
            return None
        candidate_ref = "xhs-candidate:" + hashlib.sha256(
            f"xhs:{post_id}".encode("utf-8")
        ).hexdigest()[:24]
        path = self.config.candidate_cache_root / f"{candidate_ref.replace(':', '-')}.json"
        path.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
        path.chmod(0o600)
        return {
            "candidate_ref": candidate_ref, "platform_post_id": post_id,
            "canonical_url": _canonical_xhs_url(post_id),
            "title": str(record.get("title") or ""),
        }

    def _load_candidate(self, candidate_ref: str) -> dict[str, Any]:
        if not candidate_ref.startswith("xhs-candidate:") or not all(
            ch in "0123456789abcdef" for ch in candidate_ref.split(":", 1)[1]
        ):
            raise SocialBridgeError("unsupported_input")
        path = self.config.candidate_cache_root / f"{candidate_ref.replace(':', '-')}.json"
        if not path.is_file() or path.parent.resolve() != self.config.candidate_cache_root.resolve():
            raise SocialBridgeError("unsupported_input", "unknown candidate ref")
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise SocialBridgeError("unsupported_input")
        return value


def _safe_sidecar_path(value: str) -> bool:
    path = PurePosixPath(value)
    return (
        bool(value) and not path.is_absolute() and ".." not in path.parts
        and path.suffix == ".json" and "xhs" in {part.casefold() for part in path.parts}
    )


def _git_head_commit(root: Path) -> str | None:
    git_dir = root / ".git"
    if git_dir.is_file():
        marker = git_dir.read_text(encoding="utf-8", errors="replace").strip()
        if not marker.startswith("gitdir:"):
            return None
        candidate = Path(marker.split(":", 1)[1].strip())
        git_dir = candidate if candidate.is_absolute() else (root / candidate).resolve()
    head_path = git_dir / "HEAD"
    if not head_path.is_file():
        return None
    head = head_path.read_text(encoding="utf-8", errors="replace").strip()
    if not head.startswith("ref:"):
        return head.casefold() if all(ch in "0123456789abcdefABCDEF" for ch in head) else None
    ref_name = head.split(":", 1)[1].strip()
    loose_ref = git_dir / ref_name
    if loose_ref.is_file():
        return loose_ref.read_text(encoding="utf-8", errors="replace").strip().casefold()
    packed = git_dir / "packed-refs"
    if packed.is_file():
        for line in packed.read_text(encoding="utf-8", errors="replace").splitlines():
            if line and not line.startswith(("#", "^")):
                value, _, name = line.partition(" ")
                if name == ref_name:
                    return value.casefold()
    return None


def _filter_task_records(
    records: list[dict[str, Any]], request: dict[str, Any],
) -> list[dict[str, Any]]:
    if request.get("crawler_type") == "search":
        keyword = str(request.get("keywords") or "").strip()
        return [
            item for item in records
            if str(item.get("source_keyword") or "").strip() == keyword
        ]
    target = _post_id_from_locator(str(request.get("specified_ids") or ""))
    return [item for item in records if target and _post_id(item) == target]


def _post_id_from_locator(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme and parsed.path:
        parts = [part for part in parsed.path.split("/") if part]
        return parts[-1] if parts else ""
    return value.split(",", 1)[0].strip()


def _post_id(record: dict[str, Any]) -> str:
    return str(record.get("note_id") or record.get("post_id") or record.get("id") or "").strip()


def _canonical_xhs_url(post_id: str) -> str:
    return f"https://www.xiaohongshu.com/explore/{quote(post_id, safe='')}"


def _classified_sidecar_error(message: str) -> SocialBridgeError:
    lowered = message.casefold()
    if any(token in lowered for token in ("captcha", "验证", "风控", "risk")):
        return SocialBridgeError("risk_controlled")
    if any(token in lowered for token in ("login", "登录", "cookie", "unauthorized")):
        return SocialBridgeError("authentication_required")
    return SocialBridgeError("failed")


__all__ = [
    "MediaCrawlerSidecarClient", "MediaCrawlerSidecarConfig", "SocialBridgeError",
]
