"""Stream the LLM's ``<en>``/``<hi>`` language tag into Sarvam routing.

The main Cerebras reply starts with a tiny tag the model chooses, e.g.::

    <en> Sure, I'll continue in English. What would you like to discuss?
    <hi> Haan bilkul, batao kya help chahiye?

This processor sits between the LLM and Sarvam TTS. It:

  1. Buffers only the first few characters until it can read the tag (which may
     be split across LLM text frames).
  2. Sets Sarvam's language (``TTSUpdateSettingsFrame``) *before* any spoken
     text is forwarded.
  3. Strips the tag and streams the remaining text straight to TTS — streaming
     is preserved; there is no full-response buffering.
  4. Never speaks or stores the tag.
  5. Feeds the metrics observer the spoken text + chosen language (so metrics
     never show the raw tag).

If the tag is missing/malformed but the model DID produce words, it falls back
(last response language → STT hint → English), records
``error_stage="language_prefix_parse"``, and still speaks the recovered text — it
never stalls the turn or calls the LLM again. If the model produced NO usable text
at all (an empty reply — e.g. a Cerebras 429 / empty completion), that is reported
under ``error_stage="llm_no_output"`` instead, so a provider failure is not
mislabeled as a prefix-parsing bug.

Package assumptions (verified against the installed Pipecat 1.2.1):
  * ``CerebrasLLMService`` subclasses ``OpenAILLMService``, whose streaming text
    path emits ``LLMTextFrame`` (``llm_service._push_llm_text`` →
    ``LLMTextFrame``). That is the frame this processor intercepts.
  * ``TTSService`` *consumes* the input ``TextFrame`` (it does not forward it) and
    appends its own spoken ``TTSTextFrame`` to the assistant context. So the tag
    MUST be stripped before TTS — doing so keeps it out of both the audio and the
    LLM context automatically.
"""

from __future__ import annotations

from typing import Any

