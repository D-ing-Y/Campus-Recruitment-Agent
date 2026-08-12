from __future__ import annotations

import json
import io
import os
import sys
from dataclasses import dataclass

import pytest

from campus_job_agent.cli import main
from campus_job_agent.sources import LocalCredentialStore


@dataclass
class Cookie:
    name: str
    value: str
    domain: str
    expires: float | None = None


def test_chrome_import_is_domain_scoped_and_overwrites_default(tmp_path):
    store = LocalCredentialStore(tmp_path / "credentials")

    def first_loader(**kwargs):
        assert kwargs == {"domain_name": ".zhaopin.com"}
        return [
            Cookie("acw_tc", "first", ".zhaopin.com"),
            Cookie("x-zp-client-id", "token", "www.zhaopin.com"),
            Cookie("foreign", "must-not-be-saved", ".example.com"),
        ]

    ref = store.import_chrome(source_id="zhaopin_jobs", cookie_loader=first_loader)
    assert ref.credential_type == "cookie"
    first = store.resolve(ref.credential_ref, source_id="zhaopin_jobs")["cookie"]
    assert "acw_tc=first" in first and "x-zp-client-id=token" in first
    assert "must-not-be-saved" not in first

    ref2 = store.import_chrome(
        source_id="zhaopin_jobs",
        cookie_loader=lambda **_: [
            Cookie("acw_tc", "second", ".zhaopin.com"),
            Cookie("x-zp-client-id", "token2", ".zhaopin.com"),
        ],
    )
    assert ref2.credential_ref == ref.credential_ref
    assert "first" not in store.resolve(ref.credential_ref, source_id="zhaopin_jobs")["cookie"]
    files = list((tmp_path / "credentials").glob("*.json"))
    assert len(files) == 1
    assert os.stat(files[0]).st_mode & 0o777 == 0o600


def test_zhaopin_rejects_empty_cookie_jar(tmp_path):
    store = LocalCredentialStore(tmp_path / "credentials")
    with pytest.raises(ValueError, match="no cookies found"):
        store.import_chrome(
            source_id="zhaopin_jobs",
            cookie_loader=lambda **_: [],
        )
    assert not list((tmp_path / "credentials").glob("*.json"))


def test_nowcoder_uses_source_api_key_and_rejects_chrome_cookie_import(tmp_path):
    store = LocalCredentialStore(tmp_path / "credentials")
    ref = store.save_source_api_key(
        source_id="nowcoder_experience", api_key="brave-secret",
    )
    assert store.validate_ref(
        ref.credential_ref, source_id="nowcoder_experience"
    ).credential_type == "api_key_ref"
    assert store.resolve(
        ref.credential_ref, source_id="nowcoder_experience"
    )["api_key"] == "brave-secret"
    with pytest.raises(ValueError, match="not allowed"):
        store.import_chrome(
            source_id="nowcoder_experience",
            cookie_loader=lambda **_: [
                Cookie("token", "secret", ".nowcoder.com")
            ],
        )


def test_cli_prints_brave_reference_but_never_api_key(monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CAMPUS_AGENT_DATA_ROOT", str(tmp_path / "data"))
    monkeypatch.setattr(sys, "stdin", io.StringIO("super-secret\n"))
    assert main([
        "auth", "import-api-key", "--source", "brave_search",
        "--api-key-stdin",
    ]) == 0
    output = capsys.readouterr().out
    assert "local-secret://nowcoder_experience/default" in output
    assert "super-secret" not in output
    assert f"credential_root: {tmp_path / 'data/cache/credentials'}" in output


def test_cli_accepts_zhaopin_source_alias(monkeypatch, tmp_path, capsys):
    real_import = LocalCredentialStore.import_chrome
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CAMPUS_AGENT_DATA_ROOT", str(tmp_path / "data"))

    def fake_import(self, **kwargs):
        assert kwargs["source_id"] == "zhaopin_jobs"
        return real_import(
            LocalCredentialStore(tmp_path / "target"),
            source_id="zhaopin_jobs",
            cookie_loader=lambda **_: [Cookie("session", "cookie-value-xyz", ".zhaopin.com")],
        )

    monkeypatch.setattr(LocalCredentialStore, "import_chrome", fake_import)
    assert main(["auth", "import-chrome", "--source", "zhaopin"]) == 0
    output = capsys.readouterr().out
    assert "local-secret://zhaopin_jobs/default" in output
    assert "cookie-value-xyz" not in output


def test_legacy_curl_payload_still_resolves(tmp_path):
    store = LocalCredentialStore(tmp_path / "credentials")
    target = store._target("zhaopin_jobs", "default")
    target.write_text(json.dumps({"cookie": "legacy"}), encoding="utf-8")
    assert store.resolve("local-secret://zhaopin_jobs/default", source_id="zhaopin_jobs") == {"cookie": "legacy"}
