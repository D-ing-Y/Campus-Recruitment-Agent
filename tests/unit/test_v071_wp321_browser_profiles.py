from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from campus_job_agent.integrations.browser_profiles import (
    BrowserProfileError,
    BrowserProfileManager,
)
from campus_job_agent.integrations.community_retrieval import (
    _authenticated_browser_config,
    CommunityFetchResult,
    Crawl4AICommunityFetcher,
)
from campus_job_agent.schemas import (
    BrowserProfileRef,
    BrowserProfileStatus,
    SourceAuthRequirement,
    SourceCapabilities,
    SourceDetailRequest,
    SourceQuery,
)
from campus_job_agent.sources import (
    BraveNowcoderExperienceAdapter,
    SQLiteRoleRepository,
    XiaohongshuExperienceAdapter,
)
from campus_job_agent.storage import LocalBlobStore, SQLiteRepository
from campus_job_agent.tools.role_profile import ValidateBrowserProfileTool
from campus_job_agent.tools.registry import ToolRegistry
from campus_job_agent.schemas import SearchScope, ToolResult
from campus_job_agent.workflows.role_profile.graph import (
    RoleProfileWorkflowError,
    _RoleNodes,
    create_role_profile_state,
)


NOWCODER_REF = "local-browser-profile://nowcoder_experience/default"
XHS_REF = "local-browser-profile://xiaohongshu_experience/default"


def test_browser_profile_ref_is_reference_only_and_source_bound() -> None:
    ref = BrowserProfileRef(
        browser_profile_ref=NOWCODER_REF,
        source_id="nowcoder_experience",
        name="default",
    )
    assert ref.browser_profile_ref == NOWCODER_REF
    for value in (
        "file:///tmp/profile",
        "local-browser-profile://nowcoder_experience/../daily",
        "local-browser-profile://xiaohongshu_experience/default",
        "local-browser-profile://nowcoder_experience/default/extra",
    ):
        with pytest.raises(ValueError):
            BrowserProfileRef(
                browser_profile_ref=value,
                source_id="nowcoder_experience",
                name="default",
            )


def test_operation_authorization_is_additive_and_legacy_compatible() -> None:
    capability = SourceCapabilities(
        source_id="nowcoder_experience",
        channel="experience",
        source_type="community_experience",
        adapter_version="test",
        requires_auth=True,
        authorization_mode="credential_ref",
        operation_authorization={
            "collect": ["credential_ref"],
            "fetch_detail": ["browser_profile_ref"],
        },
    )
    assert capability.authorization_for("collect") == ["credential_ref"]
    assert capability.authorization_for("fetch_detail") == [
        "browser_profile_ref"
    ]
    legacy = SourceCapabilities(
        source_id="legacy",
        channel="experience",
        source_type="community_experience",
        adapter_version="test",
        requires_auth=True,
        authorization_mode="external_session",
    )
    assert legacy.authorization_for("collect") == ["external_session"]
    requirement = SourceAuthRequirement(
        source_id="nowcoder_experience",
        operation="fetch_detail",
        mode="browser_profile_ref",
        browser_profile_ref=NOWCODER_REF,
    )
    assert "9223" not in requirement.model_dump_json()


def test_profile_init_uses_private_root_and_never_returns_local_path(
    tmp_path: Path,
) -> None:
    manager = BrowserProfileManager(tmp_path / "browser_profiles")
    ref = manager.init(source_id="nowcoder_experience", name="default")
    status = manager.status(ref.browser_profile_ref)
    assert ref.browser_profile_ref == NOWCODER_REF
    assert status == BrowserProfileStatus(
        browser_profile_ref=NOWCODER_REF,
        source_id="nowcoder_experience",
        name="default",
        configured=True,
        chrome_running=False,
        cdp_reachable=False,
        authenticated_verified=False,
        lifecycle_status="stopped",
        reason_codes=["chrome_not_running"],
    )
    assert stat.S_IMODE(manager.root.stat().st_mode) == 0o700
    safe_json = status.model_dump_json()
    assert str(tmp_path) not in safe_json
    assert "cookie" not in safe_json.casefold()
    assert "websocket" not in safe_json.casefold()


