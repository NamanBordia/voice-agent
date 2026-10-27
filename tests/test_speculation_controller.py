"""Slice 2 — SpeculationController state machine + invariants (fake stream, no network)."""

import asyncio
import copy

from pipecat.processors.aggregators.llm_context import LLMContext

from app.runtime import ACTIVE_SESSIONS
from app.services.speculation_controller import (
    NORMAL,
    RELEASE,
    SUPPRESS,
    DraftState,
    SpeculationController,
)


# --- file-local fakes (mimic the openai/cerebras streaming chunk shape) ---
class _Delta:
    def __init__(self, content):
        self.content = content


class _Choice:
    def __init__(self, content):
        self.delta = _Delta(content)


class _Chunk:
    def __init__(self, content):
        self.choices = [_Choice(content)] if content is not None else []


def _factory(tokens, *, block_evt=None, block_after=None, raise_at=None):
    """Build a draft_stream_factory yielding the given tokens.

    block_after: yield up to and including this index, then await block_evt (stay open).
    raise_at: raise RuntimeError when about to yield this index (stream error).
    """

    async def _gen(messages):
        for i, t in enumerate(tokens):
            if raise_at is not None and i == raise_at:
                raise RuntimeError("boom")
            yield _Chunk(t)
            if block_after is not None and i == block_after and block_evt is not None:
                await block_evt.wait()

    return _gen


def _seed_session(call_sid, *, memory=None, language_state=None, transcript=None):
    ACTIVE_SESSIONS[call_sid] = {
        "call_sid": call_sid,
        "stream_sid": "s",
        "metrics": {},
        "transcript": list(transcript or []),
        "first_latency": None,
        "config_snapshot": {},
        "memory": memory if memory is not None else {},
        "language_state": language_state if language_state is not None else {},
    }


def _ctl(call_sid, factory, *, enabled=True, context=None, memory_cfg=None):
    return SpeculationController(
        call_sid=call_sid,
        context=context or LLMContext(messages=[{"role": "system", "content": "BASE"}]),
        base_system_prompt="BASE",
        memory_cfg=memory_cfg or {"recent_turn_pairs": 3, "max_recall_snippets": 2,
                                  "recall_min_overlap": 2, "max_memory_block_tokens": 600,
                                  "max_pinned_facts": 12},
        draft_stream_factory=factory,
        enabled=enabled,
    )


async def _tick(n=5):
    for _ in range(n):
        await asyncio.sleep(0)


def test_eager_starts_draft_when_enabled():
    call_sid = "c_spec_a1"
    _seed_session(call_sid)
    block = asyncio.Event()
    ctl = _ctl(call_sid, _factory(["<en>", " Hi."], block_evt=block, block_after=0))

    async def run():
        await ctl.on_turn_start()
        await ctl.on_eager("hello there")
        await _tick()
        assert ctl.state == DraftState.DRAFTING
        assert ctl._draft_task is not None and not ctl._draft_task.done()
        assert ctl.marks["draft_started"] is True
        assert isinstance(ctl.marks["draft_start_at"], float)
        assert ctl._draft_id == 1
        assert ctl._token_buffer == ["<en>"]  # first token buffered before the block
        await ctl.aclose()

    try:
        asyncio.run(run())
    finally:
        ACTIVE_SESSIONS.pop(call_sid, None)


def test_eager_ignored_when_disabled():
    call_sid = "c_spec_a2"
    _seed_session(call_sid)
    ctl = _ctl(call_sid, _factory(["x"]), enabled=False)

    async def run():
        await ctl.on_turn_start()
        await ctl.on_eager("hello")
        await _tick()
        assert ctl.state == DraftState.CLEAR
        assert ctl._draft_task is None
        assert ctl._token_buffer == []
        assert not ctl.marks.get("draft_started")

    try:
        asyncio.run(run())
    finally:
        ACTIVE_SESSIONS.pop(call_sid, None)


def test_turn_resumed_cancels_active_draft():
    call_sid = "c_spec_a3"
    _seed_session(call_sid)
    block = asyncio.Event()
    ctl = _ctl(call_sid, _factory(["<en>", " Hi."], block_evt=block, block_after=0))

    async def run():
        await ctl.on_turn_start()
        await ctl.on_eager("hi")
        await _tick()
        assert ctl.state == DraftState.DRAFTING
        await ctl.on_resumed()
        assert ctl.state == DraftState.CLEAR
        assert ctl._draft_task is None
        assert ctl._token_buffer == []
        assert ctl.marks["draft_cancelled"] is True
        assert ctl.marks["draft_cancel_reason"] == "turn_resumed"
        assert ctl.marks["turn_resumed_after_draft"] is True

    try:
        asyncio.run(run())
    finally:
        ACTIVE_SESSIONS.pop(call_sid, None)


