"""Verify native WebRTC conversion preserves the application's audio contract."""

from unittest.mock import MagicMock
from app.services.transport_factory import build_browser_transport


def test_browser_transport_is_audio_only_at_16khz():
    transport = build_browser_transport(MagicMock())
    params = transport._params
    assert params.audio_in_sample_rate == params.audio_out_sample_rate == 16000
    assert params.audio_in_enabled and params.audio_out_enabled
    assert params.audio_out_auto_silence
    assert not params.video_in_enabled and not params.video_out_enabled
