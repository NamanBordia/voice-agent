"""Typed behavior configuration persisted locally and snapshotted per conversation."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator, model_validator



DEFAULT_SYSTEM_PROMPT = (
    "You are a helpful, friendly assistant in a live browser conversation. "
    "Keep replies natural, concise, and useful, usually one or two sentences. "
    "Ask one question at a time. Give more detail when the user asks for it."
)
DEFAULT_GREETING_TEXT = "Hello, how can I help you today?"


class RuntimeModel(BaseModel):
    """Reject misspelled configuration fields rather than silently ignoring them."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class STTCfg(RuntimeModel):
    """Sarvam realtime transcription and server VAD settings."""
    model: Literal["saaras:v3-realtime"] = "saaras:v3-realtime"
    language_code: Literal["auto", "en-IN", "hi-IN", "kn-IN", "mr-IN", "te-IN"] = "auto"
    stream_type: Literal["fast", "balanced"] = "fast"
    threshold: float = Field(default=0.3, ge=0, le=1)
    silence_duration_ms: int = Field(default=700, ge=100, le=5000)
    min_speech_duration_ms: int = Field(default=250, ge=0, le=2000)
    turn_stop_timeout_ms: int = Field(default=500, ge=100, le=1000)
    enable_eager_eot: Literal[False] = False


class LLMCfg(RuntimeModel):
    """Cerebras model and bounded generation settings."""
    provider: Literal["cerebras"] = "cerebras"
    model: Literal["qwen-3.8-27b", "gpt-oss-120b"] = "qwen-3.8-27b"
    reasoning_effort: Literal["none", "low", "medium", "high"] = "none"
    temperature: float = Field(default=0.7, ge=0, le=2)
    max_tokens: int = Field(default=1024, ge=16, le=2048)
    request_timeout_seconds: float = Field(default=10.0, ge=1, le=60)
    # --- Stage-2 speculative LLM (OFF by default — no behavior change) ---
    # When enabled, an out-of-band Cerebras draft is started on Deepgram Flux
    # EagerEndOfTurn and, if the normalized eager transcript matches the final
    # transcript at EndOfTurn, released/continued in place of the normal LLM call
    # (hiding the turn-stop debounce + eager->final gap). It is triggered by eager
    # events, so it REQUIRES stt.enable_eager_eot — enforced by RuntimeConfig's
    # cross-field validator. The flag lives here because it is LLM behavior.
    enable_speculative_llm: bool = False


class TTSCfg(RuntimeModel):
    """Stable Bulbul streaming model with multilingual voices."""
    provider: Literal["sarvam"] = "sarvam"
    model: Literal["bulbul:v3"] = "bulbul:v3"
    voice_id: Literal["shubh", "aditya", "ritu", "priya", "neha", "rahul", "pooja", "rohan", "simran", "kavya", "amit", "dev", "ishita", "shreya", "ratan", "varun", "manan", "sumit", "roopa", "kabir", "aayan", "ashutosh", "advait"] = "shubh"
    language_mode: Literal["per_turn"] = "per_turn"
    pace: float = Field(default=1.0, ge=0.5, le=2)
    temperature: float = Field(default=0.6, ge=0.01, le=1)
    min_buffer_size: int = Field(default=30, ge=30, le=100)

    @field_validator("min_buffer_size", mode="before")
    @classmethod
    def migrate_saved_buffer(cls, value: object, info: ValidationInfo) -> object:
        """Upgrade formerly valid saved buffers without accepting invalid UI edits."""
        if (
            (info.context or {}).get("migrate_saved_config")
            and isinstance(value, int)
            and not isinstance(value, bool)
            and 1 <= value < 30
        ):
            return 30
        return value


class PromptCfg(RuntimeModel):
    """Persona and directly spoken opening text."""
    system_prompt: str = Field(default=DEFAULT_SYSTEM_PROMPT, min_length=1)
    greeting_text: str = Field(default=DEFAULT_GREETING_TEXT, min_length=1, max_length=500)


class MemoryCfg(RuntimeModel):
    """Bounded in-conversation memory without another storage service."""

    recent_turn_pairs: int = Field(default=6, ge=1, le=30)
    enable_running_summary_after_turn: int = Field(default=10, ge=1, le=1000)
    summary_update_every_turns: int = Field(default=5, ge=1, le=100)
    max_summary_tokens: int = Field(default=250, ge=1, le=2000)
    max_pinned_facts: int = Field(default=12, ge=0, le=100)
    max_recall_snippets: int = Field(default=2, ge=0, le=10)
    # Min content-token overlap for a recall snippet to be injected (an explicit
    # "you told me…/pehle…" relaxes this to 1). Higher = less noise, fewer recalls.
    recall_min_overlap: int = Field(default=2, ge=1, le=6)
    # Aggregate budget (token ~ 4 chars) for the dynamic memory sections (facts +
    # recall + running summary) so the per-turn system block can't inflate TTFB.
    max_memory_block_tokens: int = Field(default=600, ge=100, le=2000)
    # DEPRECATED: superseded by always-on lexical recall (search always runs;
    # injection is gated by score, not by this flag). Kept for back-compat.
    recall_only_when_user_asks: bool = True


class OpsCfg(RuntimeModel):
    """Frontend-editable local readiness and shutdown budgets."""
    debug_log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    browser_ready_timeout_secs: float = Field(default=30.0, ge=5, le=120)
    shutdown_timeout_secs: float = Field(default=30.0, ge=1, le=60)


class RuntimeConfig(RuntimeModel):
    """The single global runtime config document."""

    version: int = 1
    stt: STTCfg = Field(default_factory=STTCfg)
    llm: LLMCfg = Field(default_factory=LLMCfg)
    tts: TTSCfg = Field(default_factory=TTSCfg)
    prompt: PromptCfg = Field(default_factory=PromptCfg)
    memory: MemoryCfg = Field(default_factory=MemoryCfg)
    ops: OpsCfg = Field(default_factory=OpsCfg)

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_speech(cls, value: object) -> object:
        """Translate only identifiable old speech configs; preserve all other tuning."""
        if not isinstance(value, dict):
            return value
        value = dict(value)
        stt = value.get("stt", {})
        tts = value.get("tts", {})
        legacy = isinstance(stt, dict) and str(stt.get("model", "")).startswith("flux-general-")
        if legacy:
            value["stt"] = {
                "turn_stop_timeout_ms": stt.get("turn_stop_timeout_ms", 500),
                "silence_duration_ms": min(5000, stt.get("eot_timeout_ms", 700)),
            }
            value["llm"] = {**value.get("llm", {}), "enable_speculative_llm": False}
        if isinstance(tts, dict) and (tts.get("provider") == "cartesia" or str(tts.get("model", "")).startswith("sonic")):
            value["tts"] = TTSCfg().model_dump()
        return value

    @model_validator(mode="after")
    def validate_browser_config(self) -> "RuntimeConfig":
        """Validate dependencies between generation and turn-boundary settings."""
        if self.llm.enable_speculative_llm:
            raise ValueError("Speculative LLM is unavailable with Sarvam realtime STT")
        if self.llm.model == "gpt-oss-120b" and self.llm.reasoning_effort == "none":
            raise ValueError("GPT-OSS reasoning_effort must be low, medium, or high")
        if not self.prompt.greeting_text.strip() or not self.prompt.system_prompt.strip():
            raise ValueError("Greeting and system prompt cannot be blank")
        return self

    @classmethod
    def default(cls, settings: object = None) -> "RuntimeConfig":
        """Create canonical defaults without reading behavior from the environment."""
        return cls()