def test_end_of_turn_releases_active_draft_on_match():
    call_sid = "c_spec_a4"
    _seed_session(call_sid)
    ctl = _ctl(call_sid, _factory(["<en>", " Hi."]))

    async def run():
        await ctl.on_turn_start()
        await ctl.on_eager("hello world")
        await ctl._draft_done.wait()  # let the draft fully stream
        await ctl.on_end_of_turn("hello world")
        assert ctl.state == DraftState.RELEASE_ARMED
        assert ctl.marks["draft_hit"] is True
        kind, tokens = await ctl.take_release()
        assert kind == RELEASE
        collected = [t async for t in tokens]
        assert collected == ["<en>", " Hi."]
        assert ctl.state == DraftState.RELEASED
        assert ctl._release_consumed is True
        assert ctl.marks["draft_released"] is True
        # a duplicate LLMContextFrame for the same turn is swallowed, not re-released
        kind2, tokens2 = await ctl.take_release()
        assert kind2 == SUPPRESS and tokens2 is None

    try:
        asyncio.run(run())
    finally:
        ACTIVE_SESSIONS.pop(call_sid, None)


def test_end_of_turn_discards_draft_on_mismatch():
    call_sid = "c_spec_a5"
    _seed_session(call_sid)
    block = asyncio.Event()
    ctl = _ctl(call_sid, _factory(["<en>", " Hi."], block_evt=block, block_after=0))

    async def run():
        await ctl.on_turn_start()
        await ctl.on_eager("hello")
        await _tick()
        await ctl.on_end_of_turn("completely different sentence")
        assert ctl.state == DraftState.NORMAL_LLM
        assert ctl.marks["draft_discarded"] is True
        assert ctl.marks["draft_cancel_reason"] == "transcript_mismatch"
        assert ctl._draft_task is None
        kind, tokens = await ctl.take_release()
        assert kind == NORMAL and tokens is None

    try:
        asyncio.run(run())
    finally:
        ACTIVE_SESSIONS.pop(call_sid, None)


def test_end_of_turn_with_no_draft_selects_normal():
    call_sid = "c_spec_a6"
    _seed_session(call_sid)
    ctl = _ctl(call_sid, _factory(["x"]))

    async def run():
        await ctl.on_turn_start()
        # no on_eager this turn
        await ctl.on_end_of_turn("hello")
        assert ctl.state == DraftState.NORMAL_LLM
        assert not ctl.marks.get("draft_started")
        kind, tokens = await ctl.take_release()
        assert kind == NORMAL and tokens is None

    try:
        asyncio.run(run())
    finally:
        ACTIVE_SESSIONS.pop(call_sid, None)


def test_second_eager_does_not_restart_draft():
    call_sid = "c_spec_a8"
    _seed_session(call_sid)
    block = asyncio.Event()
    ctl = _ctl(call_sid, _factory(["<en>", " Hi."], block_evt=block, block_after=0))

    async def run():
        await ctl.on_turn_start()
        await ctl.on_eager("hello")
        await _tick()
        task1 = ctl._draft_task
        await ctl.on_eager("hello there")  # second eager, same turn
        assert ctl._draft_task is task1       # NOT restarted
        assert ctl._draft_id == 1             # unchanged
        assert ctl.state == DraftState.DRAFTING
        assert ctl.marks.get("eager_superseded") is True
        await ctl.aclose()

    try:
        asyncio.run(run())
    finally:
        ACTIVE_SESSIONS.pop(call_sid, None)


