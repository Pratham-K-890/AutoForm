// popup.js

const API = 'http://localhost:8000';

// ── State ──────────────────────────────────────────────────────────────────────

let token = null;
let userEmail = null;
let scrapedData = null;   // { questions, form_title, form_type }
let analyzeResult = null; // { personal_questions, ai_answers }

// ── DOM refs ───────────────────────────────────────────────────────────────────

const screens = {
  auth:     document.getElementById('screen-auth'),
  notForm:  document.getElementById('screen-not-form'),
  ready:    document.getElementById('screen-ready'),
  loading:  document.getElementById('screen-loading'),
  results:  document.getElementById('screen-results'),
  done:     document.getElementById('screen-done'),
  debug:    document.getElementById('screen-debug'),
};

const $email      = document.getElementById('inp-email');
const $password   = document.getElementById('inp-password');
const $authError  = document.getElementById('auth-error');
const $fillError  = document.getElementById('fill-error');
const $loadingTxt = document.getElementById('loading-text');
const $userEmail  = document.getElementById('user-email');
const $btnLogout  = document.getElementById('btn-logout');

// ── Screen helper ──────────────────────────────────────────────────────────────

function show(name) {
  Object.entries(screens).forEach(([k, el]) => {
    el.style.display = k === name ? '' : 'none';
  });
}

function setHeaderUser(email) {
  $userEmail.textContent = email;
  $userEmail.style.display = email ? '' : 'none';
  $btnLogout.style.display = email ? '' : 'none';
}

// ── Storage helpers ────────────────────────────────────────────────────────────

async function loadToken() {
  return new Promise(resolve => {
    chrome.storage.local.get(['token', 'email'], data => {
      token = data.token || null;
      userEmail = data.email || null;
      resolve(token);
    });
  });
}

async function saveToken(t, email) {
  token = t;
  userEmail = email;
  return new Promise(resolve => chrome.storage.local.set({ token: t, email }, resolve));
}

async function clearToken() {
  token = null;
  userEmail = null;
  return new Promise(resolve => chrome.storage.local.remove(['token', 'email'], resolve));
}

// ── API helpers ────────────────────────────────────────────────────────────────

