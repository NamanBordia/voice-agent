"""Slices 3-5 — SpecGateProcessor: MISS forward / HIT swallow+emit / SUPPRESS dup / live-tail."""

import asyncio

from pipecat.frames.frames import (
    LLMContextFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    TextFrame,
    TTSUpdateSettingsFrame,
)
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.frame_processor import FrameDirection
from pipecat.transcriptions.language import Language

from app.runtime import ACTIVE_SESSIONS
from app.services.language import new_language_state
from app.services.language_prefix import LanguagePrefixProcessor
from app.services.speculation_controller import (
    NORMAL,
    RELEASE,
    SUPPRESS,
    SpecGateProcessor,
    SpeculationController,
)

DOWN = FrameDirection.DOWNSTREAM


# --- fakes ---
class _Delta:
    def __init__(self, content):
        self.content = content


class _Choice:
    def __init__(self, content):
        self.delta = _Delta(content)


class _Chunk:
    def __init__(self, content):
        self.choices = [_Choice(content)] if content is not None else []


def _factory(tokens, *, block_evt=None, block_after=None):
    async def _gen(messages):
        for i, t in enumerate(tokens):
            yield _Chunk(t)
            if block_after is not None and i == block_after and block_evt is not None:
                await block_evt.wait()

    return _gen


class _FakeController:
    """Scripts take_release for gate-behavior tests."""

    def __init__(self, decision, tokens=None):
        self._decision = decision
        self._tokens = tokens or []
        self.released_finished = 0
        self.interruptions = 0
        self.closed = 0

    async def take_release(self, *, timeout=0.1):
        if self._decision == RELEASE:
            async def _gen():
                for t in self._tokens:
                    yield t
            return RELEASE, _gen()
        if self._decision == SUPPRESS:
            return SUPPRESS, None
        return NORMAL, None

    def note_release_finished(self):
        self.released_finished += 1

    async def on_interruption(self):
        self.interruptions += 1

    async def aclose(self):
        self.closed += 1


def _gate(controller):
    g = SpecGateProcessor(controller=controller)
    captured: list = []

    async def _capture(frame, direction=FrameDirection.DOWNSTREAM):
        captured.append(frame)

    g.push_frame = _capture  # type: ignore[method-assign]
    return g, captured


def _ctx():
    return LLMContext(messages=[{"role": "system", "content": "x"}])


def _seed_session(call_sid):
    ACTIVE_SESSIONS[call_sid] = {
        "call_sid": call_sid, "stream_sid": "s", "metrics": {}, "transcript": [],
        "first_latency": None, "config_snapshot": {}, "memory": {}, "language_state": {},
    }


# --- Slice 3: MISS path ---
def test_gate_forwards_context_frame_on_normal_decision():
    g, captured = _gate(_FakeController(NORMAL))
    frame = LLMContextFrame(context=_ctx())
    asyncio.run(g.process_frame(frame, DOWN))
    assert captured == [frame]  # forwarded unchanged -> real LLM runs


def test_gate_passes_through_non_context_frames():
    g, captured = _gate(_FakeController(RELEASE, tokens=["should-not-emit"]))
    f = TextFrame("hello")
    asyncio.run(g.process_frame(f, DOWN))
    assert captured == [f]  # non-trigger frame untouched; take_release not consulted


# --- Slice 4: HIT path (fully buffered) ---
def test_gate_swallows_and_emits_draft_on_release():
    ctl = _FakeController(RELEASE, tokens=["<en>", " Hi."])
    g, captured = _gate(ctl)
    frame = LLMContextFrame(context=_ctx())
    asyncio.run(g.process_frame(frame, DOWN))
    # the context frame is swallowed -> the real LLM never sees it
    assert not any(isinstance(f, LLMContextFrame) for f in captured)
    assert [type(f) for f in captured] == [
        LLMFullResponseStartFrame, LLMTextFrame, LLMTextFrame, LLMFullResponseEndFrame,
    ]
    assert [f.text for f in captured if isinstance(f, LLMTextFrame)] == ["<en>", " Hi."]
    assert ctl.released_finished == 1