def test_late_chunks_from_cancelled_draft_are_ignored():
    call_sid = "c_spec_a7"
    _seed_session(call_sid)
    block = asyncio.Event()
    # yields t1, blocks; t2/t3 only after the block is released (which we never do
    # until AFTER cancel) — proving a cancelled stream contributes no further tokens.
    ctl = _ctl(call_sid, _factory(["t1", "t2", "t3"], block_evt=block, block_after=0))

    async def run():
        await ctl.on_turn_start()
        await ctl.on_eager("hi")
        await _tick()
        assert ctl._token_buffer == ["t1"]
        await ctl.on_resumed()                # cancels, bumps draft_id, clears buffer
        assert ctl._token_buffer == []
        block.set()                           # orphan stream would try t2/t3 now
        await _tick()
        assert ctl._token_buffer == []        # never re-populated by the dead draft
        # a fresh draft is fully isolated
        ctl2_block = asyncio.Event()
        # reuse the same controller; start a new draft with its own factory
        ctl._draft_stream_factory = _factory(["n1"], block_evt=ctl2_block, block_after=0)
        await ctl.on_eager("hi2")
        await _tick()
        assert ctl._token_buffer == ["n1"]
        assert "t2" not in ctl._token_buffer and "t3" not in ctl._token_buffer
        await ctl.aclose()

    try:
        asyncio.run(run())
    finally:
        ACTIVE_SESSIONS.pop(call_sid, None)


def test_per_chunk_guard_blocks_append_after_invalidation():
    # White-box: invalidate the generation WITHOUT cancelling the task, then let the
    # producer step — the per-chunk guard must self-abort before appending t2.
    call_sid = "c_spec_a7b"
    _seed_session(call_sid)
    block = asyncio.Event()
    ctl = _ctl(call_sid, _factory(["t1", "t2"], block_evt=block, block_after=0))

    async def run():
        await ctl.on_turn_start()
        await ctl.on_eager("hi")
        await _tick()
        assert ctl._token_buffer == ["t1"]
        # simulate an invalidation that bumps the generation (active_id mismatch)
        ctl._active_id = None
        ctl._draft_id += 1
        block.set()  # producer resumes, about to yield t2 -> guard raises CancelledError
        await _tick()
        assert "t2" not in ctl._token_buffer

    try:
        asyncio.run(run())
    finally:
        ACTIVE_SESSIONS.pop(call_sid, None)


def test_draft_error_falls_back_to_normal():
    call_sid = "c_spec_a10"
    _seed_session(call_sid)
    ctl = _ctl(call_sid, _factory(["t1"], raise_at=0))  # raises before any token

    async def run():
        await ctl.on_turn_start()
        await ctl.on_eager("hi")
        await ctl._draft_done.wait()
        assert ctl.marks["draft_error"] is True
        await ctl.on_end_of_turn("hi")
        assert ctl.state == DraftState.NORMAL_LLM
        kind, tokens = await ctl.take_release()
        assert kind == NORMAL and tokens is None

    try:
        asyncio.run(run())
    finally:
        ACTIVE_SESSIONS.pop(call_sid, None)


def test_reasoning_only_draft_falls_back_without_releasing_an_empty_reply():
    from types import SimpleNamespace
    call_sid = "reasoning-only-draft"
    _seed_session(call_sid)

    async def reasoning_only(messages):
        yield SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=None, reasoning="private reasoning"))])

    ctl = _ctl(call_sid, reasoning_only)

    async def run():
        await ctl.on_turn_start()
        await ctl.on_eager("hello")
        await ctl._draft_done.wait()
        await ctl.on_end_of_turn("hello")
        kind, tokens = await ctl.take_release()
        assert kind == NORMAL and tokens is None
        assert ctl.marks["draft_error"] is True
        assert not ctl._token_buffer

    asyncio.run(run())


def test_norm_match_uses_shared_normalization():
    call_sid = "c_spec_a11"
    _seed_session(call_sid)
    ctl = _ctl(call_sid, _factory(["<en>", " ok"]))

    async def run():
        await ctl.on_turn_start()
        await ctl.on_eager("Hello   World")
        await ctl._draft_done.wait()
        # differs only by case / extra spaces (NOT punctuation) -> normalized equal
        # via the shared _norm_text -> release armed
        await ctl.on_end_of_turn("hello world")
        assert ctl.state == DraftState.RELEASE_ARMED

    try:
        asyncio.run(run())
    finally:
        ACTIVE_SESSIONS.pop(call_sid, None)


def test_norm_mismatch_on_different_words_allows_normal():
    call_sid = "c_spec_a11b"
    _seed_session(call_sid)
    ctl = _ctl(call_sid, _factory(["<en>", " ok"]))

    async def run():
        await ctl.on_turn_start()
        await ctl.on_eager("apple")
        await ctl._draft_done.wait()
        await ctl.on_end_of_turn("orange")
        assert ctl.state == DraftState.NORMAL_LLM

    try:
        asyncio.run(run())
    finally:
        ACTIVE_SESSIONS.pop(call_sid, None)


