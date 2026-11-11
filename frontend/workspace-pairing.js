// Delegated handlers survive the local configuration form's HTMX replacement.
document.addEventListener('click', async (event) => {
  const action = event.target?.closest?.('#workspace-pair, #workspace-revoke, #workspace-copy');
  if (!action) return;
  const status = document.getElementById('workspace-pair-status');
  const code = document.getElementById('workspace-pair-code');
  action.disabled = true;
  try {
    if (action.id === 'workspace-copy') {
      if (!code.value) throw new Error('Create a pairing code first.');
      await navigator.clipboard.writeText(code.value);
      status.textContent = 'Code copied. Paste it only into your configured hosted workspace.';
      return;
    }
    code.value = '';
    const response = await fetch('/api/workspace/pair', {
      method: action.id === 'workspace-pair' ? 'POST' : 'DELETE',
      headers: { 'Content-Type': 'application/json' }, body: '{}',
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.detail || 'Pairing action failed.');
    code.value = result.token || '';
    status.textContent = result.token ? 'Pairing code ready. Copy it into the hosted workspace; valid for two hours.' : 'Hosted recruiter access revoked.';
  } catch (error) { status.textContent = error.message; }
  finally { action.disabled = false; }
});
