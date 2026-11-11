"""Full hosted workspace access, pairing and data APIs without provider calls."""

from unittest.mock import AsyncMock, patch
import asyncio

from fastapi.testclient import TestClient
import pytest
from pydantic import ValidationError

from app.models.runtime_config_models import OpsCfg
from app.services.workspace_access import WorkspacePairing
from main import create_app
from test_candidate_cors import activate

ORIGIN = "https://workspace-production.vercel.app"
LOCAL = {"Origin": "http://127.0.0.1:8000", "Content-Type": "application/json"}


@pytest.fixture
def client():
    app = create_app()
    with patch("main.run_local_warmup", new=AsyncMock()):
        with TestClient(app, base_url="http://127.0.0.1:8000", client=("127.0.0.1", 50000)) as local:
            yield local


def pairing(client: TestClient) -> dict[str, str]:
    """Explicitly approve the workspace locally and issue its separate credential."""
    assert client.post("/config", data={"workspace_frontend_origin": ORIGIN}).status_code == 200
    issued = client.post("/api/workspace/pair", headers=LOCAL, json={})
    assert issued.status_code == 200
    assert issued.json()["expires_in"] == 7200
    return {"Origin": ORIGIN, "Authorization": f"Bearer {issued.json()['token']}"}


def test_pairing_is_local_explicit_and_never_candidate_access(client: TestClient) -> None:
    assert client.post("/api/workspace/pair", headers=LOCAL, json={}).status_code == 409
    auth = pairing(client)
    for headers in ({}, {"Origin": ORIGIN}, {**LOCAL, "Host": "public.example"}):
        assert client.post("/api/workspace/pair", headers=headers, json={}).status_code == 403
    assert client.post("/api/workspace/pair", headers={**auth, "Content-Type": "application/json"}, json={}).status_code == 403
    assert client.delete("/api/workspace/pair", headers=auth).status_code == 403
    identity, candidate_auth = activate(client)
    client.post("/config", data={"candidate_frontend_origin": ORIGIN})
    assert client.get(f"/api/candidate/{identity}", headers=auth).status_code == 403
    assert client.get("/api/interviews", headers={**candidate_auth, "Origin": ORIGIN}).status_code == 401
    assert client.get("/api/workspace/config", headers={"Origin": ORIGIN}).status_code == 401
    assert client.get("/api/workspace/config", headers=auth).status_code == 200


def test_workspace_cors_allowlist_preflight_and_local_boundary(client: TestClient) -> None:
    auth = pairing(client)
    preflight = {"Origin": ORIGIN, "Access-Control-Request-Method": "PUT", "Access-Control-Request-Headers": "authorization,content-type"}
    response = client.options("/api/workspace/config", headers=preflight)
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == ORIGIN
    assert "access-control-allow-credentials" not in response.headers
    assert client.options("/api/workspace/config", headers={**preflight, "Access-Control-Request-Method": "DELETE"}).status_code == 400
    assert client.options("/api/workspace/config", headers={**preflight, "Access-Control-Request-Headers": "x-unlisted"}).status_code == 400
    for origin in ("https://unlisted.vercel.app", ORIGIN + ".evil.example", "null"):
        response = client.get("/api/interviews", headers={**auth, "Origin": origin})
        assert response.status_code == 403 and "access-control-allow-origin" not in response.headers
    for path in ("/config", "/calls", "/interviews", "/", "/docs", "/openapi.json"):
        assert client.get(path, headers=auth).status_code == 403
    assert client.get("/api/workspace/config", headers={**auth, "Host": "other.example"}).status_code == 403
    with TestClient(client.app, base_url="http://127.0.0.1:8000", client=("203.0.113.10", 50001)) as remote:
        assert remote.get("/api/workspace/config", headers=auth).status_code == 403
    # The local pages and original unauthenticated local recruiter flow remain usable.
    assert client.get("/config").status_code == 200
    assert client.get("/api/interviews").status_code == 200


def test_replacement_expiry_revocation_origin_changes_and_restart(client: TestClient) -> None:
    old = pairing(client)
    new = pairing(client)
    assert client.get("/api/interviews", headers=old).status_code == 401
    assert client.get("/api/interviews", headers=new).status_code == 200
    client.app.state.workspace_pairing.expires_at = 0
    assert client.get("/api/interviews", headers=new).status_code == 401
    new = pairing(client)
    assert client.delete("/api/workspace/pair", headers=LOCAL).status_code == 200
    assert client.get("/api/interviews", headers=new).status_code == 401
    new = pairing(client)
    client.post("/config", data={"workspace_frontend_origin": ""})
    client.post("/config", data={"workspace_frontend_origin": ORIGIN})
    assert client.get("/api/interviews", headers=new).status_code == 401
    assert not WorkspacePairing().accepts(ORIGIN, new["Authorization"])
    assert new["Authorization"][7:] not in client.get("/api/workspace/config").text


