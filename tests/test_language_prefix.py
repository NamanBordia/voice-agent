"""Tests for LanguagePrefixProcessor: <en>/<hi> -> Cartesia routing + streaming."""

import asyncio

import pytest
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    TextFrame,
    TTSSpeakFrame,
    TTSUpdateSettingsFrame,
)
from pipecat.observers.base_observer import FramePushed
from pipecat.processors.frame_processor import FrameDirection
from pipecat.transcriptions.language import Language

from app.runtime import ACTIVE_SESSIONS
from app.services.call_metrics import TurnMetricsObserver
from app.services.language import new_language_state
from app.services.language_prefix import LanguagePrefixProcessor


class _FakeMetrics:
    def __init__(self):
        self.assistant_text = None
        self.language = None
        self.errors = []

    def note_assistant_text(self, text):
        self.assistant_text = text

    def note_language(self, *, detected_language, response_language):
        self.language = (detected_language, response_language)

    def note_error(self, *, stage, message):
        self.errors.append((stage, message))


def _make(call_sid="c_lp", metrics=None, seed_state=None):
    if seed_state is not None:
        ACTIVE_SESSIONS[call_sid] = {
            "call_sid": call_sid, "stream_sid": "s", "metrics": {}, "transcript": [],
            "first_latency": None, "config_snapshot": {}, "language_state": seed_state,
        }
    proc = LanguagePrefixProcessor(call_sid=call_sid, metrics_observer=metrics)
    captured: list = []

    async def _capture(frame, direction=FrameDirection.DOWNSTREAM):
        captured.append(frame)

    proc.push_frame = _capture  # type: ignore[method-assign]
    return proc, captured


async def _run(proc, frames):
    for f in frames:
        await proc.process_frame(f, FrameDirection.DOWNSTREAM)


def _spoken(captured):
    return "".join(f.text for f in captured if type(f) is TextFrame)


def _tts_lang_index(captured):
    for i, f in enumerate(captured):
        if isinstance(f, TTSUpdateSettingsFrame):
            return i, f.delta.language
    return None, None


def _first_text_index(captured):
    for i, f in enumerate(captured):
        if type(f) is TextFrame:
            return i
    return None


def test_en_prefix_sets_english_and_forwards_spoken_only():
    m = _FakeMetrics()
    proc, captured = _make(metrics=m)
    asyncio.run(_run(proc, [
        LLMFullResponseStartFrame(),
        LLMTextFrame("<en> Sure, I'll continue in English."),
        LLMFullResponseEndFrame(),
    ]))
    idx, lang_enum = _tts_lang_index(captured)
    assert lang_enum == Language.EN
    assert _spoken(captured) == "Sure, I'll continue in English."
    assert "<en>" not in _spoken(captured)
    assert m.assistant_text == "Sure, I'll continue in English."
    # No STT session seeded -> detected defaults to new_language_state()'s "en".
    assert m.language == ("en", "en")
    assert isinstance(captured[0], LLMFullResponseStartFrame)
    assert isinstance(captured[-1], LLMFullResponseEndFrame)


def test_hi_prefix_sets_hindi():
    m = _FakeMetrics()
    proc, captured = _make(metrics=m)
    asyncio.run(_run(proc, [
        LLMFullResponseStartFrame(),
        LLMTextFrame("<hi> Haan bilkul, batao."),
        LLMFullResponseEndFrame(),
    ]))
    _, lang_enum = _tts_lang_index(captured)
    assert lang_enum == Language.HI
    assert _spoken(captured) == "Haan bilkul, batao."


def test_prefix_split_across_frames():
    proc, captured = _make()
    asyncio.run(_run(proc, [
        LLMFullResponseStartFrame(),
        LLMTextFrame("<"),
        LLMTextFrame("en"),
        LLMTextFrame("> Sure, "),
        LLMTextFrame("happy to help."),
        LLMFullResponseEndFrame(),
    ]))
    _, lang_enum = _tts_lang_index(captured)
    assert lang_enum == Language.EN
    assert _spoken(captured) == "Sure, happy to help."


def test_language_update_emitted_before_first_spoken_text():
    proc, captured = _make()
    asyncio.run(_run(proc, [
        LLMFullResponseStartFrame(),
        LLMTextFrame("<hi> Namaste"),
        LLMFullResponseEndFrame(),
    ]))
    tts_idx, _ = _tts_lang_index(captured)
    text_idx = _first_text_index(captured)
    assert tts_idx is not None and text_idx is not None
    assert tts_idx < text_idx