def test_released_body_frames_are_llmtextframe_not_textframe():
    ctl = _FakeController(RELEASE, tokens=["<en>", " ok"])
    g, captured = _gate(ctl)
    asyncio.run(g.process_frame(LLMContextFrame(context=_ctx()), DOWN))
    body = captured[1:-1]
    assert body and all(type(f) is LLMTextFrame for f in body)  # NOT bare TextFrame
    assert isinstance(captured[0], LLMFullResponseStartFrame)
    assert isinstance(captured[-1], LLMFullResponseEndFrame)


def test_gate_suppresses_duplicate_context_frame_after_release():
    g, captured = _gate(_FakeController(SUPPRESS))
    asyncio.run(g.process_frame(LLMContextFrame(context=_ctx()), DOWN))
    assert captured == []  # swallowed, nothing emitted, real LLM not run


# NOTE: the gate's InterruptionFrame interception calls super().process_frame, whose
# base interruption machinery needs an initialized TaskManager — only present in a
# running pipeline. So interruption-CANCEL logic is unit-tested at the controller
# level (test_speculation_controller.test_on_interruption_*); the gate's forward+cancel
# of InterruptionFrame is exercised in live/integration runs. Cleanup leak-prevention
# (the common teardown path) is unit-tested below.
def test_gate_cleanup_closes_controller():
    ctl = _FakeController(NORMAL)
    g, _ = _gate(ctl)
    asyncio.run(g.cleanup())
    assert ctl.closed == 1


# --- Slice 5: live-tail release with the REAL controller (continue, don't restart) ---
def test_gate_release_continues_live_stream_no_dup_no_gap():
    call_sid = "c_gate_livetail"
    _seed_session(call_sid)
    block = asyncio.Event()
    # yields "<en>", " Hi" (buffered before release), blocks, then " there." after release
    factory = _factory(["<en>", " Hi", " there."], block_evt=block, block_after=1)
    ctl = SpeculationController(
        call_sid=call_sid, context=LLMContext(messages=[{"role": "system", "content": "BASE"}]),
        base_system_prompt="BASE", memory_cfg={"recent_turn_pairs": 3},
        draft_stream_factory=factory,
    )
    g, captured = _gate(ctl)

    async def run():
        await ctl.on_turn_start()
        await ctl.on_eager("hello")
        for _ in range(6):
            await asyncio.sleep(0)  # buffer "<en>", " Hi", then block
        assert ctl._token_buffer == ["<en>", " Hi"]
        await ctl.on_end_of_turn("hello")  # match -> RELEASE_ARMED
        release_task = asyncio.create_task(
            g.process_frame(LLMContextFrame(context=ctl._context), DOWN)
        )
        for _ in range(4):
            await asyncio.sleep(0)  # let the gate drain the buffered tokens, then await live
        block.set()                 # producer resumes -> yields " there." -> completes (sentinel)
        await release_task

    try:
        asyncio.run(run())
    finally:
        ACTIVE_SESSIONS.pop(call_sid, None)

    texts = [f.text for f in captured if isinstance(f, LLMTextFrame)]
    assert texts == ["<en>", " Hi", " there."]  # every token once, in order, no gap/dup
    assert isinstance(captured[0], LLMFullResponseStartFrame)
    assert isinstance(captured[-1], LLMFullResponseEndFrame)
    assert not any(isinstance(f, LLMContextFrame) for f in captured)


# --- Slice 7: released draft flows through a REAL LanguagePrefixProcessor ---
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


def _spoken(captured):
    return "".join(f.text for f in captured if type(f) is TextFrame)


