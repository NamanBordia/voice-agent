// Cumulative, per-call snapshots. Coverage is a browser claim, not scene verification.
const empty = (enabled) => ({ enabled, status: 'not_started', frames_analyzed: 0,
  first_analyzed_secs: null, last_analyzed_secs: null, covered_secs: 0, unknown_secs: 0,
  last_error: null, gaps: [], history_truncated: false });

export class VisionSummary {
  constructor({ run, callSid, elapsed, consent, config }) {
    this.config = config;
    this.run = run ? structuredClone(run) : { version: 1, call_sid: callSid || 'unbound', start_secs: elapsed,
      observed_until_secs: elapsed, end_secs: null, face: empty(consent), phone: empty(consent && Boolean(config.phone_enabled)) };
  }
  gap(stats, start, end, reason) {
    const last = stats.gaps.at(-1);
    if (last) start = Math.max(start, last.end_secs);
    if (end <= start) return;
    if (last && last.end_secs === start && last.reason === reason) { last.end_secs = end; return; }
    if (stats.gaps.length < 64) stats.gaps.push({ start_secs: start, end_secs: end, reason });
    else stats.history_truncated = true;
  }
  success(name, seconds) {
    const stats = this.run[name];
    if (!stats.enabled || this.run.end_secs !== null) return;
    const previous = stats.last_analyzed_secs;
    if (previous !== null && seconds <= previous) return;
    if (previous === null) {
      stats.first_analyzed_secs = seconds; this.gap(stats, this.run.start_secs, seconds, 'loading');
    } else if (seconds - previous <= Math.max(1, 3 / (name === 'face' ? this.config.fps : this.config.phone_fps))) {
      stats.covered_secs += seconds - previous;
    } else this.gap(stats, previous, seconds, 'frame_timeout');
    stats.last_analyzed_secs = seconds; stats.frames_analyzed += 1;
    stats.status = 'active'; stats.last_error = null;
  }
  failure(name, reason, seconds) {
    const stats = this.run[name];
    if (!stats.enabled || stats.status === 'unavailable') return;
    this.gap(stats, stats.last_analyzed_secs ?? this.run.start_secs, seconds, reason);
    stats.status = 'unavailable'; stats.last_error = reason;
  }
  snapshot(seconds, end = false, reason = 'connection_ended') {
    const until = this.run.end_secs ?? Math.max(this.run.observed_until_secs, seconds);
    if (end && this.run.end_secs === null) {
      this.run.end_secs = until;
      for (const name of ['face', 'phone']) {
        const stats = this.run[name]; if (!stats.enabled) continue;
        this.gap(stats, stats.last_analyzed_secs ?? this.run.start_secs, until, reason);
        if (stats.status === 'active') stats.status = 'stopped';
        else if (stats.status === 'not_started') { stats.status = 'unavailable'; stats.last_error = reason; }
      }
    }
    this.run.observed_until_secs = until;
    for (const name of ['face', 'phone']) {
      const stats = this.run[name];
      stats.unknown_secs = stats.enabled ? Math.max(0, until - this.run.start_secs - stats.covered_secs) : 0;
    }
    return structuredClone(this.run);
  }
}