def test_missing_prefix_falls_back_without_speaking_a_tag():
    m = _FakeMetrics()
    # Seed a prior Hindi response so fallback resolves to 'hi'.
    proc, captured = _make(metrics=m, seed_state={
        "last_user_language": "hi", "response_language": "hi", "turn_count": 1,
    })
    asyncio.run(_run(proc, [
        LLMFullResponseStartFrame(),
        LLMTextFrame("Hello, I forgot my tag entirely."),
        LLMFullResponseEndFrame(),
    ]))
    _, lang_enum = _tts_lang_index(captured)
    assert lang_enum == Language.HI  # fell back to last response language
    assert _spoken(captured) == "Hello, I forgot my tag entirely."
    assert m.errors and m.errors[0][0] == "language_prefix_parse"
    ACTIVE_SESSIONS.pop("c_lp", None)


def test_prefix_never_forwarded_so_not_stored_in_context():
    # The assistant aggregator stores forwarded TextFrames; assert none carry the tag.
    proc, captured = _make()
    asyncio.run(_run(proc, [
        LLMFullResponseStartFrame(),
        LLMTextFrame("<en> Clean text only."),
        LLMFullResponseEndFrame(),
    ]))
    for f in captured:
        assert "<en>" not in getattr(f, "text", "")
        assert "<hi>" not in getattr(f, "text", "")


def test_greeting_ttsspeak_passes_through_untouched():
    proc, captured = _make()
    greeting = TTSSpeakFrame("Hello, you're connected.")
    asyncio.run(_run(proc, [greeting]))
    assert captured == [greeting]  # forwarded unchanged, not intercepted


# --- hardening: misplaced / duplicate / recovery / safe-repeat ----------------

def test_misplaced_mid_response_tag_is_stripped_before_tts():
    # The exact Maya-call bug: "...awesome?<en>Was it..." must not leak <en> to TTS.
    m = _FakeMetrics()
    proc, captured = _make(metrics=m)
    asyncio.run(_run(proc, [
        LLMFullResponseStartFrame(),
        LLMTextFrame("Got it. Which class turned out awesome? "),
        LLMTextFrame("<en>Was it a weird elective?"),
        LLMFullResponseEndFrame(),
    ]))
    spoken = _spoken(captured)
    assert "<en>" not in spoken
    assert "Got it." in spoken and "Was it a weird elective?" in spoken
    assert "<en>" not in (m.assistant_text or "")


def test_split_mid_response_tag_is_stripped_before_tts():
    proc, captured = _make()
    asyncio.run(_run(proc, [
        LLMFullResponseStartFrame(),
        LLMTextFrame("<en>Hello <e"),     # leading tag ok; a second tag begins...
        LLMTextFrame("n>there"),          # ...and completes across the chunk boundary
        LLMFullResponseEndFrame(),
    ]))
    spoken = _spoken(captured)
    assert "<en>" not in spoken
    assert "Hello" in spoken and "there" in spoken


def test_duplicate_leading_tags_are_stripped():
    proc, captured = _make()
    asyncio.run(_run(proc, [
        LLMFullResponseStartFrame(),
        LLMTextFrame("<en><hi>Okay then."),
        LLMFullResponseEndFrame(),
    ]))
    spoken = _spoken(captured)
    assert "<en>" not in spoken and "<hi>" not in spoken
    assert "Okay then." in spoken


def test_missing_prefix_with_valid_text_does_not_fake_repeat():
    # The gel-pen bug: a clear utterance must NOT become "Sorry, could you repeat".
    m = _FakeMetrics()
    proc, captured = _make(metrics=m)
    asyncio.run(_run(proc, [
        LLMFullResponseStartFrame(),
        LLMTextFrame("I use a gel pen, not girlfriend. Gel pen, got it."),
        LLMFullResponseEndFrame(),
    ]))
    spoken = _spoken(captured)
    assert "Sorry, could you repeat" not in spoken
    assert "gel pen" in spoken.lower()
    assert m.errors and m.errors[0][0] == "language_prefix_parse"  # error still recorded


def test_safe_repeat_only_when_nothing_usable():
    # Tag-only output (no real words) is the ONLY case that may safe-repeat.
    proc, captured = _make()
    asyncio.run(_run(proc, [
        LLMFullResponseStartFrame(),
        LLMTextFrame("<en>"),
        LLMFullResponseEndFrame(),
    ]))
    assert _spoken(captured) == "Sorry, could you repeat that?"


def test_empty_reply_reports_llm_no_output_not_prefix_parse():
    # A genuinely EMPTY LLM reply (zero tokens — e.g. a Cerebras 429 / empty
    # completion: the failing turns showed "LLM TTFB —") must be reported as
    # llm_no_output, NOT language_prefix_parse (which would mislabel a rate-limit
    # as a tag bug). The bot still speaks the safe fallback line.
    m = _FakeMetrics()
    proc, captured = _make(metrics=m)
    asyncio.run(_run(proc, [
        LLMFullResponseStartFrame(),
        LLMFullResponseEndFrame(),  # no text frame at all -> empty reply
    ]))
    assert _spoken(captured) == "Sorry, could you repeat that?"
    assert m.errors and m.errors[0][0] == "llm_no_output"
    assert "no output" in m.errors[0][1].lower()


