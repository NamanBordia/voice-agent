"""Stage-2 speculative-LLM controller (OFF by default).

A small, deterministic per-call object that owns an *optional early draft cache*.
On Deepgram Flux ``EagerEndOfTurn`` it starts an out-of-band Cerebras draft on the
eager transcript; at final ``EndOfTurn`` it decides — purely on a normalized
eager==final transcript match — whether to RELEASE that draft (the gate swallows
the normal LLM trigger and replays/continues the draft) or fall back to the normal
LLM. ``TurnResumed`` is the main invalidation signal.

The mental model is one final response path + this draft cache + one gate decision:

    draft hit  -> gate swallows the LLMContextFrame and continues the draft
    draft miss -> gate forwards the LLMContextFrame to the normal LLM (the ONLY fallback)

Hard invariants enforced here:
  * The draft NEVER pushes frames and NEVER mutates session memory / transcript /
    assistant history / language state / the metrics record. It only fills a
    private, generation-tagged token buffer + live queue (read-only context build).
  * A monotonic ``draft_id`` guards every token append, so a late chunk from a
    cancelled/superseded draft can never enter a releasable buffer.
  * Once a draft is RELEASED it is the active assistant response flowing through the
    normal downstream pipeline; it is no longer treated as a cancellable pre-final
    draft (StartOfTurn/TurnResumed must not corrupt it).

This module holds live asyncio objects (task/queue), so a controller instance is
per-call and closure/processor-owned — never stored in ``ACTIVE_SESSIONS`` (which
JSON-round-trips). The streaming source is injected as ``draft_stream_factory`` so
it is fully unit-testable without a network/Cerebras dependency.
"""

from __future__ import annotations

import asyncio
import enum
import inspect
import time
from typing import Any, AsyncIterator, Awaitable, Callable

from loguru import logger
from pipecat.frames.frames import (
    CancelFrame,
    EndFrame,
    Frame,
    InterruptionFrame,
    LLMContextFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
)
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from app.runtime import (
    get_session_language_state,
    get_session_memory,
    get_session_transcript,
)
from app.services import language as lang
from app.services import memory as mem
from app.services.call_metrics import _norm_text

# A stream factory takes the built draft messages and returns an async iterator of
# chunk-like objects (``chunk.choices[0].delta.content``), or an awaitable that
# resolves to one (e.g. ``await client.chat.completions.create(stream=True)``).
DraftStreamFactory = Callable[
    [list[dict[str, Any]]],
    "AsyncIterator[Any] | Awaitable[AsyncIterator[Any]]",
]

# Decision kinds returned by ``take_release`` (consumed by SpecGateProcessor):
RELEASE = "release"    # swallow the LLMContextFrame; emit/continue the draft envelope
SUPPRESS = "suppress"  # swallow a *duplicate* LLMContextFrame after release; emit nothing
NORMAL = "normal"      # forward the LLMContextFrame unchanged -> normal LLM runs


class DraftState(enum.Enum):
    IDLE = "idle"                    # constructed / quiescent between turns
    CLEAR = "clear"                  # inside a turn, no draft running, awaiting eager
    DRAFTING = "drafting"            # out-of-band stream running, tokens buffering
    RELEASE_ARMED = "release_armed"  # EndOfTurn matched -> release armed, gate not yet consumed
    NORMAL_LLM = "normal_llm"        # EndOfTurn decided no usable draft -> forward to LLM
    RELEASED = "released"            # gate consumed the release; draft is the active response


def _extract_delta(chunk: Any) -> str | None:
    """Pull the incremental text from an OpenAI/Cerebras streaming chunk."""
    choices = getattr(chunk, "choices", None)
    if not choices:
        return None
    delta = getattr(choices[0], "delta", None)
    return getattr(delta, "content", None) if delta is not None else None


