import { workspaceClient } from './workspace-api.js';
import { setupConfig, showCalls, showCall } from './workspace-pages.js';

const element = (id) => document.getElementById(id);
const message = (text) => {
  element('workspace-connection-status').textContent = text;
  element('workspace-connection-alert').hidden = false;
  element('workspace-loading').hidden = true;
};
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
for (const link of document.querySelectorAll('.topnav a')) {
  if (link.pathname === location.pathname || (location.pathname === '/' && link.pathname === '/interviews')
      || (location.pathname.startsWith('/calls/') && link.pathname === '/calls')) link.setAttribute('aria-current', 'page');
}

async function loadPage() {
  if (loaded) return;
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
  element('workspace-retry').disabled = true;
  element('workspace-connection-alert').hidden = true;
  element('workspace-loading').hidden = loaded;
  try {
    await client.request('/api/workspace/config');
    client.retain();
    await loadPage();
    element('workspace-content').hidden = false;
    element('workspace-loading').hidden = true;
  } catch (error) { if (!loaded) element('workspace-content').hidden = true; message(error.message); }
  finally { connecting = false; element('workspace-retry').disabled = false; }
}
// The browser still controls local-network permission. Retry provides a user
// gesture if Chrome requires one, while normal visits connect without a panel.
element('workspace-retry').addEventListener('click', () => { void connect(); });
void connect();
