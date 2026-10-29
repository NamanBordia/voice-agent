"""Validate cumulative camera metadata and conservatively account for missing updates."""

from fastapi import HTTPException
from app.interviews.models import CameraConfig, DetectorStats, Monitoring, VisionGap, VisionReason, VisionRun


def start_run(monitoring: Monitoring, call_sid: str, elapsed: float, consent: bool, config: CameraConfig) -> None:
    """Reserve a bounded run before browser metadata, leaving all analysis unknown."""
    if len(monitoring.runs) >= 100:
        raise HTTPException(409, "Monitoring run limit reached; create another interview")
    monitoring.runs.append(VisionRun(call_sid=call_sid, start_secs=elapsed, observed_until_secs=elapsed,
        face=DetectorStats(enabled=consent), phone=DetectorStats(enabled=consent and config.phone_enabled)))


def extend_unknown(run: VisionRun, until: float, reason: VisionReason, *, close: bool = False, mark_unavailable: bool = True) -> None:
    """Retain counters; never turn an unconfirmed tail into successful coverage."""
    until = max(run.observed_until_secs, until)
    for stats in (run.face, run.phone):
        if not stats.enabled:
            continue
        if until > run.observed_until_secs:
            if len(stats.gaps) < 64:
                stats.gaps.append(VisionGap(start_secs=run.observed_until_secs, end_secs=until, reason=reason))
            else:
                stats.history_truncated = True
        stats.unknown_secs = max(0, until - run.start_secs - stats.covered_secs)
        if stats.status in {"active", "not_started"} and (close or mark_unavailable):
            stats.status = "stopped" if close and stats.frames_analyzed else "unavailable"
            stats.last_error = reason
    run.observed_until_secs = until
    if close:
        run.end_secs = until


def merge_run(monitoring: Monitoring, incoming: VisionRun, call_sid: str | None,
              elapsed: float, consent: bool, config: CameraConfig) -> None:
    """Replace only the current call's monotonic snapshot, not accumulated totals."""
    previous = next((run for run in monitoring.runs if run.call_sid == incoming.call_sid), None)
    if incoming.call_sid != call_sid or previous is None:
        raise HTTPException(403, "Monitoring run is outside the current media call")
    if previous.end_secs is not None:
        if previous != incoming:
            raise HTTPException(409, "Monitoring run has already ended")
        return
    if incoming.start_secs != previous.start_secs or incoming.observed_until_secs < previous.observed_until_secs:
        raise HTTPException(409, "Monitoring run time cannot reset")
    if incoming.observed_until_secs > elapsed + 2:
        raise HTTPException(422, "Monitoring timing exceeds active interview time")
    for name in ("face", "phone"):
        before, after = getattr(previous, name), getattr(incoming, name)
        if after.enabled != before.enabled or (after.enabled and name == "phone" and not config.phone_enabled):
            raise HTTPException(403, "Detector was not enabled for this call")
        if not consent and after.status == "active":
            raise HTTPException(403, "Monitoring requires consent")
        if (after.frames_analyzed < before.frames_analyzed or after.covered_secs + .001 < before.covered_secs
                or (before.first_analyzed_secs is not None and after.first_analyzed_secs != before.first_analyzed_secs)
                or (before.last_analyzed_secs is not None and (after.last_analyzed_secs is None or after.last_analyzed_secs < before.last_analyzed_secs))):
            raise HTTPException(409, "Monitoring counters cannot decrease or reset")
        if after.frames_analyzed == before.frames_analyzed and (after.covered_secs != before.covered_secs
                or after.first_analyzed_secs != before.first_analyzed_secs or after.last_analyzed_secs != before.last_analyzed_secs):
            raise HTTPException(409, "Analysis changes require additional successful frames")
        cadence = config.fps if name == "face" else config.phone_fps
        if after.covered_secs > max(0, after.frames_analyzed - 1) * max(1, 3 / cadence) + .01:
            raise HTTPException(422, "Coverage exceeds the successful frame count")
        if after.frames_analyzed > (incoming.observed_until_secs - incoming.start_secs) * cadence + 10:
            raise HTTPException(422, "Frame count exceeds configured cadence")
    monitoring.runs[monitoring.runs.index(previous)] = incoming.model_copy(deep=True)
