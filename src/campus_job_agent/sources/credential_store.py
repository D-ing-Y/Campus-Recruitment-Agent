"""Local credential reference store. Secret payloads never leave this boundary."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import time
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from campus_job_agent.schemas import CredentialRef


_SECRET_HEADER = re.compile(r"(?i)^(cookie|authorization|x-[\w-]*token)$")
_CHROME_SOURCES = {
    "zhaopin_jobs": {
        "domain": ".zhaopin.com",
        "required_cookies": set(),
    },
    "nowcoder_experience": {
        "domain": ".nowcoder.com",
        "required_cookies": set(),
    },
}


class LocalCredentialStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.root, 0o700)

    def import_curl(self, *, source_id: str, path: str | Path, name: str = "default",
                    allowed_path_roots: list[str] | None = None) -> CredentialRef:
        source_path = Path(path).resolve()
        roots = [Path(value).resolve() for value in (allowed_path_roots or [])]
        if not roots or not any(source_path == root or root in source_path.parents for root in roots):
            raise ValueError("credential import path is outside allowed_path_roots")
        text = source_path.read_text(encoding="utf-8")
        if not text.lstrip().lower().startswith("curl "):
            raise ValueError("credential import expects a copied cURL request")
        headers = _extract_headers(text)
        secrets = {key: value for key, value in headers.items() if _SECRET_HEADER.match(key)}
        if not secrets:
            raise ValueError("cURL request contains no supported credential headers")
        self._write(source_id=source_id, name=name, credential_type="imported_curl", headers=secrets)
        return CredentialRef(
            credential_ref=f"local-secret://{source_id}/{name}",
            source_id=source_id,
            credential_type="imported_curl",
        )

    def import_chrome(
        self,
        *,
        source_id: str,
        name: str = "default",
        cookie_file: str | Path | None = None,
        cookie_loader: Callable[..., Iterable[Any]] | None = None,
    ) -> CredentialRef:
        """Import only allowlisted source cookies from the user's local Chrome profile."""
        source = _CHROME_SOURCES.get(source_id)
        if source is None:
            raise ValueError("Chrome credential import is not allowed for this source")
        if cookie_loader is None:
            try:
                import browser_cookie3
            except ImportError as exc:  # pragma: no cover - packaging guard
                raise ValueError("browser-cookie3 is not installed; reinstall project dependencies") from exc
            cookie_loader = browser_cookie3.chrome
        kwargs: dict[str, Any] = {"domain_name": source["domain"]}
        if cookie_file is not None:
            kwargs["cookie_file"] = str(Path(cookie_file).expanduser().resolve())
        try:
            jar = cookie_loader(**kwargs)
        except Exception as exc:
            # Browser/database errors can contain sensitive local paths. Keep CLI output generic.
            raise ValueError(
                "Chrome cookies could not be read; close Chrome, approve macOS Keychain access, "
                "or use an isolated Chrome profile"
            ) from exc
        cookies = _domain_cookies(jar, domain=str(source["domain"]))
        if not cookies:
            raise ValueError("no cookies found for the selected source; log in with Chrome first")
        missing = set(source["required_cookies"]) - set(cookies)
        if missing:
            raise ValueError("Chrome login is incomplete; required authentication cookies are missing")
        cookie_header = "; ".join(f"{key}={value}" for key, value in sorted(cookies.items()))
        self._write(
            source_id=source_id,
            name=name,
            credential_type="cookie",
            headers={"cookie": cookie_header},
        )
        return CredentialRef(
            credential_ref=f"local-secret://{source_id}/{name}",
            source_id=source_id,
            credential_type="cookie",
        )

    def resolve(self, credential_ref: str, *, source_id: str) -> dict[str, str]:
        expected = f"local-secret://{source_id}/"
        if not credential_ref.startswith(expected):
            raise ValueError("credential ref does not match source")
        name = credential_ref.removeprefix(expected)
        digest = hashlib.sha256(f"{source_id}:{name}".encode()).hexdigest()[:24]
        target = self.root / f"{digest}.json"
        if not target.is_file():
            raise ValueError("credential ref does not exist")
        payload = json.loads(target.read_text(encoding="utf-8"))
        headers = payload.get("headers", payload)
        return {str(key): str(value) for key, value in headers.items()}

    def validate_ref(self, credential_ref: str, *, source_id: str) -> CredentialRef:
        self.resolve(credential_ref, source_id=source_id)
        name = credential_ref.removeprefix(f"local-secret://{source_id}/")
        payload = json.loads(self._target(source_id, name).read_text(encoding="utf-8"))
        return CredentialRef(
            credential_ref=credential_ref,
            source_id=source_id,
            credential_type=payload.get("credential_type", "imported_curl"),
        )

    def _target(self, source_id: str, name: str) -> Path:
        digest = hashlib.sha256(f"{source_id}:{name}".encode()).hexdigest()[:24]
        return self.root / f"{digest}.json"

    def _write(self, *, source_id: str, name: str, credential_type: str,
               headers: dict[str, str]) -> None:
        if not name or "/" in name or "\\" in name:
            raise ValueError("credential name is invalid")
        payload = {"credential_type": credential_type, "headers": headers}
        target = self._target(source_id, name)
        fd, temporary_name = tempfile.mkstemp(prefix=".credential-", dir=self.root)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(payload, stream, ensure_ascii=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary_name, 0o600)
            os.replace(temporary_name, target)
            os.chmod(target, 0o600)
        finally:
            if os.path.exists(temporary_name):
                os.unlink(temporary_name)


def _extract_headers(curl_text: str) -> dict[str, str]:
    headers: dict[str, str] = {}
    for match in re.finditer(r"(?:-H|--header)\s+(?:'([^']*)'|\"([^\"]*)\")", curl_text):
        value = match.group(1) or match.group(2) or ""
        if ":" not in value:
            continue
        name, content = value.split(":", 1)
        headers[name.strip().lower()] = content.strip()
    return headers


def _domain_cookies(jar: Iterable[Any], *, domain: str) -> dict[str, str]:
    expected = domain.lstrip(".").casefold()
    now = time.time()
    cookies: dict[str, str] = {}
    for cookie in jar:
        cookie_domain = str(getattr(cookie, "domain", "")).lstrip(".").casefold()
        expires = getattr(cookie, "expires", None)
        if cookie_domain != expected and not cookie_domain.endswith(f".{expected}"):
            continue
        if expires is not None and float(expires) <= now:
            continue
        key, value = str(getattr(cookie, "name", "")), str(getattr(cookie, "value", ""))
        if not key or not value or any(char in key + value for char in "\r\n;"):
            continue
        cookies[key] = value
    return cookies
