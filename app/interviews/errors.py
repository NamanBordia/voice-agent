"""Safe failure diagnostics without model output, assessment notes, or credentials."""

from dataclasses import dataclass
import re
from openai import APIConnectionError, APIStatusError, APITimeoutError
from pydantic import ValidationError
from app.config import Settings

DECISION_FALLBACK_ISSUE = "Decision fallback: no assessment recorded"
EVIDENCE_OMITTED_ISSUE = "Assessment evidence omitted"
TRANSCRIPTION_ISSUE = "Possible transcription ambiguity"


class InterviewDecisionError(ValueError):
    """A model decision failed controller checks, rather than a state/storage error."""


class InterviewGenerationError(RuntimeError):
    """A code-owned explanation of an incomplete provider response."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def recoverable_decision_error(exc: Exception) -> bool:
    """Recover only unusable model output; preserve provider and checkpoint failures."""
    return isinstance(exc, InterviewDecisionError) or (
        isinstance(exc, InterviewGenerationError)
        and exc.code in {"decision_empty", "decision_truncated", "decision_refused", "decision_schema"}
    )


@dataclass(frozen=True)
class DecisionFailure:
    """Public summary plus an optional redacted provider error for local logs."""
    code: str
    summary: str
    provider_detail: str = ""


def _redact(text: str, settings: Settings) -> str:
    for key in (settings.cerebras_api_key, settings.sarvam_api_key,
                settings.deepgram_api_key, settings.cartesia_api_key):
        if key:
            text = text.replace(key, "[REDACTED]")
    return " ".join(text.split())[:400]


def _identifier(value: object) -> str:
    text = str(value)
    return text if re.fullmatch(r"[a-zA-Z0-9_.\[\]/-]{1,100}", text) else "unspecified"


def describe_failure(exc: Exception, settings: Settings, timeout_seconds: float | None) -> DecisionFailure:
    """Classify errors; never stringify a rejected decision or validation input."""
    if isinstance(exc, InterviewGenerationError):
        return DecisionFailure(exc.code, _redact(str(exc), settings))
    if isinstance(exc, (TimeoutError, APITimeoutError)):
        budget = f" after {timeout_seconds:g}s" if timeout_seconds is not None else ""
        return DecisionFailure("decision_timeout", f"Cerebras decision timed out{budget}. Check network or LLM timeout in Settings.")
    if isinstance(exc, APIConnectionError):
        return DecisionFailure("provider_connection", "Cerebras connection failed. Check the network and retry Resume.")
    if isinstance(exc, APIStatusError):
        body = exc.body if isinstance(exc.body, dict) else {}
        error = body.get("error", body)
        error = error if isinstance(error, dict) else {}
        code, parameter = _identifier(error.get("code", "unspecified")), _identifier(error.get("param", "unspecified"))
        hint = {401: "Check the Cerebras API key.", 403: "Check Cerebras model access.",
            429: "Check Cerebras credits/rate limits."}.get(exc.status_code, "See the server's redacted provider diagnostic.")
        return DecisionFailure("provider_http", _redact(
            f"Cerebras HTTP {exc.status_code} (code={code}, param={parameter}). {hint}", settings),
            _redact(str(error.get("message", "Provider returned no error message")), settings))
    if isinstance(exc, ValidationError):
        fields = []
        for item in exc.errors(include_input=False, include_context=False, include_url=False)[:3]:
            location = ".".join(_identifier(part) for part in item["loc"]) or "response"
            fields.append(f"{location}: {_identifier(item['type'])}")
        return DecisionFailure("decision_schema", f"Decision JSON failed validation ({'; '.join(fields)}).")
    if isinstance(exc, ValueError):
        # These are static controller messages, not text supplied by the model.
        permitted = {
            "Decision question ID does not match controller", "Spoken text contains control data",
            "Requests cannot become assessment evidence", "Evidence must cite the current answer and reviewed rubric",
            "Evidence criterion is outside reviewed rubric", "Evidence excerpt is absent from accepted answer",
            "A rating needs an actual answer excerpt", "Candidate questions are not technically assessed",
            "Clarification/follow-up requires one spoken prompt", "Follow-up requires an answer",
            "Follow-up must cite actual content from this answer", "Ask only one question at a time",
            "No pending candidate turn", "Stale decision discarded",
        }
        reason = str(exc) if str(exc) in permitted else "Unrecognized decision validation failure"
        return DecisionFailure("decision_rejected", f"Decision rejected: {reason}.")
    if isinstance(exc, OSError):
        return DecisionFailure("checkpoint_io", "Interview checkpoint read/write failed. Check local storage before retrying.")
    return DecisionFailure("decision_internal", f"Interview decision processing failed ({type(exc).__name__}).")