def test_profile_manager_rejects_symlink_and_unknown_port_owner(
    tmp_path: Path,
) -> None:
    root = tmp_path / "browser_profiles"
    manager = BrowserProfileManager(root)
    manager.init(source_id="nowcoder_experience", name="default")
    profile_dir = root / "nowcoder_experience" / "default"
    profile_dir.rename(root / "real-profile")
    profile_dir.symlink_to(root / "real-profile", target_is_directory=True)
    with pytest.raises(BrowserProfileError) as symlink:
        manager.status(NOWCODER_REF)
    assert symlink.value.code == "unsafe_profile_path"

    occupied = BrowserProfileManager(
        tmp_path / "occupied",
        cdp_probe=lambda host, port: True,
        process_command=lambda pid: "",
    )
    occupied.init(source_id="xiaohongshu_experience", name="default")
    with pytest.raises(BrowserProfileError) as conflict:
        occupied.open(XHS_REF)
    assert conflict.value.code == "port_conflict"


def test_profile_manager_rejects_replaced_chrome_data_and_unowned_lock(
    tmp_path: Path,
) -> None:
    root = tmp_path / "profiles"
    manager = BrowserProfileManager(root)
    manager.init(source_id="nowcoder_experience", name="default")
    chrome_data = root / "nowcoder_experience" / "default" / "chrome-data"
    chrome_data.rmdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    chrome_data.symlink_to(outside, target_is_directory=True)
    with pytest.raises(BrowserProfileError) as replaced:
        manager.open(NOWCODER_REF)
    assert replaced.value.code == "unsafe_profile_path"

    fresh = BrowserProfileManager(tmp_path / "locked")
    fresh.init(source_id="xiaohongshu_experience", name="default")
    lock = (
        fresh.root / "xiaohongshu_experience" / "default" / "chrome-data"
        / "SingletonLock"
    )
    lock.symlink_to("unknown-host-123")
    with pytest.raises(BrowserProfileError) as occupied:
        fresh.open(XHS_REF)
    assert occupied.value.code == "profile_in_use"


def test_open_clears_only_stale_chrome_locks_for_exact_stored_pid(
    tmp_path: Path,
) -> None:
    launched: list[list[str]] = []
    manager = BrowserProfileManager(
        tmp_path / "profiles",
        launcher=lambda command: launched.append(command) or 222,
        cdp_probe=lambda host, port: bool(launched),
        process_command=lambda pid: None,
    )
    ref = manager.init(source_id="nowcoder_experience", name="default")
    parsed = manager._parse_ref(ref.browser_profile_ref)
    manager._write_metadata(parsed, pid=111, last_verified_at=None)
    chrome_data = manager.root / "nowcoder_experience" / "default" / "chrome-data"
    (chrome_data / "SingletonLock").symlink_to("host.local-111")
    (chrome_data / "SingletonCookie").symlink_to("cookie-id")
    (chrome_data / "SingletonSocket").symlink_to("/tmp/stale-socket")
    status = manager.open(ref.browser_profile_ref)
    assert status.chrome_running is False  # injected process lookup stays absent
    assert launched
    assert not any(chrome_data.glob("Singleton*"))


def test_open_adopts_only_exact_matching_managed_profile_process(
    tmp_path: Path,
) -> None:
    root = tmp_path / "profiles"
    manager = BrowserProfileManager(
        root,
        launcher=lambda command: (_ for _ in ()).throw(
            AssertionError("matching process must be adopted, not relaunched")
        ),
        cdp_probe=lambda host, port: True,
        process_command=lambda pid: (
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome "
            "--remote-debugging-port=9223 "
            f"--user-data-dir={root / 'nowcoder_experience' / 'default' / 'chrome-data'}"
            if pid == 333 else None
        ),
    )
    ref = manager.init(source_id="nowcoder_experience", name="default")
    parsed = manager._parse_ref(ref.browser_profile_ref)
    manager._write_metadata(parsed, pid=111, last_verified_at=None)
    lock = root / "nowcoder_experience" / "default" / "chrome-data" / "SingletonLock"
    lock.symlink_to("host.local-333")
    status = manager.open(ref.browser_profile_ref)
    assert status.chrome_running is True
    assert status.cdp_reachable is True
    assert manager._read_metadata(parsed)["pid"] == 333