async function apiPost(path, body) {
  const res = await fetch(`${API}${path}`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify(body),
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
  return data;
}

// ── Tab helpers ────────────────────────────────────────────────────────────────

async function getActiveTab() {
  return new Promise(resolve => {
    chrome.tabs.query({ active: true, currentWindow: true }, tabs => resolve(tabs[0]));
  });
}

function isGoogleForm(url) {
  return url && url.startsWith('https://docs.google.com/forms/');
}

async function sendToContent(tabId, message) {
  return new Promise((resolve, reject) => {
    chrome.tabs.sendMessage(tabId, message, response => {
      if (chrome.runtime.lastError) {
        const msg = chrome.runtime.lastError.message || '';
        if (msg.includes('Receiving end does not exist') || msg.includes('Could not establish')) {
          reject(new Error('Please refresh the Google Form page first, then try again.'));
        } else {
          reject(new Error(msg));
        }
      } else {
        resolve(response);
      }
    });
  });
}

// ── Init ───────────────────────────────────────────────────────────────────────

async function init() {
  await loadToken();

  if (!token) {
    show('auth');
    setHeaderUser(null);
    return;
  }

  setHeaderUser(userEmail);

  const tab = await getActiveTab();
  if (!isGoogleForm(tab?.url)) {
    show('notForm');
    return;
  }

  // Ping content script
  try {
    await sendToContent(tab.id, { action: 'ping' });
    show('ready');
  } catch {
    // Content script not ready yet (page still loading)
    show('ready');
  }
}

// ── Login ──────────────────────────────────────────────────────────────────────

document.getElementById('btn-login').addEventListener('click', async () => {
  $authError.textContent = '';
  const email = $email.value.trim();
  const password = $password.value;

  if (!email || !password) {
    $authError.textContent = 'Please enter email and password.';
    return;
  }

  const btn = document.getElementById('btn-login');
  btn.disabled = true;
  btn.textContent = 'Signing in…';

  try {
    const data = await apiPost('/api/auth/login', { email, password });
    await saveToken(data.access_token, email);
    setHeaderUser(email);
    $password.value = '';
    init();
  } catch (e) {
    $authError.textContent = e.message;
  } finally {
    btn.disabled = false;
    btn.textContent = 'Login';
  }
});

// Allow Enter key on password field
document.getElementById('inp-password').addEventListener('keydown', e => {
  if (e.key === 'Enter') document.getElementById('btn-login').click();
});

// ── Logout ─────────────────────────────────────────────────────────────────────

$btnLogout.addEventListener('click', async () => {
  await clearToken();
  setHeaderUser(null);
  analyzeResult = null;
  scrapedData = null;
  show('auth');
});

// ── Analyze ────────────────────────────────────────────────────────────────────

document.getElementById('btn-analyze').addEventListener('click', async () => {
  const tab = await getActiveTab();
  show('loading');
  $loadingTxt.textContent = 'Reading form questions…';

  try {
    // 1. Scrape questions via content script
    const scraped = await sendToContent(tab.id, { action: 'scrapeQuestions' });
    if (!scraped.success) throw new Error(scraped.error || 'Failed to read form');
    if (!scraped.questions?.length) throw new Error('No questions found on this page');

    scrapedData = scraped;
    $loadingTxt.textContent = `Sending ${scraped.questions.length} question(s) to AI…`;

    // 2. Send to backend
    const result = await apiPost('/api/extension/analyze', {
      questions: scraped.questions,
      form_url: tab.url,
      form_title: scraped.form_title || '',
      form_type: scraped.form_type || 'general',
    });

    analyzeResult = result;
    renderResults(scraped, result);
    show('results');
  } catch (e) {
    show('ready');
    // Show error briefly in ready screen
    const hint = document.querySelector('#screen-ready .hint');
    hint.style.color = '#f87171';
    hint.textContent = e.message;
    setTimeout(() => {
      hint.style.color = '';
      hint.innerHTML = '<span class="dot"></span> Google Form detected';
    }, 4000);
  }
});

// ── Render results ─────────────────────────────────────────────────────────────

function renderResults(scraped, result) {
  const { personal_questions = [], ai_answers = {} } = result;

  // Personal info fields
  const $personalSection = document.getElementById('personal-section');
  const $personalFields = document.getElementById('personal-fields');
  $personalFields.innerHTML = '';

  if (personal_questions.length) {
    personal_questions.forEach(q => {
      const div = document.createElement('div');
      div.className = 'field-group';
      div.innerHTML = `
        <label class="field-label">
          ${q.text}
          <span class="field-badge">${q.personal_category || 'personal'}</span>
        </label>
        <input type="text" id="pi_${q.id}" placeholder="Your answer…" />
      `;
      $personalFields.appendChild(div);
    });
    $personalSection.style.display = '';
  } else {
    $personalSection.style.display = 'none';
  }

  // AI answers preview
  const $aiList = document.getElementById('ai-list');
  const $aiCount = document.getElementById('ai-count');
  $aiList.innerHTML = '';
  const aiEntries = Object.entries(ai_answers);
  $aiCount.textContent = aiEntries.length;

  aiEntries.forEach(([qId, answer]) => {
    const question = scraped.questions.find(q => q.id === qId);
    const div = document.createElement('div');
    div.className = 'ai-item';
    div.innerHTML = `
      <div class="q-text">${question?.text || qId}</div>
      <div class="a-text">${Array.isArray(answer) ? answer.join(', ') : answer}</div>
    `;
    $aiList.appendChild(div);
  });

  $fillError.textContent = '';
}

// ── Fill ───────────────────────────────────────────────────────────────────────

document.getElementById('btn-fill').addEventListener('click', async () => {
  const tab = await getActiveTab();
  $fillError.textContent = '';

  // Collect personal info answers
  const answers = { ...analyzeResult.ai_answers };

  (analyzeResult.personal_questions || []).forEach(q => {
    const input = document.getElementById(`pi_${q.id}`);
    if (input?.value.trim()) answers[q.id] = input.value.trim();
  });

  // Build option indices: for MCQ/checkbox/dropdown, find which index in the
  // scraped options list matches the AI answer — content script clicks by index,
  // no fragile text matching needed.
  const answerIndices = {};
  (scrapedData.questions || []).forEach(q => {
    if (!q.options?.length) return;
    if (!['mcq', 'dropdown', 'checkbox'].includes(q.type)) return;
    const answer = answers[q.id];
    if (answer === undefined) return;

    if (q.type === 'checkbox') {
      const arr = Array.isArray(answer) ? answer : [String(answer)];
      answerIndices[q.id] = arr.map(a =>
        q.options.findIndex(o => o.toLowerCase().trim() === String(a).toLowerCase().trim())
      ).filter(i => i >= 0);
    } else {
      const idx = q.options.findIndex(
        o => o.toLowerCase().trim() === String(answer).toLowerCase().trim()
      );
      if (idx >= 0) answerIndices[q.id] = idx;
    }
  });

  try {
    const resp = await sendToContent(tab.id, { action: 'fillAnswers', answers, answerIndices });
    if (!resp.success) throw new Error(resp.error || 'Fill failed');
    show('done');
  } catch (e) {
    $fillError.textContent = e.message;
  }
});

// ── Debug ──────────────────────────────────────────────────────────────────────

document.getElementById('btn-debug').addEventListener('click', async () => {
  const tab = await getActiveTab();
  show('debug');
  const $out = document.getElementById('debug-output');
  $out.innerHTML = '<div style="color:#64748b">Running diagnostics…</div>';

  try {
    const resp = await sendToContent(tab.id, { action: 'debug' });
    if (!resp.success) throw new Error(resp.error || 'Failed');
    $out.innerHTML = renderDebugReport(resp.report);
  } catch (e) {
    $out.innerHTML = `<div class="debug-error">Error: ${e.message}</div>`;
  }
});

document.getElementById('btn-debug-back').addEventListener('click', () => show('ready'));

function renderDebugReport(r) {
  const ok  = v => `<span class="dbg-ok">${v}</span>`;
  const bad = v => `<span class="dbg-bad">${v}</span>`;
  const dim = v => `<span class="dbg-dim">${v}</span>`;

  let html = '';

  // Form title
  html += `<div class="dbg-row"><b>Form title</b> ${r.form_title || dim('(none)')}</div>`;
  html += `<div class="dbg-row"><b>Total blocks found</b> ${r.total_blocks > 0 ? ok(r.total_blocks) : bad(0)}</div>`;
  html += `<div class="dbg-row"><b>Winning selector</b> ${r.winning_container ? ok(r.winning_container) : bad('none matched')}</div>`;

  // Container selector results
  html += '<div class="dbg-section">Container selectors</div>';
  r.container_selectors.forEach(s => {
    html += `<div class="dbg-row dbg-indent">
      ${s.count > 0 ? ok('✓') : bad('✗')} <code>${s.selector}</code> → ${s.count} match(es)
    </div>`;
  });

  // Title selector results
  if (r.title_selectors.length) {
    html += '<div class="dbg-section">Title selectors (on block 0)</div>';
    r.title_selectors.forEach(s => {
      html += `<div class="dbg-row dbg-indent">
        ${s.found ? ok('✓') : bad('✗')} <code>${s.selector}</code>
        ${s.text ? ` → ${dim('"' + s.text + '"')}` : ''}
      </div>`;
    });
  }

  // Sample questions
  if (r.sample_questions.length) {
    html += '<div class="dbg-section">First questions detected</div>';
    r.sample_questions.forEach(q => {
      html += `<div class="dbg-question">
        <div class="dbg-q-text">#${q.index} <b>${q.text || dim('(no text)')} </b>${dim(q.type)}</div>
        ${q.options.length
          ? `<div class="dbg-q-opts">${ok('Options:')} ${q.options.map(o => `<code>${o}</code>`).join(' ')}</div>`
          : (q.has_radio || q.has_checkbox ? `<div class="dbg-q-opts">${bad('Options: empty — selector fix needed')}</div>` : '')}
        <div class="dbg-q-dom">
          ${q.has_text_input ? ok('text-input') + ' ' : ''}
          ${q.has_textarea   ? ok('textarea')   + ' ' : ''}
          ${q.has_radio      ? ok('radio')       + ' ' : ''}
          ${q.has_checkbox   ? ok('checkbox')    + ' ' : ''}
        </div>
      </div>`;
    });
  }

  // Radio HTML inspection
  if (r.radio_debug) {
    html += '<div class="dbg-section">First radio button — attributes</div>';
    const a = r.radio_debug.outer_attrs || {};
    html += `<div class="dbg-row dbg-indent">aria-label: ${a.aria_label ? ok('"' + a.aria_label + '"') : bad('(none)')}</div>`;
    html += `<div class="dbg-row dbg-indent">data-value: ${a.data_value ? ok('"' + a.data_value + '"') : bad('(none)')}</div>`;
    html += `<div class="dbg-row dbg-indent">aria-checked: ${dim(a.aria_checked || '?')}</div>`;
    if (r.radio_debug.inner_html) {
      html += '<div class="dbg-section">First radio button — inner HTML</div>';
      html += `<div class="dbg-row" style="word-break:break-all;font-size:10px;color:#94a3b8">${
        r.radio_debug.inner_html.replace(/</g, '&lt;').replace(/>/g, '&gt;')
      }</div>`;
    }
  }

  return html;
}

// ── Back / reset ───────────────────────────────────────────────────────────────

document.getElementById('btn-back').addEventListener('click', () => show('ready'));

document.getElementById('btn-fill-another').addEventListener('click', () => {
  analyzeResult = null;
  scrapedData = null;
  show('ready');
});

// ── Boot ───────────────────────────────────────────────────────────────────────

init();
