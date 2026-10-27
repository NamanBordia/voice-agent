"""Unit tests for the in-pipeline processors (memory governance + language frame).

These cover behavior that the assembly test cannot: that MemoryProcessor trims
history and rebuilds the system block (the current user message is appended
later by the aggregator, not here), and that the Cartesia language-update frame
carries the correct delta type.
"""

from pipecat.frames.frames import TTSUpdateSettingsFrame
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.services.sarvam.tts import SarvamTTSSettings
from pipecat.services.settings import TTSSettings
from pipecat.transcriptions.language import Language

from app.runtime import ACTIVE_SESSIONS
from app.services.pipecat_pipeline import MemoryProcessor


def _seed_session(call_sid: str):
    ACTIVE_SESSIONS[call_sid] = {
        "call_sid": call_sid,
        "stream_sid": "s",
        "metrics": {},
        "transcript": [],
        "first_latency": None,
        "config_snapshot": {},
        "memory": {"pinned_facts": [], "running_summary": None, "summary_last_updated_turn": 0},
        "language_state": {
            "last_user_language": "en", "response_language": "en", "turn_count": 0,
        },
    }


def test_memory_processor_trims_history_and_rebuilds_system():
    call_sid = "c_mem"
    _seed_session(call_sid)
    try:
        ctx = LLMContext(messages=[{"role": "system", "content": "BASE"}])
        for i in range(10):
            ctx.add_message({"role": "user", "content": f"u{i}"})
            ctx.add_message({"role": "assistant", "content": f"a{i}"})

        mp = MemoryProcessor(
            call_sid=call_sid, context=ctx, base_system_prompt="BASE",
            memory_cfg={"recent_turn_pairs": 3, "max_pinned_facts": 12,
                        "recall_only_when_user_asks": True, "max_recall_snippets": 2},
        )
        # Simulates the finalized user turn arriving (current msg NOT appended here;
        # the user aggregator appends it downstream).
        mp._govern_context("I want low latency replies")

        msgs = ctx.messages
        # 1 system + 3 pairs (6 messages) retained.
        assert len(msgs) == 7
        assert msgs[0]["role"] == "system"
        assert "BASE" in msgs[0]["content"]
        assert "I want low latency replies" in msgs[0]["content"]  # pinned fact injected
        # Most recent history kept; oldest dropped; current user msg not present yet.
        assert msgs[-1] == {"role": "assistant", "content": "a9"}
        assert msgs[1] == {"role": "user", "content": "u7"}
        assert all(m["content"] != "u0" for m in msgs)
    finally:
        ACTIVE_SESSIONS.pop(call_sid, None)


def test_memory_processor_does_not_inject_full_transcript():
    call_sid = "c_mem2"
    _seed_session(call_sid)
    try:
        ctx = LLMContext(messages=[{"role": "system", "content": "BASE"}])
        for i in range(30):
            ctx.add_message({"role": "user", "content": f"u{i}"})
            ctx.add_message({"role": "assistant", "content": f"a{i}"})
        mp = MemoryProcessor(
            call_sid=call_sid, context=ctx, base_system_prompt="BASE",
            memory_cfg={"recent_turn_pairs": 6},
        )
        mp._govern_context("just talking")
        # system + 12 recent messages, never the whole 60-message transcript.
        assert len(ctx.messages) == 13
    finally:
        ACTIVE_SESSIONS.pop(call_sid, None)


def test_govern_context_recalls_assistant_claim_past_window():
    # Stage-1 fix: an assistant claim older than the recent window is retrieved by
    # lexical recall and injected into the system block as a quote (not asserted).
    call_sid = "c_recall"
    _seed_session(call_sid)
    ACTIVE_SESSIONS[call_sid]["transcript"] = [
        {"event_index": 1, "role": "assistant", "phase": "task",
         "text": "We have demo stations at Delhi Mall, Mumbai Central, Bangalore Tech Park."},
    ] + [
        {"event_index": i + 2, "role": "user", "phase": "task", "text": t}
        for i, t in enumerate(["okay", "sure", "thanks", "got it", "no problem", "alright"])
    ]
    try:
        ctx = LLMContext(messages=[{"role": "system", "content": "BASE"}])
        ctx.add_message({"role": "user", "content": "u"})
        ctx.add_message({"role": "assistant", "content": "a"})
        mp = MemoryProcessor(
            call_sid=call_sid, context=ctx, base_system_prompt="BASE",
            memory_cfg={"recent_turn_pairs": 3, "max_recall_snippets": 2,
                        "recall_min_overlap": 2, "max_memory_block_tokens": 600},
        )
        mp._govern_context("you already told me the demo locations, don't you remember?")
        system = ctx.messages[0]["content"]
        assert "Delhi Mall" in system        # the older assistant claim is recalled
        assert "assistant:" in system         # surfaced as a quote, not asserted as truth
    finally:
        ACTIVE_SESSIONS.pop(call_sid, None)