def test_open_and_stop_require_exact_owned_process_identity(tmp_path: Path) -> None:
    commands: list[list[str]] = []
    stopped: list[int] = []
    process_commands: dict[int, str] = {}

    def launch(command: list[str]) -> int:
        commands.append(command)
        process_commands[4312] = " ".join(command)
        return 4312

    manager = BrowserProfileManager(
        tmp_path / "profiles",
        chrome_executable=Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
        launcher=launch,
        cdp_probe=lambda host, port: bool(commands),
        process_command=lambda pid: process_commands.get(pid),
        terminate=lambda pid: stopped.append(pid),
    )
    manager.init(source_id="nowcoder_experience", name="default")
    opened = manager.open(NOWCODER_REF)
    assert opened.chrome_running is True and opened.cdp_reachable is True
    command = commands[0]
    assert "--remote-debugging-address=127.0.0.1" in command
    assert "--remote-debugging-port=9223" in command
    assert "https://www.nowcoder.com/" == command[-1]
    assert not any("stealth" in value or "proxy" in value for value in command)

    process_commands[4312] = "/Applications/Google Chrome unrelated"
    with pytest.raises(BrowserProfileError) as mismatch:
        manager.stop(NOWCODER_REF)
    assert mismatch.value.code == "process_ownership_mismatch"
    assert stopped == []

    process_commands[4312] = " ".join(command)
    stopped_status = manager.stop(NOWCODER_REF)
    assert stopped == [4312]
    assert stopped_status.lifecycle_status == "stopped"


def test_mark_verified_persists_only_safe_aggregate_timestamp(tmp_path: Path) -> None:
    manager = BrowserProfileManager(tmp_path / "profiles")
    manager.init(source_id="xiaohongshu_experience", name="default")
    manager.mark_authenticated_verified(XHS_REF, verified_at="2026-08-13T12:00:00Z")
    status = manager.status(XHS_REF)
    assert status.authenticated_verified is True
    assert status.last_verified_at == "2026-08-13T12:00:00Z"
    metadata = json.loads(
        (
            manager.root / "xiaohongshu_experience" / "default"
            / "profile.json"
        ).read_text(encoding="utf-8")
    )
    assert set(metadata) == {
        "schema_version", "browser_profile_ref", "source_id", "name",
        "port", "pid", "last_verified_at",
    }


def _community_stores(tmp_path: Path):
    return (
        LocalBlobStore(tmp_path / "blobs"),
        SQLiteRepository(tmp_path / "evidence.sqlite3"),
        SQLiteRoleRepository(tmp_path / "role.sqlite3"),
    )


class _ReadyProfileManager:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def resolve_cdp(self, value: str, *, source_id: str) -> str:
        self.calls.append((value, source_id))
        return "http://127.0.0.1:9223" if source_id.startswith("nowcoder") else "http://127.0.0.1:9222"

    def mark_authenticated_verified(self, value: str, *, verified_at: str) -> None:
        self.calls.append((value, "verified"))


def test_crawl4ai_batch_runner_receives_external_cdp_without_owning_browser() -> None:
    calls: list[tuple[list[str], int, str]] = []

    def runner(urls: list[str], concurrency: int, cdp_url: str):
        calls.append((urls, concurrency, cdp_url))
        return [CommunityFetchResult(urls[0], urls[0], True, 200)]

    fetcher = Crawl4AICommunityFetcher(runner=runner)
    fetcher.fetch_many(
        ["https://www.nowcoder.com/discuss/12345"],
        max_concurrency=9,
        cdp_url="http://127.0.0.1:9223",
    )
    assert calls == [([
        "https://www.nowcoder.com/discuss/12345"
    ], 2, "http://127.0.0.1:9223")]


