import { workspaceClient } from './workspace-api.js';
import { setupConfig, showCalls, showCall } from './workspace-pages.js';

const element = (id) => document.getElementById(id);
const message = (text) => { element('workspace-connection-status').textContent = text; };
let loaded = false;
let connecting = false;
const client = workspaceClient({ onAccessDenied() {
  element('workspace-content').hidden = true;
  message('Hosted access is disabled. Save this site’s exact origin in local Configuration, then reconnect.');
}, onConnectionLost(error) {
  // Retain the current page's Stop/Retry controls when a live voice call loses HTTP access.
  message(error.message);
} });
globalThis.workspaceRequest = client.request;
element('workspace-origin').textContent = location.origin;
for (const link of document.querySelectorAll('.topnav a')) {
  if (link.pathname === location.pathname || (location.pathname === '/' && link.pathname === '/interviews')
      || (location.pathname.startsWith('/calls/') && link.pathname === '/calls')) link.setAttribute('aria-current', 'page');
}

async function loadPage() {
  if (loaded) { location.reload(); return; }
  const page = document.body.dataset.page;
  if (page === 'interviews') await import('./recruiter.js');
  else if (page === 'talk') {
    globalThis.workspaceVoice = client.voice();
    await import('./demo.js');
  } else if (page === 'config') await setupConfig(client.request, element('workspace-content'));
  else if (page === 'calls') await showCalls(client.request, element('workspace-content'));
  else if (page === 'call') await showCall(client.request, element('workspace-content'), location.pathname.split('/').filter(Boolean).at(-1));
  loaded = true;
}
async function connect() {
  if (connecting) return;
  connecting = true;
  element('workspace-connect').disabled = true;
  message('Connecting to FastAPI on this laptop…');
  try {
    await client.request('/api/workspace/config');
    client.retain();
    await loadPage();
    element('workspace-content').hidden = false;
    message('Connected to your local backend. Provider keys and saved records remain on this laptop.');
  } catch (error) { element('workspace-content').hidden = true; message(error.message); }
  finally { connecting = false; element('workspace-connect').disabled = false; }
}
element('workspace-connect-form').addEventListener('submit', (event) => { event.preventDefault(); void connect(); });
element('workspace-disconnect').addEventListener('click', () => {
  // Reload also releases a Talk connection through its existing page-exit cleanup.
  client.clear(); location.reload();
});
// A successful first connection already requested Chrome's network permission.
// Keep navigation/refresh convenient without persisting anything beyond this tab.
if (client.connected()) void connect();
