// BitCadence — live gateway adapter.
// Wraps the demo store (data.js) in a facade. When connected, all reads/writes
// go to the real MCOrchestr8 REST API:
//   GET  /api/jobs            GET /api/agents       GET /api/jobs/{id}/events
//   POST /api/jobs            POST /api/jobs/{id}/approve|reject|retry
//   PUT  /api/jobs/{id}
//   POST /api/agents          POST /api/agents/{id}/reset-token
//   DELETE /api/agents/{id}
//   GET  /api/integrations    POST /api/integrations/{name}/sync
//   GET/POST /api/context     WS /ws/broadcast
// Live updates ride /ws/broadcast (token-only auth frame); polling drops to a
// 30s safety net when the socket is up, 4s otherwise. Poll() synthesizes
// toasts + an activity feed from status diffs either way.
(function () {
  const demo = window.BitCadenceStore; // set by data.js (must load first)
  const listeners = new Set();
  const toastFns = new Set();
  let cfg = null;
  try {
    // "baton_conn" is the pre-BitCadence key: read it once so an existing
    // console keeps its saved gateway instead of dropping back to demo.
    const saved = localStorage.getItem("bitcadence_conn") || localStorage.getItem("baton_conn");
    cfg = JSON.parse(saved || "null");
  } catch (e) { cfg = null; }
  let connState = "demo"; // demo | connecting | live
  let lastError = null;
  let pollTimer = null;
  let ws = null, wsOk = false, wsRetryTimer = null, wsBackoff = 2000;

  let jobs = [];
  let projectJobs = [];
  let projectCoverage = { count: 0, truncated: false, ceiling: 5000 };
  let projectFetchedAt = 0;
  let projectFetchPromise = null;
  let portfolio = null;
  let portfolioError = null;
  let portfolioFetchedAt = 0;
  let portfolioFetchPromise = null;
  let agents = [];
  let eventsCache = {};
  let prevStatus = {};
  let liveActivity = []; // synthesized feed: {id, job_id, event, created_at, actor_id, actor_role}

  const isLive = () => connState === "live";
  const emit = () => listeners.forEach((fn) => fn());
  const rid = () => Math.random().toString(36).slice(2);
  const toast = (kind, title, body) => toastFns.forEach((fn) => fn({ id: rid(), kind, title, body }));
  function withWorkflow(job) {
    const wf = job && job.input_payload && job.input_payload.workflow;
    if (!wf || job.workflow) return job;
    return Object.assign({}, job, { workflow: wf.name || wf.run, workflow_run: wf.run, workflow_step: wf.step });
  }

  // Re-emit demo store changes while in demo mode
  demo.subscribe(() => { if (connState === 'demo') emit(); });
  demo.onToast((t) => { if (connState === 'demo') toastFns.forEach((fn) => fn(t)); });

  async function api(path, opts = {}) {
    const base = (cfg && cfg.url ? cfg.url : "").replace(/\/+$/, "");
    const res = await fetch(base + path, Object.assign({}, opts, {
      headers: Object.assign({
        "Authorization": "Bearer " + ((cfg && cfg.token) || ""),
        "Content-Type": "application/json",
      }, opts.headers || {}),
    }));
    if (!res.ok) {
      let detail = "";
      try { detail = (await res.json()).detail || ""; } catch (e) { /* ignore */ }
      throw new Error("HTTP " + res.status + (detail ? " — " + detail : ""));
    }
    return res.json();
  }

  // A missing approver right is never shown as "HTTP 403": say it in words, point at the
  // one-key fix, and tell the Approvals page so it can show its own card.
  const APPROVER_WORDS = "Your account isn't an approver yet. Run `bitcadence fix` in a terminal and answer Y.";
  function plainFailure(e, fallback) {
    const text = String((e && e.message) || e || "");
    if (/^HTTP 403/.test(text)) {
      try { window.dispatchEvent(new CustomEvent("bc-approver-blocked")); } catch (err) { /* old browser */ }
      return APPROVER_WORDS;
    }
    return fallback ? fallback(text) : text;
  }

  const exchangeFns = new Set();

  const TOAST_FOR = {
    completed: ["ok", "Completed"],
    failed: ["err", "Failed"],
    needs_approval: ["approval", "Approval needed"],
    in_progress: ["info", "Started"],
    rejected: ["err", "Rejected"],
  };

  async function poll() {
    try {
      const [j, a] = await Promise.all([api("/api/jobs?limit=200"), api("/api/agents")]);
      const normalized = (j || []).map(withWorkflow);
      const seenBefore = Object.keys(prevStatus).length > 0;
      normalized.forEach((job) => {
        const old = prevStatus[job.id];
        if (seenBefore && old !== job.status) {
          liveActivity.unshift({
            id: rid(), job_id: job.id,
            event: old ? "status:" + job.status : "created",
            actor_id: job.leased_by_instance_id || job.source_agent_id, actor_role: job.target_agent_role,
            created_at: new Date().toISOString(),
          });
          const t = old ? TOAST_FOR[job.status] : ["info", "New job"];
          if (t) toast(t[0], t[1], job.title);
          delete eventsCache[job.id]; // refresh audit on next open
        }
        prevStatus[job.id] = job.status;
      });
      liveActivity = liveActivity.slice(0, 50);
      jobs = normalized; agents = a || [];
      if (connState === 'offline') connState = 'live';
      if (lastError) { lastError = null; }
      emit();
    } catch (e) {
      lastError = e.message;
      if (connState === 'live') connState = 'offline';
      emit();
    }
  }

  async function refreshProjectView(force) {
    if (!isLive()) return;
    if (!force && projectFetchedAt && Date.now() - projectFetchedAt < 60000) return;
    if (projectFetchPromise) return projectFetchPromise;
    projectFetchPromise = api("/api/jobs/project-view").then((projectView) => {
      projectJobs = ((projectView && projectView.jobs) || []).map(withWorkflow);
      projectCoverage = {
        count: Number(projectView && projectView.count) || projectJobs.length,
        truncated: !!(projectView && projectView.truncated),
        ceiling: Number(projectView && projectView.ceiling) || 5000,
      };
      projectFetchedAt = Date.now();
      emit();
    }).finally(() => { projectFetchPromise = null; });
    return projectFetchPromise;
  }

  async function refreshPortfolio(force) {
    if (!isLive()) return;
    if (!force && portfolioFetchedAt && Date.now() - portfolioFetchedAt < 30000) return;
    if (portfolioFetchPromise) return portfolioFetchPromise;
    portfolioFetchPromise = api("/api/portfolio").then((snapshot) => {
      portfolio = snapshot;
      portfolioError = null;
      portfolioFetchedAt = Date.now();
      emit();
      return snapshot;
    }).catch((error) => {
      portfolioError = error.message || "Portfolio could not be refreshed";
      emit();
      throw error;
    }).finally(() => { portfolioFetchPromise = null; });
    return portfolioFetchPromise;
  }

  function startPolling() {
    if (pollTimer) clearInterval(pollTimer);
    // With a live WebSocket feed, polling is just a safety net (agents list,
    // missed frames); without one it's the primary refresh path.
    pollTimer = setInterval(poll, wsOk ? 30000 : 4000);
  }
  function stopPolling() {
    if (pollTimer) clearInterval(pollTimer);
    pollTimer = null;
  }

  // ---- live event feed over /ws/broadcast (polling stays as fallback) ----
  function startWs() {
    if (!isLive() || typeof WebSocket === "undefined") return;
    const base = ((cfg && cfg.url) || "").replace(/\/+$/, "").replace(/^http/, "ws");
    if (!base) return;
    try { ws = new WebSocket(base + "/ws/broadcast"); } catch (e) { return; }
    ws.onopen = () => {
      // Token-only auth frame; the gateway resolves identity from the hash.
      try { ws.send(JSON.stringify({ type: "authenticate", payload: { token: (cfg && cfg.token) || "" } })); } catch (e) { /* onclose retries */ }
    };
    ws.onmessage = (m) => {
      let msg = null;
      try { msg = JSON.parse(m.data); } catch (e) { return; }
      if (msg.type === "authenticated") {
        if (msg.payload && msg.payload.success === false) { wsOk = false; try { ws.close(); } catch (e) { } return; }
        if (!wsOk) { wsOk = true; wsBackoff = 2000; startPolling(); }
        return;
      }
      if (msg.type === "event") {
        if (!wsOk) { wsOk = true; wsBackoff = 2000; startPolling(); } // zero-config gateways send no ack
        if (msg.payload && msg.payload.event === "exchange.created") {
          // A hint only: subscribers re-read authoritative rows over HTTP.
          exchangeFns.forEach((fn) => { try { fn(msg.payload); } catch (e) { /* ignore */ } });
          return;
        }
        poll(); // refresh state now; poll() synthesizes toasts + activity
      }
    };
    ws.onclose = () => {
      ws = null;
      if (wsOk) { wsOk = false; if (isLive()) startPolling(); } // back to 4s
      if (isLive()) { wsRetryTimer = setTimeout(startWs, wsBackoff); wsBackoff = Math.min(wsBackoff * 2, 30000); }
    };
    ws.onerror = () => { try { ws && ws.close(); } catch (e) { } };
  }
  function stopWs() {
    if (wsRetryTimer) clearTimeout(wsRetryTimer);
    wsRetryTimer = null;
    wsOk = false;
    const w = ws; ws = null;
    if (w) { try { w.close(); } catch (e) { } }
  }

  const facade = {
    // ---- connection management ----
    mode: () => connState,
    lastError: () => lastError,
    config: () => cfg || { url: (/^https?:$/.test(location.protocol) ? location.origin : "http://127.0.0.1:18789"), token: "" },
    async connect(url, token) {
      cfg = { url: url.trim(), token: token.trim() };
      connState = "connecting"; lastError = null; emit();
      try {
        await api("/api/agents"); // auth + reachability check
        localStorage.setItem("bitcadence_conn", JSON.stringify(cfg));
        demo.stopSim();
        connState = "live";
        jobs = []; projectJobs = []; projectFetchedAt = 0;
        portfolio = null; portfolioError = null; portfolioFetchedAt = 0;
        projectCoverage = { count: 0, truncated: false, ceiling: 5000 };
        agents = []; eventsCache = {}; prevStatus = {}; liveActivity = [];
        await poll();
        startPolling();
        startWs();
        toast("ok", "Live", "Connected. Your helpers are ready.");
        emit();
        return true;
      } catch (e) {
        connState = "offline"; lastError = e.message;
        toast("err", "Connection failed", e.message);
        emit();
        return false;
      }
    },
    disconnect() {
      stopWs();
      stopPolling();
      connState = "demo"; lastError = null;
      projectJobs = []; projectFetchedAt = 0;
      portfolio = null; portfolioError = null; portfolioFetchedAt = 0;
      projectCoverage = { count: 0, truncated: false, ceiling: 5000 };
      localStorage.removeItem("bitcadence_conn");
      localStorage.removeItem("baton_conn");
      toast("info", "Demo mode", "Showing simulated data again.");
      emit();
    },

    // ---- reads ----
    getJobs: () => connState === "demo" ? demo.getJobs() : jobs.slice(),
    getProjectJobs: () => connState === "demo" ? demo.getJobs() : (projectFetchedAt ? projectJobs.slice() : jobs.slice()),
    getProjectCoverage: () => connState === "demo"
      ? { count: demo.getJobs().length, truncated: false, ceiling: 5000 }
      : projectFetchedAt ? { ...projectCoverage } : { count: jobs.length, truncated: false, ceiling: 5000 },
    refreshProjectView,
    getPortfolio: () => portfolio,
    getPortfolioError: () => portfolioError,
    refreshPortfolio,
    getAgents: () => connState === "demo" ? demo.getAgents() : agents.slice(),
    async getScoreRun(runId) {
      if (connState === "demo") return null;
      return api("/api/score/autonomy/runs/" + encodeURIComponent(runId));
    },
    getEvents(jobId) {
      if (connState === "demo") return demo.getEvents(jobId);
      if (!eventsCache[jobId]) {
        eventsCache[jobId] = [];
        api("/api/jobs/" + jobId + "/events")
          .then((ev) => { eventsCache[jobId] = ev || []; emit(); })
          .catch(() => { /* leave empty; drawer shows none */ });
      }
      return eventsCache[jobId];
    },
    getAllEvents: (n) => isLive() ? liveActivity.slice(0, n || 12) : demo.getAllEvents(n),

    // ---- writes ----
    async approve(jobId, actor) {
      if (connState === "demo") return demo.approve(jobId, actor);
      try { await api("/api/jobs/" + jobId + "/approve", { method: "POST" }); await poll(); }
      catch (e) { toast("err", "Approve failed", plainFailure(e)); }
    },
    async reject(jobId, actor, reason) {
      if (connState === "demo") return demo.reject(jobId, actor, reason);
      try { await api("/api/jobs/" + jobId + "/reject", { method: "POST", body: JSON.stringify({ reason: reason || "" }) }); await poll(); }
      catch (e) { toast("err", "Reject failed", plainFailure(e)); }
    },
    async retryNow(jobId) {
      if (connState === "demo") return demo.retryNow(jobId);
      try { await api("/api/jobs/" + jobId + "/retry", { method: "POST" }); await poll(); toast("ok", "Re-queued", "Job sent back to the board."); }
      catch (e) { toast("err", "Retry failed", plainFailure(e, (t) => t + " (retry needs an approver-role token)")); }
    },
    async cancelJob(jobId, reason) {
      if (connState === "demo") return demo.cancelJob ? demo.cancelJob(jobId, reason) : null;
      try {
        await api("/api/jobs/" + jobId + "/cancel", { method: "POST", body: JSON.stringify({ reason: reason || "" }) });
        await poll();
        toast("ok", "Cancelled", "The job was called off.");
      }
      catch (e) { toast("err", "Cancel failed", e.message + " (cancel needs an approver-role token)"); }
    },
    async reassignJob(jobId, toRole, toInstance) {
      if (connState === "demo") return demo.reassignJob ? demo.reassignJob(jobId, toRole, toInstance) : null;
      try {
        const body = { to_role: toRole };
        if (toInstance) body.to_instance = toInstance;
        await api("/api/jobs/" + jobId + "/reassign", { method: "POST", body: JSON.stringify(body) });
        await poll();
        toast("ok", "Reassigned", "Now waiting on " + (toInstance || toRole) + ".");
      }
      catch (e) { toast("err", "Reassign failed", e.message + " (reassign needs an approver-role token)"); }
    },
    async assignProject(jobId, name) {
      if (connState === "demo") return demo.assignProject(jobId, name);
      try {
        await api("/api/jobs/" + jobId + "/project", { method: "POST", body: JSON.stringify({ name: String(name || "").trim() }) });
        await refreshProjectView(true);
        toast("ok", name ? "Project assigned" : "Project cleared", name || "The job is now unassigned.");
      } catch (e) { toast("err", "Project assignment failed", e.message); throw e; }
    },
    async batchAction(action, jobIds, extra = {}) {
      if (connState === "demo") {
        const res = demo.batchAction ? demo.batchAction(action, jobIds, extra) : { ok: true, success_count: jobIds.length, failure_count: 0 };
        toast("ok", "Batch " + action, `Action applied to ${res.success_count} job(s).`);
        return res;
      }
      try {
        const payload = Object.assign({ action, job_ids: jobIds }, extra);
        const res = await api("/api/jobs/batch-action", { method: "POST", body: JSON.stringify(payload) });
        await poll();
        toast(res.failure_count > 0 ? "info" : "ok", "Batch " + action, `Completed: ${res.success_count} succeeded, ${res.failure_count} failed.`);
        return res;
      } catch (e) {
        toast("err", "Batch " + action + " failed", plainFailure(e));
        throw e;
      }
    },
    async createJob(payload) {
      if (connState === "demo") return demo.createJob(payload);
      try {
        const res = await api("/api/jobs", { method: "POST", body: JSON.stringify(payload) });
        await poll();
        toast(payload.requires_approval ? "approval" : "ok", "Job created", payload.title);
        return res.job;
      } catch (e) { toast("err", "Create failed", e.message); }
    },
    // "Ask for something": the gateway draws the plan (same planner as `bitcadence ask`).
    async draftAsk(body) {
      if (connState === "demo") throw new Error("Connect to your BitCadence in Settings first, then you can ask for something.");
      const res = await api("/api/ask/plan", { method: "POST", body: JSON.stringify(body) });
      return res.plan;
    },
    async startAsk(body) {
      if (connState === "demo") throw new Error("Connect to your BitCadence in Settings first, then you can ask for something.");
      const res = await api("/api/ask/start", { method: "POST", body: JSON.stringify(body) });
      await poll();
      toast("ok", "Approved", "Starting now.");
      return res;
    },
    // Schedules page: same code as `bitcadence schedule`.
    async schedulesList() {
      return api("/api/schedules");
    },
    async schedulePreview(body) {
      return api("/api/schedules/preview", { method: "POST", body: JSON.stringify(body) });
    },
    async addSchedule(body) {
      return api("/api/schedules", { method: "POST", body: JSON.stringify(body) });
    },
    async setScheduleOn(id, on) {
      return api("/api/schedules/" + encodeURIComponent(id) + "/enabled", { method: "POST", body: JSON.stringify({ on }) });
    },
    // Helpers page: same code as `bitcadence helpers` / `bitcadence fix`.
    async helpersList() {
      return api("/api/helpers");
    },
    async addHelper(body) {
      const res = await api("/api/helpers/add", { method: "POST", body: JSON.stringify(body) });
      await poll();
      return res;
    },
    async fixHelpers(confirm) {
      const res = await api("/api/helpers/fix", { method: "POST", body: JSON.stringify({ confirm: !!confirm }) });
      if (confirm) await poll();
      return res;
    },
    // Connect an AI page: same code as `bitcadence connect`.
    async connectList() {
      return api("/api/connect-ai");
    },
    async connectAction(app, verb) {
      return api("/api/connect-ai/" + encodeURIComponent(app) + "/" + verb, { method: "POST", body: JSON.stringify({}) });
    },
    async connectOther() {
      return api("/api/connect-ai/other");
    },
    async seedDemoPipeline() {
      if (connState === "demo") return demo.seedDemoPipeline();
      try {
        const res = await api("/api/workflows/demo-pipeline", { method: "POST", body: JSON.stringify({}) });
        await poll();
        toast("ok", "Demo pipeline running", "Claude -> Codex -> reviewer is now visible in the live feed.");
        return res;
      } catch (e) { toast("err", "Demo seed failed", e.message); }
    },
    async exportEvidencePack(payload) {
      if (connState === "demo") return demo.exportEvidencePack(payload);
      return api("/api/governance/evidence-pack", { method: "POST", body: JSON.stringify(payload || {}) });
    },

    // ---- settings & connectors (live only) ----
    async getSettings() {
      if (!isLive()) throw new Error("Connect to your orchestrator first.");
      return api("/api/settings");
    },
    async saveSettings(values) {
      if (!isLive()) throw new Error("Connect to your orchestrator first.");
      const result = await api("/api/settings", { method: "PUT", body: JSON.stringify(values) });
      await poll();
      return result;
    },
    async testConnector(name) {
      if (!isLive()) throw new Error("Connect to your orchestrator first.");
      return api("/api/settings/test-connector", { method: "POST", body: JSON.stringify({ name }) });
    },
    async registerAgent(payload) {
      if (!isLive()) throw new Error("Connect to your orchestrator first.");
      const res = await api("/api/agents", { method: "POST", body: JSON.stringify(payload) });
      await poll();
      return res;
    },
    async resetToken(instanceId) {
      if (!isLive()) throw new Error("Connect to your orchestrator first.");
      return api("/api/agents/" + instanceId + "/reset-token", { method: "POST" });
    },
    async deleteAgent(instanceId) {
      if (!isLive()) throw new Error("Connect to your orchestrator first.");
      const res = await api("/api/agents/" + instanceId, { method: "DELETE" });
      await poll();
      return res;
    },
    async getIntegrations() {
      if (!isLive()) throw new Error("Connect to your orchestrator first.");
      return api("/api/integrations");
    },
    async syncConnector(name) {
      if (!isLive()) throw new Error("Connect to your orchestrator first.");
      return api("/api/integrations/" + name + "/sync", { method: "POST" });
    },

    // ---- cross-job audit feed + tenancy (live only) ----
    async getRecentEvents(opts) {
      if (!isLive()) throw new Error("Connect to your orchestrator first.");
      const o = opts || {};
      const params = new URLSearchParams();
      if (o.since) params.set("since", o.since);
      if (o.limit) params.set("limit", String(o.limit));
      const qs = params.toString();
      return api("/api/events" + (qs ? "?" + qs : ""));
    },
    async getOrgs() {
      if (!isLive()) throw new Error("Connect to your orchestrator first.");
      return api("/api/agents/orgs");
    },
    async updateAgent(instanceId, payload) {
      if (!isLive()) throw new Error("Connect to your orchestrator first.");
      const res = await api("/api/agents/" + instanceId, { method: "PATCH", body: JSON.stringify(payload) });
      await poll();
      return res;
    },

    // ---- Drumline shared context (live only) ----
    async getContext(opts) {
      if (!isLive()) throw new Error("Connect to your orchestrator first.");
      const o = opts || {};
      const params = new URLSearchParams();
      if (o.query) params.set("query", o.query);
      if (o.tags) params.set("tags", o.tags);
      if (o.limit) params.set("limit", String(o.limit));
      const qs = params.toString();
      return api("/api/context" + (qs ? "?" + qs : ""));
    },
    async addContext(payload) {
      if (!isLive()) throw new Error("Connect to your orchestrator first.");
      return api("/api/context", { method: "POST", body: JSON.stringify(payload) });
    },

    // ---- Drumline Agent Exchange (live only; non-authoritative discussion) ----
    async getExchanges(opts) {
      if (!isLive()) throw new Error("Connect to your orchestrator first.");
      const params = new URLSearchParams();
      Object.keys(opts || {}).forEach((k) => { if (opts[k] !== undefined && opts[k] !== "") params.set(k, String(opts[k])); });
      return api("/api/exchanges?" + params.toString());
    },
    async getExchange(id) {
      if (!isLive()) throw new Error("Connect to your orchestrator first.");
      return api("/api/exchanges/" + encodeURIComponent(id));
    },
    async addExchange(payload) {
      if (!isLive()) throw new Error("Connect to your orchestrator first.");
      return api("/api/exchanges", { method: "POST", body: JSON.stringify(payload) });
    },
    async promoteExchange(id, payload) {
      if (!isLive()) throw new Error("Connect to your orchestrator first.");
      return api("/api/exchanges/" + encodeURIComponent(id) + "/promotions", { method: "POST", body: JSON.stringify(payload) });
    },
    onExchange(fn) { exchangeFns.add(fn); return () => exchangeFns.delete(fn); },

    // ---- autonomy & score conductor controls ----
    async getAutonomy() {
      if (!isLive()) {
        return {
          ok: true,
          conductor_active: true,
          conductor_leader: true,
          sweep_paused: false,
          sweep_interval_sec: 10.0,
          live_runs_count: 2,
          live_runs: [
            { run_id: "demo-run-001", stage: "execute_plan", target_agent: "codex-build-1", remaining_sec: 240, score_progress: 0.75 },
            { run_id: "demo-run-002", stage: "qa_verification", target_agent: "gemini-qa-1", remaining_sec: 180, score_progress: 0.90 }
          ],
          active_workers: ["codex-build-1", "gemini-qa-1", "claude-research-1"],
          telemetry: { total_sweeps: 142, tasks_dispatched: 38, failures: 1, pass_rate: 0.974 }
        };
      }
      return api("/api/score/autonomy/status");
    },
    async pauseAutonomy() {
      if (!isLive()) {
        toast("ok", "Autonomy Paused", "Conductor sweep paused in demo mode.");
        return { ok: true, sweep_paused: true };
      }
      try {
        const res = await api("/api/score/autonomy/pause", { method: "POST" });
        toast("ok", "Autonomy Paused", "Autonomous conductor sweep is now paused.");
        return res;
      } catch (e) {
        toast("err", "Pause failed", e.message);
        throw e;
      }
    },
    async resumeAutonomy() {
      if (!isLive()) {
        toast("ok", "Autonomy Resumed", "Conductor sweep resumed in demo mode.");
        return { ok: true, sweep_paused: false };
      }
      try {
        const res = await api("/api/score/autonomy/resume", { method: "POST" });
        toast("ok", "Autonomy Resumed", "Autonomous conductor sweep is active.");
        return res;
      } catch (e) {
        toast("err", "Resume failed", e.message);
        throw e;
      }
    },
    async tickAutonomy() {
      if (!isLive()) {
        toast("ok", "Autonomy Tick", "Triggered manual sweep tick in demo mode.");
        return { ok: true, swept: true };
      }
      try {
        const res = await api("/api/score/autonomy/tick", { method: "POST" });
        toast("ok", "Autonomy Tick", "Conductor sweep executed successfully.");
        return res;
      } catch (e) {
        toast("err", "Tick failed", e.message);
        throw e;
      }
    },
    async abortRun(runId, reason) {
      if (!isLive()) {
        toast("ok", "Run Aborted", `Run ${runId} aborted.`);
        return { ok: true, run_id: runId, status: "aborted" };
      }
      try {
        const res = await api("/api/score/autonomy/abort", { method: "POST", body: JSON.stringify({ run_id: runId, reason: reason || "" }) });
        toast("ok", "Run Aborted", `Run ${runId} aborted.`);
        return res;
      } catch (e) {
        toast("err", "Abort failed", e.message);
        throw e;
      }
    },

    // ---- demo sim controls (no-ops while live) ----
    startSim() { if (!isLive()) demo.startSim(); },
    stopSim() { demo.stopSim(); },

    // ---- pub/sub ----
    subscribe(fn) { listeners.add(fn); return () => listeners.delete(fn); },
    onToast(fn) { toastFns.add(fn); return () => toastFns.delete(fn); },
  };

  window.BitCadenceStore = facade;

  // Auto-reconnect if a saved connection exists
  if (cfg && cfg.url && cfg.token) {
    facade.connect(cfg.url, cfg.token);
  }
})();
