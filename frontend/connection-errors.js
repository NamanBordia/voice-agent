// SDK and device helpers may reject with a Response, string or errorMsg object.
export async function connectionFailureText(cause, phase = 'signaling') {
  let detail = typeof cause === 'string' ? cause : '';
  for (const value of [cause?.data?.error, cause?.errorMsg, cause?.message, cause?.error,
    cause?.details?.message, cause?.cause?.message]) {
    if (typeof value === 'string' && value.trim()) { detail = value; break; }
  }
  if (typeof cause?.status === 'number' && typeof cause?.clone === 'function') {
    try {
      const body = await cause.clone().json();
      if (typeof body.detail === 'string') detail = body.detail;
      else if (Array.isArray(body.detail)) detail = body.detail.map((item) => `${(item.loc || []).join('.')}: ${item.msg}`).join('; ');
    } catch { /* Use the HTTP status for a non-JSON response. */ }
    detail = `HTTP ${cause.status}${detail ? `: ${detail}` : ''}`;
  }
  const name = cause?.name || cause?.type || '';
  if (/NotAllowed|PermissionDenied|permissions?/i.test(`${name} ${detail}`)) {
    return 'Microphone access was denied. In Chrome site settings, allow Microphone for this hosted site, then retry. Check Windows microphone privacy settings if access remains blocked.';
  }
  if (/NotFound|DevicesNotFound/.test(name)) return 'No microphone was found. Connect or enable a microphone, then retry.';
  if (/NotReadable|TrackStartError/.test(name)) return 'Chrome could not open the microphone. Close other apps using it, check Windows microphone access, then retry.';
  const prefix = phase === 'microphone' ? 'Microphone setup failed'
    : phase === 'audio helper' ? 'Audio device helper could not load' : 'Voice connection failed';
  return `${prefix}${detail ? `: ${detail.slice(0, 700)}` : '. Check the browser console and local FastAPI logs for details.'}`;
}
