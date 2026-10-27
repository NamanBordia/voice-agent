import pytest
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient

from main import app
from app.config import get_settings
from app.models.runtime_config_models import RuntimeConfig
from app.runtime import ACTIVE_SESSIONS


@pytest.fixture
def client():
    ACTIVE_SESSIONS.clear()
    with patch("main.run_local_warmup", new_callable=AsyncMock):
        with TestClient(app) as test_client:
            yield test_client
    ACTIVE_SESSIONS.clear()


def test_config_page_renders(client):
    response = client.get("/config")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "Runtime Configuration" in response.text
    assert "saaras:v3-realtime" in response.text  # default STT model shown
    assert "Place outbound call" not in response.text
    # Grouped sections + new editable fields with helper text.
    assert "Sarvam realtime" in response.text
    assert "Memory" in response.text
    assert "End-of-speech silence (ms)" in response.text
    assert "Post-STT debounce (ms)" in response.text  # LAT-2 configurable debounce
    assert "Flux eager EOT is unavailable" in response.text
    assert "Speculative generation is unavailable with Sarvam" in response.text


def test_root_renders_browser(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "Start conversation" in response.text


def test_config_save(client):
    config = RuntimeConfig.default(get_settings())
    with patch(
        "app.routers.dashboard.save_runtime_config", new_callable=AsyncMock
    ) as mock_save:
        mock_save.return_value = config
        response = client.post(
            "/config",
            data={
                "stt_model": "saaras:v3-realtime",
                "stt_language_hints": "en, hi",
                "stt_eot_threshold": "0.65",
                "stt_eot_timeout_ms": "700",
                "llm_model": "qwen-3.8-27b",
                "llm_reasoning_effort": "none",
                "llm_temperature": "0.7",
                "llm_max_tokens": "160",
                "llm_request_timeout_seconds": "10",
                "tts_model": "bulbul:v3",
                "tts_voice_id": "ritu",
                "tts_language_mode": "per_turn",
                "system_prompt": "Be helpful.",
                "greeting_text": "Say hi.",
                "debug_log_level": "INFO",
            },
        )
    assert response.status_code == 200
    assert mock_save.await_count == 1
    # The saved config passed to save_runtime_config reflects the edits.
    saved = mock_save.call_args.args[0]
    assert saved.tts.voice_id == "ritu"
    assert saved.prompt.system_prompt == "Be helpful."


def test_sarvam_vad_settings_roundtrip_and_speculation_unavailable(client):
    values = {"stt_language_code": "kn-IN", "stt_silence_duration_ms": "850",
              "stt_threshold": "0.4", "tts_pace": "1.2"}
    assert client.post("/config", data=values).status_code == 200
    from app.services.runtime_config import get_cached_runtime_config
    config = get_cached_runtime_config()
    assert config.stt.language_code == "kn-IN" and config.stt.silence_duration_ms == 850
    assert config.stt.threshold == 0.4 and config.tts.pace == 1.2
    assert client.post("/config", data={"llm_enable_speculative_llm": "true"}).status_code == 422
    assert client.post("/config", data={"stt_enable_eager_eot": "true"}).status_code == 422
    assert client.post("/config", data={"stt_language_code": "xx-IN"}).status_code == 422
    assert client.post("/config", data={"tts_voice_id": "cartesia-uuid"}).status_code == 422
    assert client.post("/config", data={"tts_min_buffer_size": "20"}).status_code == 422


def test_calls_list_renders(client):
    with patch("app.routers.dashboard._fetch_recent_logs", new_callable=AsyncMock) as mock_fetch:
        mock_fetch.return_value = ([], True)
        response = client.get("/calls")
    assert response.status_code == 200
    assert "No conversations yet." in response.text


def test_call_detail_renders_turn_table(client):
    fake_log = {
        "_id": "x",
        "call_sid": "browser_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "stream_sid": "stream_abc",
        "config_snapshot": {"runtime_config": {"llm": {"model": "gpt-oss-120b"}}},
        "transcript": [{"role": "assistant", "phase": "greeting", "text": "Hi there!"}],
        "metrics": {
            "greeting_text_source": "llm",
            "turn_records": [
                {
                    "turn_id": 1, "user_text": "hello", "assistant_text": "hi",
                    "detected_language": "en", "response_language": "en",
                    "primary_latency_ms": 850.0, "eot_delay_ms": 300.0,
                    "llm_ttfb_ms": 200.0, "llm_total_ms": 400.0, "tts_first_audio_ms": 120.0,
                    "error_stage": None, "error_message": None,
                }
            ],
        },
    }

    with patch("app.routers.dashboard.get_call_log", new=AsyncMock(return_value=fake_log)):
        response = client.get("/calls/browser_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")

    assert response.status_code == 200
    assert "browser_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" in response.text
    assert "Hi there!" in response.text
    assert "hello" in response.text
    assert "850 ms" in response.text


def test_call_detail_renders_breakdown_merged_and_error(client):
    fake_log = {
        "_id": "x",
        "call_sid": "browser_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        "stream_sid": "s",
        "config_snapshot": {"runtime_config": {}},
        "transcript": [{"role": "assistant", "phase": "greeting", "text": "Hi!"}],
        "metrics": {
            "greeting_text_source": "llm",
            "turn_records": [
                {
                    "turn_id": 3, "merged_user_turn_ids": [3, 4],
                    "merged_user_texts": ["Hello.", "Computer science."],
                    "merged_user_stop_times": [1.0, 12.0],
                    "user_text": "Hello.\nComputer science.", "assistant_text": "Samajh gaya.",
                    "detected_language": "hi", "response_language": "hi",
                    "user_stop_to_first_audio_ms": 12552.0, "primary_latency_ms": 12552.0,
                    "user_stop_to_llm_start_ms": 11800.0, "turn_committed_to_llm_start_ms": 11790.0,
                    "llm_ttfb_ms": 558.0, "llm_total_ms": 592.0, "llm_end_to_tts_start_ms": 30.0,
                    "tts_first_audio_ms": 114.0, "memory_processor_duration_ms": 3.2,
                    "earliest_user_stop_to_first_audio_ms": 12552.0,
                    "latest_user_stop_to_first_audio_ms": 700.0,
                    "bot_was_speaking_when_user_stopped": False,
                    "error_stage": "language_prefix_parse", "error_message": "missing tag",
                }
            ],
        },
    }

    with patch("app.routers.dashboard.get_call_log", new=AsyncMock(return_value=fake_log)):
        response = client.get("/calls/browser_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb")

    assert response.status_code == 200
    assert "merged 3, 4" in response.text                 # merged ids visible
    assert "Pipeline" in response.text                    # grouped breakdown line label
    assert "latest&#8594;audio" in response.text or "latest→audio" in response.text       # merged earliest/latest
    assert "language_prefix_parse" in response.text       # error visible
    assert "12552 ms" in response.text                    # total latency shown (VAD anchor)


def test_eager_probe_summary_aggregates():
    from app.routers.dashboard import _eager_probe_summary
    records = [
        {"eager_probe": {"eager_eot_seen": True, "verdict": "eligible", "turn_resumed_seen": False,
                         "eager_final_match": True, "head_start_ms": 300.0, "estimated_saved_ms": 200.0}},
        {"eager_probe": {"eager_eot_seen": True, "verdict": "wasted_turn_resumed", "turn_resumed_seen": True,
                         "eager_final_match": True, "head_start_ms": 500.0, "estimated_saved_ms": 0.0}},
        {"eager_probe": {"eager_eot_seen": False, "verdict": "no_eager_event"}},
    ]
    s = _eager_probe_summary(records)
    assert s["total_turns"] == 3
    assert s["eager_eot_turns"] == 2
    assert s["eligible_turns"] == 1
    assert s["turn_resumed_turns"] == 1
    assert s["turn_resumed_rate"] == round(1 / 2, 3)         # resumed / eager
    assert s["median_head_start_ms"] == 400.0               # median of [300, 500]
    assert s["total_estimated_saved_ms"] == 200.0
    # No probes on any turn (telemetry off) -> no summary.
    assert _eager_probe_summary([{"primary_latency_ms": 800}]) is None


def test_memory_summary_aggregates():
    from app.routers.dashboard import _memory_summary

    records = [
        {"memory": {"recall_used": True, "recall_triggered": True, "recall_snippets": ["user: a", "user: b"],
                    "context_message_count": 8, "approx_memory_tokens": 200}},
        {"memory": {"recall_used": False, "recall_triggered": False, "recall_snippets": [],
                    "context_message_count": 6, "approx_memory_tokens": 150}},
        {"primary_latency_ms": 700},  # turn without a memory snapshot
    ]
    s = _memory_summary(records)
    assert s["total_turns"] == 3
    assert s["memory_turns"] == 2
    assert s["recall_used_turns"] == 1
    assert s["recall_triggered_turns"] == 1
    assert s["recall_use_rate"] == 0.5                      # 1 of 2 memory turns
    assert s["total_recall_snippets"] == 2
    assert s["median_context_messages"] == 7.0             # median(8, 6)
    assert s["max_approx_memory_tokens"] == 200
    # No memory on any turn -> no summary card.
    assert _memory_summary([{"primary_latency_ms": 800}]) is None


def test_call_detail_renders_memory_block(client):
    fake_log = {
        "_id": "x", "call_sid": "browser_cccccccccccccccccccccccccccccccc", "stream_sid": "s",
        "config_snapshot": {"runtime_config": {}},
        "transcript": [{"role": "assistant", "phase": "greeting", "text": "Hi!"}],
        "metrics": {
            "greeting_text_source": "llm",
            "turn_records": [
                {
                    "turn_id": 2, "user_text": "what did i say about Acme", "assistant_text": "You said Acme.",
                    "detected_language": "en", "response_language": "en",
                    "primary_latency_ms": 900.0,
                    "memory": {
                        "recall_used": True, "recall_triggered": True, "recall_min_overlap": 1,
                        "recall_snippets": ["user: I work at Acme Corporation"],
                        "recall_source_turns": [1], "pinned_facts_count": 1,
                        "running_summary_present": False, "context_message_count": 9,
                        "approx_memory_tokens": 180,
                    },
                    "error_stage": None, "error_message": None,
                }
            ],
        },
    }

    with patch("app.routers.dashboard.get_call_log", new=AsyncMock(return_value=fake_log)):
        response = client.get("/calls/browser_cccccccccccccccccccccccccccccccc")

    assert response.status_code == 200
    assert "recall yes" in response.text                     # per-turn recall badge
    assert "I work at Acme Corporation" in response.text     # recalled snippet shown verbatim
    assert "from turns 1" in response.text                   # source-turn linkage (D6)
    assert "explicit ask" in response.text                   # recall_triggered chip
    assert "Turns with memory" in response.text              # call-level rollup card


def test_call_detail_renders_eager_probe(client):
    fake_log = {
        "_id": "x", "call_sid": "browser_dddddddddddddddddddddddddddddddd", "stream_sid": "s",
        "config_snapshot": {"runtime_config": {}},
        "transcript": [{"role": "assistant", "phase": "greeting", "text": "Hi!"}],
        "metrics": {
            "greeting_text_source": "llm",
            "turn_records": [
                {
                    "turn_id": 1, "user_text": "hello", "assistant_text": "hi",
                    "detected_language": "en", "response_language": "en",
                    "primary_latency_ms": 820.0,
                    "eager_probe": {
                        "eager_eot_seen": True, "turn_resumed_seen": False,
                        "eager_final_match": True, "head_start_ms": 320.0,
                        "estimated_saved_ms": 240.0, "verdict": "eligible",
                        "eager_transcript_preview": "hello", "final_transcript_preview": "hello",
                    },
                    "error_stage": None, "error_message": None,
                }
            ],
        },
    }

    with patch("app.routers.dashboard.get_call_log", new=AsyncMock(return_value=fake_log)):
        response = client.get("/calls/browser_dddddddddddddddddddddddddddddddd")

    assert response.status_code == 200
    assert "EagerEOT speculative probe" in response.text    # call-level summary panel
    assert "head start" in response.text                    # per-turn spec line label
    assert "eligible" in response.text                      # verdict badge
    assert "320 ms" in response.text                        # head start
    assert "240 ms" in response.text                        # est. saved


def test_call_detail_renders_overlap_ready_and_latest_commit(client):
    # MET-1 (overlap), OBS-1 (tri-state STT ready), LAT-1 (latest Commit->LLM).
    fake_log = {
        "_id": "x", "call_sid": "browser_eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee", "stream_sid": "s",
        "config_snapshot": {"runtime_config": {}},
        "transcript": [{"role": "assistant", "phase": "greeting", "text": "Hi!"}],
        "metrics": {
            "greeting_text_source": "llm",
            "turn_records": [
                {
                    "turn_id": 5, "merged_user_turn_ids": [5, 6],
                    "merged_user_texts": ["Hello.", "Yes please."],
                    "user_text": "Hello.\nYes please.", "assistant_text": "Sure.",
                    "detected_language": "en", "response_language": "en",
                    "user_stop_to_first_audio_ms": 8000.0, "primary_latency_ms": 8000.0,
                    "earliest_user_stop_to_first_audio_ms": 8000.0,
                    "latest_user_stop_to_first_audio_ms": 1100.0,
                    "turn_committed_to_llm_start_ms": 7500.0,
                    "latest_turn_committed_to_llm_start_ms": 480.0,
                    "llm_ttfb_ms": 400.0, "llm_total_ms": 430.0,
                    "llm_end_to_tts_start_ms": -9.0,  # streaming overlap
                    "tts_first_audio_ms": 120.0,
                    "stt_ready_before_user_turn": False,
                    "error_stage": None, "error_message": None,
                }
            ],
        },
    }

    with patch("app.routers.dashboard.get_call_log", new=AsyncMock(return_value=fake_log)):
        response = client.get("/calls/browser_eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee")

    assert response.status_code == 200
    assert "overlap 9" in response.text                       # MET-1: negative shown as overlap
    assert "STT not ready before turn" in response.text       # OBS-1: surfaced only when not ready
    assert "latest Commit" in response.text                   # LAT-1: latest-anchored commit chip
    assert "480" in response.text                             # latest Commit->LLM value rendered


def test_latency_summary_responsiveness_and_total():
    from app.routers.dashboard import _latency_summary

    records = [
        # responsiveness = last-utterance->audio (latest anchor); total = VAD primary.
        {"primary_latency_ms": 1200, "latest_user_stop_to_first_audio_ms": 500},
        {"primary_latency_ms": 1400, "latest_user_stop_to_first_audio_ms": 600},
        {"primary_latency_ms": 900},        # no latest -> responsiveness falls back to primary
        {"primary_latency_ms": None},       # unmeasured (no first-audio)
        {},                                 # no key at all
    ]
    s = _latency_summary(records)
    assert s["total_turns"] == 5
    # Total (VAD) measured on 3 turns.
    assert s["total"]["n"] == 3
    assert s["total"]["median"] == 1200          # median(900, 1200, 1400)
    assert s["total"]["max"] == 1400
    # Responsiveness measured on the same 3 turns, but far lower (excludes endpointing).
    assert s["responsiveness"]["n"] == 3
    assert s["responsiveness"]["median"] == 600  # median(500, 600, 900-fallback)
    assert s["responsiveness"]["max"] == 900

    # All-null -> empty stat blocks (em-dash fallback in the UI).
    empty = _latency_summary([{"primary_latency_ms": None}, {}])
    assert empty["responsiveness"]["n"] == 0
    assert empty["total"]["median"] is None