def test_non_secret_config_validation_and_local_origin_controls(client: TestClient) -> None:
    auth = pairing(client)
    value = client.get("/api/workspace/config", headers=auth).json()
    assert "test_sarvam" not in str(value) and "test_cb" not in str(value)
    value["llm"]["temperature"] = 0.3
    saved = client.put("/api/workspace/config", headers=auth, json=value)
    assert saved.status_code == 200 and saved.json()["llm"]["temperature"] == 0.3
    value["tts"]["min_buffer_size"] = 1
    assert client.put("/api/workspace/config", headers=auth, json=value).status_code == 422
    value["tts"]["min_buffer_size"] = 30
    for name in ("candidate_frontend_origin", "workspace_frontend_origin"):
        updated = {**value, "ops": {**value["ops"], name: "https://changed.example"}}
        assert client.put("/api/workspace/config", headers=auth, json=updated).status_code == 403
    assert client.get("/api/workspace/config", headers=auth).json()["ops"]["workspace_frontend_origin"] == ORIGIN


def test_paired_interview_builder_report_history_and_general_signaling(client: TestClient) -> None:
    auth = pairing(client)
    identity, _ = activate(client)
    assert client.get("/api/interviews", headers=auth).json()["sessions"][0]["id"] == identity
    response = client.get(f"/api/interviews/sessions/{identity}/report", headers=auth)
    assert response.status_code == 200 and response.headers["access-control-allow-origin"] == ORIGIN
    assert client.put(f"/api/interviews/sessions/{identity}/review", headers=auth,
        json={"recruiter_notes": "Review manually", "human_decision": "Pending"}).status_code == 200
    assert client.get("/api/workspace/calls", headers=auth).json() == {"logs": [], "db_connected": True}
    assert client.get(f"/api/workspace/calls/browser_{'a' * 32}", headers=auth).status_code == 404
    manager = client.app.state.browser_sessions
    with patch.object(manager, "offer", new=AsyncMock(return_value={"pc_id": "test", "type": "answer", "sdp": "test"})) as offer:
        assert client.post("/api/offer", headers=auth, json={"sdp": "test", "type": "offer"}).status_code == 200
        assert offer.call_args.kwargs == {}
    with patch.object(manager, "candidates", new=AsyncMock()) as ice:
        assert client.patch("/api/offer", headers=auth, json={"pc_id": "test", "candidates": []}).status_code == 200
        assert ice.await_count == 1
    from app.models.browser_models import EndResult
    with patch.object(manager, "end", new=AsyncMock(return_value=EndResult(call_sid=f"browser_{'b' * 32}", saved=True))):
        assert client.post(f"/api/sessions/browser_{'b' * 32}/end", headers=auth).json()["saved"]


def test_saved_history_reuses_existing_timing_calculations(client: TestClient) -> None:
    auth = pairing(client)
    identity = f"browser_{'c' * 32}"
    log = {"call_sid": identity, "created_at": "Synthetic test", "metrics": {"turn_count": 1,
        "turn_records": [{"turn_id": 1, "primary_latency_ms": 600, "latest_user_stop_to_first_audio_ms": 400}]},
        "transcript": [], "config_snapshot": {}}
    with patch("app.routers.workspace.get_call_log", new=AsyncMock(return_value=log)):
        response = client.get(f"/api/workspace/calls/{identity}", headers=auth)
        assert response.status_code == 200
        assert response.json()["latency"]["responsiveness"]["median"] == 400
        assert response.json()["latency"]["total"]["median"] == 600
    with patch("app.routers.dashboard._fetch_recent_logs", new=AsyncMock(return_value=([log], True))):
        assert client.get("/api/workspace/calls", headers=auth).json()["logs"][0]["measured_latency_turns"] == 1


@pytest.mark.parametrize("origin", ["*", "http://workspace.vercel.app", "https://workspace.vercel.app/", "https://*.vercel.app"])
def test_workspace_origin_validation(origin: str) -> None:
    with pytest.raises(ValidationError):
        OpsCfg(workspace_frontend_origin=origin)


@pytest.mark.asyncio
async def test_hosted_save_cannot_undo_a_concurrent_local_origin_revocation() -> None:
    """Queue a stale hosted save behind a durable local policy update."""
    from app.models.runtime_config_models import RuntimeConfig
    from app.services.config_store import ConfigStore

    store = ConfigStore()
    stale = RuntimeConfig.default()
    stale.ops.workspace_frontend_origin = ORIGIN
    store._config = stale.model_copy(deep=True)
    revoked = stale.model_copy(deep=True)
    revoked.ops.workspace_frontend_origin = ""
    stale.llm.temperature = 0.2
    writing, release = asyncio.Event(), asyncio.Event()
    written = []

    async def write(value: RuntimeConfig) -> None:
        if not written:
            writing.set()
            await release.wait()
        written.append(value.model_copy(deep=True))

    with patch("app.storage.write_config", new=write):
        local = asyncio.create_task(store.save(revoked))
        await writing.wait()
        hosted = asyncio.create_task(store.save(stale, preserve_access_origins=True))
        await asyncio.sleep(0)
        release.set()
        await asyncio.gather(local, hosted)
    assert written[-1].ops.workspace_frontend_origin == ""
    assert store.get().ops.workspace_frontend_origin == ""
    assert store.get().llm.temperature == 0.2
