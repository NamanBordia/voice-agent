// Pure temporal rules. Detector outputs are observations, never suitability judgments.
export class ObservationRules {
  constructor(config) { this.config = config; this.runs = new Map(); this.lastEvents = new Map(); this.baseline = null; this.lastTime = null; }
  update(observation, seconds) {
    const { count, confidence, center } = observation;
    const valid = Number.isInteger(count) && count >= 0 && Number.isFinite(seconds);
    if (!valid) return [];
    if (this.lastTime !== null && seconds - this.lastTime > Math.max(1, 3 / this.config.fps)) this.runs.clear();
    this.lastTime = seconds;
    if (count === 1 && center && !this.baseline) this.baseline = center;
    const displaced = count === 1 && center && this.baseline && Math.hypot(center.x - this.baseline.x, center.y - this.baseline.y) >= this.config.displacement_ratio;
    const conditions = [
      ['face_absent', count === 0, this.config.absent_secs],
      ['multiple_faces_detected', count > 1 && confidence >= this.config.confidence, this.config.multiple_secs],
      ['face_position_changed', displaced && confidence >= this.config.confidence, this.config.displacement_secs],
    ];
    const events = [];
    for (const [type, present, threshold] of conditions) {
      if (!present) { this.runs.delete(type); continue; }
      if (!this.runs.has(type)) this.runs.set(type, seconds);
      const start = this.runs.get(type);
      if (seconds - start >= threshold && seconds - (this.lastEvents.get(type) ?? -Infinity) >= this.config.cooldown_secs) {
        events.push({ type, start_secs: start, duration_secs: seconds - start, face_count: count, confidence: count ? confidence : null });
        this.lastEvents.set(type, seconds); this.runs.set(type, seconds);
        if (type === 'face_position_changed') this.baseline = center;
      }
    }
    return events;
  }
}