class SpeculationController:
    """Owns the draft state machine for one call. See module docstring."""

    _SENTINEL = object()  # marks end-of-stream on the live token queue

    def __init__(
        self,
        *,
        call_sid: str,
        context: LLMContext,
        base_system_prompt: str,
        memory_cfg: dict[str, Any],
        draft_stream_factory: DraftStreamFactory,
        enabled: bool = True,
        now: Callable[[], float] = time.time,
    ) -> None:
        self._call_sid = call_sid
        self._context = context
        self._base_system = base_system_prompt
        self._cfg = memory_cfg or {}
        self._draft_stream_factory = draft_stream_factory
        self._enabled = enabled
        self._now = now

        self._state: DraftState = DraftState.IDLE
        # Generation guard: bumped on every draft start AND every cancel so a stale
        # chunk's captured id can never equal the live id.
        self._draft_id = 0
        self._active_id: int | None = None   # the id currently authorized to emit
        self._turn_seq = 0

        self._draft_task: asyncio.Task | None = None
        self._token_buffer: list[str] = []   # tokens received so far (diagnostics)
        self._queue: asyncio.Queue = asyncio.Queue()   # live token channel (drained on release)
        self._draft_done: asyncio.Event = asyncio.Event()
        self._context_snapshot: list[dict[str, Any]] | None = None
        self._eager_text_norm = ""

        self._decision_ready = asyncio.Event()  # set when EndOfTurn picks release/normal
        self._release_consumed = False
        self._released_generation: int | None = None
        self._lock = asyncio.Lock()
        self._marks: dict[str, Any] = {}

    # -- read-only accessors (handy for tests / metrics) --
    @property
    def state(self) -> DraftState:
        return self._state

    @property
    def marks(self) -> dict[str, Any]:
        return self._marks

    def drain_marks(self) -> dict[str, Any]:
        """Return the JSON-safe speculative marks accumulated this turn."""
        return dict(self._marks)

    # ----------------------------------------------------------------- events
    # NOTE: Deepgram Flux dispatches these handlers as independent sync=False asyncio
    # tasks, so ordering across them is best-effort (relied on only via asyncio FIFO
    # scheduling of distinct WS frames). The controller is deliberately ORDER-TOLERANT:
    # every transition is guarded by state + the monotonic generation id + _decision_ready,
    # so a worst-case reorder degrades to the normal-LLM fallback, never to wrong output.
    async def on_turn_start(self) -> None:
        """New user turn: cancel an UNRELEASED draft; never touch a released one."""
        async with self._lock:
            self._turn_seq += 1
            if self._state in (DraftState.DRAFTING, DraftState.RELEASE_ARMED):
                # an unreleased draft from a prior eager (including one armed but never
                # consumed by the gate, e.g. a no-commit EOT) is stale for the new turn
                await self._cancel_active("new_turn")
            # if RELEASED: the draft has become the active assistant response flowing
            # through the pipeline; do NOT cancel it here (Correction 3) — the normal
            # interruption policy owns it. We only reset our own per-turn bookkeeping;
            # the released drain holds its own captured queue, so resetting is safe.
            self._state = DraftState.CLEAR
            self._marks = {}
            self._decision_ready = asyncio.Event()
            self._release_consumed = False
            self._context_snapshot = None

    async def on_eager(self, transcript: str) -> None:
        """EagerEndOfTurn: start a background draft (only from CLEAR; never restart)."""
        if not self._enabled:
            return
        async with self._lock:
            if self._state != DraftState.CLEAR:
                # Second eager mid-draft (or any non-CLEAR): do NOT restart — that would
                # throw away the head start. Keep streaming the current draft.
                self._marks["eager_superseded"] = True
                return
            # Build the read-only snapshot BEFORE committing any draft state, so a
            # build failure degrades cleanly to "no draft -> normal LLM" (stay CLEAR).
            try:
                snapshot = self._build_draft_messages(transcript)
            except Exception as exc:  # never let a draft hiccup break the turn
                logger.warning("Speculative draft snapshot build failed: {err}", err=str(exc))
                return
            self._draft_id += 1
            my_id = self._draft_id
            self._active_id = my_id
            self._eager_text_norm = _norm_text(transcript)
            self._token_buffer = []
            queue: asyncio.Queue = asyncio.Queue()
            done = asyncio.Event()
            self._queue = queue
            self._draft_done = done
            self._context_snapshot = snapshot
            # A prior cancelled/superseded draft in the SAME Flux turn (eager -> resume ->
            # eager, no intervening StartOfTurn) leaves stale outcome marks; clear them so
            # this turn's record can't be counted as BOTH released and cancelled.
            for _k in ("draft_cancelled", "draft_cancel_reason", "draft_hit",
                       "draft_discarded", "turn_resumed_after_draft"):
                self._marks.pop(_k, None)
            self._marks["draft_started"] = True
            self._marks["draft_start_at"] = self._now()
            self._draft_task = asyncio.create_task(
                self._run_draft(my_id, snapshot, queue, done, self._token_buffer)
            )
            self._state = DraftState.DRAFTING

    async def on_resumed(self) -> None:
        """TurnResumed (MAIN invalidation): cancel an unreleased draft, discard it."""
        async with self._lock:
            self._marks["turn_resumed_seen"] = True
            if self._state == DraftState.DRAFTING:
                self._marks["draft_cancelled"] = True
                self._marks["draft_cancel_reason"] = "turn_resumed"
                self._marks["turn_resumed_after_draft"] = True
                await self._cancel_active("turn_resumed")
                self._state = DraftState.CLEAR
            # RELEASED -> leave the active response alone; CLEAR/other -> no draft to kill

    async def on_end_of_turn(self, final_transcript: str) -> None:
        """Final EndOfTurn: arm release on a normalized match, else allow normal LLM.

        The decision is committed synchronously (under the lock) and ``_decision_ready``
        is set, so the gate — which sees the debounced LLMContextFrame ~250-500ms later —
        reads a settled decision (and may briefly await this event if it races ahead).
        """
        async with self._lock:
            # Defensive: a fresh EndOfTurn while we are still latched in a prior turn's
            # post-decision terminal state (RELEASED, or a consumed NORMAL_LLM) means a
            # new turn began without its StartOfTurn (a dropped/missed boundary). Reset
            # per-turn bookkeeping first, so the gate can't read the prior decision and
            # SUPPRESS/short-circuit this new turn's context frame. In normal flow
            # on_turn_start already ran, so this is a no-op.
            if self._state == DraftState.RELEASED or (
                self._state == DraftState.NORMAL_LLM and self._decision_ready.is_set()
            ):
                self._turn_seq += 1
                self._state = DraftState.CLEAR
                self._marks = {}
                self._decision_ready = asyncio.Event()
                self._release_consumed = False
                self._context_snapshot = None
            if self._state == DraftState.DRAFTING:
                if _norm_text(final_transcript) == self._eager_text_norm:
                    self._state = DraftState.RELEASE_ARMED
                    self._marks["draft_hit"] = True
                else:
                    # Minimal mismatch handling: log + discard + normal LLM. No semantics.
                    self._marks["draft_discarded"] = True
                    self._marks["draft_cancel_reason"] = "transcript_mismatch"
                    await self._cancel_active("transcript_mismatch")
                    self._state = DraftState.NORMAL_LLM
            elif self._state in (DraftState.CLEAR, DraftState.IDLE):
                self._state = DraftState.NORMAL_LLM
            # RELEASE_ARMED/RELEASED/NORMAL_LLM already decided -> leave as-is
            self._decision_ready.set()

    # ------------------------------------------------------------ gate API
    async def take_release(self, *, timeout: float = 0.5) -> tuple[str, AsyncIterator[str] | None]:
        """Consume the per-turn response decision for one LLMContextFrame.

        Returns ``(RELEASE, token_iter)`` to swallow + continue the draft, ``(SUPPRESS,
        None)`` to swallow a duplicate context frame after release (merged turns emit
        more than one), or ``(NORMAL, None)`` to forward to the normal LLM.
        """
        if not self._decision_ready.is_set():
            # The decision is normally set well before the debounced LLMContextFrame
            # arrives; this bounded wait only covers a rare race, then fails safe to NORMAL.
            try:
                await asyncio.wait_for(self._decision_ready.wait(), timeout)
            except asyncio.TimeoutError:
                pass
        async with self._lock:
            if self._state == DraftState.RELEASE_ARMED and not self._release_consumed:
                self._release_consumed = True
                self._released_generation = self._draft_id
                self._state = DraftState.RELEASED
                self._marks["draft_released"] = True
                self._marks["draft_used"] = True
                queue = self._queue   # capture so a later turn's reset can't redirect the drain
                return RELEASE, self._drain(queue)
            if self._state == DraftState.RELEASED:
                # duplicate LLMContextFrame for the same turn -> swallow, emit nothing
                return SUPPRESS, None
            # Decision not ready (still DRAFTING/CLEAR after the bounded wait): forward
            # NORMAL and LATCH it, so a late on_end_of_turn arm + a subsequent context
            # frame can never release a draft into an already-forwarded normal reply
            # (the double-response guard). Cancel any in-flight draft for this turn.
            if self._state in (DraftState.DRAFTING, DraftState.CLEAR):
                if self._state == DraftState.DRAFTING:
                    await self._cancel_active("decision_not_ready")
                self._state = DraftState.NORMAL_LLM
                self._release_consumed = True
                self._decision_ready.set()
                self._marks.setdefault("draft_cancel_reason", "decision_not_ready")
            return NORMAL, None

    async def _drain(self, queue: asyncio.Queue) -> AsyncIterator[str]:
        """Yield buffered + still-streaming draft tokens in order until end-of-stream."""
        while True:
            tok = await queue.get()
            if tok is self._SENTINEL:
                break
            yield tok

    def note_release_finished(self) -> None:
        """Called by the gate after it has emitted the End frame for a released draft."""
        # Nothing to clean here beyond bookkeeping; the next on_turn_start resets state.
        self._marks.setdefault("draft_released", True)

    # ------------------------------------------------------------ lifecycle
    async def aclose(self) -> None:
        """Cancel any active draft on call end / teardown (leak protection)."""
        async with self._lock:
            await self._cancel_active("teardown")
            self._state = DraftState.IDLE

    async def on_interruption(self) -> None:
        """Barge-in / pipeline interruption: cancel an unreleased draft.

        A released draft is the active response and is owned by the normal
        interruption policy downstream, so it is not cancelled here.
        """
        async with self._lock:
            if self._state in (DraftState.DRAFTING, DraftState.RELEASE_ARMED):
                self._marks["draft_cancelled"] = True
                self._marks.setdefault("draft_cancel_reason", "interruption")
                await self._cancel_active("interruption")
                self._state = DraftState.CLEAR

    # ------------------------------------------------------------ internals
    async def _cancel_active(self, reason: str) -> None:
        """Invalidate the current draft generation synchronously, then cancel its task."""
        # Order matters: bump the generation BEFORE awaiting cancel so any in-flight
        # chunk self-aborts at its next per-chunk guard check.
        self._active_id = None
        self._draft_id += 1
        self._token_buffer = []
        task = self._draft_task
        self._draft_task = None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:  # teardown of the draft stream must never crash the call
                pass

    def _handle_draft_error(self, my_id: int, exc: Exception) -> None:
        """Record a draft-stream error and demote to the normal path (draft = miss).

        CONTRACT: this MUST stay synchronous (no ``await``) and MUST NOT take
        ``self._lock``. It is called from the draft task, which ``_cancel_active``
        ``await``s while holding the lock — acquiring the lock here would deadlock.
        Being await-free makes it atomic under cooperative scheduling, and the
        ``my_id != self._draft_id`` generation guard prevents a stale error from
        flipping a newer generation's state. Do not add an await or a lock here.
        """
        if my_id != self._draft_id:
            return  # stale generation's error is irrelevant
        self._marks["draft_error"] = True
        if self._state == DraftState.RELEASED:
            # tokens may already be speaking; the gate finishes the envelope on the
            # sentinel (emitted by _run_draft below). Record and let it close cleanly.
            self._marks["draft_error_after_release"] = True
        else:
            self._active_id = None
            if self._state == DraftState.DRAFTING:
                self._state = DraftState.CLEAR        # EndOfTurn will pick NORMAL_LLM
            elif self._state == DraftState.RELEASE_ARMED:
                self._state = DraftState.NORMAL_LLM
                self._decision_ready.set()

    async def _run_draft(
        self,
        my_id: int,
        messages: list[dict[str, Any]],
        queue: asyncio.Queue,
        done: asyncio.Event,
        buf: list[str],
    ) -> None:
        """Out-of-band draft stream consumer. Appends to buf + queue under the guard.

        Pushes NO pipeline frames and mutates NO session state — it only fills the
        private buffer/queue. On normal completion or error it emits a sentinel so a
        draining (released) gate can finish the envelope cleanly; on cancel it does
        not (a cancelled draft is discarded, never released).
        """
        try:
            result = self._draft_stream_factory(messages)
            if inspect.isawaitable(result):
                result = await result
            agen = result.__aiter__()
            try:
                async for chunk in agen:
                    # Per-chunk generation guard: a cancelled/superseded draft stops here.
                    # A RELEASED draft is EXEMPT: it is the active response the gate is
                    # draining, so it must run to natural completion and post its sentinel
                    # even though the NEXT turn's on_eager has already bumped
                    # _draft_id/_active_id — otherwise the gate's drain hangs forever on
                    # queue.get() and wedges the call. Genuinely cancelled (pre-release)
                    # drafts still self-abort here because my_id != _released_generation.
                    if my_id != self._released_generation and (
                        my_id != self._draft_id or my_id != self._active_id
                    ):
                        raise asyncio.CancelledError
                    delta = _extract_delta(chunk)
                    if delta:
                        if my_id == self._draft_id and self._marks.get("draft_first_token_at") is None:
                            self._marks["draft_first_token_at"] = self._now()
                        buf.append(delta)
                        queue.put_nowait(delta)
            finally:
                aclose = getattr(agen, "aclose", None)
                if aclose is not None:
                    await aclose()
            if not buf:
                raise RuntimeError("Draft completed without final-answer text")
            # normal completion -> end the live stream for a draining gate
            queue.put_nowait(self._SENTINEL)
        except asyncio.CancelledError:
            # A discarded pre-final draft is abandoned with no sentinel (its queue is
            # never drained). But if this generation was already RELEASED and is somehow
            # cancelled, the gate IS draining its queue — post the sentinel so the drain
            # can never hang. (Defense-in-depth; the guard exemption above already keeps
            # a released draft from reaching here via the generation guard.)
            if my_id == self._released_generation:
                queue.put_nowait(self._SENTINEL)
            raise
        except Exception as exc:  # network/API/parse error
            self._handle_draft_error(my_id, exc)
            queue.put_nowait(self._SENTINEL)  # let a released, draining gate close cleanly
        finally:
            done.set()
            if my_id == self._draft_id:
                self._draft_task = None

    def _build_draft_messages(self, eager_text: str) -> list[dict[str, Any]]:
        """Build the draft's PRIVATE, read-only message list (no session mutation).

        Mirrors MemoryProcessor._govern_context's *reads* (system block + recent
        history + the eager transcript as the latest user message) but calls NO
        setter and NO ``extract_pinned_facts``. Recent-history element dicts are
        COPIED per-element because downstream adapters can rewrite message dicts
        in place. There is no ``await`` between reading and copying the shared
        session structures, so no torn read can occur.
        """
        memory = get_session_memory(self._call_sid) or mem.new_call_memory()
        lstate = get_session_language_state(self._call_sid) or lang.new_language_state()
        hint = lang.language_hint_line(lstate)

        recent_pairs = int(self._cfg.get("recent_turn_pairs", 6))
        min_overlap = int(self._cfg.get("recall_min_overlap", 2))
        if mem.recall_triggered(eager_text):
            min_overlap = 1
        recall = mem.search_recall_snippets(
            get_session_transcript(self._call_sid),
            query=eager_text,
            max_snippets=int(self._cfg.get("max_recall_snippets", 2)),
            min_overlap=min_overlap,
            exclude_recent=recent_pairs * 2,
        )
        system_block = mem.build_system_block(
            self._base_system,
            language_policy=lang.RESPONSE_LANGUAGE_POLICY,
            factual_guardrail=lang.FACTUAL_GUARDRAIL,
            language_hint=hint,
            pinned_facts=memory.get("pinned_facts", []),
            running_summary=memory.get("running_summary"),
            recall_snippets=recall,
            max_dynamic_tokens=int(self._cfg.get("max_memory_block_tokens", 600)),
        )
        trimmed = [
            dict(m) if isinstance(m, dict) else m
            for m in mem.trim_recent_messages(self._context.messages, recent_turn_pairs=recent_pairs)
        ]
        return (
            [{"role": "system", "content": system_block}]
            + trimmed
            + [{"role": "user", "content": eager_text}]
        )