def test_crawl4ai_external_config_reuses_context_without_browser_ownership() -> None:
    config = _authenticated_browser_config("http://127.0.0.1:9223")
    assert config.browser_mode == "cdp"
    assert config.cdp_url == "http://127.0.0.1:9223"
    assert config.use_managed_browser is False
    assert config.create_isolated_context is False
    assert config.cdp_cleanup_on_close is False
    assert config.enable_stealth is False
    assert config.proxy is None and config.proxy_config is None


def test_nowcoder_detail_requires_source_bound_profile_before_fetch(
    tmp_path: Path,
) -> None:
    blob, evidence, role = _community_stores(tmp_path)
    adapter = BraveNowcoderExperienceAdapter(
        blob_store=blob,
        evidence_repository=evidence,
        role_repository=role,
        owner_id="owner",
        live_enabled=True,
        browser_profile_manager=_ReadyProfileManager(),
    )
    request = SourceDetailRequest(
        source_id="nowcoder_experience",
        channel="experience",
        query_id="q",
        candidate_id="candidate",
        parent_document_id="search",
        detail_url="https://www.nowcoder.com/discuss/12345",
        expected_document_kind="experience_post",
    )
    batch = adapter.fetch_detail(request)
    assert batch.status == "authentication_required"
    assert batch.needs_user_action is True


def test_xiaohongshu_search_requires_profile_and_idle_sidecar(tmp_path: Path) -> None:
    class Bridge:
        def __init__(self, status: str = "idle") -> None:
            self.status = status
            self.search_calls = 0

        def health(self):
            return {"status": self.status}

        def search_posts(self, *, keywords: str, limit: int):
            self.search_calls += 1
            return {"candidates": []}

    blob, evidence, role = _community_stores(tmp_path)
    bridge = Bridge()
    profiles = _ReadyProfileManager()
    adapter = XiaohongshuExperienceAdapter(
        bridge_client=bridge,
        browser_profile_manager=profiles,
        blob_store=blob,
        evidence_repository=evidence,
        role_repository=role,
        owner_id="owner",
        live_enabled=True,
    )
    query = SourceQuery(
        channel="experience",
        source_id="xiaohongshu_experience",
        keywords=["美团", "后端开发", "工作体验"],
        company="美团",
        role_family="backend_engineering",
        graduation_year="2027",
        recruitment_type="autumn_campus",
    )
    missing = adapter.collect(query)
    assert missing.status == "authentication_required"
    assert bridge.search_calls == 0

    fresh_blob, fresh_evidence, fresh_role = _community_stores(tmp_path / "busy")
    busy_bridge = Bridge("running")
    busy = XiaohongshuExperienceAdapter(
        bridge_client=busy_bridge,
        browser_profile_manager=profiles,
        blob_store=fresh_blob,
        evidence_repository=fresh_evidence,
        role_repository=fresh_role,
        owner_id="owner",
        live_enabled=True,
    ).collect(query, browser_profile_ref=XHS_REF)
    assert busy.status == "adapter_required"
    assert busy.error_type == "sidecar_busy"
    assert busy_bridge.search_calls == 0


def test_validate_browser_profile_tool_checks_operation_without_exposing_cdp(
    tmp_path: Path,
) -> None:
    class Adapter:
        source_id = "nowcoder_experience"
        capabilities = SourceCapabilities(
            source_id=source_id,
            channel="experience",
            source_type="community_experience",
            adapter_version="test",
            operation_authorization={
                "collect": ["credential_ref"],
                "fetch_detail": ["browser_profile_ref"],
            },
        )

    from campus_job_agent.sources import SourceAdapterRegistry

    adapters = SourceAdapterRegistry()
    adapters.register(Adapter())
    manager = BrowserProfileManager(
        tmp_path / "profiles",
        cdp_probe=lambda host, port: True,
        process_command=lambda pid: (
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome "
            "--remote-debugging-port=9223 "
            f"--user-data-dir={tmp_path / 'profiles' / 'nowcoder_experience' / 'default' / 'chrome-data'}"
        ),
    )
    manager.init(source_id="nowcoder_experience", name="default")
    metadata = manager._read_metadata(manager._parse_ref(NOWCODER_REF))
    manager._write_metadata(
        manager._parse_ref(NOWCODER_REF), pid=101,
        last_verified_at=metadata.get("last_verified_at"),
    )
    tool = ValidateBrowserProfileTool(adapters, manager)
    result = tool.run({
        "source_id": "nowcoder_experience",
        "operation": "fetch_detail",
        "browser_profile_ref": NOWCODER_REF,
    })
    assert result.status == "success"
    payload = result.model_dump_json()
    assert "9223" not in payload
    assert str(tmp_path) not in payload

    wrong_operation = tool.run({
        "source_id": "nowcoder_experience",
        "operation": "collect",
        "authorization_mode": "browser_profile_ref",
        "browser_profile_ref": NOWCODER_REF,
    })
    assert wrong_operation.status == "failed"
    assert wrong_operation.metadata["error_type"] == "unsupported_input"


