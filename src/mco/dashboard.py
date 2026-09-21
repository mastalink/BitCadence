"""
Control-plane dashboard served by the gateway at /dashboard.

Single static HTML page, no build step - a System-Settings-style control
panel with full CLI parity:

- Lock screen: nothing renders until a bearer token authenticates.
- Operations: approval queue (approve/reject), job board (+retry), audit viewer.
- Agents: register (token shown once), reset token, edit role/scopes/status,
  delete - the `mco register` surface, point-and-click.
- Model Connections: add/edit/delete/test named connections to LLM providers
  (Anthropic, OpenAI, Gemini, or a custom OpenAI-compatible endpoint) for
  custom workers that need one. Not a chat gateway - BitCadence orchestrates
  agents, it doesn't call a model on their behalf. API keys are write-only,
  mirroring every other secret in the Control Panel.
- Workflows: paste YAML, submit a governed DAG (`mco workflow` parity).
- Settings: server-driven groups (governance, memory, edition, security,
  notifications) rendered from /api/settings metadata - the whitelist on the
  server is the single source of truth. Plus the edition matrix and
  connector health/sync.

Auth model: the operator pastes a token once (kept in localStorage). Every
panel degrades by scope - a jobs:read token sees operations; agents:manage
unlocks the agent panel; settings require admin.
"""

