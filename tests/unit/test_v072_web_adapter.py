from __future__ import annotations

from starlette.testclient import TestClient

from apps.web.server.app import LOCAL_USER_ID, create_app


def test_web_adapter_health_and_session_workspace_are_local_only(tmp_path) -> None:
    app = create_app(tmp_path)
    client = TestClient(app)

    health = client.get("/api/health")
    assert health.status_code == 200
    assert health.json()["data"]["mode"] == "local-only"
    assert "api_key" not in str(health.json()).lower()

    created = client.post("/api/sessions", json={})
    assert created.status_code == 201
    workspace = created.json()["data"]
    assert workspace["session"]["user_id"] == LOCAL_USER_ID
    assert workspace["session"]["next_action"] == "resume.import"
    assert workspace["resume"] is None
    assert workspace["candidate_profile"] is None

    session_id = workspace["session"]["session_id"]
    restored = client.get(f"/api/sessions/{session_id}/workspace")
    assert restored.status_code == 200
    assert restored.json()["data"]["session"]["session_id"] == session_id


def test_web_adapter_rejects_foreign_sessions_and_non_pdf_resume(tmp_path) -> None:
    app = create_app(tmp_path)
    client = TestClient(app)
    runtime = app.state.runtime

    foreign = runtime.session_service.start(user_id="foreign-user")
    denied = client.get(f"/api/sessions/{foreign.session_id}")
    assert denied.status_code == 403
    assert denied.json()["error"]["type"] == "permission_denied"

    session = client.post("/api/sessions", json={}).json()["data"]["session"]
    rejected = client.post(
        f"/api/sessions/{session['session_id']}/resume",
        files={"file": ("resume.txt", b"not a pdf", "text/plain")},
    )
    assert rejected.status_code == 400
    assert rejected.json()["error"]["type"] == "contract_violation"
    assert str(tmp_path) not in str(rejected.json())


def test_candidate_diff_route_is_not_shadowed_by_snapshot_route(tmp_path) -> None:
    client = TestClient(create_app(tmp_path))

    response = client.get("/api/candidate/diff")

    assert response.status_code == 400
    assert response.json()["error"]["message"] == "from 与 to 都是必填参数。"