def test_role_state_and_auth_resume_are_operation_and_profile_bound(
    tmp_path: Path,
) -> None:
    capability = SourceCapabilities(
        source_id="nowcoder_experience",
        channel="experience",
        source_type="community_experience",
        adapter_version="test",
        operation_authorization={
            "collect": ["credential_ref"],
            "fetch_detail": ["browser_profile_ref"],
        },
    )
    state = create_role_profile_state(
        thread_id="thread-1",
        user_id="user-1",
        run_id="run-1",
        search_scope=SearchScope(
            target_role_queries=["美团 后端开发"],
            target_role_family="backend_engineering",
            graduation_year="2027",
            recruitment_type="autumn_campus",
            companies=["美团"],
        ),
        enabled_source_ids=["nowcoder_experience"],
        source_capabilities={
            "nowcoder_experience": capability.model_dump(mode="json")
        },
        browser_profile_refs={"nowcoder_experience": NOWCODER_REF},
    )
    assert state["browser_profile_refs"] == {
        "nowcoder_experience": NOWCODER_REF
    }
    state["pending_auth_source_id"] = "nowcoder_experience"
    state["pending_auth_requirement"] = {
        "source_id": "nowcoder_experience",
        "operation": "fetch_detail",
        "mode": "browser_profile_ref",
        "browser_profile_ref": NOWCODER_REF,
    }

    class ValidateTool:
        name = "source.validate_browser_profile"

        def run(self, args):
            assert args["operation"] == "fetch_detail"
            assert args["browser_profile_ref"] == NOWCODER_REF
            return ToolResult(
                tool_name=self.name,
                status="success",
                records=[],
                evidence_ids=[],
                metadata={
                    "error_type": None,
                    "retryable": False,
                    "needs_user_action": False,
                },
            )

    registry = ToolRegistry()
    registry.register(ValidateTool())
    nodes = _RoleNodes(
        registry,
        SQLiteRepository(tmp_path / "auth-evidence.sqlite3"),
        SQLiteRoleRepository(tmp_path / "auth-role.sqlite3"),
        planner=object(),
    )
    request_update = nodes.plan_source_auth(state)
    request = request_update["pending_interaction"]
    assert request["operation"] == "fetch_detail"
    assert request["authorization_mode"] == "browser_profile_ref"
    state.update(request_update)
    state["resume_input"] = {
        "request_id": request["request_id"],
        "thread_id": "thread-1",
        "user_id": "user-1",
        "source_id": "nowcoder_experience",
        "operation": "collect",
        "authorization_mode": "browser_profile_ref",
        "action": "authorized",
        "browser_profile_ref": NOWCODER_REF,
    }
    with pytest.raises(RoleProfileWorkflowError, match="operation mismatch"):
        nodes.validate_source_authorization(state)

    state["resume_input"]["operation"] = "fetch_detail"
    resumed = nodes.validate_source_authorization(state)
    assert resumed["browser_profile_refs"]["nowcoder_experience"] == NOWCODER_REF
    assert resumed["pending_auth_requirement"] is None
    assert resumed["last_auth_action"] == "retry_detail"
