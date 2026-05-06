// ── State ──────────────────────────────────────────────────────────────────
let selectedBrowsers = ['chromium'];
let currentReportFile = null;
let eventSource = null;
let testCount = 0;
let completedCount = 0;

// ── Init ───────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  loadRuns();
});

// ── Tab switching ──────────────────────────────────────────────────────────
document.querySelectorAll('.tab').forEach(tab => {
  tab.addEventListener('click', () => {
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    tab.classList.add('active');
    const isSpec = tab.dataset.tab === 'spec';
    document.getElementById('tab-url').style.display  = isSpec ? 'none'  : 'block';
    document.getElementById('tab-spec').style.display = isSpec ? 'block' : 'none';
    const browserCard = document.getElementById('browser-card');
    if (browserCard) browserCard.style.display = isSpec ? 'none' : '';
  });
});

// ── Browser selection ──────────────────────────────────────────────────────
document.querySelectorAll('.browser-option').forEach(opt => {
  opt.addEventListener('click', () => {
    const browser = opt.dataset.browser;
    const isSelected = opt.classList.contains('selected');
    // prevent deselecting the last browser
    if (isSelected && selectedBrowsers.length === 1) return;
    opt.classList.toggle('selected');
    if (!isSelected) {
      if (!selectedBrowsers.includes(browser)) selectedBrowsers.push(browser);
    } else {
      selectedBrowsers = selectedBrowsers.filter(b => b !== browser);
    }
    updateBrowserCount();
  });
});

function updateBrowserCount() {
  const el = document.getElementById('browser-count');
  if (el) el.textContent = selectedBrowsers.length;
}

function selectAllBrowsers() {
  document.querySelectorAll('.browser-option').forEach(opt => {
    opt.classList.add('selected');
    const b = opt.dataset.browser;
    if (!selectedBrowsers.includes(b)) selectedBrowsers.push(b);
  });
  updateBrowserCount();
}

function clearBrowsers() {
  // keep at least one (first selected stays)
  let kept = false;
  document.querySelectorAll('.browser-option').forEach(opt => {
    if (!kept && opt.classList.contains('selected')) { kept = true; return; }
    opt.classList.remove('selected');
    selectedBrowsers = selectedBrowsers.filter(b => b !== opt.dataset.browser);
  });
  // if nothing was selected at all, re-select chromium
  if (selectedBrowsers.length === 0) {
    const first = document.querySelector('.browser-option');
    if (first) { first.classList.add('selected'); selectedBrowsers = [first.dataset.browser]; }
  }
  updateBrowserCount();
}

// ── File drop zone ─────────────────────────────────────────────────────────
const dropZone = document.getElementById('drop-zone');
const fileInput = document.getElementById('spec-file');

dropZone.addEventListener('click', () => fileInput.click());
dropZone.addEventListener('dragover', e => { e.preventDefault(); dropZone.classList.add('dragover'); });
dropZone.addEventListener('dragleave', () => dropZone.classList.remove('dragover'));
dropZone.addEventListener('drop', e => {
  e.preventDefault();
  dropZone.classList.remove('dragover');
  if (e.dataTransfer.files[0]) {
    fileInput.files = e.dataTransfer.files;
    document.getElementById('spec-filename').textContent = e.dataTransfer.files[0].name;
  }
});
fileInput.addEventListener('change', () => {
  if (fileInput.files[0])
    document.getElementById('spec-filename').textContent = fileInput.files[0].name;
});

// ── Terminal ───────────────────────────────────────────────────────────────
function appendToTerminal(message, type = 'info') {
  const terminal = document.getElementById('terminal');
  const line = document.createElement('div');
  line.className = `log-${type}`;
  const time = new Date().toLocaleTimeString('fr-FR');
  line.textContent = `[${time}] ${message}`;
  terminal.appendChild(line);
  terminal.scrollTop = terminal.scrollHeight;
}

function clearTerminal() {
  document.getElementById('terminal').innerHTML = '';
}

// ── SSE handler ────────────────────────────────────────────────────────────
function handleSSEMessage(event) {
  const raw = event.data;
  if (!raw || raw === 'ping') return;

  // Try to parse as JSON (structured event)
  let data = null;
  try { data = JSON.parse(raw); } catch { /* plain text */ }

  if (data && typeof data === 'object') {
    // Structured event — live matrix cell update
    if (data.test_id && data.browser && data.status) {
      updateMatrixCell(data.test_id, data.browser, data.status, data.error || '');
      completedCount++;
      updateProgress(data);
    }
    const msg = data.message || data.log ||
      (data.test_id ? `[${data.browser || ''}] ${data.test_id} — ${data.status}` : JSON.stringify(data));
    const st = (data.status || '').toUpperCase();
    const type = st === 'FAILED' ? 'error' : st === 'PASSED' ? 'success' : st === 'PARTIAL' ? 'warning' : 'info';
    appendToTerminal(msg, type);
  } else {
    // Plain text from print()
    if (raw !== '__DONE__') appendToTerminal(raw, 'info');
  }
}

