"""Hosted candidate origin policy with actual routes and mocked media/provider work."""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.models.runtime_config_models import OpsCfg
from main import create_app
from test_interview_controller import job

ORIGIN = "https://candidate-production.vercel.app"


@pytest.fixture
def client():
    app = create_app()
    with patch("main.run_local_warmup", new=AsyncMock()):
        with TestClient(app, base_url="http://127.0.0.1:8000", client=("127.0.0.1", 50000)) as local:
            yield local


def activate(client: TestClient) -> tuple[str, dict[str, str]]:
    """Create a normal local interview and retain its one-time scoped token."""
    definition = client.post("/api/interviews", json=job().model_dump()).json()
    assert client.put(f"/api/interviews/{definition['id']}/plan", json=definition["plan"]).status_code == 200
    value = client.post(f"/api/interviews/{definition['id']}/activate").json()
    assert value["candidate_url"].startswith("/candidate/")
    return value["session_id"], {"Authorization": f"Bearer {value['candidate_url'].split('#token=')[1]}"}


def test_origin_is_opt_in_exact_and_frontend_editable(client: TestClient) -> None:
    identity, auth = activate(client)
    path = f"/api/candidate/{identity}"
    assert client.get(path, headers={**auth, "Origin": ORIGIN}).status_code == 403
    saved = client.post("/config", data={"candidate_frontend_origin": ORIGIN})
    assert saved.status_code == 200 and ORIGIN in saved.text
    response = client.get(path, headers={**auth, "Origin": ORIGIN})
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == ORIGIN
    assert "access-control-allow-credentials" not in response.headers
    assert "resume" not in response.json() and "plan" not in response.json()
    for origin in ("https://preview-123.vercel.app", ORIGIN + ".evil.example", "null"):
        rejected = client.get(path, headers={**auth, "Origin": origin})
        assert rejected.status_code == 403
        assert "access-control-allow-origin" not in rejected.headers
    assert client.post("/config", data={"candidate_frontend_origin": ""}).status_code == 200
    assert client.get(path, headers={**auth, "Origin": ORIGIN}).status_code == 403


def test_preflight_methods_headers_and_authentication_errors(client: TestClient) -> None:
    identity, auth = activate(client)
    client.post("/config", data={"candidate_frontend_origin": ORIGIN})
    path = f"/api/candidate/{identity}/offer"
    preflight = {"Origin": ORIGIN, "Access-Control-Request-Method": "PATCH", "Access-Control-Request-Headers": "authorization,content-type"}
    response = client.options(path, headers=preflight)
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == ORIGIN
    assert "PATCH" in response.headers["access-control-allow-methods"]
    assert client.options(path, headers={**preflight, "Access-Control-Request-Method": "DELETE"}).status_code == 400
    assert client.options(path, headers={**preflight, "Access-Control-Request-Headers": "x-unlisted-header"}).status_code == 400
    assert client.options(path, headers={**preflight, "Origin": "https://unlisted.example"}).status_code == 403
    invalid = client.get(f"/api/candidate/{identity}", headers={"Origin": ORIGIN, "Authorization": "Bearer invalid"})
    assert invalid.status_code in {401, 403}
    assert invalid.headers["access-control-allow-origin"] == ORIGIN
    assert client.get(f"/api/candidate/{identity}", headers=auth).status_code == 200
    assert client.get(f"/api/candidate/{identity}", headers={**auth, "Origin": "http://127.0.0.1:8000"}).status_code == 200


def test_hosted_prepare_offer_ice_and_end_preserve_scope(client: TestClient) -> None:
    identity, auth = activate(client)
    client.post("/config", data={"candidate_frontend_origin": ORIGIN})
    headers = {**auth, "Origin": ORIGIN}
    base = f"/api/candidate/{identity}"
    assert client.post(base + "/prepare", headers=headers, json={"phase": "ready", "camera_consent": False}).status_code == 200
    manager = client.app.state.browser_sessions
    with patch.object(manager, "offer", new=AsyncMock(return_value={"sdp": "test-answer", "type": "answer", "pc_id": "peer"})) as offer:
        response = client.post(base + "/offer", headers=headers, json={"sdp": "test-offer", "type": "offer"})
        assert response.status_code == 200 and response.json()["pc_id"] == "peer"
        assert offer.call_args.kwargs["interview"].id == identity
    with patch.object(manager, "owns_interview", return_value=True), patch.object(manager, "candidates", new=AsyncMock()) as ice:
        response = client.patch(base + "/offer", headers=headers, json={"pc_id": "peer", "candidates": []})
        assert response.status_code == 200 and ice.await_count == 1
    assert client.patch(base + "/offer", headers=headers, json={"pc_id": "other", "candidates": []}).status_code == 403
    assert client.post(base + "/end", headers=headers).json()["saved"] is True
    assert client.post(base + "/revoke-camera-consent", headers=headers).status_code == 200


def test_hosted_origin_cannot_access_management_or_spoof_local_host(client: TestClient) -> None:
    identity, auth = activate(client)
    client.post("/config", data={"candidate_frontend_origin": ORIGIN})
    for path in ("/", "/config", "/calls", "/interviews", "/api/interviews", f"/api/interviews/sessions/{identity}/report"):
        response = client.get(path, headers={**auth, "Origin": ORIGIN})
        assert response.status_code == 403
        assert "access-control-allow-origin" not in response.headers
    assert client.post("/config", headers={"Origin": ORIGIN}, data={}).status_code == 403
    response = client.get(f"/api/candidate/{identity}", headers={**auth, "Origin": ORIGIN, "Host": "public.example"})
    assert response.status_code == 403
    with TestClient(client.app, base_url="http://127.0.0.1:8000", client=("203.0.113.8", 50001)) as remote:
        assert remote.get(f"/api/candidate/{identity}", headers={**auth, "Origin": ORIGIN}).status_code == 403


@pytest.mark.parametrize("origin", ["*", "https://*.vercel.app", "https://candidate.vercel.app/", "https://candidate.vercel.app/path",
    "http://candidate.vercel.app", "https://user:secret@candidate.vercel.app", "https://candidate.vercel.app?token=x"])
def test_invalid_origin_configuration_is_rejected(origin: str) -> None:
    with pytest.raises(ValidationError):
        OpsCfg(candidate_frontend_origin=origin)
