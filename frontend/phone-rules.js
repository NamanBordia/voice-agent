// Detect sustained visible-phone observations, never use or intent.
export class PhoneRules {
  constructor(config) {
    this.config = config; this.started = null; this.last = null; this.emitted = -Infinity;
    this.reason = 'waiting'; this.resetReason = ''; this.eventsCreated = 0;
    this.lastPositive = null; this.positiveFrames = 0; this.missedFrames = 0;
  }
  reset(reason) {
    this.started = null; this.lastPositive = null; this.positiveFrames = 0; this.missedFrames = 0;
    this.resetReason = reason;
  }
  update(observation, seconds) {
    const gap = Math.max(1, 3 / this.config.phone_fps);
    if (!Number.isFinite(seconds) || (this.last !== null && (seconds <= this.last || seconds - this.last > gap))) {
      this.reset('analysis gap');
    }
    this.last = seconds;
    if (!Number.isFinite(seconds) || !Number.isInteger(observation.count) || observation.count < 0
      || (observation.count > 0 && (!Number.isFinite(observation.confidence) || observation.confidence < 0 || observation.confidence > 1))) {
      this.reset('invalid observation'); this.reason = 'invalid observation'; return [];
    }
    if (observation.count < 1 || observation.confidence < this.config.phone_confidence) {
      this.reason = observation.count < 1 ? 'no phone candidate in the latest frame' : 'phone score below event threshold';
      if (this.started !== null) {
        this.missedFrames += 1;
        if (this.missedFrames > 1 || seconds - this.lastPositive > gap) this.reset(this.reason);
        else this.reason = 'brief miss';
      }
      return [];
    }
    if (this.lastPositive !== null && seconds - this.lastPositive > gap) this.reset('gap between phone sightings');
    if (this.started === null) this.started = seconds;
    this.lastPositive = seconds; this.positiveFrames += 1; this.missedFrames = 0;
    this.reason = 'collecting';
    // An isolated missed sample is not a whole new sighting. Still require the
    // configured span AND enough actual positive samples; one flash cannot emit.
    const requiredFrames = Math.ceil(this.config.phone_sustain_secs * this.config.phone_fps) + 1;
    if (seconds - this.started < this.config.phone_sustain_secs || this.positiveFrames < requiredFrames) return [];
    if (seconds - this.emitted < this.config.phone_cooldown_secs) { this.reason = 'cooldown'; return []; }
    const event = { type: 'phone_detected', start_secs: this.started, duration_secs: seconds - this.started,
      confidence: observation.confidence, face_count: null };
    this.emitted = seconds; this.started = seconds;
    this.positiveFrames = 1;
    this.reason = 'event created'; this.eventsCreated += 1; this.resetReason = '';
    return [event];
  }
  describe() {
    const progress = this.reason === 'collecting'
      ? `Possible phone visible; sighting span ${(this.lastPositive - this.started).toFixed(1)}/${this.config.phone_sustain_secs}s, positive samples ${this.positiveFrames}/${Math.ceil(this.config.phone_sustain_secs * this.config.phone_fps) + 1}`
      : this.reason === 'brief miss' ? 'One missed phone sample; awaiting confirmation before creating an event'
      : this.reason === 'cooldown' ? 'Possible phone visible; repeat event cooldown'
        : this.reason === 'event created' ? 'Possible phone sighting event queued' : this.reason;
    return `${progress}${this.resetReason ? `; previous timer reset: ${this.resetReason}` : ''}. Events created: ${this.eventsCreated}.`;
  }
}
