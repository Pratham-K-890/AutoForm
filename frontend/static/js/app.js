/* ── AutoForm dashboard JS ──────────────────────────────────────────────── */

const API = '';  // same origin

// ── Token helpers ─────────────────────────────────────────────────────────────
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

// ── Auth page ─────────────────────────────────────────────────────────────────
if (document.getElementById('authPage')) {
  const tabs   = document.querySelectorAll('.tab-btn');
  const signupForm = document.getElementById('signupForm');
  const loginForm  = document.getElementById('loginForm');
  const errEl  = document.getElementById('authError');

  tabs.forEach(t => t.addEventListener('click', () => {
    tabs.forEach(x => x.classList.remove('active'));
    t.classList.add('active');
    signupForm.style.display = t.dataset.tab === 'signup' ? 'block' : 'none';
    loginForm.style.display  = t.dataset.tab === 'login'  ? 'block' : 'none';
    errEl.style.display = 'none';
  }));

  async function doAuth(endpoint, email, pass) {
    errEl.style.display = 'none';
    const res = await apiFetch(`/api/auth/${endpoint}`, {
      method: 'POST',
      body: JSON.stringify({ email, password: pass }),
    });
    if (!res) return;
    const data = await res.json();
    if (!res.ok) { errEl.textContent = data.detail || 'Error'; errEl.style.display = 'block'; return; }
    setToken(data.access_token);
    location.href = '/dashboard.html';
  }

  signupForm.addEventListener('submit', e => {
    e.preventDefault();
    doAuth('signup', e.target.email.value, e.target.password.value);
  });
  loginForm.addEventListener('submit', e => {
    e.preventDefault();
    doAuth('login', e.target.email.value, e.target.password.value);
  });
}

