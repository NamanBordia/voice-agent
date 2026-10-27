"""Editable local settings and saved conversation metrics."""

from pathlib import Path
import math
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from loguru import logger
from pydantic import ValidationError

from app.models.runtime_config_models import RuntimeConfig
from app.services.runtime_config import load_runtime_config, save_runtime_config
from app.storage import CALL_SID_PATTERN, get_call_log, list_call_logs

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))


async def _fetch_recent_logs(limit: int = 50) -> tuple[list[dict[str, Any]], bool]:
    try:
        return await list_call_logs(limit), True
    except OSError:
        logger.exception("History storage is unavailable")
        return [], False


@router.get("/config", response_class=HTMLResponse)
async def get_config(request: Request) -> HTMLResponse:
    """Render saved runtime settings."""
    config = await load_runtime_config()
    return templates.TemplateResponse(request=request, name="config.html",
                                      context={"config": config, "saved": False, "error": None})


def _parse_config_form(form: Any, base: RuntimeConfig) -> RuntimeConfig:
    """Parse posted fields strictly; Pydantic owns coercion and validation."""
    data = base.model_dump(mode="json")
    fields = {
        "stt": {"model": "stt_model", "enable_eager_eot": "stt_enable_eager_eot", "language_code": "stt_language_code",
                "stream_type": "stt_stream_type", "threshold": "stt_threshold",
                "silence_duration_ms": "stt_silence_duration_ms",
                "min_speech_duration_ms": "stt_min_speech_duration_ms",
                "turn_stop_timeout_ms": "stt_turn_stop_timeout_ms"},
        "llm": {"model": "llm_model", "temperature": "llm_temperature", "max_tokens": "llm_max_tokens",
                "reasoning_effort": "llm_reasoning_effort", "request_timeout_seconds": "llm_request_timeout_seconds",
                "enable_speculative_llm": "llm_enable_speculative_llm"},
        "tts": {"model": "tts_model", "voice_id": "tts_voice_id", "pace": "tts_pace",
                "temperature": "tts_temperature", "min_buffer_size": "tts_min_buffer_size"},
        "prompt": {"system_prompt": "system_prompt", "greeting_text": "greeting_text"},
        "memory": {"recent_turn_pairs": "mem_recent_turn_pairs",
                   "enable_running_summary_after_turn": "mem_enable_running_summary_after_turn",
                   "summary_update_every_turns": "mem_summary_update_every_turns",
                   "recall_min_overlap": "mem_recall_min_overlap", "max_memory_block_tokens": "mem_max_memory_block_tokens"},
        "ops": {"debug_log_level": "debug_log_level", "browser_ready_timeout_secs": "browser_ready_timeout_secs",
                "shutdown_timeout_secs": "shutdown_timeout_secs"},
    }
    for group, mapping in fields.items():
        for key, field in mapping.items():
            if field in form:
                value = str(form[field]).strip()
                data[group][key] = False if key == "enable_eager_eot" and value.lower() == "false" else value
    return RuntimeConfig.model_validate(data)


@router.post("/config", response_class=HTMLResponse)
async def post_config(request: Request) -> HTMLResponse:
    """Save validated behavior without changing an active conversation."""
    base = await load_runtime_config()
    status, error, saved = 200, None, False
    try:
        updated = _parse_config_form(await request.form(), base)
        base = await save_runtime_config(updated)
        saved = True
    except ValidationError as exc:
        status = 422
        error = "; ".join(f"{'.'.join(map(str, item['loc']))}: {item['msg']}" for item in exc.errors())
    except OSError:
        logger.exception("Settings save failed")
        status, error = 503, "Settings could not be written; previous settings are still active."
    return templates.TemplateResponse(request=request, name="config.html", status_code=status,
                                      context={"config": base, "saved": saved, "error": error})


def _num(v: Any) -> float | None:
    return v if isinstance(v, (int, float)) else None


def _stat_block(xs: list[float]) -> dict[str, Any]:
    """avg/median/p95/max + measured count for a latency series (None when empty)."""
    if not xs:
        return {"avg": None, "median": None, "p95": None, "max": None, "n": 0}
    return {
        "avg": round(sum(xs) / len(xs), 1),
        "median": _median(xs),
        "p95": _p95(xs),
        "max": max(xs),
        "n": len(xs),
    }


def _latency_summary(turn_records: list[dict[str, Any]]) -> dict[str, Any]:
    """Two distinct per-call latencies, each with avg/median/p95/max + measured count.

    - ``responsiveness`` = time from the user's LAST utterance to first audio. This is
      the felt latency: it excludes both the endpointing window (system confirming the
      turn ended) AND any mid-thought pause on a merged turn. Uses the latest-anchored
      turn-release value, falling back to the release/primary value for older records.
    - ``total`` = the VAD-anchored "true silence -> first audio" (``primary_latency_ms``):
      physically honest but includes ~600-900ms of endpointing the caller does not
      perceive as waiting.

    Null-latency turns are excluded; the measured count (``n``) is reported so the UI can
    show ``median over n of total`` instead of hiding the gap.
    """
    total_vals: list[float] = []
    resp_vals: list[float] = []
    for r in turn_records:
        total = _num(r.get("primary_latency_ms"))
        if total is not None:
            total_vals.append(total)
        resp = _num(r.get("latest_user_stop_to_first_audio_ms"))
        if resp is None:
            resp = _num(r.get("user_stop_to_first_audio_release_ms"))
        if resp is None:
            resp = total
        if resp is not None:
            resp_vals.append(resp)
    return {
        "total_turns": len(turn_records),
        "responsiveness": _stat_block(resp_vals),
        "total": _stat_block(total_vals),
    }