def test_on_interruption_cancels_unreleased_draft():
    call_sid = "c_spec_intr"
    _seed_session(call_sid)
    block = asyncio.Event()
    ctl = _ctl(call_sid, _factory(["<en>", " Hi."], block_evt=block, block_after=0))

    async def run():
        await ctl.on_turn_start()
        await ctl.on_eager("hi")
        await _tick()
        assert ctl.state == DraftState.DRAFTING
        await ctl.on_interruption()
        assert ctl.state == DraftState.CLEAR
        assert ctl._draft_task is None
        assert ctl._token_buffer == []
        assert ctl.marks["draft_cancelled"] is True

    try:
        asyncio.run(run())
    finally:
        ACTIVE_SESSIONS.pop(call_sid, None)


def test_on_interruption_leaves_released_draft_alone():
    # Once released, the draft is the active assistant response owned by the normal
    # interruption policy downstream — on_interruption must not cancel it as a draft.
    call_sid = "c_spec_intr2"
    _seed_session(call_sid)
    ctl = _ctl(call_sid, _factory(["<en>", " Hi."]))

    async def run():
        await ctl.on_turn_start()
        await ctl.on_eager("hi")
        await ctl._draft_done.wait()
        await ctl.on_end_of_turn("hi")
        kind, tokens = await ctl.take_release()
        assert kind == RELEASE
        _ = [t async for t in tokens]
        assert ctl.state == DraftState.RELEASED
        await ctl.on_interruption()
        assert ctl.state == DraftState.RELEASED  # untouched

    try:
        asyncio.run(run())
    finally:
        ACTIVE_SESSIONS.pop(call_sid, None)


def test_released_draft_completes_across_next_turn_generation_bump():
    # REGRESSION (review HIGH): a released draft still mid-stream when the NEXT turn's
    # on_eager bumps the generation must NOT self-abort sentinel-less — that hung the
    # gate's drain and wedged the call. With the guard exemption it runs to completion.
    call_sid = "c_spec_hang"
    _seed_session(call_sid)
    block = asyncio.Event()
    ctl = _ctl(call_sid, _factory(["t1", "t2"], block_evt=block, block_after=0))
    collected: list[str] = []

    async def run():
        await ctl.on_turn_start()
        await ctl.on_eager("hi")
        await _tick()
        assert ctl._token_buffer == ["t1"]            # t1 buffered, producer blocked on t2
        await ctl.on_end_of_turn("hi")
        kind, tokens = await ctl.take_release()
        assert kind == RELEASE

        async def _drain():
            async for t in tokens:
                collected.append(t)

        drain_task = asyncio.create_task(_drain())
        await _tick()
        assert collected == ["t1"]                     # drained the buffer; now awaiting the tail
        # NEXT turn begins while the released producer is still streaming
        await ctl.on_turn_start()                      # RELEASED -> CLEAR (does not bump id)
        ctl._draft_stream_factory = _factory(["n1"])
        await ctl.on_eager("hi2")                      # bumps _draft_id -> would trip old guard
        block.set()                                    # unblock the OLD released producer
        # the released drain MUST terminate (sentinel arrives), not hang
        await asyncio.wait_for(drain_task, timeout=2.0)
        await ctl.aclose()

    try:
        asyncio.run(run())
    finally:
        ACTIVE_SESSIONS.pop(call_sid, None)
    assert collected == ["t1", "t2"]                    # full released reply, no hang


def test_redraft_after_resume_then_release_clears_stale_cancel_marks():
    # REGRESSION (review MEDIUM): eager -> resume -> eager -> EOT-hit in ONE Flux turn
    # left BOTH draft_cancelled and draft_released on the marks -> dashboard double-count.
    call_sid = "c_spec_redraft"
    _seed_session(call_sid)
    ctl = _ctl(call_sid, _factory(["<en>", " ok"], block_evt=asyncio.Event(), block_after=0))

    async def run():
        await ctl.on_turn_start()
        await ctl.on_eager("hello")
        await _tick()
        await ctl.on_resumed()
        assert ctl.marks.get("draft_cancelled") is True
        ctl._draft_stream_factory = _factory(["<en>", " ok"])  # fresh, completes
        await ctl.on_eager("hello there")
        await ctl._draft_done.wait()
        await ctl.on_end_of_turn("hello there")
        kind, tokens = await ctl.take_release()
        assert kind == RELEASE
        _ = [t async for t in tokens]
        assert ctl.marks.get("draft_released") is True
        assert ctl.marks.get("draft_cancelled") is None       # stale cancel mark cleared
        assert ctl.marks.get("turn_resumed_after_draft") is None

    try:
        asyncio.run(run())
    finally:
        ACTIVE_SESSIONS.pop(call_sid, None)


