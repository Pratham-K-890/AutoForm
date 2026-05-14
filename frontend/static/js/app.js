/* ── AutoForm JS ─────────────────────────────────────────────────────────── */

const API = '';

function getToken() { return localStorage.getItem('autoform_token'); }
function setToken(t) { localStorage.setItem('autoform_token', t); }
function clearToken() { localStorage.removeItem('autoform_token'); }

async function apiFetch(path, opts = {}) {
  const token = getToken();
  const headers = { 'Content-Type': 'application/json', ...(opts.headers || {}) };
  if (token) headers['Authorization'] = `Bearer ${token}`;
  const res = await fetch(API + path, { ...opts, headers });
  if (res.status === 401) { clearToken(); location.href = '/'; return null; }
  return res;
}

// ── Dashboard ─────────────────────────────────────────────────────────────────
if (document.getElementById('dashboardPage')) {

  async function init() {
    const res = await apiFetch('/api/auth/me');
    if (!res || !res.ok) { clearToken(); location.href = '/'; return; }
    const me = await res.json();
    const sidebarEl = document.getElementById('sidebarEmail');
    if (sidebarEl) sidebarEl.textContent = me.email;
    await loadGoogleStatus();

    const params = new URLSearchParams(location.search);
    if (params.get('google') === 'connected') {
      showToast('Google account connected!', 'success');
      history.replaceState({}, '', '/dashboard.html');
    }
  }

  async function loadGoogleStatus() {
    const res = await apiFetch('/api/google/status');
    if (!res) return;
    const data = await res.json();
    const connectBtn     = document.getElementById('googleConnectBtn');
    const disconnectBtn  = document.getElementById('googleDisconnectBtn');
    const googleEmail    = document.getElementById('googleEmail');
    const googleStatus   = document.getElementById('googleStatusText');

    if (data.connected) {
      connectBtn.style.display    = 'none';
      disconnectBtn.style.display = 'inline-flex';
      googleEmail.textContent  = data.email || 'Connected';
      googleStatus.textContent = 'Google account connected';
      googleStatus.style.color = 'var(--green)';
    } else {
      connectBtn.style.display    = 'inline-flex';
      disconnectBtn.style.display = 'none';
      googleEmail.textContent  = 'Not connected';
      googleStatus.textContent = 'Optional — connect for signed-in form access';
      googleStatus.style.color = '';
    }
  }

  document.getElementById('googleConnectBtn').addEventListener('click', async () => {
    const res = await apiFetch('/api/google/connect');
    if (!res || !res.ok) { showToast('Google OAuth not configured on the server.', 'error'); return; }
    const data = await res.json();
    location.href = data.auth_url;
  });

  document.getElementById('googleDisconnectBtn').addEventListener('click', async () => {
    await apiFetch('/api/google/disconnect', { method: 'DELETE' });
    await loadGoogleStatus();
    showToast('Google account disconnected.', 'info');
  });

  document.getElementById('logoutBtn').addEventListener('click', () => {
    clearToken(); location.href = '/';
  });

  function showToast(msg, type = 'info') {
    const toast = document.getElementById('toast');
    toast.textContent = msg;
    toast.className = `toast toast-${type} show`;
    setTimeout(() => toast.classList.remove('show'), 3500);
  }

  init();
}
