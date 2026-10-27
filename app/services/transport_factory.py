"""Audio-only browser transport at the fixed pipeline sample rate."""

from pipecat.transports.base_transport import TransportParams
from pipecat.transports.smallwebrtc.connection import SmallWebRTCConnection
from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport

from app.config import V1_SAMPLE_RATE


def build_browser_transport(connection: SmallWebRTCConnection) -> SmallWebRTCTransport:
    """Let WebRTC convert device audio at the 16 kHz pipeline boundary."""
    return SmallWebRTCTransport(connection, TransportParams(
        audio_in_enabled=True,
        audio_out_enabled=True,
        audio_in_sample_rate=V1_SAMPLE_RATE,
        audio_out_sample_rate=V1_SAMPLE_RATE,
        audio_in_channels=1,
        audio_out_channels=1,
        audio_out_auto_silence=True,
        video_in_enabled=False,
        video_out_enabled=False,
    ))
