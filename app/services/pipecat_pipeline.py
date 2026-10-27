"""Build the existing browser pipeline with Sarvam speech and Cerebras generation.

Realtime STT gates accepted finals before memory, collection, and aggregation.
The language-prefix processor switches streaming TTS before forwarding speech.
Transport, observers, memory, aggregators, and session ownership remain unchanged.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from loguru import logger
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import (
    InterimTranscriptionFrame,
    TranscriptionFrame,
    TTSSpeakFrame,
)
from pipecat.observers.user_bot_latency_observer import UserBotLatencyObserver
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.services.sarvam.tts import SarvamTTSService
from pipecat.services.cerebras.llm import CerebrasLLMService, CerebrasLLMSettings
from pipecat.services.tts_service import TextAggregationMode
from pipecat.transports.base_transport import BaseTransport
from pipecat.turns.user_mute import (
    AlwaysUserMuteStrategy,
    MuteUntilFirstBotCompleteUserMuteStrategy,
)
from pipecat.turns.user_start import ExternalUserTurnStartStrategy
from pipecat.turns.user_stop import ExternalUserTurnStopStrategy
from pipecat.turns.user_turn_strategies import UserTurnStrategies

from app.config import Settings, V1_SAMPLE_RATE
from app.models.conversation_models import TurnEvent
from app.runtime import (
    ConfigSnapshot,
    append_session_transcript_event,
    append_turn_record,
    get_session_language_state,
    get_session_memory,
    get_session_transcript,
    set_session_language_state,
    set_session_memory,
    update_session_metric,
    update_session_first_latency,
)
from app.services import language as lang
from app.services import memory as mem
from app.services.call_metrics import TurnMetricsObserver
from app.services.cerebras_service import VoiceCerebrasLLMService
from app.services.sarvam_realtime_stt import SarvamRealtimeSTTService
from app.services.language_prefix import LanguagePrefixProcessor
from app.services.speculation_controller import (
    SpecGateProcessor,
    SpeculationController,
    make_cerebras_draft_factory,
)
from app.services.turn_collector import TurnCollector, utc_now
from app.services.user_input import UserInputGate

def _stt_cfg(snapshot: ConfigSnapshot) -> dict[str, Any]:
    return (snapshot.get("runtime_config") or {}).get("stt") or {}


def _llm_cfg(snapshot: ConfigSnapshot) -> dict[str, Any]:
    return (snapshot.get("runtime_config") or {}).get("llm") or {}


def _build_stt(snapshot: ConfigSnapshot, settings: Settings) -> SarvamRealtimeSTTService:
    """Construct original-language realtime STT at the fixed PCM boundary."""
    cfg = _stt_cfg(snapshot)
    return SarvamRealtimeSTTService(
        api_key=settings.sarvam_api_key,
        settings=SarvamRealtimeSTTService.Settings(
            model=cfg.get("model", "saaras:v3-realtime"),
            language_code=cfg.get("language_code", "auto"),
            stream_type=cfg.get("stream_type", "fast"),
            threshold=cfg.get("threshold", 0.3),
            silence_duration_ms=cfg.get("silence_duration_ms", 700),
            min_speech_duration_ms=cfg.get("min_speech_duration_ms", 250),
        ),
    )


def _build_tts(snapshot: ConfigSnapshot, settings: Settings) -> SarvamTTSService:
    """Use built-in websocket synthesis, sentence streaming and 16 kHz PCM."""
    cfg = (snapshot.get("runtime_config") or {}).get("tts") or {}
    return SarvamTTSService(
        api_key=settings.sarvam_api_key,
        sample_rate=V1_SAMPLE_RATE,
        text_aggregation_mode=TextAggregationMode.SENTENCE,
        settings=SarvamTTSService.Settings(
            voice=snapshot["TTS_VOICE_ID"], model=snapshot["TTS_MODEL"],
            language=lang.to_language_enum("en"),
            pace=cfg.get("pace", 1.0), temperature=cfg.get("temperature", 0.6),
            min_buffer_size=cfg.get("min_buffer_size", 30),
        ),
    )


def _build_llm(snapshot: ConfigSnapshot, settings: Settings) -> CerebrasLLMService:
    """Construct the streamed Cerebras LLM service from the runtime config."""

    cfg = _llm_cfg(snapshot)
    # The wrapper enforces a total deadline, including streamed reads; the base
    # retry_timeout_secs alone is inert when retry_on_timeout is false.
    return VoiceCerebrasLLMService(
        api_key=settings.cerebras_api_key,
        settings=CerebrasLLMSettings(
            model=cfg.get("model", "qwen-3.8-27b"),
            temperature=cfg.get("temperature", 0.7),
            max_completion_tokens=cfg.get("max_tokens", 1024),
            extra={"reasoning_effort": cfg.get("reasoning_effort", "none")},
        ),
        request_timeout_seconds=float(cfg.get("request_timeout_seconds", 10.0)),
    )


def _build_context(snapshot: ConfigSnapshot) -> LLMContext:
    """Build the LLM context seeded with the system prompt."""

    return LLMContext(
        messages=[{"role": "system", "content": snapshot["SYSTEM_PROMPT"]}],
    )


def _user_mute_strategies() -> list[Any]:
    """Mute through greeting completion and whenever the assistant speaks."""
    return [
        MuteUntilFirstBotCompleteUserMuteStrategy(),
        AlwaysUserMuteStrategy(),
    ]


def _build_user_turn_strategies(*, turn_stop_timeout_ms: int) -> UserTurnStrategies:
    """Use provider boundaries with the existing post-STT debounce.

    Sarvam speech-end alone never releases a turn. The adapter forwards a stop
    only after the final passes collection and mute acceptance. System frames
    may overtake transcript data frames, so the existing debounce remains.
    """
    return UserTurnStrategies(
        start=[ExternalUserTurnStartStrategy()],
        stop=[ExternalUserTurnStopStrategy(timeout=turn_stop_timeout_ms / 1000.0)],
    )


def _build_aggregator_pair(
    context: LLMContext, *, turn_stop_timeout_ms: int = 500
) -> LLMContextAggregatorPair:
    user_params = LLMUserAggregatorParams(
        user_turn_strategies=_build_user_turn_strategies(turn_stop_timeout_ms=turn_stop_timeout_ms),
        vad_analyzer=SileroVADAnalyzer(sample_rate=V1_SAMPLE_RATE),
        user_mute_strategies=_user_mute_strategies(),
    )
    return LLMContextAggregatorPair(context=context, user_params=user_params)


def _normalize_transcript_text(text: str) -> str:
    return " ".join(text.split())


def _stt_ready_state(service: Any, turn_time: float) -> bool | None:
    """Per-turn STT readiness (tri-state), from the actual connect time.

    Replaces the old one-way "early audio dropped at startup" latch, which once
    tripped marked EVERY later turn not-ready even though the EOT proves STT was
    connected (OBS-1). The deferred STT exposes ``connected_at``: ready iff it
    connected before this turn. A non-deferred service has no ``connected_at`` and
    connects (blocking) in ``start()``, so it is ready by the first turn -> True.
    A missing/not-yet-observed connect time is unknown (None), never False.
    """
    if not hasattr(service, "connected_at"):
        return True
    connected_at = service.connected_at
    if connected_at is None:
        return None
    return connected_at <= turn_time


class SttLanguageHintProcessor(FrameProcessor):
    """Record the weak STT-detected language as a per-call hint + log transcripts.

    It does NOT decide the response language and does NOT switch TTS — the LLM's
    ``<en>``/``<hi>`` tag is the authority (routed downstream by the
    LanguagePrefixProcessor). The hint feeds the prompt and the metrics
    detected-language field. Does not generate replies.

    It also logs the raw STT view at DEBUG — interim and finalized transcripts
    with their detected language — so STT-quality issues (truncation, fragmented
    or mis-recognized Hinglish/proper nouns) can be measured directly against the
    audio rather than inferred from the LLM's reply (STT-1). Logging only carries
    call_sid + (clipped) transcript text + language; no secrets.
    """

    def __init__(self, *, call_sid: str, accept_turn: Callable[[], bool] | None = None) -> None:
        super().__init__()
        self._call_sid = call_sid
        self._accept_turn = accept_turn or (lambda: True)

    async def process_frame(
        self, frame: Any, direction: FrameDirection = FrameDirection.DOWNSTREAM
    ) -> None:
        await super().process_frame(frame, direction)
        if (direction == FrameDirection.DOWNSTREAM
                and isinstance(frame, (InterimTranscriptionFrame, TranscriptionFrame))
                and not self._accept_turn()):
            return
        if direction == FrameDirection.DOWNSTREAM:
            self._observe_transcript(frame)
        await self.push_frame(frame, direction)

    def _observe_transcript(self, frame: Any) -> None:
        """Log the raw STT view (interim + final) and record the weak final hint.

        Logging carries only call_sid + clipped transcript text + detected
        language — never settings or credentials (STT-1). Tolerant of frames that
        lack a ``language``/``text`` attribute so a malformed frame can't crash STT.
        """
        if isinstance(frame, InterimTranscriptionFrame):
            logger.debug(
                "STT interim transcript",
                call_sid=self._call_sid,
                language=lang.detect_language_from_transcription(frame),
                text=_normalize_transcript_text(getattr(frame, "text", "") or "")[:160],
            )
        elif isinstance(frame, TranscriptionFrame) and getattr(frame, "finalized", False):
            detected = lang.detect_language_from_transcription(frame)
            logger.debug(
                "STT final transcript",
                call_sid=self._call_sid,
                language=detected,
                finalized=True,
                text=_normalize_transcript_text(getattr(frame, "text", "") or "")[:160],
            )
            state = get_session_language_state(self._call_sid) or lang.new_language_state()
            lang.record_stt_language_hint(state, detected)
            set_session_language_state(self._call_sid, state)


# Per-snippet/per-fact length cap before persisting memory marks, so a verbose
# turn can't bloat the persisted turn record or the dashboard (D7).
_MEMORY_SNIPPET_CAP = 200


class MemoryProcessor(FrameProcessor):
    """Govern the LLM context for bounded in-call memory.

    On each finalized user turn (before the user aggregator appends the message)
    it rebuilds the system message from the per-call memory (language
    instruction + pinned facts + running summary + optional recall snippets) and
    trims history to the most recent N turn pairs. The current user message is
    appended by the downstream aggregator; the running summary is refreshed
    post-turn (never here), so this stays cheap and non-blocking.
    """

    def __init__(
        self,
        *,
        call_sid: str,
        context: LLMContext,
        base_system_prompt: str,
        memory_cfg: dict[str, Any],
        metrics_observer: Any | None = None,
    ) -> None:
        super().__init__()
        self._call_sid = call_sid
        self._context = context
        self._base_system = base_system_prompt
        self._cfg = memory_cfg
        self._metrics = metrics_observer
        # Per-turn memory snapshot (what the agent recalled/used this turn). Replaced
        # wholesale on each finalized user turn in _govern_context; the observer pins
        # it at the LLM-response start (snapshot-at-promote), so on a MERGED turn the
        # LAST sub-utterance's memory — the one that shaped the reply — is the one
        # recorded. Mirrors the SpeculationController._marks / drain_marks pattern.
        self._memory_marks: dict[str, Any] = {}

    def drain_memory_marks(self) -> dict[str, Any]:
        """Return the current per-turn memory snapshot (for the metrics observer)."""
        return dict(self._memory_marks)

    async def process_frame(
        self, frame: Any, direction: FrameDirection = FrameDirection.DOWNSTREAM
    ) -> None:
        await super().process_frame(frame, direction)
        if (
            direction == FrameDirection.DOWNSTREAM
            and isinstance(frame, TranscriptionFrame)
            and getattr(frame, "finalized", False)
        ):
            started = time.perf_counter()
            self._govern_context(frame.text or "")
            duration_ms = round((time.perf_counter() - started) * 1000.0, 2)
            if self._metrics is not None:
                self._metrics.note_memory_timing(
                    duration_ms=duration_ms,
                    context_message_count=len(self._context.messages),
                )
        await self.push_frame(frame, direction)

    def _govern_context(self, user_text: str) -> None:
        memory = get_session_memory(self._call_sid) or mem.new_call_memory()
        memory["pinned_facts"] = mem.extract_pinned_facts(
            user_text, memory.get("pinned_facts", []),
            max_pinned=int(self._cfg.get("max_pinned_facts", 12)),
        )
        set_session_memory(self._call_sid, memory)

        lstate = get_session_language_state(self._call_sid) or lang.new_language_state()
        hint = lang.language_hint_line(lstate)

        # Always lexical-search; inject only what scores above the bar (never
        # regex-gated). An explicit back-reference ("you told me…", "pehle…")
        # relaxes the threshold so an explicit ask reliably surfaces something.
        recent_pairs = int(self._cfg.get("recent_turn_pairs", 6))
        min_overlap = int(self._cfg.get("recall_min_overlap", 2))
        triggered = mem.recall_triggered(user_text)
        if triggered:
            min_overlap = 1
        recall_results = mem.search_recall_with_sources(
            get_session_transcript(self._call_sid),
            query=user_text,
            max_snippets=int(self._cfg.get("max_recall_snippets", 2)),
            min_overlap=min_overlap,
            exclude_recent=recent_pairs * 2,
        )
        recall = [r["text"] for r in recall_results]

        system_block = mem.build_system_block(
            self._base_system,
            language_policy=lang.RESPONSE_LANGUAGE_POLICY,
            factual_guardrail=lang.FACTUAL_GUARDRAIL,
            language_hint=hint,
            pinned_facts=memory["pinned_facts"],
            running_summary=memory.get("running_summary"),
            recall_snippets=recall,
            max_dynamic_tokens=int(self._cfg.get("max_memory_block_tokens", 600)),
        )
        trimmed = mem.trim_recent_messages(
            self._context.messages,
            recent_turn_pairs=int(self._cfg.get("recent_turn_pairs", 6)),
        )
        self._context.set_messages([{"role": "system", "content": system_block}] + trimmed)

        # Per-turn memory snapshot for the dashboard. "recall_used" means relevant
        # earlier context WAS injected into the system block — an opportunity signal,
        # not proof the LLM leaned on it. approx_memory_tokens is char/4 (the module's
        # own budget unit), not a real tokenizer count.
        self._memory_marks = {
            "recall_used": bool(recall),
            "recall_triggered": bool(triggered),
            "recall_min_overlap": min_overlap,
            "recall_snippets": [s[:_MEMORY_SNIPPET_CAP] for s in recall],
            "recall_source_turns": [
                r["source_event_index"]
                for r in recall_results
                if r.get("source_event_index") is not None
            ],
            "pinned_facts": [f[:_MEMORY_SNIPPET_CAP] for f in memory["pinned_facts"]],
            "pinned_facts_count": len(memory["pinned_facts"]),
            "running_summary_present": bool(memory.get("running_summary")),
            "context_message_count": len(self._context.messages),
            "memory_block_char_len": len(system_block),
            "approx_memory_tokens": len(system_block) // 4,
        }


def _record_greeting(*, call_sid: str, greeting_text: str) -> None:
    append_session_transcript_event(
        call_sid, role="assistant", phase="greeting", text=greeting_text
    )


@dataclass
class PipelineHooks:
    """Explicit live lifecycle callbacks, never stored in runtime data."""

    on_task: Callable[[PipelineTask, Callable[[], Awaitable[None]]], None]
    on_connected: Callable[[], None]
    on_disconnected: Callable[[], None]
    on_error: Callable[[str], None]


def _build_pipeline_processors(
    *,
    transport: BaseTransport,
    snapshot: ConfigSnapshot,
    settings: Settings,
    call_sid: str,
    stream_sid: str | None,
    metrics_observer: TurnMetricsObserver,
    on_provider_error: Callable[[str], None] | None = None,
) -> list[Any]:
    """Construct the full V3 processor list and wire the EOT side channel."""

    stt = _build_stt(snapshot, settings)
    tts = _build_tts(snapshot, settings)
    llm = _build_llm(snapshot, settings)
    context = _build_context(snapshot)
    aggregator_pair = _build_aggregator_pair(
        context,
        turn_stop_timeout_ms=int(_stt_cfg(snapshot).get("turn_stop_timeout_ms", 500)),
    )
    input_gate = UserInputGate()
    stt_language_hint = SttLanguageHintProcessor(call_sid=call_sid, accept_turn=lambda: input_gate.accepting_turn)

    @aggregator_pair.user().event_handler("on_user_mute_started")
    async def user_muted(_aggregator: Any) -> None:
        input_gate.set_muted(True)

    @aggregator_pair.user().event_handler("on_user_mute_stopped")
    async def user_unmuted(_aggregator: Any) -> None:
        input_gate.set_muted(False)
    memory_processor = MemoryProcessor(
        call_sid=call_sid,
        context=context,
        base_system_prompt=snapshot["SYSTEM_PROMPT"],
        memory_cfg=(snapshot.get("runtime_config") or {}).get("memory") or {},
        metrics_observer=metrics_observer,
    )
    # Per-turn memory snapshot -> turn record (snapshot-at-promote in the observer,
    # so a merged turn records the last sub-utterance's memory).
    metrics_observer.set_memory_marks_provider(memory_processor.drain_memory_marks)

    # F6: attribute provider failures to the open turn with a stage-specific label
    # (instead of the generic incomplete_at_call_end / no_bot_response). Each service
    # fires on_error with its own ErrorFrame, so the stage is implicit per service —
    # cleaner than parsing processor identity off an upstream ErrorFrame. note_provider_error
    # does NOT overwrite a more specific stage (e.g. the parser's llm_no_output), so the LLM
    # path keeps its precise label while TTS/STT failures stop reading as incomplete_at_call_end.
    def _register_provider_error(service: Any, stage: str) -> None:
        if not hasattr(service, "event_handler"):
            return

        @service.event_handler("on_error")
        async def _on_provider_error(_service: Any, error: Any) -> None:
            msg = str(getattr(error, "error", None) or error)
            for api_key in (
                settings.sarvam_api_key, settings.cerebras_api_key,
                settings.deepgram_api_key, settings.cartesia_api_key,
            ):
                if api_key:
                    msg = msg.replace(api_key, "[REDACTED]")
            detail = f"{stage}: {' '.join(msg.split())}"[:300]
            metrics_observer.note_provider_error(stage=stage, message=detail)
            if on_provider_error is not None:
                on_provider_error(detail)

    _register_provider_error(stt, "stt_provider_error")
    _register_provider_error(tts, "tts_provider_error")
    _register_provider_error(llm, "llm_provider_error")

    language_prefix = LanguagePrefixProcessor(
        call_sid=call_sid, metrics_observer=metrics_observer
    )
    turn_collector = TurnCollector(call_sid=call_sid, stream_sid=stream_sid, source="pipecat_turn_complete")

    # Stage-1 EagerEndOfTurn telemetry. A per-turn probe accumulator shared by the
    # Flux turn handlers; it ONLY records timestamps + a transcript preview (no
    # speculative LLM draft, no TTS). It is reset at start_of_turn and consumed at
    # end_of_turn. Attached to the turn record only when eager telemetry is enabled,
    # so with the flag off the records are exactly as today.
    eager_enabled = False  # Sarvam partials are not reliable eager-EOT signals.
    eager_probe: dict[str, Any] = {}

    # Stage-2 speculative LLM (OFF by default). Requires eager events to trigger the
    # draft, so it is additionally gated on eager_enabled (belt-and-suspenders with the
    # RuntimeConfig cross-field validator). When disabled, NO gate/controller exists and
    # the processor list below is the byte-identical baseline.
    spec_enabled = bool(_llm_cfg(snapshot).get("enable_speculative_llm")) and eager_enabled
    spec_controller: SpeculationController | None = None
    spec_gate: SpecGateProcessor | None = None
    if spec_enabled:
        spec_controller = SpeculationController(
            call_sid=call_sid,
            context=context,
            base_system_prompt=snapshot["SYSTEM_PROMPT"],
            memory_cfg=(snapshot.get("runtime_config") or {}).get("memory") or {},
            draft_stream_factory=make_cerebras_draft_factory(llm),
        )
        spec_gate = SpecGateProcessor(controller=spec_controller)
        # Stage-2 metrics ride the existing eager_probe via the controller's marks,
        # pulled at finalize so a draft_released set after EOT is captured.
        metrics_observer.set_speculation_marks_provider(spec_controller.drain_marks)

    stt.accept_turn = lambda: input_gate.accepting_turn
    if hasattr(stt, "event_handler"):
        async def _on_end_of_turn(service: Any, transcript: str) -> bool:
            if not input_gate.accepting_turn:
                return False
            now = time.time()
            # Snapshot + clear the eager probe up front so it is consumed on EVERY
            # EndOfTurn (even a no-commit one) and can never bleed into a later turn
            # if a StartOfTurn is missed.
            probe_snapshot = dict(eager_probe) if eager_enabled else None
            eager_probe.clear()
            # Stage-2: commit the per-turn release/normal decision on the FINAL
            # transcript BEFORE the debounced LLMContextFrame reaches the gate. Done
            # before any early-return so an armed/draft state is always resolved.
            if spec_controller is not None:
                await spec_controller.on_end_of_turn(transcript)
            result = turn_collector.observe(
                TurnEvent(event_type="end_of_turn", text=transcript, timestamp=utc_now(),
                          metadata={"provider_turn_index": service.provider_turn_index}),
                phase="task",
                greeting_pending=False,
            )
            turn = result.turn
            if turn is None:
                return False
            # Per-turn readiness from the actual connect time (tri-state), not the
            # startup early-audio latch which poisoned every later turn (OBS-1).
            stt_ready = _stt_ready_state(service, now)
            logger.info(
                "Sarvam end_of_turn",
                call_sid=call_sid,
                turn_id=turn.turn_index,
                detected_language=service.last_detected_language,
                stt_ready=stt_ready,
                transcript=_normalize_transcript_text(turn.text)[:160],
            )
            append_session_transcript_event(
                call_sid, role="user", phase="task",
                text=_normalize_transcript_text(turn.text),
            )
            metrics_observer.note_user_turn(
                turn_id=turn.turn_index,
                user_text=turn.text,
                stt_eot_received_at=now,
                turn_committed_at=time.time(),
                stt_ready=stt_ready,
                # Attach the eager probe ONLY when telemetry is enabled (already a
                # decoupled snapshot taken + cleared at the top of this handler).
                eager_probe=probe_snapshot,
            )
            # Language is annotated by the LanguagePrefixProcessor (from the LLM
            # tag) and resolved at metric finalize, so it is not read here.

            return True

        stt.commit_turn = _on_end_of_turn

        # Flux turn-boundary events. start/eager/resumed feed the Stage-1 eager
        # probe (timestamps + transcript preview only — no draft, no TTS) and stay
        # DEBUG-logged for diagnostics. They never fire unless eager_eot_threshold
        # is configured, so this is inert with the flag off.
        @stt.event_handler("on_start_of_turn")
        async def _on_start_of_turn(service: Any, *args: Any) -> None:
            input_gate.start_turn()
            eager_probe.clear()  # new turn boundary
            if spec_controller is not None:
                await spec_controller.on_turn_start()
            logger.debug("Sarvam start_of_turn", call_sid=call_sid)

        @stt.event_handler("on_eager_end_of_turn")
        async def _on_eager_end_of_turn(service: Any, *args: Any) -> None:
            if not input_gate.accepting_turn:
                return
            transcript = _normalize_transcript_text(str(args[0])) if args else ""
            eager_probe["eager_eot_seen"] = True
            eager_probe["eager_eot_at"] = time.time()
            eager_probe["eager_transcript"] = transcript
            eager_probe["resumed_since_eager"] = False
            if spec_controller is not None:
                # Start the out-of-band draft on the eager transcript (Stage-2).
                await spec_controller.on_eager(transcript)
            logger.debug("Flux eager_end_of_turn", call_sid=call_sid, transcript=transcript[:80])

        @stt.event_handler("on_turn_resumed")
        async def _on_turn_resumed(service: Any, *args: Any) -> None:
            if not input_gate.accepting_turn:
                return
            eager_probe["turn_resumed_seen"] = True
            eager_probe["turn_resumed_at"] = time.time()
            eager_probe["resumed_since_eager"] = True
            if spec_controller is not None:
                # MAIN invalidation: discard the unreleased draft.
                await spec_controller.on_resumed()
            logger.debug("Flux turn_resumed", call_sid=call_sid)

    return [
        transport.input(),
        input_gate,
        stt,
        stt_language_hint,
        memory_processor,
        aggregator_pair.user(),
        # Stage-2 response gate (only present when speculation is enabled). Sits between
        # the user aggregator and the LLM so it can swallow the turn's LLMContextFrame on
        # a draft hit; absent -> the list is the byte-identical baseline.
        *([spec_gate] if spec_gate is not None else []),
        llm,
        language_prefix,
        tts,
        transport.output(),
        aggregator_pair.assistant(),
    ]


class PipelineExecutionResult:
    """Serializable pipeline outcome returned to the route."""

    def __init__(
        self, *, completed: bool, error: str | None = None, disconnected: bool = False
    ) -> None:
        self.completed = completed
        self.error = error
        # Structured hang-up signal so the call-outcome derivation never has to
        # substring-match "disconnect" in arbitrary exception text (which would
        # misread a real crash as a user hang-up).
        self.disconnected = disconnected


async def run_pipeline(
    *,
    transport: BaseTransport,
    call_sid: str,
    stream_sid: str,
    snapshot: ConfigSnapshot,
    settings: Settings,
    hooks: PipelineHooks,
) -> PipelineExecutionResult:
    """Assemble and run the Pipecat pipeline for one call.

    Receives the already-constructed transport and serializable inputs, runs the
    graph to completion, and returns a serializable result. The router owns
    session creation/cleanup and the finalize boundary.
    """

    log = logger.bind(call_sid=call_sid)
    memory_cfg = (snapshot.get("runtime_config") or {}).get("memory") or {}

    def _on_turn_record(record: dict[str, Any]) -> None:
        append_turn_record(call_sid, record)
        assistant_text = record.get("assistant_text")
        if assistant_text:
            append_session_transcript_event(
                call_sid, role="assistant", phase="task",
                text=_normalize_transcript_text(assistant_text),
            )
        # Post-turn (never before the reply): refresh the running summary if due.
        memory = get_session_memory(call_sid) or mem.new_call_memory()
        mem.update_summary_if_due(
            memory,
            turn_count=int(record.get("turn_id") or 0),
            transcript=get_session_transcript(call_sid),
            memory_cfg=memory_cfg,
        )
        set_session_memory(call_sid, memory)

    def _language_provider() -> tuple[str | None, str | None]:
        state = get_session_language_state(call_sid) or {}
        return state.get("last_user_language"), state.get("response_language")

    metrics_observer = TurnMetricsObserver(
        on_record=_on_turn_record, language_provider=_language_provider
    )

    processors = _build_pipeline_processors(
            transport=transport,
            snapshot=snapshot,
            settings=settings,
            call_sid=call_sid,
            stream_sid=stream_sid,
            metrics_observer=metrics_observer,
            on_provider_error=hooks.on_error,
        )
    pipeline = Pipeline(processors)

    latency_observer = UserBotLatencyObserver()

    task = PipelineTask(
        pipeline,
        params=PipelineParams(
            enable_metrics=True,
            audio_in_sample_rate=V1_SAMPLE_RATE,
            audio_out_sample_rate=V1_SAMPLE_RATE,
        ),
        observers=[latency_observer, metrics_observer],
    )

    @latency_observer.event_handler("on_first_bot_speech_latency")
    async def _on_first_bot_speech(observer: Any, latency_seconds: float) -> None:
        log.info("First bot speech latency", latency_secs=f"{latency_seconds:.3f}")
        update_session_first_latency(call_sid, latency_seconds, observed_at=time.time())

    @transport.event_handler("on_client_connected")
    async def _on_client_connected(_transport: Any, _client: Any) -> None:
        hooks.on_connected()

    @transport.event_handler("on_client_disconnected")
    async def _on_client_disconnected(_transport: Any, _client: Any) -> None:
        hooks.on_disconnected()

    stt = next(processor for processor in processors if isinstance(processor, SarvamRealtimeSTTService))

    async def wait_for_stt() -> None:
        await stt.wait_until_ready()

    hooks.on_task(task, wait_for_stt)

    runner = PipelineRunner(handle_sigint=False)
    log.info("Pipeline runner starting", stream_sid=stream_sid)

    try:
        await runner.run(task)
        log.info("Pipeline runner completed normally")
        return PipelineExecutionResult(completed=True)
    except asyncio.CancelledError:
        log.info("Pipeline runner cancelled (user disconnected)")
        return PipelineExecutionResult(completed=True, disconnected=True)
    except Exception as exc:
        log.error("Pipeline runner failed: {error}", error=str(exc))
        return PipelineExecutionResult(completed=False, error=str(exc))
