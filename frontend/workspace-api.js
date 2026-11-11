import { PUBLIC_API_BASE_URL, candidateFetchOptions, fetchCandidate, candidateOfferRequest } from './candidate-config.js';

export const workspaceStorageKey = (apiBase) => `workspace-connected-${apiBase}`;

function tabStorage() { try { return globalThis.sessionStorage; } catch { return undefined; } }

export function workspaceClient({ apiBase = PUBLIC_API_BASE_URL, storage = tabStorage(),
  fetcher = globalThis.fetch, onAccessDenied = () => {}, onConnectionLost = () => {} } = {}) {
  // Remove obsolete recruiter secrets left by the previous paired build.
  try { storage?.removeItem(`workspace-pairing-${apiBase}`); } catch { /* Storage is optional. */ }
  const headers = () => ({ 'Content-Type': 'application/json' });
  function connected() { try { return storage?.getItem(workspaceStorageKey(apiBase)) === 'yes'; } catch { return false; } }
  function retain() { try { storage?.setItem(workspaceStorageKey(apiBase), 'yes'); } catch { /* Current page remains usable. */ } }
  function clear() { try { storage?.removeItem(workspaceStorageKey(apiBase)); } catch { /* No credentials are held. */ } }
  async function fetchPath(path, init = {}) {
    if (!/^\/api\/(?:interviews(?:\/|$)|workspace\/(?:config|calls)(?:\/|$)|offer$|sessions\/browser_[a-f0-9]{32}\/end$)/.test(path)) {
      throw new Error('Request is outside the recruiter workspace API.');
    }
    let response;
    try {
      response = await fetchCandidate(`${apiBase}${path}`, {
        ...init, headers: { ...headers(), ...init.headers },
      }, apiBase, fetcher);
    } catch (error) {
      clear(); onConnectionLost(error); throw error;
    }
    if (response.status === 401 || response.status === 403) { clear(); onAccessDenied(); }
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
  return { request, fetch: fetchPath, headers, connected, retain, clear, voice, networkOptions: candidateFetchOptions(apiBase) };
}
