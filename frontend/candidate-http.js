// The SDK adds JSON Content-Type itself. Passing it through Headers again makes
// its case-sensitive object merge produce "application/json, application/json".
export function createCandidateHeaders(token) {
  const signalingHeaders = new Headers({ Authorization: `Bearer ${token}` });
  const jsonHeaders = new Headers(signalingHeaders);
  jsonHeaders.set('Content-Type', 'application/json');
  return { signalingHeaders, jsonHeaders };
}