def test_take_release_latches_normal_when_decision_not_ready():
    # REGRESSION (review LOW): a context frame that beats on_end_of_turn must latch NORMAL
    # so a later arm can't double-respond (normal LLM + released draft).
    call_sid = "c_spec_latch"
    _seed_session(call_sid)
    ctl = _ctl(call_sid, _factory(["<en>", " ok"], block_evt=asyncio.Event(), block_after=0))

    async def run():
        await ctl.on_turn_start()
        await ctl.on_eager("hello")
        await _tick()
        kind, _ = await ctl.take_release(timeout=0.01)         # decision not ready yet
        assert kind == NORMAL
        assert ctl.state == DraftState.NORMAL_LLM              # latched
        await ctl.on_end_of_turn("hello")                     # late arm attempt
        kind2, _ = await ctl.take_release(timeout=0.01)
        assert kind2 == NORMAL                                 # never releases -> no double

    try:
        asyncio.run(run())
    finally:
        ACTIVE_SESSIONS.pop(call_sid, None)


def test_missed_start_of_turn_does_not_suppress_new_turn():
    # REGRESSION (review MEDIUM): if StartOfTurn is dropped, a stale RELEASED state must
    # not make the gate SUPPRESS (drop) the new turn's context frame.
    call_sid = "c_spec_missed"
    _seed_session(call_sid)
    ctl = _ctl(call_sid, _factory(["<en>", " ok"]))

    async def run():
        await ctl.on_turn_start()
        await ctl.on_eager("hello")
        await ctl._draft_done.wait()
        await ctl.on_end_of_turn("hello")
        kind, tokens = await ctl.take_release()
        assert kind == RELEASE
        _ = [t async for t in tokens]
        assert ctl.state == DraftState.RELEASED
        # turn 2: StartOfTurn MISSED (no on_turn_start) -> on_end_of_turn arrives directly
        await ctl.on_end_of_turn("a brand new question")
        kind2, _ = await ctl.take_release()
        assert kind2 == NORMAL                                 # forwarded, not suppressed

    try:
        asyncio.run(run())
    finally:
        ACTIVE_SESSIONS.pop(call_sid, None)


def test_cancelled_draft_does_not_mutate_session_state():
    call_sid = "c_spec_a9"
    seed_memory = {"pinned_facts": ["likes tea"], "running_summary": "prior summary"}
    seed_lang = {"last_user_language": "en"}
    seed_transcript = [{"event_index": 1, "role": "user", "phase": "task", "text": "earlier"}]
    _seed_session(call_sid, memory=seed_memory, language_state=seed_lang, transcript=seed_transcript)
    ctx = LLMContext(messages=[{"role": "system", "content": "BASE"},
                               {"role": "user", "content": "earlier"},
                               {"role": "assistant", "content": "<en> sure"}])
    ctx_before = copy.deepcopy(ctx.messages)
    mem_before = copy.deepcopy(seed_memory)
    lang_before = copy.deepcopy(seed_lang)
    transcript_before = copy.deepcopy(seed_transcript)
    block = asyncio.Event()
    ctl = _ctl(call_sid, _factory(["<en>", " new"], block_evt=block, block_after=0), context=ctx)

    async def run():
        await ctl.on_turn_start()
        await ctl.on_eager("a brand new fact about latency")
        await _tick()
        await ctl.on_resumed()
        # the draft only read session state; nothing must have changed
        assert ACTIVE_SESSIONS[call_sid]["memory"] == mem_before
        assert ACTIVE_SESSIONS[call_sid]["language_state"] == lang_before
        assert ACTIVE_SESSIONS[call_sid]["transcript"] == transcript_before
        assert ctx.messages == ctx_before

    try:
        asyncio.run(run())
    finally:
        ACTIVE_SESSIONS.pop(call_sid, None)
