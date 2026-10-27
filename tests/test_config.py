"""Server settings contain secrets, never conversation tuning."""

import os

from app.config import Settings, V1_SAMPLE_RATE


def test_sample_rate_remains_fixed():
    assert V1_SAMPLE_RATE == 16000


def test_settings_need_only_sarvam_and_cerebras_keys(monkeypatch):
    for name in list(os.environ):
        if name.startswith(("EXOTEL_", "MONGO_", "PUBLIC_NGROK")):
            monkeypatch.delenv(name)
    settings = Settings(_env_file=None)
    assert settings.sarvam_api_key == "test_sarvam"
    assert settings.host == "127.0.0.1"
    assert set(Settings.model_fields) == {"host", "port", "deepgram_api_key", "cerebras_api_key", "cartesia_api_key", "sarvam_api_key"}
    assert "test_sarvam" not in repr(settings)
    monkeypatch.delenv("DEEPGRAM_API_KEY", raising=False)
    monkeypatch.delenv("CARTESIA_API_KEY", raising=False)
    assert Settings(_env_file=None).sarvam_api_key == "test_sarvam"
