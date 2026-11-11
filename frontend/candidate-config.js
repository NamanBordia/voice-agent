// The static build replaces this one public value. Local builds stay same-origin.
export const PUBLIC_API_BASE_URL = typeof __CANDIDATE_API_BASE_URL__ === 'undefined' ? '' : __CANDIDATE_API_BASE_URL__;
export const BACKEND_HELP = 'Cannot reach the local interview backend. Start FastAPI at http://127.0.0.1:8000 on this same laptop. In Chrome, allow this site to access apps on your device / the local network, and check that its exact Vercel origin is saved in local Configuration.';

export function validateApiBase(value) {
  if (!value) return '';
  const url = new URL(value);
  if (url.protocol !== 'http:' || !['127.0.0.1', 'localhost', '[::1]'].includes(url.hostname)
    || url.username || url.password || url.pathname !== '/' || url.search || url.hash) {
    throw new Error('PUBLIC_API_BASE_URL must be an HTTP loopback origin, for example http://127.0.0.1:8000.');
  }
  return url.origin;
}

export function candidateLocation(root, location, storage, history, apiBase = PUBLIC_API_BASE_URL) {
  const api = validateApiBase(apiBase);
  const pathId = /^\/candidate\/([a-f0-9]{32})\/?$/.exec(location.pathname)?.[1];
  const sessionId = root.dataset.sessionId || pathId || '';
  const key = api ? `interview-token-${api}-${sessionId}` : `interview-token-${sessionId}`;
  const fragmentToken = new URLSearchParams(location.hash.slice(1)).get('token') || '';
  let saved = '';
  try { saved = storage.getItem(key) || ''; } catch { /* The original fragment remains usable when storage is denied. */ }
  const token = fragmentToken || saved;
  let retained = false;
  try { if (sessionId && token) { storage.setItem(key, token); retained = true; } } catch { /* Preserve fragment for refresh. */ }
  if (!fragmentToken || retained) history.replaceState(null, '', location.pathname);
  root.dataset.sessionId = sessionId;
  return { apiBase: api, sessionId, token, base: `${api}/api/candidate/${encodeURIComponent(sessionId)}` };
}

export function candidateFetchOptions(apiBase) {
  return apiBase ? { credentials: 'omit', referrerPolicy: 'no-referrer', targetAddressSpace: 'loopback' } : {};
}

export async function fetchCandidate(input, init, apiBase, fetcher = globalThis.fetch) {
  try { return await fetcher(input, { ...init, ...candidateFetchOptions(apiBase) }); }
  catch (error) { if (apiBase) throw new Error(BACKEND_HELP); throw error; }
}

export function candidateOfferRequest(url, jsonHeaders, apiBase) {
  // Credentials/headers travel with the Request. The static SDK fetch adapter
  // restores network/referrer options after the SDK's intermediate clones.
  return apiBase ? { endpoint: new Request(url, { method: 'POST', headers: jsonHeaders, ...candidateFetchOptions(apiBase) }) } : undefined;
}

export function sdkCandidateFetch(input, init, apiBase = PUBLIC_API_BASE_URL, fetcher = globalThis.fetch) {
  const url = input instanceof Request ? input.url : String(input);
  return apiBase && (url.startsWith(`${apiBase}/api/candidate/`) || url === `${apiBase}/api/offer`)
    ? fetchCandidate(input, init, apiBase, fetcher) : fetcher(input, init);
}
