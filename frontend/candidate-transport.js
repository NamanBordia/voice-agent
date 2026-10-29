// SmallWebRTC 1.10.2 schedules retries even after stop, and flushes ICE after a
// failed offer. Candidate sessions need an explicit Resume with server cleanup.
export function candidateTransport(BaseTransport) {
  return class CandidateTransport extends BaseTransport {
    interviewClosed = false;
    interviewFailureReported = false;

    async attemptReconnection() {
      if (this.interviewClosed || this.interviewFailureReported) return;
      this.interviewFailureReported = true;
      this._callbacks?.onError?.(new Error('Interview connection failed. Wait for cleanup, then click Resume interview.'));
    }

    async flushIceCandidates() {
      if (this.interviewClosed || this.interviewFailureReported || !this.pc_id) {
        clearTimeout(this.__flushTimeout);
        this.__flushTimeout = null;
        return;
      }
      await super.flushIceCandidates();
    }

    async stop() {
      this.interviewClosed = true;
      clearTimeout(this.__flushTimeout);
      this._candidateQueue = [];
      await super.stop();
    }
  };
}
