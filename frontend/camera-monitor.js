import { ObservationRules } from './camera-rules.js';
import { PhoneRules } from './phone-rules.js';
import { VisionSummary } from './vision-summary.js';

export class CameraMonitor {
  constructor({ video, config, send, heartbeat, status, elapsed = 0, callSid, run, workerFactory = () => new Worker('/static/camera-worker.js'), bitmapFactory = (source) => createImageBitmap(source, { resizeWidth: 640, resizeHeight: 360 }) }) {
    Object.assign(this, { video, send, heartbeat, status, elapsed, callSid, run, workerFactory, bitmapFactory });
    this.config = { phone_enabled: false, phone_fps: 2, phone_confidence: .7, phone_sustain_secs: 2, phone_cooldown_secs: 15, ...config };
    this.rules = new ObservationRules(this.config); this.phoneRules = new PhoneRules(this.config); this.worker = null; this.stream = null;
    this.busy = false; this.ready = false; this.closed = false; this.timer = null; this.watchdog = null;
    this.heartbeatTimer = null; this.startedAt = 0; this.monitoring = 'not_started';
    this.bestPhoneScore = null; this.lastPhoneScore = null;
    this.phoneEventsSaved = 0; this.phoneEventsUnconfirmed = 0;
  }
  seconds() { return this.elapsed + (performance.now() - this.startedAt) / 1000; }
  emit(status) {
    const snapshot = this.summary.snapshot(this.seconds());
    return Promise.resolve(this.heartbeat(status, snapshot));
  }
  showStatus() {
    const label = (name) => {
      const stats = this.summary.run[name];
      return !stats.enabled ? 'not enabled' : stats.status === 'active' ? `${stats.frames_analyzed} frames analyzed`
        : stats.status === 'unavailable' ? `unavailable (${stats.last_error})` : 'waiting for analysis';
    };
    let phoneDetails = '';
    if (this.summary.run.phone.status === 'active') {
      const score = this.lastPhoneScore === null ? 'none' : this.lastPhoneScore.toFixed(2);
      const best = this.bestPhoneScore === null ? 'none' : this.bestPhoneScore.toFixed(2);
      phoneDetails = ` Phone score: ${score}; best: ${best}; event threshold: ${this.config.phone_confidence.toFixed(2)}. ${this.phoneRules.describe()} Phone events saved: ${this.phoneEventsSaved}; delivery unconfirmed: ${this.phoneEventsUnconfirmed}.`;
    }
    this.status(`Face: ${label('face')}. Phone: ${label('phone')}.${phoneDetails} Images remain on this device.`);
  }
  sendPhoneEvent(event) {
    let delivery;
    try { delivery = Promise.resolve(this.send(event)); }
    catch (error) { delivery = Promise.reject(error); }
    void delivery.then((receipt) => {
      if (receipt?.delivered === true) this.phoneEventsSaved += 1;
      else this.phoneEventsUnconfirmed += 1;
    }, () => { this.phoneEventsUnconfirmed += 1; }).then(() => {
      if (!this.closed) this.showStatus();
    });
  }
  async start(stream, consent) {
    this.startedAt = performance.now(); this.stream = stream;
    this.summary = new VisionSummary({ run: this.run, callSid: this.callSid, elapsed: this.elapsed, consent, config: this.config });
    if (!consent || !stream || !stream.getVideoTracks().some((track) => track.readyState !== 'ended') || !globalThis.Worker || !globalThis.OffscreenCanvas || !globalThis.createImageBitmap) {
      this.fail(consent && !stream ? 'camera_unavailable' : 'monitoring_unavailable', !consent ? 'no_consent' : !stream ? 'camera_unavailable' : 'unsupported_browser'); return;
    }
    void this.emit('not_started');
    try {
      this.worker = this.workerFactory();
      this.worker.onmessage = ({ data }) => {
        if (this.closed) return;
        if (data.type === 'ready') {
          if (this.ready) return;
          this.available = { face: (data.detectors?.face || 'ready') === 'ready', phone: data.detectors?.phone === 'ready' };
          for (const name of ['face', 'phone']) {
            if (!this.available[name]) this.summary.failure(name, 'model_load_failed', this.seconds());
          }
          if (!this.available.face && !this.available.phone) { this.fail('monitoring_unavailable', 'model_load_failed'); return; }
          clearTimeout(this.watchdog); this.ready = true; this.lastResultAt = performance.now();
          this.showStatus();
          if (this.config.phone_enabled && !this.available.phone) void this.emit('not_started');
          this.watchdog = setTimeout(() => this.fail('monitoring_unavailable', 'frame_timeout'), 5000);
          this.heartbeatTimer = setInterval(() => {
            if (performance.now() - this.lastResultAt > 5000) this.fail('monitoring_unavailable', 'frame_timeout');
            else void this.emit(this.monitoring);
          }, 10000);
          this.timer = setInterval(() => void this.tick(), 1000 / this.config.fps);
        } else if (data.type === 'result') {
          if (!this.ready || !this.busy) { this.fail('monitoring_unavailable', 'worker_error'); return; }
          const face = data.face || (data.count !== undefined ? data : null);
          const phone = data.phone || null;
          for (const value of [face, phone]) {
            if (value && (!Number.isInteger(value.count) || value.count < 0
              || (value.count > 0 && (!Number.isFinite(value.confidence) || value.confidence < 0 || value.confidence > 1)))) {
              this.fail('monitoring_unavailable', 'worker_error'); return;
            }
          }
          const previous = this.monitoring; const seconds = this.seconds();
          for (const name of ['face', 'phone']) {
            if (data.errors?.[name]) {
              this.available[name] = false; this.summary.failure(name, 'inference_failed', seconds);
              if (name === 'phone') this.phoneVisible = false;
            }
          }
          if (face && this.available.face) {
            this.summary.success('face', seconds);
            for (const event of this.rules.update(face, seconds)) void this.send(event);
          }
          if (phone && this.available.phone && this.config.phone_enabled) {
            this.lastPhoneScore = phone.count > 0 ? phone.confidence : null;
            if (this.lastPhoneScore !== null) this.bestPhoneScore = Math.max(this.bestPhoneScore ?? 0, this.lastPhoneScore);
            this.phoneVisible = phone.count > 0 && phone.confidence >= this.config.phone_confidence;
            this.summary.success('phone', seconds);
            for (const event of this.phoneRules.update(phone, seconds)) this.sendPhoneEvent(event);
          }
          if (!this.available.face && !this.available.phone) { this.fail('monitoring_unavailable', 'inference_failed'); return; }
          clearTimeout(this.watchdog); this.busy = false;
          if (face || phone) this.lastResultAt = performance.now();
          this.monitoring = ['face', 'phone'].some((name) => this.summary.run[name].status === 'active') ? 'active' : 'unavailable';
          this.watchdog = setTimeout(() => this.fail('monitoring_unavailable', 'frame_timeout'), 5000);
          this.showStatus();
          if (this.monitoring !== previous || Object.keys(data.errors || {}).length) void this.emit(this.monitoring);
        } else this.fail('monitoring_unavailable', 'worker_error');
      };
      this.worker.onerror = () => this.fail('monitoring_unavailable', 'worker_error');
      for (const track of stream.getVideoTracks()) track.addEventListener('ended', () => this.fail('camera_unavailable', 'camera_unavailable'), { once: true });
      this.watchdog = setTimeout(() => this.fail('monitoring_unavailable', 'model_load_failed'), 15000);
      this.worker.postMessage({ type: 'init', confidence: this.config.confidence, phone_enabled: this.config.phone_enabled,
        phone_fps: this.config.phone_fps, phone_confidence: this.config.phone_confidence });
    } catch { this.fail('monitoring_unavailable', 'worker_error'); }
  }
  async tick() {
    if (this.closed || !this.ready || this.busy || this.video.readyState < 2) return;
    this.busy = true; let bitmap;
    // Covers both stalled bitmap creation and stalled detector inference.
    clearTimeout(this.watchdog);
    this.watchdog = setTimeout(() => this.fail('monitoring_unavailable', 'frame_timeout'), 5000);
    try {
      bitmap = await this.bitmapFactory(this.video);
      if (this.closed || !this.worker) { bitmap.close(); this.busy = false; return; }
      this.worker.postMessage({ type: 'frame', bitmap, timestamp: performance.now() }, [bitmap]);
    } catch { bitmap?.close(); this.fail('monitoring_unavailable', 'frame_timeout'); }
  }
  fail(type, reason = 'worker_error') {
    if (this.closed) return;
    const seconds = this.seconds();
    for (const name of ['face', 'phone']) this.summary.failure(name, reason, seconds);
    this.stop(false, reason); this.monitoring = 'unavailable';
    this.status(`${type}: voice can continue. No technical rating is affected.`);
    void this.send({ type, start_secs: seconds, duration_secs: 0 });
    this.finalDelivery = this.emit('unavailable');
  }
  stop(notify = true, reason = 'connection_ended') {
    if (this.closed) return this.finalDelivery || Promise.resolve();
    this.closed = true; this.ready = false;
    clearInterval(this.timer); clearInterval(this.heartbeatTimer); clearTimeout(this.watchdog);
    if (this.worker) { try { this.worker.postMessage({ type: 'close' }); } catch {} this.worker.terminate(); this.worker = null; }
    this.stream?.getTracks().forEach((track) => track.stop()); this.stream = null;
    this.video.srcObject = null;
    this.summary?.snapshot(this.seconds(), true, reason);
    if (notify && this.summary) {
      this.monitoring = this.summary.run.face.status;
      this.finalDelivery = this.emit(this.monitoring);
    }
    return this.finalDelivery || Promise.resolve();
  }
}