// ── Dashboard page ────────────────────────────────────────────────────────────
if (document.getElementById('dashboardPage')) {
  let activeSubmissionId = null;
  let pollTimer = null;
  let waitingForPersonalInfo = false;

  // ── Init ───────────────────────────────────────────────────────────────────
  async function init() {
    const res = await apiFetch('/api/auth/me');
    if (!res || !res.ok) { clearToken(); location.href = '/'; return; }
    const me = await res.json();
    const emailEl = document.getElementById('userEmail');
    if (emailEl) emailEl.textContent = me.email;
    const sidebarEl = document.getElementById('sidebarEmail');
    if (sidebarEl) sidebarEl.textContent = me.email;

    await loadGoogleStatus();
    await loadSubmissions();

    // Check for OAuth redirect callback
    const params = new URLSearchParams(location.search);
    if (params.get('google') === 'connected') {
      showToast('Google account connected!', 'success');
      history.replaceState({}, '', '/dashboard.html');
    }
  }

  // ── Google account ─────────────────────────────────────────────────────────
  async function loadGoogleStatus() {
    const res = await apiFetch('/api/google/status');
    if (!res) return;
    const data = await res.json();
    const connectBtn = document.getElementById('googleConnectBtn');
    const googleEmail = document.getElementById('googleEmail');
    const googleStatusText = document.getElementById('googleStatusText');
    const disconnectBtn = document.getElementById('googleDisconnectBtn');

    const setupBtn = document.getElementById('googleSetupBtn');
    if (data.connected) {
      connectBtn.style.display = 'none';
      disconnectBtn.style.display = 'inline-flex';
      if (setupBtn) setupBtn.style.display = 'inline-flex';
      googleEmail.textContent = data.email || 'Connected';
      googleStatusText.textContent = 'Google account connected';
      googleStatusText.style.color = '#137333';
    } else {
      connectBtn.style.display = 'inline-flex';
      disconnectBtn.style.display = 'none';
      if (setupBtn) setupBtn.style.display = 'none';
      googleEmail.textContent = '';
      googleStatusText.textContent = 'Not connected – forms will be submitted anonymously';
      googleStatusText.style.color = '#b06000';
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

  document.getElementById('googleSetupBtn').addEventListener('click', async () => {
    const btn = document.getElementById('googleSetupBtn');
    btn.disabled = true;
    btn.textContent = 'Browser opening… sign in then wait';
    showToast('A browser window is opening on your machine. Sign into Google and the window will close automatically.', 'info');
    try {
      const res = await apiFetch('/api/google/setup-session', { method: 'POST' });
      if (res && res.ok) {
        showToast('Browser session set up! Forms requiring Google sign-in will now work.', 'success');
      } else {
        const err = res ? await res.json() : {};
        showToast(err.detail || 'Setup timed out. Please try again.', 'error');
      }
    } catch (e) {
      showToast('Setup failed: ' + e.message, 'error');
    } finally {
      btn.disabled = false;
      btn.textContent = 'Setup Browser Session';
    }
  });

  // ── Submit form ────────────────────────────────────────────────────────────
  document.getElementById('submitFormBtn').addEventListener('click', submitForm);
  document.getElementById('formUrl').addEventListener('keydown', e => {
    if (e.key === 'Enter') submitForm();
  });

  async function submitForm() {
    const url = document.getElementById('formUrl').value.trim();
    if (!url) { showToast('Please enter a Google Forms URL.', 'error'); return; }

    // Disable button to prevent duplicate submissions
    const btn = document.getElementById('submitFormBtn');
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner"></span> Starting…';

    clearProgress();
    showProgress();

    try {
      const res = await apiFetch('/api/forms/submit', {
        method: 'POST',
        body: JSON.stringify({ form_url: url }),
      });
      if (!res || !res.ok) {
        const err = await res.json();
        showProgressError(err.detail || 'Failed to start submission.');
        return;
      }
      const data = await res.json();
      activeSubmissionId = data.submission_id;
      startPolling();
    } finally {
      // Re-enable only after polling starts (or on error)
      btn.disabled = false;
      btn.innerHTML = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></svg> Submit';
    }
  }

  // ── Polling ────────────────────────────────────────────────────────────────
  const STATUS_STEPS = {
    pending:              0,
    validating:           1,
    scraping:             2,
    personal_info_needed: 3,
    llm_voting:           4,
    filling:              5,
    completed:            6,
    failed:               6,
  };
  const STEP_LABELS = ['Pending','Validating','Scraping','Personal Info','LLM Voting','Filling','Done'];
  const STATUS_MSGS = {
    pending:              'Queued for processing…',
    validating:           'Checking if the form is open and accessible…',
    scraping:             'Reading form questions with Playwright…',
    personal_info_needed: 'Waiting for your personal details…',
    llm_voting:           'Three AI models are voting on your answers…',
    filling:              'Playwright is filling the form…',
    completed:            'Form submitted successfully!',
    failed:               'Submission failed.',
  };

  function startPolling() {
    if (pollTimer) clearInterval(pollTimer);
    pollTimer = setInterval(pollStatus, 2000);
    pollStatus();
  }
  function stopPolling() { if (pollTimer) { clearInterval(pollTimer); pollTimer = null; } }

  async function pollStatus() {
    if (!activeSubmissionId) return;
    const res = await apiFetch(`/api/forms/status/${activeSubmissionId}`);
    if (!res || !res.ok) return;
    const data = await res.json();
    updateProgress(data);

    if (data.status === 'personal_info_needed' && !waitingForPersonalInfo) {
      waitingForPersonalInfo = true;
      showPersonalInfoModal(data.personal_info_questions || []);
    }

    if (data.status === 'completed' || data.status === 'failed') {
      stopPolling();
      waitingForPersonalInfo = false;
      await loadSubmissions();
      if (data.status === 'completed') {
        showResults(data.results || {});
      }
    }
  }

  // ── Progress UI ────────────────────────────────────────────────────────────
  function showProgress() {
    document.getElementById('progressSection').style.display = 'block';
    renderSteps(0);
  }
  function clearProgress() {
    document.getElementById('progressSection').style.display = 'none';
    document.getElementById('progressError').style.display = 'none';
    document.getElementById('progressWarning').style.display = 'none';
    document.getElementById('resultsSection').style.display = 'none';
  }

  function renderSteps(currentStep, isError = false) {
    const container = document.getElementById('progressSteps');
    container.innerHTML = STEP_LABELS.map((label, i) => {
      let cls = '';
      if (isError && i === currentStep) cls = 'error';
      else if (i < currentStep) cls = 'done';
      else if (i === currentStep) cls = 'active';
      const icon = cls === 'done' ? '✓' : cls === 'error' ? '✗' : (i + 1);
      return `<div class="step ${cls}">
        <div class="step-dot">${icon}</div>
        <div class="step-label">${label}</div>
      </div>`;
    }).join('');
  }

  function updateProgress(data) {
    const step = STATUS_STEPS[data.status] ?? 0;
    const isError = data.status === 'failed';
    renderSteps(step, isError);

    document.getElementById('progressMessage').textContent =
      STATUS_MSGS[data.status] || data.status;

    const errEl = document.getElementById('progressError');
    if (data.error_message) {
      errEl.textContent = '⚠ ' + data.error_message;
      errEl.style.display = 'block';
    } else {
      errEl.style.display = 'none';
    }

    const warnEl = document.getElementById('progressWarning');
    if (data.has_file_upload) {
      warnEl.textContent = '📎 This form has file upload questions – those have been skipped. Please upload files manually.';
      warnEl.style.display = 'block';
    }
  }

  function showProgressError(msg) {
    document.getElementById('progressSection').style.display = 'block';
    document.getElementById('progressError').textContent = '⚠ ' + msg;
    document.getElementById('progressError').style.display = 'block';
  }

  // ── Personal-info modal ────────────────────────────────────────────────────
  function showPersonalInfoModal(questions) {
    const modal = document.getElementById('personalInfoModal');
    const fields = document.getElementById('personalInfoFields');
    fields.innerHTML = questions.map(q => `
      <div class="form-group">
        <label>${q.text}${q.required ? ' *' : ''}</label>
        <input class="input" data-id="${q.id}" placeholder="Your answer" ${q.required ? 'required' : ''}>
      </div>
    `).join('');
    modal.style.display = 'flex';
  }

  document.getElementById('personalInfoSubmitBtn').addEventListener('click', async () => {
    const modal = document.getElementById('personalInfoModal');
    const inputs = modal.querySelectorAll('input[data-id]');
    const answers = {};

    // Validate required fields before sending
    let missing = false;
    for (const inp of inputs) {
      if (inp.required && !inp.value.trim()) {
        inp.style.borderColor = 'var(--red)';
        missing = true;
      } else {
        inp.style.borderColor = '';
        answers[inp.dataset.id] = inp.value.trim();
      }
    }
    if (missing) { showToast('Please fill in all required fields.', 'error'); return; }

    const res = await apiFetch(`/api/forms/personal-info/${activeSubmissionId}`, {
      method: 'POST',
      body: JSON.stringify({ answers }),
    });
    if (res && res.ok) {
      modal.style.display = 'none';
      showToast('Personal info submitted!', 'success');
    } else {
      showToast('Failed to submit personal info.', 'error');
    }
  });

  // ── Results ────────────────────────────────────────────────────────────────
  function showResults(results) {
    const section = document.getElementById('resultsSection');
    const grid = document.getElementById('resultsGrid');
    const entries = Object.entries(results);
    if (!entries.length) return;

    grid.innerHTML = entries.map(([id, r]) => {
      const pct = Math.round((r.confidence || 0) * 100);
      const answer = Array.isArray(r.answer) ? r.answer.join(', ') : r.answer;
      return `
        <div class="result-item">
          <div class="result-question">${escHtml(r.question || id)}</div>
          <div class="result-answer">${escHtml(String(answer))}</div>
          <div class="confidence-bar">
            <div class="confidence-fill" style="width:${pct}%"></div>
          </div>
          <div class="result-meta">
            Confidence: ${pct}% · Models agreed: ${r.models_agreed ?? '?'}
            ${r.tiebreak ? ' · (tiebreak)' : ''}
          </div>
        </div>`;
    }).join('');
    section.style.display = 'block';
  }

  // ── Submissions list ───────────────────────────────────────────────────────
  async function loadSubmissions() {
    const res = await apiFetch('/api/forms/submissions');
    if (!res || !res.ok) return;
    const rows = await res.json();
    const tbody = document.getElementById('submissionsBody');
    if (!rows.length) {
      tbody.innerHTML = '<tr><td colspan="5" style="text-align:center;color:var(--muted);padding:28px">No submissions yet</td></tr>';
      return;
    }
    tbody.innerHTML = rows.map(r => `
      <tr>
        <td><a class="url-cell" href="${escHtml(r.form_url)}" target="_blank" title="${escHtml(r.form_url)}">${escHtml(r.form_url)}</a></td>
        <td><span class="badge badge-${r.status}">${r.status.replace(/_/g,' ')}</span></td>
        <td>${r.form_type || '—'}</td>
        <td>${new Date(r.created_at).toLocaleString()}</td>
        <td>
          <button class="btn btn-outline btn-sm" onclick="viewSubmission(${r.id})">View</button>
          <button class="btn btn-danger btn-sm" onclick="deleteSubmission(${r.id})" style="margin-left:6px">Delete</button>
        </td>
      </tr>
    `).join('');
  }

  window.deleteSubmission = async function(id) {
    if (!confirm('Delete this submission?')) return;
    await apiFetch(`/api/forms/submissions/${id}`, { method: 'DELETE' });
    await loadSubmissions();
  };

  window.viewSubmission = async function(id) {
    // Stop any live poll for the current submission before switching
    stopPolling();
    waitingForPersonalInfo = false;

    const res = await apiFetch(`/api/forms/status/${id}`);
    if (!res || !res.ok) return;
    const data = await res.json();
    activeSubmissionId = id;
    clearProgress();
    showProgress();
    updateProgress(data);
    if (data.results) showResults(data.results);

    // If this submission is still in progress, resume polling it
    if (data.status !== 'completed' && data.status !== 'failed') {
      startPolling();
    }

    document.getElementById('dashboardPage').scrollTop = 0;
  };

  // ── Logout ─────────────────────────────────────────────────────────────────
  document.getElementById('logoutBtn').addEventListener('click', () => {
    clearToken();
    location.href = '/';
  });

  // ── Utilities ──────────────────────────────────────────────────────────────
  function escHtml(s) {
    return String(s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function showToast(msg, type = 'info') {
    const toast = document.getElementById('toast');
    toast.textContent = msg;
    toast.className = `toast toast-${type} show`;
    setTimeout(() => toast.classList.remove('show'), 3500);
  }

  // ── Kick off ───────────────────────────────────────────────────────────────
  init();
}