from pipecat.frames.frames import (
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    TextFrame,
    TTSUpdateSettingsFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.services.sarvam.tts import SarvamTTSSettings

from app.runtime import get_session_language_state, set_session_language_state
from app.services.language import (
    LANGUAGE_PREFIX_MAX_CHARS,
    fallback_response_language,
    new_language_state,
    record_response_language,
    sanitize_language_control_tags,
    sanitize_prefix_fallback_text,
    split_safe_emit,
    strip_control_tags_inline,
    to_language_enum,
    try_parse_language_prefix,
)

_PREFIX_ERROR_STAGE = "language_prefix_parse"
# A genuinely EMPTY LLM reply (zero usable text) is NOT a tag-parse failure — it
# means the model produced nothing (e.g. a Cerebras 429 / empty completion). It is
# reported under its own stage so the dashboard stops mislabeling rate-limit/empty
# turns as a prefix problem.
_NO_OUTPUT_ERROR_STAGE = "llm_no_output"
# Spoken only when a malformed/empty reply leaves nothing safe to say.
_SAFE_REPEAT_LINES = {
    "en": "Sorry, could you repeat that?",
    "hi": "माफ़ कीजिए, क्या आप दोहरा सकते हैं?",
    "kn": "ಕ್ಷಮಿಸಿ, ಮತ್ತೊಮ್ಮೆ ಹೇಳುತ್ತೀರಾ?",
    "mr": "माफ करा, तुम्ही पुन्हा सांगाल का?",
    "te": "క్షమించండి, మళ్లీ చెబుతారా?",
}


class LanguagePrefixProcessor(FrameProcessor):
    """Parse the LLM ``<en>``/``<hi>`` prefix and route Sarvam language."""

    def __init__(
        self,
        *,
        call_sid: str,
        metrics_observer: Any | None = None,
        max_prefix_chars: int = LANGUAGE_PREFIX_MAX_CHARS,
    ) -> None:
        super().__init__()
        self._call_sid = call_sid
        self._metrics = metrics_observer
        self._max_prefix_chars = max_prefix_chars
        self._reset_response_state()

    def _reset_response_state(self) -> None:
        self._active = False          # inside an LLM response (Start..End)
        self._prefix_done = False     # tag resolved (parsed or fell back)
        self._prefix_buffer = ""      # accumulates until the tag resolves
        self._spoken_parts: list[str] = []
        self._prefix_error: str | None = None
        self._emit_carry = ""         # held trailing fragment (split-tag safety)
        self._raw_llm_text = ""        # full raw LLM text (for last-resort recovery)

    async def process_frame(
        self, frame: Any, direction: FrameDirection = FrameDirection.DOWNSTREAM
    ) -> None:
        await super().process_frame(frame, direction)

        if direction == FrameDirection.DOWNSTREAM and isinstance(frame, LLMFullResponseStartFrame):
            self._reset_response_state()
            self._active = True
            await self.push_frame(frame, direction)
            return

        if direction == FrameDirection.DOWNSTREAM and isinstance(frame, LLMTextFrame) and self._active:
            await self._handle_llm_text(frame.text or "", direction)
            return  # the raw (tagged) LLM text frame is never forwarded

        if direction == FrameDirection.DOWNSTREAM and isinstance(frame, LLMFullResponseEndFrame) and self._active:
            await self._finish_response(direction)
            await self.push_frame(frame, direction)
            self._active = False
            return

        # Greeting TTSSpeakFrame and everything else passes straight through.
        await self.push_frame(frame, direction)

    async def _handle_llm_text(self, text: str, direction: FrameDirection) -> None:
        self._raw_llm_text += text
        if self._prefix_done:
            await self._emit_spoken(text, direction)
            return

        self._prefix_buffer += text
        language, remainder, done = try_parse_language_prefix(
            self._prefix_buffer, max_prefix_chars=self._max_prefix_chars
        )
        if not done:
            return  # keep buffering until the tag (or its absence) is clear

        await self._resolve_prefix(language, remainder, direction)

    async def _finish_response(self, direction: FrameDirection) -> None:
        if not self._prefix_done:
            # Force a decision: response ended before a full tag formed.
            language, remainder, _ = try_parse_language_prefix(
                self._prefix_buffer, max_prefix_chars=0
            )
            await self._resolve_prefix(language, remainder, direction)

        # Drop any held streaming fragment: split_safe_emit only ever holds an
        # UNFINISHED control-tag fragment (e.g. "<e"), which never completed, so it
        # is control junk — never speakable content.
        self._emit_carry = ""

        spoken = "".join(self._spoken_parts).strip()
        if not spoken:
            # Last-resort RECOVERY before any safe line: a prefix parse failure
            # ALONE must never become a fake "repeat that". Sanitize the full raw
            # LLM text and speak whatever real words it had.
            recovered = sanitize_language_control_tags(self._raw_llm_text)
            if recovered:
                await self._push_spoken_text(recovered, direction)
                spoken = recovered
        if not spoken:
            # Only now — the model genuinely produced nothing usable.
            language = fallback_response_language(get_session_language_state(self._call_sid))
            spoken = _SAFE_REPEAT_LINES[language]
            await self._push_spoken_text(spoken, direction)

        if self._metrics is not None:
            # Record the error BEFORE the assistant text: note_assistant_text is
            # the finalize signal, and a later note_error would no-op on an
            # already-emitted record (P1-1).
            if self._prefix_error:
                # Distinguish a genuinely EMPTY reply (LLM emitted ZERO text — e.g. a
                # Cerebras 429 / empty completion, the "LLM TTFB —" turns) from a real
                # tag-parse failure (the model DID produce words, just no valid
                # <en>/<hi>). _prefix_error is set for both because an empty buffer also
                # "has no tag", so disambiguate on whether the model produced any raw
                # text at all. Empty = an LLM/upstream failure, not a prefix bug; a
                # partial tag like "<en" counts as text produced (-> prefix parse).
                empty_reply = not self._raw_llm_text.strip()
                if empty_reply:
                    stage = _NO_OUTPUT_ERROR_STAGE
                    message = "LLM produced no output (empty reply — e.g. provider rate limit/timeout); spoke a safe fallback line."
                else:
                    stage = _PREFIX_ERROR_STAGE
                    message = "LLM reply missing/malformed language tag; used fallback language."
                self._metrics.note_error(stage=stage, message=message)
            # Defensive: never persist a control tag even if streaming missed one.
            self._metrics.note_assistant_text(sanitize_language_control_tags(spoken))

    async def _resolve_prefix(
        self, language: str | None, remainder: str, direction: FrameDirection
    ) -> None:
        if language is None:
            language = fallback_response_language(get_session_language_state(self._call_sid))
            self._prefix_error = _PREFIX_ERROR_STAGE
            # On fallback the remainder may carry a malformed tag fragment; clean it.
            remainder = sanitize_prefix_fallback_text(remainder)
        self._prefix_done = True
        await self._apply_language(language, direction)
        await self._emit_spoken(remainder, direction)

    async def _apply_language(self, language: str, direction: FrameDirection) -> None:
        state = get_session_language_state(self._call_sid) or new_language_state()
        record_response_language(state, language)
        # Only switch Sarvam when the language actually changes. The first
        # response always emits (last_tts_language is unset); thereafter a repeat
        # of the same language is a no-op, avoiding per-turn TTS context churn.
        changed = state.get("last_tts_language") != language
        state["last_tts_language"] = language
        set_session_language_state(self._call_sid, state)

        if changed:
            enum = to_language_enum(language)
            if enum is not None:
                await self.push_frame(
                    TTSUpdateSettingsFrame(delta=SarvamTTSSettings(language=enum)), direction
                )
        if self._metrics is not None:
            self._metrics.note_language(
                detected_language=state.get("last_user_language"),
                response_language=language,
            )

    async def _emit_spoken(self, text: str, direction: FrameDirection) -> None:
        """Stream spoken text to TTS, stripping any misplaced/duplicate control tag.

        A trailing fragment that could still be forming a ``<en>``/``<hi>`` tag is
        held back (split across LLM chunks) and reconsidered on the next chunk, so a
        control tag is never emitted half-stripped. Complete tags found anywhere are
        removed before the text reaches TTS.
        """
        if not text:
            return
        self._emit_carry += text
        emittable, self._emit_carry = split_safe_emit(self._emit_carry)
        if not emittable:
            return
        emittable = strip_control_tags_inline(emittable)
        # Strip the space that follows the tag from the very first spoken chunk.
        if not self._spoken_parts:
            emittable = emittable.lstrip()
        if not emittable:
            return
        await self._push_spoken_text(emittable, direction)

    async def _push_spoken_text(self, text: str, direction: FrameDirection) -> None:
        """Record + forward already-sanitized spoken text to TTS."""
        if not text:
            return
        self._spoken_parts.append(text)
        await self.push_frame(TextFrame(text), direction)