def _badge_class(ms: float | None, *, warn: float, crit: float) -> str:
    """Latency → badge class. Thresholds live here (UI helper), not in templates."""
    if ms is None:
        return "neutral"
    if ms > crit:
        return "crit"
    if ms > warn:
        return "warn"
    return "ok"


def _median(xs: list[float]) -> float | None:
    if not xs:
        return None
    s = sorted(xs)
    n = len(s)
    mid = n // 2
    return round((s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2), 1)


def _p95(xs: list[float]) -> float | None:
    if not xs:
        return None
    s = sorted(xs)
    # nearest-rank p95 (index ceil(0.95*n)-1)
    idx = max(0, math.ceil(0.95 * len(s)) - 1)
    return round(s[idx], 1)


def _eager_probe_summary(records: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Derive the call-level EagerEOT telemetry rollup from per-turn eager probes.

    Returns None when no turn carried a probe (eager telemetry was off), so the UI
    only shows the summary for calls that actually ran the probe.
    """
    probes = [r.get("eager_probe") for r in records if isinstance(r.get("eager_probe"), dict)]
    if not probes:
        return None
    total = len(records)
    eager = [p for p in probes if p.get("eager_eot_seen")]
    eligible = [p for p in probes if p.get("verdict") == "eligible"]
    # Count resumes only among eager turns so the numerator can never exceed the
    # denominator (a TurnResumed only matters as the cancel signal for an eager).
    resumed = [p for p in eager if p.get("turn_resumed_seen")]
    mismatch = [p for p in probes if p.get("verdict") == "discarded_transcript_mismatch"]
    matched = [p for p in eager if p.get("eager_final_match") is True]
    # Only positive head starts (drop the degenerate eager-at/after-EOT cases that
    # store a <=0 head_start) so the median/p95 reflect real head room.
    head_starts = [
        p["head_start_ms"] for p in eager
        if isinstance(p.get("head_start_ms"), (int, float)) and p["head_start_ms"] > 0
    ]
    saved = [p["estimated_saved_ms"] for p in eligible if isinstance(p.get("estimated_saved_ms"), (int, float))]

    def _rate(num: int, den: int) -> float:
        return round(num / den, 3) if den else 0.0

    summary = {
        "total_turns": total,
        "eager_eot_turns": len(eager),
        "eligible_turns": len(eligible),
        "turn_resumed_turns": len(resumed),
        "transcript_mismatch_turns": len(mismatch),
        "eager_eot_rate": _rate(len(eager), total),
        "eligible_rate": _rate(len(eligible), total),
        "turn_resumed_rate": _rate(len(resumed), len(eager)),
        "eager_final_match_rate": _rate(len(matched), len(eager)),
        "median_head_start_ms": _median(head_starts),
        "p95_head_start_ms": _p95(head_starts),
        "median_estimated_saved_ms": _median(saved),
        "p95_estimated_saved_ms": _p95(saved),
        "total_estimated_saved_ms": round(sum(saved), 1) if saved else 0.0,
        "avg_estimated_saved_ms_per_turn": round(sum(saved) / total, 1) if total else 0.0,
    }

    # --- Stage-2 speculative-draft rollup (probes carry draft_* only when on) ---
    draft_started = [p for p in probes if p.get("draft_started")]
    if draft_started:
        draft_released = [p for p in probes if p.get("draft_released")]
        # A turn that resumed-then-redrafted-then-released carries BOTH draft_cancelled
        # and draft_released; release wins so it is not double-counted (defensive — the
        # controller also clears the stale cancel marks when the redraft starts).
        draft_cancelled = [p for p in probes if p.get("draft_cancelled") and not p.get("draft_released")]
        normal_used = [p for p in probes if p.get("normal_llm_used")]
        draft_errors = [p for p in probes if p.get("draft_error")]
        extra_calls = sum(int(p.get("extra_llm_call") or 0) for p in probes)
        # MEASURED draft head start over RELEASED turns (not the Stage-1 estimate).
        headstarts = [
            p["draft_headstart_ms"] for p in draft_released
            if isinstance(p.get("draft_headstart_ms"), (int, float))
        ]
        # Empirical end-to-end comparison: median user-stop->first-audio for released
        # vs normal turns (the honest "real saving" signal across the call). Use the
        # turn-release anchor (defined identically for EVERY turn) rather than the
        # headline user_stop_to_first_audio_ms, which is VAD-anchored on some turns and
        # release-anchored on others — mixing the two would make released-vs-normal an
        # apples-to-oranges comparison (F1 review). Fall back to the headline for old
        # records persisted before the release field existed.
        def _release_fa(r: dict[str, Any]) -> float | None:
            v = r.get("user_stop_to_first_audio_release_ms")
            if not isinstance(v, (int, float)):
                v = r.get("user_stop_to_first_audio_ms")
            return v if isinstance(v, (int, float)) else None

        released_fa = [
            fa for r in records
            if isinstance(r.get("eager_probe"), dict) and r["eager_probe"].get("draft_released")
            for fa in [_release_fa(r)] if fa is not None
        ]
        normal_fa = [
            fa for r in records
            if isinstance(r.get("eager_probe"), dict) and r["eager_probe"].get("normal_llm_used")
            for fa in [_release_fa(r)] if fa is not None
        ]
        med_released, med_normal = _median(released_fa), _median(normal_fa)
        summary.update({
            "speculation_on": True,
            "draft_start_count": len(draft_started),
            "draft_release_count": len(draft_released),
            "draft_cancel_count": len(draft_cancelled),
            "draft_error_count": len(draft_errors),
            "normal_llm_used_count": len(normal_used),
            "draft_use_rate": _rate(len(draft_released), len(draft_started)),
            "cancel_rate": _rate(len(draft_cancelled), len(draft_started)),
            "additional_llm_call_rate": _rate(extra_calls, total),
            "median_draft_headstart_ms": _median(headstarts),
            "p95_draft_headstart_ms": _p95(headstarts),
            "median_user_stop_to_first_audio_released_ms": med_released,
            "median_user_stop_to_first_audio_normal_ms": med_normal,
            "empirical_saved_ms_population": (
                round(med_normal - med_released, 1)
                if med_normal is not None and med_released is not None else None
            ),
        })
    return summary


def _memory_summary(records: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Call-level rollup of the per-turn memory snapshots (recall/context size).

    Returns None when no turn carried a memory snapshot (feature off / no user
    turns), so the UI only shows the card for calls that actually have data.
    """
    mems = [r.get("memory") for r in records if isinstance(r.get("memory"), dict)]
    if not mems:
        return None
    total = len(records)
    recall_used = [m for m in mems if m.get("recall_used")]
    triggered = [m for m in mems if m.get("recall_triggered")]
    ctx_counts = [
        m["context_message_count"] for m in mems
        if isinstance(m.get("context_message_count"), (int, float))
    ]
    toks = [
        m["approx_memory_tokens"] for m in mems
        if isinstance(m.get("approx_memory_tokens"), (int, float))
    ]
    total_snippets = sum(len(m.get("recall_snippets") or []) for m in mems)

    def _rate(num: int, den: int) -> float:
        return round(num / den, 3) if den else 0.0

    return {
        "total_turns": total,
        "memory_turns": len(mems),
        "recall_used_turns": len(recall_used),
        "recall_triggered_turns": len(triggered),
        "recall_use_rate": _rate(len(recall_used), len(mems)),
        "total_recall_snippets": total_snippets,
        "median_context_messages": _median([float(c) for c in ctx_counts]),
        "median_approx_memory_tokens": _median([float(t) for t in toks]),
        "max_approx_memory_tokens": max(toks) if toks else None,
    }


@router.get("/calls", response_class=HTMLResponse)
async def get_calls_list(request: Request) -> HTMLResponse:
    """Render the completed-calls list with per-call latency stats."""
    logs, db_connected = await _fetch_recent_logs()
    for log in logs:
        records = log.get("metrics", {}).get("turn_records", [])
        lat = _latency_summary(records)
        resp, total = lat["responsiveness"], lat["total"]
        # Responsiveness (felt latency) is the headline column; total (incl. endpointing)
        # is shown muted alongside. Median, not avg — a few slow-LLM turns skew the mean.
        log["resp_ms"] = resp["median"]
        log["resp_badge"] = _badge_class(resp["median"], warn=800, crit=1500)
        log["total_ms"] = total["median"]
        log["turn_count"] = log.get("metrics", {}).get("turn_count", len(records))
        log["measured_latency_turns"] = resp["n"]
    return templates.TemplateResponse(
        request=request,
        name="calls.html",
        context={"logs": logs, "db_connected": db_connected},
    )


@router.get("/calls/{call_sid}", response_class=HTMLResponse)
async def get_call_detail(request: Request, call_sid: str) -> HTMLResponse:
    """Render the detail page for one completed call."""
    if not CALL_SID_PATTERN.fullmatch(call_sid):
        return HTMLResponse("Conversation not found.", status_code=404)
    try:
        log = await get_call_log(call_sid)
    except ValueError:
        return HTMLResponse("Invalid conversation file or identifier.", status_code=500)
    except OSError:
        return HTMLResponse("Conversation could not be read.", status_code=500)
    if log is None:
        return HTMLResponse("Conversation not found.", status_code=404)

    records = log.get("metrics", {}).get("turn_records", [])
    return templates.TemplateResponse(
        request=request,
        name="call_detail.html",
        context={
            "log": log,
            "turn_records": records,
            "latency": _latency_summary(records),
            "eager_summary": _eager_probe_summary(records),
            "memory_summary": _memory_summary(records),
        },
    )