def test_released_draft_through_prefix_strips_tag_and_sets_language():
    call_sid = "c_gate_prefix"
    ACTIVE_SESSIONS[call_sid] = {
        "call_sid": call_sid, "stream_sid": "s", "metrics": {}, "transcript": [],
        "first_latency": None, "config_snapshot": {}, "language_state": new_language_state(),
        "memory": {},
    }
    metrics = _FakeMetrics()
    prefix = LanguagePrefixProcessor(call_sid=call_sid, metrics_observer=metrics)
    captured: list = []

    async def _capture(frame, direction=FrameDirection.DOWNSTREAM):
        captured.append(frame)

    prefix.push_frame = _capture  # type: ignore[method-assign]

    ctl = SpeculationController(
        call_sid=call_sid, context=LLMContext(messages=[{"role": "system", "content": "BASE"}]),
        base_system_prompt="BASE", memory_cfg={"recent_turn_pairs": 3},
        draft_stream_factory=_factory(["<en>", " Hello there."]),
    )
    gate = SpecGateProcessor(controller=ctl)
    # the gate emits its released-draft frames straight INTO the real prefix processor
    gate.push_frame = prefix.process_frame  # type: ignore[method-assign]

    async def run():
        await ctl.on_turn_start()
        await ctl.on_eager("hello there")
        await ctl._draft_done.wait()
        await ctl.on_end_of_turn("hello there")  # match -> RELEASE_ARMED
        await gate.process_frame(LLMContextFrame(context=ctl._context), DOWN)

    try:
        asyncio.run(run())
    finally:
        ACTIVE_SESSIONS.pop(call_sid, None)

    # TTS language set from the <en> tag, before any spoken text
    tts_idx = next((i for i, f in enumerate(captured) if isinstance(f, TTSUpdateSettingsFrame)), None)
    first_text_idx = next((i for i, f in enumerate(captured) if type(f) is TextFrame), None)
    assert tts_idx is not None and first_text_idx is not None and tts_idx < first_text_idx
    lang_enum = captured[tts_idx].delta.language
    assert lang_enum == Language.EN
    # tag stripped; spoken text clean; nothing leaked to TTS
    assert _spoken(captured) == "Hello there."
    assert "<en>" not in _spoken(captured) and "<hi>" not in _spoken(captured)
    assert metrics.assistant_text == "Hello there."
    # envelope preserved end-to-end
    assert isinstance(captured[0], LLMFullResponseStartFrame)
    assert isinstance(captured[-1], LLMFullResponseEndFrame)


def test_released_draft_through_prefix_handles_split_tag():
    # the <en> tag fragmented across draft tokens must still be stripped (same
    # streaming-carry path as a real LLM stream).
    call_sid = "c_gate_prefix_split"
    ACTIVE_SESSIONS[call_sid] = {
        "call_sid": call_sid, "stream_sid": "s", "metrics": {}, "transcript": [],
        "first_latency": None, "config_snapshot": {}, "language_state": new_language_state(),
        "memory": {},
    }
    metrics = _FakeMetrics()
    prefix = LanguagePrefixProcessor(call_sid=call_sid, metrics_observer=metrics)
    captured: list = []

    async def _capture(frame, direction=FrameDirection.DOWNSTREAM):
        captured.append(frame)

    prefix.push_frame = _capture  # type: ignore[method-assign]
    ctl = SpeculationController(
        call_sid=call_sid, context=LLMContext(messages=[{"role": "system", "content": "BASE"}]),
        base_system_prompt="BASE", memory_cfg={"recent_turn_pairs": 3},
        draft_stream_factory=_factory(["<", "en", "> Namaste", " ji."]),
    )
    gate = SpecGateProcessor(controller=ctl)
    gate.push_frame = prefix.process_frame  # type: ignore[method-assign]

    async def run():
        await ctl.on_turn_start()
        await ctl.on_eager("namaste")
        await ctl._draft_done.wait()
        await ctl.on_end_of_turn("namaste")
        await gate.process_frame(LLMContextFrame(context=ctl._context), DOWN)

    try:
        asyncio.run(run())
    finally:
        ACTIVE_SESSIONS.pop(call_sid, None)

    assert _spoken(captured) == "Namaste ji."
    assert "<en>" not in _spoken(captured)
