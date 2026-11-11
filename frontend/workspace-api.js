import { PUBLIC_API_BASE_URL, candidateFetchOptions, fetchCandidate, candidateOfferRequest } from './candidate-config.js';

export const PAIRING_HELP = 'Pair this workspace from local Configuration. Start FastAPI on this laptop, save this exact Vercel origin in Hosted workspace origin, create a pairing code and paste it here.';
export const workspaceStorageKey = (apiBase) => `workspace-pairing-${apiBase}`;

function tabStorage() { try { return globalThis.sessionStorage; } catch { return undefined; } }

export function workspaceClient({ apiBase = PUBLIC_API_BASE_URL, storage = tabStorage(),
  fetcher = globalThis.fetch, onUnauthorized = () => {} } = {}) {
  let token = '';
  try { token = storage?.getItem(workspaceStorageKey(apiBase)) || ''; } catch { /* Ask for pairing again. */ }
  const headers = () => ({ 'Content-Type': 'application/json', ...(apiBase ? { Authorization: `Bearer ${token}` } : {}) });
  function setToken(value) { token = value.trim(); }
  function retain() { try { storage?.setItem(workspaceStorageKey(apiBase), token); } catch { /* Current page remains usable. */ } }
  function clear() { token = ''; try { storage?.removeItem(workspaceStorageKey(apiBase)); } catch { /* Memory is already cleared. */ } }
  async function fetchPath(path, init = {}) {
    if (!/^\/api\/(?:interviews(?:\/|$)|workspace\/(?:config|calls)(?:\/|$)|offer$|sessions\/browser_[a-f0-9]{32}\/end$)/.test(path)) {
      throw new Error('Request is outside the recruiter workspace API.');
    }
    const response = await fetchCandidate(`${apiBase}${path}`, {
      ...init, headers: { ...headers(), ...init.headers },
    }, apiBase, fetcher);
    if (response.status === 401) { clear(); onUnauthorized(); throw new Error(PAIRING_HELP); }
    return response;
  }
  async function request(path, method = 'GET', body) {
    const response = await fetchPath(path, { method, body: body === undefined ? undefined : JSON.stringify(body) });
    const result = await response.json();
    if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : JSON.stringify(result.detail));
    return result;
  }
  function voice() {
    return { headers: headers(), offerEndpoint: `${apiBase}/api/offer`,
      offerRequest: candidateOfferRequest(`${apiBase}/api/offer`, headers(), apiBase),
      endEndpoint: (sid) => `${apiBase}/api/sessions/${encodeURIComponent(sid)}/end`,
      fetch: (input, init) => {
        const url = new URL(input instanceof Request ? input.url : input, `${apiBase}/`);
        if (url.origin !== apiBase) throw new Error('Invalid workspace API origin.');
        return fetchPath(url.pathname, init);
      } };
  }
  return { request, fetch: fetchPath, headers, setToken, retain, clear, voice, networkOptions: candidateFetchOptions(apiBase) };
}