def test_govern_context_injects_code_owned_protocol_and_guardrail_before_persona():
    call_sid = "c_guard"
    _seed_session(call_sid)
    try:
        ctx = LLMContext(messages=[{"role": "system", "content": "PERSONA-X"}])
        mp = MemoryProcessor(
            call_sid=call_sid, context=ctx, base_system_prompt="PERSONA-X",
            memory_cfg={"recent_turn_pairs": 6},
        )
        mp._govern_context("hello")
        system = ctx.messages[0]["content"]
        assert "OUTPUT PROTOCOL" in system            # code-owned language contract
        assert "Do not invent real addresses" in system  # code-owned factual guardrail
        assert "PERSONA-X" in system                  # persona still present
        # both system rules precede the persona, so the persona can't override them
        assert system.index("OUTPUT PROTOCOL") < system.index("PERSONA-X")
        assert system.index("Do not invent real addresses") < system.index("PERSONA-X")
    finally:
        ACTIVE_SESSIONS.pop(call_sid, None)


def test_memory_hot_path_is_synchronous_no_llm():
    # Structural guarantee: governance runs on the hot path, so it must be pure/
    # synchronous — no awaits means no network/LLM call is possible there.
    import asyncio
    assert not asyncio.iscoroutinefunction(MemoryProcessor._govern_context)


def test_cartesia_language_update_frame_delta_type():
    frame = TTSUpdateSettingsFrame(delta=SarvamTTSSettings(language=Language.HI))
    assert isinstance(frame.delta, TTSSettings)
    assert isinstance(frame.delta, SarvamTTSSettings)
    assert frame.delta.language == Language.HI


# --- STT-1: transcript observability -----------------------------------------

def test_stt_observability_logs_final_and_records_hint():
    from pipecat.frames.frames import InterimTranscriptionFrame, TranscriptionFrame
    from app.runtime import get_session_language_state
    from app.services.pipecat_pipeline import SttLanguageHintProcessor

    call_sid = "c_stt"
    _seed_session(call_sid)
    try:
        proc = SttLanguageHintProcessor(call_sid=call_sid)
        # Interim: logged, no hint recorded, no crash.
        proc._observe_transcript(
            InterimTranscriptionFrame(text="pe", user_id="u", timestamp="t", language=Language.EN)
        )
        # Final: logged + weak hint recorded.
        proc._observe_transcript(
            TranscriptionFrame(
                text="pen please", user_id="u", timestamp="t",
                language=Language.HI, finalized=True,
            )
        )
        assert get_session_language_state(call_sid)["last_user_language"] == "hi"
    finally:
        ACTIVE_SESSIONS.pop(call_sid, None)


def test_stt_observability_tolerates_missing_language_and_text():
    from pipecat.frames.frames import TextFrame, TranscriptionFrame
    from app.services.pipecat_pipeline import SttLanguageHintProcessor

    call_sid = "c_stt2"
    _seed_session(call_sid)
    try:
        proc = SttLanguageHintProcessor(call_sid=call_sid)
        # Finalized but no language and empty text -> must not crash.
        proc._observe_transcript(
            TranscriptionFrame(text="", user_id="u", timestamp="t", finalized=True)
        )
        # A non-transcript frame is simply ignored.
        proc._observe_transcript(TextFrame(text="not a transcript"))
    finally:
        ACTIVE_SESSIONS.pop(call_sid, None)


def test_stt_observability_does_not_leak_secrets():
    from loguru import logger
    from pipecat.frames.frames import TranscriptionFrame
    from app.config import get_settings
    from app.services.pipecat_pipeline import SttLanguageHintProcessor

    call_sid = "c_stt3"
    _seed_session(call_sid)
    captured: list[str] = []
    # Render structured `extra` fields so the assertion sees the logged transcript.
    sink_id = logger.add(captured.append, level="DEBUG", format="{message} {extra}")
    try:
        proc = SttLanguageHintProcessor(call_sid=call_sid)
        proc._observe_transcript(
            TranscriptionFrame(text="pen please", user_id="u", timestamp="t", finalized=True)
        )
        blob = "".join(captured)
        assert "pen please" in blob                          # transcript IS logged
        assert get_settings().sarvam_api_key not in blob   # but never the API key
    finally:
        logger.remove(sink_id)
        ACTIVE_SESSIONS.pop(call_sid, None)