class SpecGateProcessor(FrameProcessor):
    """The single response gate (between context_aggregator.user() and the LLM).

    On the turn-trigger ``LLMContextFrame`` it asks the controller for the per-turn
    decision and either:
      * RELEASE  — swallow the frame and emit the buffered+continuing draft as a
        normal LLM envelope (Start / LLMTextFrame* / End), so the real LLM never runs;
      * SUPPRESS — swallow a *duplicate* context frame for an already-released turn
        (merged/multi-inference turns emit more than one) and emit nothing;
      * NORMAL   — forward the frame unchanged so the real LLM runs (the only fallback).

    Every other frame is passed through untouched, so when speculation is disabled the
    gate is never inserted and the pipeline is byte-identical. An ``InterruptionFrame``
    cancels an unreleased draft (a released draft is owned by the normal interruption
    policy downstream). The gate owns the controller's lifecycle via ``cleanup``.
    """

    def __init__(self, *, controller: SpeculationController) -> None:
        super().__init__()
        self._controller = controller

    async def process_frame(
        self, frame: Frame, direction: FrameDirection = FrameDirection.DOWNSTREAM
    ) -> None:
        await super().process_frame(frame, direction)

        if isinstance(frame, (EndFrame, CancelFrame)):
            # Call/pipeline teardown: cancel any in-flight draft task + Cerebras stream
            # HERE, not only in cleanup(). Pipeline._cleanup_processors has no per-processor
            # try/except, so a processor upstream of this gate raising in cleanup() would
            # skip our cleanup() and leak the draft task/socket. aclose() is idempotent.
            await self._controller.aclose()
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, InterruptionFrame):
            await self._controller.on_interruption()
            await self.push_frame(frame, direction)
            return

        if direction == FrameDirection.DOWNSTREAM and isinstance(frame, LLMContextFrame):
            kind, tokens = await self._controller.take_release()
            if kind == RELEASE:
                await self._emit_draft(tokens)
                return  # context frame swallowed -> normal LLM never fires this turn
            if kind == SUPPRESS:
                return  # duplicate context frame for a released turn -> swallow, emit nothing
            await self.push_frame(frame, direction)  # NORMAL -> forward unchanged
            return

        await self.push_frame(frame, direction)

    async def _emit_draft(self, tokens: AsyncIterator[str] | None) -> None:
        """Replay/continue the released draft as a normal LLM response envelope.

        The End frame is emitted in a ``finally`` so that even a mid-stream draft
        error (the producer still posts a sentinel, but this is belt-and-suspenders)
        closes the response cleanly — no downstream consumer is left waiting.
        """
        await self.push_frame(LLMFullResponseStartFrame(), FrameDirection.DOWNSTREAM)
        try:
            if tokens is not None:
                async for tok in tokens:
                    await self.push_frame(LLMTextFrame(tok), FrameDirection.DOWNSTREAM)
        except Exception as exc:  # never leave the envelope unclosed
            logger.warning("Speculative draft release errored mid-stream: {err}", err=str(exc))
        finally:
            await self.push_frame(LLMFullResponseEndFrame(), FrameDirection.DOWNSTREAM)
            self._controller.note_release_finished()

    async def cleanup(self) -> None:
        await self._controller.aclose()
        await super().cleanup()


def make_cerebras_draft_factory(llm: Any) -> DraftStreamFactory:
    """Build a streaming draft factory bound to the in-pipeline Cerebras service.

    Mirrors ``BaseOpenAILLMService.run_inference``'s param building (adapter ->
    ``get_llm_invocation_params`` -> ``build_chat_completion_params``) but keeps
    ``stream=True`` so the draft can be CONTINUED at release rather than restarted.
    It issues its own ``create(...)`` on the shared HTTP client (concurrency-safe at
    the connection-pool layer) and never touches the service's per-instance
    TTFB/metrics/frame state.
    """

    def _factory(messages: list[dict[str, Any]]) -> AsyncIterator[Any] | Awaitable[AsyncIterator[Any]]:
        if hasattr(llm, "stream_draft"):
            return llm.stream_draft(messages)
        async def _open() -> AsyncIterator[Any]:
            ctx = LLMContext(messages=messages)
            adapter = llm.get_llm_adapter()
            invocation_params = adapter.get_llm_invocation_params(
                ctx,
                convert_developer_to_user=not llm.supports_developer_role,
            )
            params = llm.build_chat_completion_params(invocation_params)
            params["stream"] = True
            return await llm._client.chat.completions.create(**params)

        return _open()

    return _factory