DASHBOARD_HTML = r'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>BitCadence Control Plane</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  body { font-family: -apple-system, "Segoe UI", Roboto, sans-serif; background: #0d1117; color: #e6edf3; margin: 0; }
  h1 { font-size: 1.15rem; margin: 0; }
  h2 { font-size: .95rem; margin: 1.4rem 0 .5rem; color: #8b949e; text-transform: uppercase; letter-spacing: .05em; }
  table { width: 100%; border-collapse: collapse; font-size: .85rem; }
  th, td { text-align: left; padding: .4rem .6rem; border-bottom: 1px solid #21262d; vertical-align: top; }
  th { color: #8b949e; font-weight: 600; }
  .badge { padding: .1rem .5rem; border-radius: 999px; font-size: .75rem; font-weight: 600; white-space: nowrap; }
  .s-pending { background:#1f3d2b; color:#3fb950; } .s-waiting { background:#2d2a1f; color:#d29922; }
  .s-needs_approval { background:#3d2d1f; color:#f0883e; } .s-leased,.s-in_progress { background:#1f2d3d; color:#58a6ff; }
  .s-completed { background:#1f3d2b; color:#3fb950; } .s-failed,.s-rejected { background:#3d1f1f; color:#f85149; }
  .s-online { background:#1f3d2b; color:#3fb950; } .s-offline { background:#3d1f1f; color:#f85149; }
  .s-disabled { background:#2d2a1f; color:#d29922; }
  button { background:#21262d; color:#e6edf3; border:1px solid #30363d; border-radius:6px; padding:.3rem .8rem; cursor:pointer; font-size:.8rem; }
  button:hover { background:#30363d; }
  button.ok { border-color:#238636; color:#3fb950; } button.no { border-color:#da3633; color:#f85149; }
  button.primary { background:#238636; border-color:#2ea043; color:#fff; }
  button.primary:hover { background:#2ea043; }
  input, select, textarea { background:#0d1117; border:1px solid #30363d; border-radius:6px; color:#e6edf3; padding:.4rem .6rem; font-size:.85rem; }
  input:focus, select:focus, textarea:focus { border-color:#58a6ff; outline:none; }
  .muted { color:#8b949e; } pre { white-space:pre-wrap; margin:0; font-size:.78rem; }
  .err { color:#f85149; } .good { color:#3fb950; }

  /* lock screen */
  #lock { position:fixed; inset:0; background:#0d1117; display:flex; align-items:center; justify-content:center; z-index:50; }
  #lock .card { background:#161b22; border:1px solid #30363d; border-radius:12px; padding:2rem 2.4rem; width:26rem; text-align:center; }
  #lock input { width:100%; margin:.9rem 0 .6rem; text-align:center; }
  #lock .logo { font-size:1.6rem; margin-bottom:.2rem; }

  /* shell */
  #shell { display:none; min-height:100vh; }
  #shell.on { display:flex; }
  aside { width:13.5rem; background:#161b22; border-right:1px solid #21262d; padding:1rem .7rem; flex-shrink:0; }
  aside .brand { font-weight:700; padding:.3rem .6rem 1rem; font-size:1rem; }
  aside button { display:block; width:100%; text-align:left; background:none; border:none; padding:.55rem .8rem; border-radius:8px; font-size:.9rem; color:#e6edf3; margin-bottom:.15rem; }
  aside button:hover { background:#21262d; }
  aside button.active { background:#1f2d3d; color:#58a6ff; font-weight:600; }
  main { flex:1; padding:1.3rem 1.8rem; max-width:72rem; }
  .topbar { display:flex; justify-content:space-between; align-items:center; margin-bottom:.4rem; }
  .view { display:none; } .view.on { display:block; }

  /* settings cards */
  .card { background:#161b22; border:1px solid #30363d; border-radius:10px; padding:1rem 1.2rem; margin-bottom:1rem; }
  .card h3 { margin:.1rem 0 .8rem; font-size:.95rem; }
  .row { display:flex; align-items:center; justify-content:space-between; gap:1rem; padding:.45rem 0; border-bottom:1px solid #21262d; }
  .row:last-of-type { border-bottom:none; }
  .row label.lbl { flex:1; font-size:.85rem; }
  .row .ctl input[type=text], .row .ctl input[type=password] { width:17rem; }
  .danger { border-color:#da3633; }
  .danger h3 { color:#f85149; }

  /* toggle */
  .switch { position:relative; display:inline-block; width:2.6rem; height:1.45rem; }
  .switch input { opacity:0; width:0; height:0; }
  .slider { position:absolute; cursor:pointer; inset:0; background:#30363d; border-radius:999px; transition:.15s; }
  .slider:before { content:""; position:absolute; height:1.1rem; width:1.1rem; left:.18rem; top:.18rem; background:#e6edf3; border-radius:50%; transition:.15s; }
  .switch input:checked + .slider { background:#238636; }
  .switch input:checked + .slider:before { transform:translateX(1.15rem); }

  /* modal */
  #modal-bg { position:fixed; inset:0; background:rgba(0,0,0,.6); display:none; z-index:60; align-items:center; justify-content:center; }
  #modal-bg.on { display:flex; }
  #modal { background:#161b22; border:1px solid #30363d; border-radius:12px; padding:1.4rem 1.6rem; width:34rem; max-height:85vh; overflow:auto; }
  #modal h3 { margin-top:0; }
  .token-box { background:#0d1117; border:1px dashed #3fb950; border-radius:8px; padding:.8rem; font-family:monospace; font-size:.85rem; word-break:break-all; margin:.8rem 0; }
  .scopes-grid { display:grid; grid-template-columns:repeat(2,1fr); gap:.25rem .8rem; margin:.5rem 0; font-size:.85rem; }
  .formgrid { display:grid; grid-template-columns:8rem 1fr; gap:.6rem .8rem; align-items:center; margin:.6rem 0 1rem; }
  textarea#wf-yaml { width:100%; min-height:16rem; font-family:monospace; }
  @media (max-width: 760px) { #shell { display:block; } aside { width:auto; display:flex; overflow-x:auto; } aside .brand { display:none; } main { padding:1rem; } .row .ctl input[type=text], .row .ctl input[type=password] { width:10rem; } }
</style>
</head>
<body>

<div id="lock">
  <div class="card">
    <div class="logo">&#129345;</div>
    <h1>BitCadence Control Plane</h1>
    <p class="muted">Paste an agent bearer token to unlock.<br>Approver/admin tokens see everything; scoped tokens see what they may.</p>
    <input id="lock-token" type="password" placeholder="mco_tok_..." onkeydown="if(event.key==='Enter')unlock()">
    <button class="primary" style="width:100%" onclick="unlock()">Unlock</button>
    <p id="lock-msg" class="err" style="min-height:1.1em"></p>
  </div>
</div>

<div id="shell">
  <aside>
    <div class="brand">&#129345; BitCadence</div>
    <button id="nav-ops" class="active" onclick="nav('ops')">Operations</button>
    <button id="nav-agents" onclick="nav('agents')">Agents &amp; Tokens</button>
    <button id="nav-models" onclick="nav('models')">Model Connections</button>
    <button id="nav-workflows" onclick="nav('workflows')">Workflows</button>
    <button id="nav-settings" onclick="nav('settings')">Settings</button>
    <div style="margin-top:1.2rem; padding:0 .8rem">
      <span id="conn" class="muted" style="font-size:.75rem"></span><br>
      <button style="margin-top:.6rem" onclick="lockUp()">Lock</button>
    </div>
  </aside>

  <main>
    <!-- ── Operations ─────────────────────────────────────────── -->
    <section id="view-ops" class="view on">
      <div class="topbar"><h1>Operations</h1></div>

      <!-- Autonomy Status Bar -->
      <div id="autonomy-banner" class="card" style="margin-bottom:1rem; display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:.8rem;">
        <div>
          <span style="font-weight:600; margin-right:.6rem">Autonomy Engine:</span>
          <span id="autonomy-badge" class="badge s-waiting">-</span>
          <span id="autonomy-desc" class="muted" style="margin-left:.6rem; font-size:.82rem"></span>
        </div>
        <div style="display:flex; gap:.5rem; align-items:center">
          <button id="btn-toggle-pause" onclick="toggleAutonomyPause()">Pause</button>
          <button id="btn-tick-now" onclick="triggerManualTick()">Tick Now</button>
          <button class="primary" onclick="openLiveLook()">Live Look</button>
        </div>
      </div>

      <!-- Bulk Actions Bar -->
      <div id="bulk-bar" class="card" style="display:none; padding:.6rem 1rem; margin-bottom:.8rem; background:#1f2d3d; border-color:#58a6ff; justify-content:space-between; align-items:center;">
        <div>
          <b id="bulk-count" style="color:#58a6ff">0</b> <span style="font-size:.85rem">jobs selected</span>
        </div>
        <div style="display:flex; gap:.4rem; align-items:center; flex-wrap:wrap">
          <button class="ok" onclick="bulkAction('approve')">Approve</button>
          <button class="no" onclick="bulkAction('reject')">Reject</button>
          <button onclick="bulkAction('retry')">Retry</button>
          <button class="no" onclick="bulkAction('cancel')">Cancel</button>
          <button onclick="bulkAction('archive')">Archive</button>
          <button onclick="bulkAction('reassign')">Reassign</button>
          <button onclick="clearSelection()">Clear</button>
        </div>
      </div>

      <h2>Approval Queue</h2>
      <table><thead><tr><th style="width:2rem"><input type="checkbox" id="chk-all-approvals" onchange="toggleSelectAllApprovals(this.checked)"></th><th>Job</th><th>Title</th><th>Target</th><th>From</th><th>Decide</th></tr></thead>
      <tbody id="approvals"><tr><td colspan="6" class="muted">-</td></tr></tbody></table>

      <h2>Job Board</h2>
      <table><thead><tr>
        <th style="width:2rem"><input type="checkbox" id="chk-all-jobs" onchange="toggleSelectAllJobs(this.checked)"></th>
        <th onclick="toggleSort('id')" style="cursor:pointer">Job <span id="sort-ind-id"></span></th>
        <th onclick="toggleSort('title')" style="cursor:pointer">Title <span id="sort-ind-title"></span></th>
        <th onclick="toggleSort('status')" style="cursor:pointer">Status <span id="sort-ind-status"></span></th>
        <th onclick="toggleSort('priority')" style="cursor:pointer">Priority <span id="sort-ind-priority"></span></th>
        <th onclick="toggleSort('target')" style="cursor:pointer">Target <span id="sort-ind-target"></span></th>
        <th onclick="toggleSort('leased_by')" style="cursor:pointer">Leased By <span id="sort-ind-leased_by"></span></th>
        <th onclick="toggleSort('created')" style="cursor:pointer">Created <span id="sort-ind-created"></span></th>
        <th></th>
      </tr></thead>
      <tbody id="jobs"><tr><td colspan="9" class="muted">-</td></tr></tbody></table>

      <div id="audit-panel" class="card" style="display:none; margin-top:1rem">
        <h3>Audit Trail: <span id="audit-job"></span></h3>
        <pre id="audit-body"></pre>
      </div>
    </section>

    <!-- ── Agents ─────────────────────────────────────────────── -->
    <section id="view-agents" class="view">
      <div class="topbar"><h1>Agents &amp; Tokens</h1>
        <button class="primary" onclick="openRegister()">+ Register agent</button></div>
      <p class="muted" style="font-size:.83rem">Tokens are shown exactly once at creation or reset, and stored only as hashes - same contract as <code>mco register</code>. Workers heartbeat every time they poll; an online agent silent past the threshold (Settings &rarr; Presence) shows offline.</p>
      <table><thead><tr><th>Instance</th><th>Role</th><th>Org</th><th>Scopes</th><th>Status</th><th>Actions</th></tr></thead>
      <tbody id="agents"><tr><td colspan="6" class="muted">-</td></tr></tbody></table>
      <p id="agents-msg" class="err"></p>
    </section>

    <!-- ── Model Connections ──────────────────────────────────── -->
    <section id="view-models" class="view">
      <div class="topbar"><h1>Model Connections</h1>
        <button class="primary" onclick="openAddModel()">+ Add connection</button></div>
      <p class="muted" style="font-size:.83rem">Named, testable connections to LLM providers, for custom workers/executors that need one. BitCadence orchestrates agents (Claude, Codex, Gemini, ...) rather than calling a model directly - this is credential management, not a chat gateway. <b>Test</b> makes one real call to the provider to prove the key works. Keys are never shown again after saving.</p>
      <table><thead><tr><th>Name</th><th>Provider</th><th>Model</th><th>Key</th><th>Actions</th></tr></thead>
      <tbody id="models"><tr><td colspan="5" class="muted">-</td></tr></tbody></table>
      <p id="models-msg" class="err"></p>
    </section>

    <!-- ── Workflows ──────────────────────────────────────────── -->
    <section id="view-workflows" class="view">
      <div class="topbar"><h1>Workflows</h1></div>
      <p class="muted" style="font-size:.83rem">Paste a workflow YAML (same format as <code>mco workflow</code>). Steps become governed jobs; dependent steps receive the full Context Exchange thread automatically.</p>
      <textarea id="wf-yaml" placeholder="name: release-pipeline&#10;steps:&#10;  - id: research&#10;    role: claude&#10;    title: Research the change&#10;    instructions: ...&#10;  - id: build&#10;    role: codex&#10;    title: Implement&#10;    instructions: ...&#10;    depends_on: [research]&#10;    requires_approval: true"></textarea>
      <div style="margin-top:.6rem"><button class="primary" onclick="submitWorkflow()">Submit workflow</button>
      <span id="wf-msg" style="margin-left:.8rem"></span></div>
      <pre id="wf-result" style="margin-top:.8rem"></pre>
    </section>

    <!-- ── Settings ───────────────────────────────────────────── -->
    <section id="view-settings" class="view">
      <div class="topbar"><h1>Settings</h1>
        <button onclick="loadSettings()">Reload</button></div>
      <p id="settings-msg" class="muted" style="font-size:.83rem">Settings persist to the global config home (<code>~/.mco/.env</code>) and apply immediately. Admin token required.</p>
      <div id="edition-card"></div>
      <div id="settings-groups"></div>
      <div id="integrations-card" class="card">
        <h3>Connectors</h3>
        <div id="integrations-body" class="muted">-</div>
      </div>
    </section>
  </main>
</div>

<div id="modal-bg" onclick="if(event.target===this)closeModal()">
  <div id="modal"></div>
</div>

<script>
const $ = id => document.getElementById(id);
let token = localStorage.getItem("mco_token") || "";
let SCOPES = ["admin","agents:manage","agents:read","context:read","context:write",
              "integrations:manage","integrations:read","jobs:approve","jobs:read","jobs:write"];
let opsTimer = null;

function esc(s) {
  return String(s ?? "").replace(/[&<>"'/]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;","/":"&#47;"}[c]));
}
function short(id) { return String(id ?? "").slice(0, 8); }
function badge(s) { return `<span class="badge s-${esc(s)}">${esc(s)}</span>`; }

async function api(path, opts = {}) {
  const res = await fetch(path, {
    ...opts,
    headers: { "Authorization": "Bearer " + token, "Content-Type": "application/json", ...(opts.headers || {}) },
  });
  if (!res.ok) {
    let detail = ""; try { detail = (await res.json()).detail || ""; } catch (e) { }
    const err = new Error(detail || ("HTTP " + res.status)); err.status = res.status; throw err;
  }
  return res.json();
}

/* ── lock screen ─────────────────────────────────────────────── */
async function unlock() {
  token = $("lock-token").value.trim();
  if (!token) { $("lock-msg").textContent = "Token required."; return; }
  try {
    await api("/api/jobs");                  // cheapest authenticated probe
    localStorage.setItem("mco_token", token);
    $("lock").style.display = "none";
    $("shell").classList.add("on");
    startOps();
  } catch (e) {
    $("lock-msg").textContent = e.status === 401 ? "Invalid token." : "Gateway error: " + e.message;
  }
}
function lockUp() {
  localStorage.removeItem("mco_token"); token = "";
  clearInterval(opsTimer);
  $("shell").classList.remove("on"); $("lock").style.display = "flex";
  $("lock-token").value = "";
}

/* ── navigation ──────────────────────────────────────────────── */
function nav(view) {
  for (const v of ["ops","agents","models","workflows","settings"]) {
    $("view-" + v).classList.toggle("on", v === view);
    $("nav-" + v).classList.toggle("active", v === view);
  }
  if (view === "agents") loadAgents();
  if (view === "models") loadModels();
  if (view === "settings") loadSettings();
}

/* ── operations & multi-select & sorting ─────────────────────── */
let selectedJobs = new Set();
let currentSort = localStorage.getItem("mco_job_sort") || "created_desc";
let sortCol = "created";
let sortDir = "desc";

function updateSortIndicators() {
  for (const c of ["id", "title", "status", "priority", "target", "leased_by", "created"]) {
    const el = $("sort-ind-" + c);
    if (el) el.textContent = (sortCol === c) ? (sortDir === "asc" ? " ▲" : " ▼") : "";
  }
}

function toggleSort(col) {
  if (sortCol === col) {
    sortDir = (sortDir === "asc") ? "desc" : "asc";
  } else {
    sortCol = col;
    sortDir = (col === "priority" || col === "created") ? "desc" : "asc";
  }
  currentSort = sortCol + "_" + sortDir;
  localStorage.setItem("mco_job_sort", currentSort);
  updateSortIndicators();
  refreshOps();
}

function toggleSelectAllApprovals(checked) {
  document.querySelectorAll(".approval-cb").forEach(cb => {
    cb.checked = checked;
    if (checked) selectedJobs.add(cb.value);
    else selectedJobs.delete(cb.value);
  });
  updateSelectedCount();
}

function toggleSelectAllJobs(checked) {
  document.querySelectorAll(".job-cb").forEach(cb => {
    cb.checked = checked;
    if (checked) selectedJobs.add(cb.value);
    else selectedJobs.delete(cb.value);
  });
  updateSelectedCount();
}

function toggleJobSelection(id, checked) {
  if (checked) selectedJobs.add(id);
  else selectedJobs.delete(id);
  updateSelectedCount();
}

function updateSelectedCount() {
  const count = selectedJobs.size;
  $("bulk-count").textContent = count;
  $("bulk-bar").style.display = count > 0 ? "flex" : "none";
}

function clearSelection() {
  selectedJobs.clear();
  document.querySelectorAll(".job-cb, .approval-cb").forEach(cb => cb.checked = false);
  const allJ = $("chk-all-jobs"); if (allJ) allJ.checked = false;
  const allA = $("chk-all-approvals"); if (allA) allA.checked = false;
  updateSelectedCount();
}

async function bulkAction(action) {
  const ids = [...selectedJobs];
  if (!ids.length) { alert("No jobs selected."); return; }
  let reason = "";
  let targetRole = "";
  if (action === "reject") {
    reason = prompt("Reason for batch rejection?") || "";
    if (!reason) return;
  } else if (action === "cancel") {
    reason = prompt("Reason for batch cancellation?") || "";
  } else if (action === "reassign") {
    targetRole = prompt("Target agent role (e.g. claude, codex, antigravity):") || "";
    if (!targetRole) return;
  }

  try {
    const res = await api("/api/jobs/batch-action", {
      method: "POST",
      body: JSON.stringify({
        job_ids: ids,
        action: action,
        reason: reason,
        target_agent_role: targetRole || undefined
      })
    });
    const succ = res.succeeded ? res.succeeded.length : 0;
    const fail = res.failed ? Object.keys(res.failed).length : 0;
    alert(`Batch ${action} completed: ${succ} succeeded, ${fail} failed.`);
    clearSelection();
    refreshOps();
  } catch (e) {
    alert("Batch action error: " + e.message);
  }
}

/* ── autonomy controls & live look ──────────────────────────── */
async function toggleAutonomyPause() {
  try {
    const cur = $("btn-toggle-pause").textContent;
    const endpoint = cur === "Resume" ? "/api/score/autonomy/resume" : "/api/score/autonomy/pause";
    await api(endpoint, { method: "POST" });
    refreshOps();
  } catch (e) { alert("Autonomy toggle error: " + e.message); }
}

async function triggerManualTick() {
  try {
    $("btn-tick-now").textContent = "Ticking...";
    const res = await api("/api/score/autonomy/tick", { method: "POST" });
    $("btn-tick-now").textContent = "Tick Now";
    const t = res.result && res.result.ticked ? res.result.ticked.length : 0;
    const a = res.result && res.result.advanced ? res.result.advanced.length : 0;
    alert(`Manual tick completed: ${t} ticked, ${a} advanced.`);
    refreshOps();
  } catch (e) {
    $("btn-tick-now").textContent = "Tick Now";
    alert("Manual tick error: " + e.message);
  }
}

async function abortActiveRun(runId) {
  const reason = prompt("Reason for aborting run " + runId + "?") || "Aborted by operator";
  try {
    await api("/api/score/autonomy/abort", {
      method: "POST",
      body: JSON.stringify({ run_id: runId, reason: reason })
    });
    alert("Run " + runId + " aborted.");
    openLiveLook();
    refreshOps();
  } catch (e) {
    alert("Abort error: " + e.message);
  }
}

async function openLiveLook() {
  try {
    const a = await api("/api/score/autonomy");
    const activeHtml = a.active_runs && a.active_runs.length ? a.active_runs.map(r => `
      <tr>
        <td><b>${esc(r.id)}</b></td>
        <td>${badge(r.status)}</td>
        <td>${esc(r.tasks_accepted ? r.tasks_accepted.length : 0)} tasks</td>
        <td><button class="no" onclick="abortActiveRun('${esc(r.id)}')">Abort Run</button></td>
      </tr>
    `).join("") : '<tr><td colspan="4" class="muted">No active Score runs in progress.</td></tr>';

    const failHtml = a.failing_runs && a.failing_runs.length ? `
      <h4 style="color:#f85149; margin-top:1rem">Failing / Stuck Runs</h4>
      <table><thead><tr><th>Run</th><th>Status</th></tr></thead>
      <tbody>${a.failing_runs.map(r => `<tr><td>${esc(r.id)}</td><td>${badge(r.status)}</td></tr>`).join("")}</tbody></table>
    ` : "";

    openModal(`<h3>Autonomy Live Look</h3>
      <div style="margin-bottom:1rem; font-size:.85rem; display:grid; grid-template-columns:9rem 1fr; gap:.4rem .8rem">
        <span class="muted">Autonomy State:</span> <b>${badge(a.status)}</b>
        <span class="muted">Conductor Sweep:</span> <span>${a.interval_seconds}s interval (${a.paused ? "PAUSED" : (a.configured ? "Running" : "Standby")})</span>
        <span class="muted">Live Repository:</span> <span>${a.live_repository_write ? '<span class="good">Enabled</span>' : '<span class="muted">Shadow / Read-Only</span>'}</span>
        <span class="muted">Conductor State DB:</span> <span style="font-family:monospace; font-size:.78rem">${esc(a.database)}</span>
      </div>
      <div style="margin-bottom:1rem">
        <button onclick="triggerManualTick()">Trigger Sweep Now</button>
        <button onclick="toggleAutonomyPause()">${a.paused ? "Resume Conductor" : "Pause Conductor"}</button>
      </div>
      <h4>Active Score Runs (${a.active_runs ? a.active_runs.length : 0})</h4>
      <table><thead><tr><th>Run ID</th><th>Status</th><th>Accepted</th><th>Action</th></tr></thead>
      <tbody>${activeHtml}</tbody></table>
      ${failHtml}
      <div style="margin-top:1.2rem"><button onclick="closeModal()">Close</button></div>
    `);
  } catch (e) {
    alert("Live Look error: " + e.message);
  }
}

function startOps() { refreshOps(); clearInterval(opsTimer); opsTimer = setInterval(refreshOps, 5000); }

async function refreshOps() {
  if (!token) return;
  try {
    updateSortIndicators();
    const [jobs, autonomy] = await Promise.all([
      api("/api/jobs?sort=" + encodeURIComponent(currentSort)),
      api("/api/score/autonomy").catch(() => null)
    ]);
    $("conn").textContent = "connected " + new Date().toLocaleTimeString();
    $("conn").className = "muted";

    if (autonomy) {
      const st = autonomy.status;
      $("autonomy-badge").className = "badge " + (st === "active" ? "s-online" : (st === "paused" ? "s-disabled" : (st === "frozen" ? "s-failed" : "s-waiting")));
      $("autonomy-badge").textContent = st.toUpperCase();
      const runCount = autonomy.active_runs ? autonomy.active_runs.length : 0;
      $("autonomy-desc").textContent = `${autonomy.interval_seconds}s sweep | ${autonomy.live_repository_write ? "Live-Write" : "Shadow"} | ${runCount} active runs`;
      $("btn-toggle-pause").textContent = autonomy.paused ? "Resume" : "Pause";
    }

    const approvals = jobs.filter(j => j.status === "needs_approval");
    $("approvals").innerHTML = approvals.length ? approvals.map(j => `
      <tr>
        <td><input type="checkbox" class="approval-cb" value="${j.id}" onchange="toggleJobSelection('${j.id}',this.checked)" ${selectedJobs.has(j.id) ? "checked" : ""}></td>
        <td>${short(j.id)}</td><td>${esc(j.title)}</td><td>${esc(j.target_agent_role)}</td>
        <td>${esc(j.source_agent_id)}</td>
        <td><button class="ok" onclick="decide('${j.id}','approve')">Approve</button>
            <button class="no" onclick="decide('${j.id}','reject')">Reject</button></td></tr>`).join("")
      : '<tr><td colspan="6" class="muted">Nothing awaiting approval.</td></tr>';

    $("jobs").innerHTML = jobs.length ? jobs.map(j => `
      <tr>
        <td><input type="checkbox" class="job-cb" value="${j.id}" onchange="toggleJobSelection('${j.id}',this.checked)" ${selectedJobs.has(j.id) ? "checked" : ""}></td>
        <td>${short(j.id)}</td><td>${esc(j.title)}</td><td>${badge(j.status)}</td>
        <td>${esc(j.priority || 0)}</td>
        <td>${esc(j.target_agent_role)}${j.target_agent_id ? " / " + esc(j.target_agent_id) : ""}</td>
        <td>${esc(j.leased_by_instance_id || "-")}</td><td>${esc((j.created_at || "").slice(0, 19))}</td>
        <td><button onclick="showAudit('${j.id}')">Audit</button>
            ${["failed","rejected"].includes(j.status) ? `<button class="ok" onclick="retryJob('${j.id}')">Retry</button>` : ""}
        </td></tr>`).join("")
      : '<tr><td colspan="9" class="muted">No jobs.</td></tr>';
    updateSelectedCount();
  } catch (e) {
    $("conn").textContent = "error: " + e.message; $("conn").className = "err";
    if (e.status === 401) lockUp();
  }
}

async function decide(jobId, action) {
  let body = {};
  if (action === "reject") body.reason = prompt("Reason for rejection?") || "";
  try { await api(`/api/jobs/${jobId}/${action}`, { method: "POST", body: JSON.stringify(body) }); refreshOps(); }
  catch (e) { alert(action + " failed: " + e.message); }
}
async function retryJob(jobId) {
  try { await api(`/api/jobs/${jobId}/retry`, { method: "POST" }); refreshOps(); }
  catch (e) { alert("retry failed: " + e.message); }
}
async function showAudit(jobId) {
  try {
    const events = await api(`/api/jobs/${jobId}/events`);
    $("audit-job").textContent = jobId;
    $("audit-body").textContent = events.length
      ? events.map(ev => `${ev.created_at}  ${ev.event}  by ${ev.actor_id || "-"} (${ev.actor_role || "-"})  ${JSON.stringify(ev.detail || {})}`).join("\n")
      : "No audit events.";
    $("audit-panel").style.display = "block";
  } catch (e) { alert("audit fetch failed: " + e.message); }
}

/* ── modal helpers ───────────────────────────────────────────── */
function openModal(html) { $("modal").innerHTML = html; $("modal-bg").classList.add("on"); }
function closeModal() { $("modal-bg").classList.remove("on"); }

function connectHelp(instance, role, tok) {
  const origin = location.origin;
  const mcp = JSON.stringify({ mcpServers: { mco: {
    command: "mco", args: ["mcp"],
    env: { MCO_GATEWAY_URL: origin, MCO_AGENT_TOKEN: tok,
           AGENT_ROLE: role, AGENT_INSTANCE_ID: instance } } } }, null, 2);
  const worker =
`# PowerShell:
$env:MCO_AGENT_TOKEN = "${tok}"
mco listen --role ${role} --instance ${instance}

# bash/zsh:
MCO_AGENT_TOKEN="${tok}" mco listen --role ${role} --instance ${instance}`;
  return `
  <details style="margin-top:.9rem"><summary style="cursor:pointer"><b>Run it as a worker</b> (polls the job board)</summary>
    <pre class="token-box" style="border-style:solid;border-color:#30363d" id="hlp-worker">${esc(worker)}</pre>
    <button onclick="navigator.clipboard.writeText($('hlp-worker').textContent).then(()=>this.textContent='Copied!')">Copy</button>
  </details>
  <details style="margin-top:.6rem"><summary style="cursor:pointer"><b>Wire it into Claude Desktop / Codex / Antigravity</b> (MCP)</summary>
    <p class="muted" style="font-size:.78rem">Add to the agent's MCP config (e.g. <code>claude_desktop_config.json</code> &rarr; <code>mcpServers</code>; templates in <code>configs/</code>). The agent gets mco_inbox / mco_lease / mco_complete / mco_remember / mco_recall as native tools.</p>
    <pre class="token-box" style="border-style:solid;border-color:#30363d" id="hlp-mcp">${esc(mcp)}</pre>
    <button onclick="navigator.clipboard.writeText($('hlp-mcp').textContent).then(()=>this.textContent='Copied!')">Copy</button>
  </details>`;
}

function tokenModal(title, tok, extra) {
  openModal(`<h3>${esc(title)}</h3>
    <p>Copy this token now - <b>it will not be shown again.</b></p>
    <div class="token-box" id="tok-box">${esc(tok)}</div>
    <button class="primary" onclick="navigator.clipboard.writeText($('tok-box').textContent).then(()=>this.textContent='Copied!')">Copy token</button>
    <button onclick="closeModal()">Done</button>
    ${extra || ""}`);
}

function ago(secs) {
  if (secs === null || secs === undefined) return "never";
  if (secs < 90) return secs + "s ago";
  if (secs < 5400) return Math.round(secs / 60) + "m ago";
  if (secs < 129600) return Math.round(secs / 3600) + "h ago";
  return Math.round(secs / 86400) + "d ago";
}

/* ── agents ──────────────────────────────────────────────────── */
async function loadAgents() {
  try {
    const agents = await api("/api/agents");
    $("agents-msg").textContent = "";
    $("agents").innerHTML = agents.length ? agents.map(a => `
      <tr><td>${esc(a.instance_id)}</td><td>${esc(a.role)}</td>
      <td class="muted">${esc(a.org_id || "default")}</td>
      <td class="muted">${(a.scopes || []).map(esc).join(", ") || "role defaults"}</td>
      <td>${badge(a.effective_status || a.status)}<br>
          <span class="muted" style="font-size:.72rem">seen ${ago(a.last_seen_seconds)}</span></td>
      <td><button onclick="openEdit(${esc(JSON.stringify(a))})">Edit</button>
          <button onclick="resetToken('${esc(a.instance_id)}','${esc(a.role)}')">Reset token</button>
          <button class="no" onclick="deleteAgent('${esc(a.instance_id)}')">Delete</button></td></tr>`).join("")
      : '<tr><td colspan="6" class="muted">No agents registered yet.</td></tr>';
  } catch (e) { $("agents-msg").textContent = e.message; }
}

function scopeChecks(selected) {
  return `<div class="scopes-grid">` + SCOPES.map(s =>
    `<label><input type="checkbox" class="scope-cb" value="${s}" ${selected && selected.includes(s) ? "checked" : ""}> ${s}</label>`).join("") + `</div>`;
}
function pickedScopes() {
  return [...document.querySelectorAll(".scope-cb:checked")].map(cb => cb.value);
}

async function openRegister() {
  let orgs = ["default"];
  try { orgs = (await api("/api/agents/orgs")).orgs || ["default"]; } catch (e) { }
  const orgCtl = orgs.length > 1
    ? `<select id="reg-org">` + orgs.map(o => `<option ${o === "default" ? "selected" : ""}>${esc(o)}</option>`).join("") + `</select>`
    : `<input id="reg-org" value="default" disabled title="Add orgs in Settings -> Tenancy">`;
  openModal(`<h3>Register a new agent</h3>
    <div class="formgrid">
      <label>Name</label><input id="reg-name" placeholder="codex-worker-2">
      <label>Role</label><input id="reg-role" placeholder="codex">
      <label>Org</label>${orgCtl}
    </div>
    <p class="muted" style="font-size:.78rem;margin:.2rem 0 .6rem">Org is the <b>tenant boundary</b>, not a label: agents only see jobs and memory inside their own org. New orgs are added in Settings &rarr; Tenancy by an admin - never minted here.</p>
    <b style="font-size:.85rem">Scopes</b> <span class="muted" style="font-size:.78rem">(none checked = role-derived defaults)</span>
    ${scopeChecks([])}
    <button class="primary" onclick="registerAgent()">Register &amp; generate token</button>
    <button onclick="closeModal()">Cancel</button>
    <p id="reg-msg" class="err"></p>`);
}
async function registerAgent() {
  try {
    const res = await api("/api/agents", { method: "POST", body: JSON.stringify({
      instance_id: $("reg-name").value.trim(), role: $("reg-role").value.trim(),
      org: $("reg-org").value.trim() || "default", scopes: pickedScopes(),
    })});
    tokenModal(`Agent '${res.agent.instance_id}' registered`, res.token,
               connectHelp(res.agent.instance_id, res.agent.role, res.token));
    loadAgents();
  } catch (e) { $("reg-msg").textContent = e.message; }
}

function openEdit(a) {
  openModal(`<h3>Edit '${esc(a.instance_id)}'</h3>
    <div class="formgrid">
      <label>Role</label><input id="ed-role" value="${esc(a.role)}">
      <label>Status</label><select id="ed-status">
        ${["online","offline","disabled"].map(s => `<option ${a.status === s ? "selected" : ""}>${s}</option>`).join("")}
      </select>
    </div>
    <b style="font-size:.85rem">Scopes</b> <span class="muted" style="font-size:.78rem">(none = role-derived defaults)</span>
    ${scopeChecks(a.scopes || [])}
    <button class="primary" onclick="saveEdit('${esc(a.instance_id)}')">Save</button>
    <button onclick="closeModal()">Cancel</button>
    <p id="ed-msg" class="err"></p>`);
}
async function saveEdit(id) {
  try {
    await api(`/api/agents/${encodeURIComponent(id)}`, { method: "PATCH", body: JSON.stringify({
      role: $("ed-role").value.trim(), status: $("ed-status").value, scopes: pickedScopes(),
    })});
    closeModal(); loadAgents();
  } catch (e) { $("ed-msg").textContent = e.message; }
}
async function resetToken(id, role) {
  if (!confirm(`Rotate the token for '${id}'? The old token stops working immediately.`)) return;
  try {
    const res = await api(`/api/agents/${encodeURIComponent(id)}/reset-token`, { method: "POST" });
    tokenModal(`New token for '${id}'`, res.token, connectHelp(id, role || "codex", res.token));
  } catch (e) { alert("reset failed: " + e.message); }
}
async function deleteAgent(id) {
  if (!confirm(`Delete agent '${id}'? Its token stops working immediately.`)) return;
  try { await api(`/api/agents/${encodeURIComponent(id)}`, { method: "DELETE" }); loadAgents(); }
  catch (e) { alert("delete failed: " + e.message); }
}

/* ── model connections ──────────────────────────────────────── */
let MODEL_PROVIDERS = {};

async function loadModels() {
  try {
    const [conns, providers] = await Promise.all([
      api("/api/llm-connections"), api("/api/llm-connections/providers"),
    ]);
    MODEL_PROVIDERS = providers;
    $("models-msg").textContent = "";
    $("models").innerHTML = conns.length ? conns.map(c => `
      <tr><td>${esc(c.name)}</td>
      <td class="muted">${esc((providers[c.provider] || {}).label || c.provider)}</td>
      <td class="muted">${esc(c.model || "-")}</td>
      <td>${c.key_set ? '<span class="good">set</span>' : '<span class="err">not set</span>'}</td>
      <td><button onclick="testModelConn('${esc(c.id)}')">Test</button>
          <button onclick="openEditModel(${esc(JSON.stringify(c))})">Edit</button>
          <button class="no" onclick="deleteModelConn('${esc(c.id)}')">Delete</button></td></tr>`).join("")
      : '<tr><td colspan="5" class="muted">No model connections yet.</td></tr>';
  } catch (e) { $("models-msg").textContent = e.message; }
}

function providerOptions(selected) {
  return Object.entries(MODEL_PROVIDERS).map(([key, meta]) =>
    `<option value="${esc(key)}" ${key === selected ? "selected" : ""}>${esc(meta.label)}</option>`).join("");
}

async function openAddModel() {
  try { MODEL_PROVIDERS = await api("/api/llm-connections/providers"); } catch (e) { }
  openModal(`<h3>Add a model connection</h3>
    <div class="formgrid">
      <label>Name</label><input id="mc-name" placeholder="prod-anthropic">
      <label>Provider</label><select id="mc-provider" onchange="toggleModelBaseUrl()">${providerOptions("anthropic")}</select>
      <label id="mc-baseurl-lbl" style="display:none">Base URL</label>
      <input id="mc-baseurl" style="display:none" placeholder="http://localhost:11434/v1">
      <label>Model</label><input id="mc-model" placeholder="claude-opus-4 (optional)">
      <label>API key</label><input id="mc-key" type="password" placeholder="sk-...">
    </div>
    <button class="primary" onclick="submitAddModel()">Add &amp; save</button>
    <button onclick="closeModal()">Cancel</button>
    <p id="mc-msg" class="err"></p>`);
  toggleModelBaseUrl();
}
function toggleModelBaseUrl() {
  const provider = $("mc-provider").value;
  const editable = !MODEL_PROVIDERS[provider] || MODEL_PROVIDERS[provider].base_url_editable;
  $("mc-baseurl-lbl").style.display = editable ? "" : "none";
  $("mc-baseurl").style.display = editable ? "" : "none";
}
async function submitAddModel() {
  try {
    await api("/api/llm-connections", { method: "POST", body: JSON.stringify({
      name: $("mc-name").value.trim(), provider: $("mc-provider").value,
      base_url: $("mc-baseurl").value.trim(), model: $("mc-model").value.trim(),
      api_key: $("mc-key").value.trim(),
    })});
    closeModal(); loadModels();
  } catch (e) { $("mc-msg").textContent = e.message; }
}

function openEditModel(c) {
  const meta = MODEL_PROVIDERS[c.provider] || {};
  openModal(`<h3>Edit '${esc(c.name)}'</h3>
    <div class="formgrid">
      <label>Name</label><input id="me-name" value="${esc(c.name)}">
      ${meta.base_url_editable ? `<label>Base URL</label><input id="me-baseurl" value="${esc(c.base_url || "")}">` : ""}
      <label>Model</label><input id="me-model" value="${esc(c.model || "")}">
      <label>New API key</label><input id="me-key" type="password" placeholder="blank keeps the current key">
    </div>
    <button class="primary" onclick="saveEditModel('${esc(c.id)}', ${meta.base_url_editable ? "true" : "false"})">Save</button>
    <button onclick="closeModal()">Cancel</button>
    <p id="me-msg" class="err"></p>`);
}
async function saveEditModel(id, baseUrlEditable) {
  const payload = { name: $("me-name").value.trim(), model: $("me-model").value.trim() };
  if (baseUrlEditable) payload.base_url = $("me-baseurl").value.trim();
  const key = $("me-key").value.trim();
  if (key) payload.api_key = key;
  try {
    await api(`/api/llm-connections/${encodeURIComponent(id)}`, { method: "PATCH", body: JSON.stringify(payload) });
    closeModal(); loadModels();
  } catch (e) { $("me-msg").textContent = e.message; }
}
async function testModelConn(id) {
  try {
    const res = await api(`/api/llm-connections/${encodeURIComponent(id)}/test`, { method: "POST" });
    alert(res.ok ? `OK (${res.latency_ms}ms): ${res.detail}` : `Failed: ${res.detail}`);
  } catch (e) { alert("test failed: " + e.message); }
}
async function deleteModelConn(id) {
  if (!confirm("Delete this connection? Its stored key is removed too.")) return;
  try { await api(`/api/llm-connections/${encodeURIComponent(id)}`, { method: "DELETE" }); loadModels(); }
  catch (e) { alert("delete failed: " + e.message); }
}

/* ── workflows ───────────────────────────────────────────────── */
async function submitWorkflow() {
  $("wf-msg").textContent = ""; $("wf-result").textContent = "";
  try {
    const res = await api("/api/workflows", { method: "POST",
      body: JSON.stringify({ yaml: $("wf-yaml").value }) });
    $("wf-msg").innerHTML = '<span class="good">Submitted - run ' + esc(res.run) + '</span>';
    $("wf-result").textContent = JSON.stringify(res.jobs, null, 2);
    refreshOps();
  } catch (e) { $("wf-msg").innerHTML = '<span class="err">' + esc(e.message) + '</span>'; }
}

/* ── settings ────────────────────────────────────────────────── */
async function loadSettings() {
  try {
    const s = await api("/api/settings");
    SCOPES = s.known_scopes || SCOPES;
    renderEdition(s.edition);
    renderGroups(s.groups);
    $("settings-msg").className = "muted";
    $("settings-msg").innerHTML = 'Settings persist to <code>~/.mco/.env</code> and apply immediately.';
    loadIntegrations();
  } catch (e) {
    $("settings-msg").className = "err";
    $("settings-msg").textContent = e.status === 403
      ? "Settings require an admin-scoped token. (" + e.message + ")"
      : "Could not load settings: " + e.message;
    $("settings-groups").innerHTML = ""; $("edition-card").innerHTML = "";
  }
}

function renderEdition(ed) {
  const feats = Object.entries(ed.features).map(([name, f]) =>
    `<tr><td>${esc(name)}</td><td class="muted">${esc(f.minimum_edition)}</td>
     <td>${f.available ? '<span class="good">yes</span>' : '<span class="err">no</span>'}</td></tr>`).join("");
  $("edition-card").innerHTML = `<div class="card"><h3>Edition:
    <span style="text-transform:capitalize">${esc(ed.edition)}</span>
    <span class="muted" style="font-size:.78rem">(${esc(ed.source)} - pin with MCO_EDITION below)</span></h3>
    <table><thead><tr><th>Feature</th><th>Minimum edition</th><th>Available</th></tr></thead>
    <tbody>${feats}</tbody></table></div>`;
}

const GROUP_TITLES = { governance: "Governance", memory: "Drumline (shared memory)",
                       presence: "Presence & health", tenancy: "Tenancy (orgs)",
                       observability: "Observability", edition: "Edition",
                       security: "Security & SSO", notifications: "Notifications" };

function renderGroups(groups) {
  $("settings-groups").innerHTML = Object.entries(groups).map(([gname, items]) => {
    const rows = items.map(it => {
      let ctl = "";
      if (it.type === "bool") {
        ctl = `<label class="switch"><input type="checkbox" data-key="${it.key}" data-type="bool" ${it.value ? "checked" : ""}><span class="slider"></span></label>`;
      } else if (it.type === "choice") {
        ctl = `<select data-key="${it.key}" data-type="choice">` +
          it.choices.map(c => `<option value="${c}" ${String(it.value) === c ? "selected" : ""}>${c || "(infer)"}</option>`).join("") + `</select>`;
      } else if (it.type === "secret") {
        ctl = `<input type="password" data-key="${it.key}" data-type="secret"
                 placeholder="${it.value ? "configured - blank keeps it" : "not set"}">
               ${it.value ? `<button onclick="clearSecret('${it.key}')" title="Remove this secret">Clear</button>` : ""}`;
      } else {
        ctl = `<input type="text" data-key="${it.key}" data-type="text" value="${esc(it.value)}" placeholder="${esc(it.placeholder || "")}">`;
      }
      return `<div class="row"><label class="lbl">${esc(it.label)}<br><span class="muted" style="font-size:.72rem">${it.key}</span></label><span class="ctl">${ctl}</span></div>`;
    }).join("");
    const danger = gname === "governance" ? "" : "";
    return `<div class="card ${danger}" id="grp-${gname}"><h3>${GROUP_TITLES[gname] || gname}</h3>${rows}
      <div style="margin-top:.8rem"><button class="primary" onclick="saveGroup('${gname}')">Save ${GROUP_TITLES[gname] || gname}</button>
      <span id="grp-msg-${gname}" style="margin-left:.7rem; font-size:.8rem"></span></div></div>`;
  }).join("");
}

async function saveGroup(gname) {
  const card = $("grp-" + gname);
  const payload = {};
  for (const el of card.querySelectorAll("[data-key]")) {
    const t = el.dataset.type;
    if (t === "bool") payload[el.dataset.key] = el.checked;
    else if (t === "secret") { if (el.value.trim()) payload[el.dataset.key] = el.value.trim(); }
    else payload[el.dataset.key] = el.value.trim();
  }
  const msg = $("grp-msg-" + gname);
  try {
    await api("/api/settings", { method: "PUT", body: JSON.stringify(payload) });
    msg.innerHTML = '<span class="good">Saved.</span>'; setTimeout(() => msg.textContent = "", 2500);
    if ("MCO_EDITION" in payload) loadSettings();
  } catch (e) { msg.innerHTML = '<span class="err">' + esc(e.message) + '</span>'; }
}
async function clearSecret(key) {
  if (!confirm("Remove " + key + "?")) return;
  try { await api("/api/settings", { method: "PUT", body: JSON.stringify({ [key]: "" }) }); loadSettings(); }
  catch (e) { alert(e.message); }
}

/* ── integrations ────────────────────────────────────────────── */
async function loadIntegrations() {
  try {
    const conns = await api("/api/integrations");
    $("integrations-body").innerHTML = conns.length ? `<table>
      <thead><tr><th>Connector</th><th>Health</th><th>Actions</th><th></th></tr></thead><tbody>` +
      conns.map(c => `<tr><td>${esc(c.name)}</td>
        <td>${c.health && c.health.ok ? '<span class="good">ok</span>' : '<span class="err">' + esc((c.health || {}).detail || "down") + '</span>'}</td>
        <td class="muted">${(c.actions || []).map(esc).join(", ")}</td>
        <td><button onclick="syncConn('${esc(c.name)}')">Sync now</button></td></tr>`).join("") + `</tbody></table>`
      : '<span class="muted">No connectors configured. Set credentials via environment, then reload.</span>';
  } catch (e) {
    $("integrations-body").innerHTML = '<span class="muted">' + esc(e.message) + '</span>';
  }
}
async function syncConn(name) {
  try {
    const res = await api(`/api/integrations/${name}/sync`, { method: "POST" });
    alert(`Sync '${name}': ${(res.created || []).length} created, ${(res.skipped || []).length} skipped`);
    refreshOps();
  } catch (e) { alert("sync failed: " + e.message); }
}

/* ── boot ────────────────────────────────────────────────────── */
if (token) { $("lock-token").value = token; unlock(); }
</script>
</body>
</html>
'''