def test_no_control_tag_reaches_tts_or_metrics_on_prefixless_plus_mid_tag():
    m = _FakeMetrics()
    proc, captured = _make(metrics=m)
    asyncio.run(_run(proc, [
        LLMFullResponseStartFrame(),
        LLMTextFrame("Okay, that was oddly specific.<en> Anyway."),
        LLMFullResponseEndFrame(),
    ]))
    for f in captured:
        t = getattr(f, "text", "")
        assert "<en>" not in t and "<hi>" not in t
    assert m.assistant_text and "<en>" not in m.assistant_text


# --- P2-2: malformed tags must never be spoken --------------------------------

@pytest.mark.parametrize("raw,banned", [
    ("<eng> hello there", "<eng>"),
    ("<<en>> hello there", "<<en>"),
    ("<hi hello there", None),
    ("<en", None),
])
def test_malformed_prefix_is_not_spoken(raw, banned):
    m = _FakeMetrics()
    proc, captured = _make(metrics=m)
    asyncio.run(_run(proc, [
        LLMFullResponseStartFrame(),
        LLMTextFrame(raw),
        LLMFullResponseEndFrame(),
    ]))
    spoken = _spoken(captured)
    assert not spoken.startswith("<") and not spoken.startswith(">")
    assert "<en>" not in spoken and "<hi>" not in spoken
    if banned:
        assert banned not in spoken
    _, lang_enum = _tts_lang_index(captured)        # fallback still routes a language
    assert lang_enum in (Language.EN, Language.HI)
    assert m.errors and m.errors[0][0] == "language_prefix_parse"


# --- P2-3: TTS language update only when the language changes ------------------

def test_tts_language_update_deduplicated_across_turns():
    call_sid = "c_dedup"
    ACTIVE_SESSIONS[call_sid] = {
        "call_sid": call_sid, "stream_sid": "s", "metrics": {}, "transcript": [],
        "first_latency": None, "config_snapshot": {}, "language_state": new_language_state(),
    }
    try:
        proc = LanguagePrefixProcessor(call_sid=call_sid, metrics_observer=None)
        captured: list = []

        async def cap(frame, direction=FrameDirection.DOWNSTREAM):
            captured.append(frame)

        proc.push_frame = cap  # type: ignore[method-assign]

        def updates():
            return [f for f in captured if isinstance(f, TTSUpdateSettingsFrame)]

        def turn(tag_text):
            asyncio.run(_run(proc, [
                LLMFullResponseStartFrame(), LLMTextFrame(tag_text), LLMFullResponseEndFrame(),
            ]))

        turn("<en> one");   assert len(updates()) == 1   # first turn always emits
        turn("<en> two");   assert len(updates()) == 1   # same language -> no new emit
        turn("<hi> teen");  assert len(updates()) == 2   # change -> emit
        turn("<hi> chaar"); assert len(updates()) == 2   # same -> no emit
        turn("<en> five");  assert len(updates()) == 3   # change back -> emit
    finally:
        ACTIVE_SESSIONS.pop(call_sid, None)


# --- P1-1: prefix-parse error survives audio-before-LLM-end (integration) ------

def _fp(frame):
    return FramePushed(
        source=None, destination=None, frame=frame,
        direction=FrameDirection.DOWNSTREAM, timestamp=0,
    )


def test_prefix_parse_error_survives_audio_before_llm_end():
    """LanguagePrefixProcessor + TurnMetricsObserver: when bot audio starts before
    the LLM stream ends, the emitted record must still carry BOTH error_stage and
    the spoken text (regresses P1-1 in either file)."""
    out: list = []
    obs = TurnMetricsObserver(on_record=out.append)
    proc = LanguagePrefixProcessor(call_sid="c_p11", metrics_observer=obs)
    captured: list = []

    async def cap(frame, direction=FrameDirection.DOWNSTREAM):
        captured.append(frame)

    proc.push_frame = cap  # type: ignore[method-assign]

    async def scenario():
        obs.note_user_turn(turn_id=1, user_text="hi", stt_eot_received_at=1.0, turn_committed_at=1.0)
        await obs.on_push_frame(_fp(LLMFullResponseStartFrame()))           # promote
        await proc.process_frame(LLMFullResponseStartFrame(), FrameDirection.DOWNSTREAM)
        await proc.process_frame(LLMTextFrame("Hello, I forgot the tag."), FrameDirection.DOWNSTREAM)
        await obs.on_push_frame(_fp(BotStartedSpeakingFrame()))             # audio FIRST
        await obs.on_push_frame(_fp(LLMFullResponseEndFrame()))             # timing only
        assert out == []                                                   # not finalized yet
        await proc.process_frame(LLMFullResponseEndFrame(), FrameDirection.DOWNSTREAM)  # parser finishes

    asyncio.run(scenario())
    assert len(out) == 1
    assert out[0]["error_stage"] == "language_prefix_parse"
    assert out[0]["assistant_text"] == "Hello, I forgot the tag."
