"""Browser HTTP contracts, settings validation, and payload construction."""

from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
import pytest

from app.config import get_settings
from app.runtime import build_config_snapshot
from app.services.pipecat_pipeline import _build_llm
from main import app


@pytest.fixture
def client():
    with patch("main.run_local_warmup", new=AsyncMock()):
        with TestClient(app, base_url="http://localhost", client=("127.0.0.1", 50000)) as client:
            yield client


def test_start_requires_voice_before_any_connection(client):
    from app.services import runtime_config
    runtime_config._store._config.tts.voice_id = ""
    with patch("app.services.browser_session.SmallWebRTCConnection") as peer:
        response = client.post("/api/offer", json={"sdp": "offer", "type": "offer"})
    assert response.status_code == 409
    assert "Choose a voice" in response.json()["detail"]
    peer.assert_not_called()


def test_invalid_offer_and_unknown_peer(client):
    assert client.post("/api/offer", json={"sdp": "", "type": "answer"}).status_code == 422
    assert client.patch("/api/offer", json={"pc_id": "unknown", "candidates": []}).status_code == 404
    assert client.post("/api/sessions/unknown/end").status_code == 404


def test_config_validation_is_not_silently_ignored(client):
    assert client.post("/config", data={"llm_max_tokens": "broken"}).status_code == 422
    assert client.post("/config", data={"llm_model": "gpt-oss-120b", "llm_reasoning_effort": "none"}).status_code == 422
    response = client.post("/config", data={"tts_voice_id": "ritu", "greeting_text": "Good morning."})
    assert response.status_code == 200
    assert "Good morning." in client.get("/config").text


def test_config_write_failure_and_cross_origin_mutation(client):
    with patch("app.storage.write_config", new=AsyncMock(side_effect=OSError("full"))):
        assert client.post("/config", data={"tts_voice_id": "priya"}).status_code == 503
    assert client.post("/config", data={}, headers={"origin": "https://another.example"}).status_code == 403


def test_assets_are_local_and_no_telephony_route_remains(client):
    page = client.get("/")
    assert page.status_code == 200
    assert 'href="/static/talk.css"' in page.text
    stylesheet = client.get("/static/talk.css")
    assert stylesheet.status_code == 200
    assert "text/css" in stylesheet.headers["content-type"]
    assert client.get("/static/demo.js").status_code == 200
    assert client.get("/static/htmx.min.js").status_code == 200
    assert client.get("/telephony/webhook").status_code == 404


def test_cerebras_wire_request_has_completion_cap_and_reasoning():
    snapshot = build_config_snapshot(get_settings())
    params = _build_llm(snapshot, get_settings()).build_chat_completion_params({"messages": []})
    assert params["max_completion_tokens"] == 1024
    assert params["model"] == "qwen-3.8-27b"
    assert params["reasoning_effort"] == "none"
    assert params["stream"] is True
    assert "max_tokens" not in params


def test_sarvam_settings_show_five_languages_without_credentials(client):
    page = client.get("/config").text
    assert "saaras:v3-realtime" in page and "bulbul:v3" in page
    for code in ["en-IN", "hi-IN", "kn-IN", "mr-IN", "te-IN"]:
        assert code in page
    assert get_settings().sarvam_api_key not in page
    assert "stt_enable_eager_eot" not in page and "llm_enable_speculative_llm" not in page