function updateMatrixCell(testId, browser, status, error) {
  const cell = document.querySelector(`[data-test="${testId}"][data-browser="${browser}"]`);
  if (!cell) return;
  const s = status.toLowerCase();
  cell.className = `status-${s}`;
  cell.textContent = status.substring(0, 4).toUpperCase();
  if (error) cell.title = error;
}

function updateProgress(data) {
  if (testCount > 0) {
    const total = testCount * Math.max(selectedBrowsers.length, 1);
    const pct = Math.min(100, Math.round((completedCount / total) * 100));
    document.getElementById('progress-bar').style.width = `${pct}%`;
    document.getElementById('progress-pct').textContent = `${pct}%`;
    document.getElementById('progress-label').textContent =
      `${data.test_id || 'Test en cours'} — ${data.browser || ''}`;
  }
}

// ── Start agent ────────────────────────────────────────────────────────────
async function startAgent() {
  const activeTab = document.querySelector('.tab.active').dataset.tab;
  // In spec mode the browser card is hidden — use chromium silently
  const browsers = activeTab === 'spec' ? ['chromium'] : selectedBrowsers;
  if (browsers.length === 0) {
    alert('Sélectionnez au moins un navigateur.');
    return;
  }
  const btn = document.getElementById('run-btn');
  btn.disabled = true;
  btn.textContent = 'EXÉCUTION EN COURS...';
  document.getElementById('agent-status').textContent = '● RUNNING';
  document.getElementById('agent-status').style.color = '#4ADE80';
  document.getElementById('live-dot').style.display = 'inline-block';
  document.getElementById('progress-container').style.display = 'block';
  document.getElementById('results-card').style.display = 'none';
  document.getElementById('metrics-row').style.display = 'none';
  document.getElementById('download-btn').style.display = 'none';
  clearTerminal();
  completedCount = 0;
  currentReportFile = null;

  // Start SSE
  if (eventSource) eventSource.close();
  eventSource = new EventSource('/api/logs');
  eventSource.onmessage = handleSSEMessage;

  let response;
  try {
    if (activeTab === 'url') {
      const url = document.getElementById('target-url').value.trim();
      if (!url) { alert('Entrez une URL.'); resetUI(); return; }
      const formData = new FormData();
      formData.append('url', url);
      formData.append('browsers', browsers.join(','));
      response = await fetch('/api/run-agent', { method: 'POST', body: formData });
    } else {
      const file = fileInput.files[0];
      const specUrl = document.getElementById('spec-url').value.trim();
      if (!file && !specUrl) { alert('Fournissez un fichier spec ou une URL.'); resetUI(); return; }
      const formData = new FormData();
      if (file) formData.append('spec_file', file);
      formData.append('url', specUrl || '');
      formData.append('browsers', browsers.join(','));
      response = await fetch('/api/run-agent-with-spec', { method: 'POST', body: formData });
    }

    if (!response.ok) {
      const err = await response.json().catch(() => ({ detail: response.statusText }));
      throw new Error(err.detail || `HTTP ${response.status}`);
    }

    const data = await response.json();
    handleResults(data);
  } catch (err) {
    appendToTerminal(`Erreur : ${err.message}`, 'error');
  } finally {
    resetUI();
    loadRuns();
  }
}

// ── Handle results ─────────────────────────────────────────────────────────
function handleResults(data) {
  if (!data) return;

  const matrix = data.results || {};
  const browsers = data.browsers || selectedBrowsers;
  currentReportFile = data.report_file || null;

  const total   = Object.keys(matrix).length;
  const passed  = Object.values(matrix).filter(r => (r.overall || '').toUpperCase() === 'PASSED').length;
  const failed  = Object.values(matrix).filter(r => (r.overall || '').toUpperCase() === 'FAILED').length;
  const partial = Object.values(matrix).filter(r => (r.overall || '').toUpperCase() === 'PARTIAL').length;

  testCount = total;
  document.getElementById('m-total').textContent  = total;
  document.getElementById('m-passed').textContent = passed;
  document.getElementById('m-failed').textContent = failed;
  document.getElementById('m-partial').textContent = partial;
  document.getElementById('metrics-row').style.display = 'grid';

  renderCrossBrowserMatrix(matrix, browsers);

  document.getElementById('results-card').style.display = 'block';
  document.getElementById('results-card').classList.add('animate-in');

  if (currentReportFile) {
    document.getElementById('download-btn').style.display = 'inline-flex';
  }

  const rate = total > 0 ? ((passed / total) * 100).toFixed(1) : '0.0';
  appendToTerminal(
    `Campagne terminée — ${passed}/${total} tests réussis (${rate}%)`,
    'success'
  );
}

// ── Render cross-browser matrix ────────────────────────────────────────────
function renderCrossBrowserMatrix(matrix, browsers) {
  const container = document.getElementById('matrix-container');

  if (Object.keys(matrix).length === 0) {
    container.innerHTML = '<p style="color:var(--neutral);font-size:0.9rem;text-align:center;padding:2rem">Aucun résultat disponible.</p>';
    return;
  }

  let html = `<table class="matrix-table"><thead><tr>
    <th style="width:110px">ID</th>
    <th>Description</th>`;
  browsers.forEach(b => html += `<th>${b.charAt(0).toUpperCase() + b.slice(1)}</th>`);
  html += `<th>Global</th></tr></thead><tbody>`;

  Object.entries(matrix).forEach(([testId, data]) => {
    html += `<tr>
      <td style="font-family:var(--font-mono);font-size:0.75rem;white-space:nowrap">${escHtml(testId)}</td>
      <td style="max-width:260px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis" title="${escHtml(data.description || data.title || '')}">${escHtml(data.title || data.description || testId)}</td>`;

    browsers.forEach(b => {
      const info = (data.browsers || {})[b] || {};
      const s = (info.status || 'SKIPPED').toLowerCase();
      const err = info.error || '';
      html += `<td class="status-${s}"
                   data-test="${escHtml(testId)}"
                   data-browser="${escHtml(b)}"
                   title="${escHtml(err)}"
                   style="font-size:0.72rem;font-weight:700">
                 ${(info.status || 'SKIP').substring(0, 4).toUpperCase()}
               </td>`;
    });

    const overall = (data.overall || 'SKIPPED').toLowerCase();
    const mixed = (() => {
      if (!data.browsers) return '';
      const statuses = Object.values(data.browsers).map(v => v.status || '');
      const hasFail = statuses.some(s => s === 'FAILED');
      const hasPass = statuses.some(s => s === 'PASSED');
      return '';
    })();
    html += `<td class="status-${overall}" style="font-size:0.72rem;font-weight:700">${(data.overall || 'SKIP').substring(0, 4).toUpperCase()}${mixed}</td></tr>`;
  });

  // Totals row
  html += `<tr style="font-weight:700;background:var(--light)">
    <td colspan="2" style="text-align:right;padding-right:1rem;font-size:0.78rem;color:var(--secondary)">TOTAL RÉUSSIS</td>`;
  browsers.forEach(b => {
    const cnt = Object.values(matrix).filter(r => ((r.browsers || {})[b] || {}).status === 'PASSED').length;
    html += `<td class="status-passed" style="font-weight:900">${cnt}</td>`;
  });
  const totalPassed = Object.values(matrix).filter(r => (r.overall || '').toUpperCase() === 'PASSED').length;
  html += `<td class="status-passed" style="font-weight:900">${totalPassed}</td></tr>`;

  html += `</tbody></table>`;
  container.innerHTML = html;
}

// ── Download report ────────────────────────────────────────────────────────
function downloadReport() {
  if (currentReportFile) {
    window.location.href = `/api/runs/${currentReportFile}/export/word`;
  }
}

// ── Load runs history ──────────────────────────────────────────────────────
async function loadRuns() {
  try {
    const res = await fetch('/api/runs');
    if (!res.ok) return;
    const runs = await res.json();
    const el = document.getElementById('runs-list');
    if (!runs.length) {
      el.textContent = 'Aucune exécution antérieure.';
      return;
    }
    el.innerHTML = runs.slice(0, 8).map(r => {
      const status = r.passed === r.total_tests && r.total_tests > 0 ? 'passed'
                   : r.failed > 0 ? 'failed' : 'partial';
      const dt = r.timestamp ? new Date(r.timestamp).toLocaleString('fr-FR', {
        day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit'
      }) : '';
      return `<div class="run-item" onclick="loadRun('${escHtml(r.file)}')">
        <span class="run-item-url" title="${escHtml(r.url || '')}">${escHtml(r.url || r.file)}</span>
        <span class="run-item-meta">
          <span class="badge badge-${status}">${r.passed}/${r.total_tests}</span>
          <span style="margin-left:0.4rem">${dt}</span>
        </span>
      </div>`;
    }).join('');
  } catch { /* ignore */ }
}

async function loadRun(filename) {
  try {
    const res = await fetch(`/api/runs/${filename}`);
    if (!res.ok) return;
    const data = await res.json();
    currentReportFile = filename;
    handleResults(data);
    clearTerminal();
    appendToTerminal(`Rapport chargé : ${filename}`, 'info');
  } catch (err) {
    appendToTerminal(`Erreur chargement : ${err.message}`, 'error');
  }
}

// ── Reset UI ───────────────────────────────────────────────────────────────
function resetUI() {
  const btn = document.getElementById('run-btn');
  btn.disabled = false;
  btn.textContent = 'LANCER LES TESTS';
  document.getElementById('agent-status').textContent = '● IDLE';
  document.getElementById('agent-status').style.color = '';
  document.getElementById('live-dot').style.display = 'none';
  document.getElementById('progress-bar').style.width = '100%';
  document.getElementById('progress-pct').textContent = '100%';
  if (eventSource) { eventSource.close(); eventSource = null; }
}

// ── Utilities ──────────────────────────────────────────────────────────────
function escHtml(str) {
  return String(str || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}
