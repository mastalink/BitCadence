// BitCadence — mock data store + live simulation engine.
// Shapes mirror the real MCOrchestr8 schema (agent_jobs, agent_registry,
// agent_job_events) so screens can later be pointed at the real REST API:
//   GET /api/jobs, GET /api/agents, GET /api/jobs/{id}/events,
//   POST /api/jobs, POST /api/jobs/{id}/approve|reject
(function () {
  let seq = 0;
  const uuid = () => {
    seq += 1;
    const hex = "0123456789abcdef";
    let s = "";
    for (let i = 0; i < 32; i++) s += hex[(Math.imul(seq * 2654435761 + i * 40503, 1) >>> (i % 24)) & 15];
    return s.slice(0, 8) + "-" + s.slice(8, 12) + "-4" + s.slice(13, 16) + "-a" + s.slice(17, 20) + "-" + s.slice(20, 32);
  };
  const now = () => new Date().toISOString();
  const minsAgo = (m) => new Date(Date.now() - m * 60000).toISOString();

  // ---------------- Agents ----------------
  const agents = [
    { instance_id: "codex-build-1", role: "codex", status: "online", last_seen_at: minsAgo(0.2) },
    { instance_id: "codex-build-2", role: "codex", status: "online", last_seen_at: minsAgo(0.4) },
    { instance_id: "claude-research-1", role: "claude", status: "online", last_seen_at: minsAgo(0.1) },
    { instance_id: "claude-research-2", role: "claude", status: "offline", last_seen_at: minsAgo(94) },
    { instance_id: "gemini-qa-1", role: "gemini", status: "online", last_seen_at: minsAgo(0.6) },
    { instance_id: "joe-laptop", role: "human", status: "online", last_seen_at: minsAgo(1) },
    { instance_id: "ops-console-1", role: "operator", status: "offline", last_seen_at: minsAgo(312) },
  ];

  // ---------------- Jobs ----------------
  const events = []; // {id, job_id, event, actor_id, actor_role, detail, created_at}
  function record(job_id, event, actor_id, actor_role, detail) {
    events.push({ id: uuid(), job_id, event, actor_id, actor_role, detail: detail || null, created_at: now() });
  }
  function recordAt(job_id, event, actor_id, actor_role, detail, iso) {
    events.push({ id: uuid(), job_id, event, actor_id, actor_role, detail: detail || null, created_at: iso });
  }

  function mkJob(o) {
    return Object.assign({
      id: uuid(), title: "", description: "", source_agent_id: "joe-laptop", source_agent_role: "human",
      target_agent_role: "codex", target_agent_id: null, status: "pending", depends_on: [],
      input_payload: {}, output_payload: null, error_message: null,
      requires_approval: false, max_retries: 0, retry_count: 0, escalate_to_role: null,
      approved_by: null, leased_by_instance_id: null,
      workflow: null, created_at: now(), updated_at: now(),
    }, o);
  }

  // Seed: a release workflow mid-flight + assorted singles
  const wfResearch = mkJob({ title: "Research open issues for v2.4", description: "Summarize open issues and breaking changes targeted at the v2.4 release.", target_agent_role: "claude", status: "completed", workflow: "release-pipeline", created_at: minsAgo(42), output_payload: { summary: "14 issues triaged; 2 breaking changes flagged in auth module." } });
  const wfBuild = mkJob({ title: "Implement v2.4 fixes", description: "Apply the fixes identified by the research step.", target_agent_role: "codex", status: "in_progress", depends_on: [wfResearch.id], leased_by_instance_id: "codex-build-1", max_retries: 2, escalate_to_role: "human", workflow: "release-pipeline", created_at: minsAgo(42) });
  const wfQa = mkJob({ title: "Run regression suite", description: "Execute the full regression suite against the release branch.", target_agent_role: "gemini", status: "waiting", depends_on: [wfBuild.id], workflow: "release-pipeline", created_at: minsAgo(42) });
  const wfShip = mkJob({ title: "Tag and publish v2.4", description: "Tag the release and publish artifacts to the registry.", target_agent_role: "codex", status: "waiting", depends_on: [wfQa.id], requires_approval: true, workflow: "release-pipeline", created_at: minsAgo(42) });

  const jobs = [
    mkJob({ title: "Deploy hotfix to production", description: "Roll the auth-token expiry hotfix to the production gateway.", target_agent_role: "codex", status: "needs_approval", requires_approval: true, source_agent_id: "claude-research-1", source_agent_role: "claude", created_at: minsAgo(8) }),
    mkJob({ title: "Rotate Supabase service keys", description: "Rotate service-role keys and update the secret vault.", target_agent_role: "codex", status: "needs_approval", requires_approval: true, source_agent_id: "gemini-qa-1", source_agent_role: "gemini", created_at: minsAgo(23) }),
    wfShip, wfQa, wfBuild,
    mkJob({ title: "Summarize weekly agent activity", description: "Compile the weekly digest of fleet activity for the ops channel.", target_agent_role: "claude", status: "pending", created_at: minsAgo(5) }),
    mkJob({ title: "Index new docs into memory", target_agent_role: "claude", status: "leased", leased_by_instance_id: "claude-research-1", created_at: minsAgo(11) }),
    wfResearch,
    mkJob({ title: "Nightly dependency audit", description: "Scan lockfiles for vulnerable packages.", target_agent_role: "gemini", status: "completed", created_at: minsAgo(125), output_payload: { vulnerabilities: 0, scanned: 312 } }),
    mkJob({ title: "Backfill audit events to cold storage", target_agent_role: "codex", status: "failed", error_message: "S3 bucket policy denied PutObject (403).", max_retries: 2, retry_count: 2, escalate_to_role: "human", created_at: minsAgo(58) }),
    mkJob({ title: "Draft release notes for v2.3.1", target_agent_role: "claude", status: "completed", created_at: minsAgo(240), output_payload: { doc: "release-notes-v2.3.1.md" } }),
    mkJob({ title: "Prune stale agent registrations", target_agent_role: "codex", status: "rejected", requires_approval: true, approved_by: "joe-laptop", error_message: "Rejected by joe-laptop: ops-console-1 may come back after the office move.", created_at: minsAgo(300) }),
  ];

  // Seed plausible audit history
  jobs.forEach((j) => {
    recordAt(j.id, "created", j.source_agent_id, j.source_agent_role, { status: j.depends_on.length ? "waiting" : (j.requires_approval ? "needs_approval" : "pending"), target_agent_role: j.target_agent_role }, j.created_at);
    if (["leased", "in_progress", "completed", "failed"].includes(j.status)) {
      recordAt(j.id, "leased", j.leased_by_instance_id || (j.target_agent_role + "-build-1"), j.target_agent_role, null, j.created_at);
    }
    if (j.status === "completed") recordAt(j.id, "status:completed", j.leased_by_instance_id || "codex-build-2", j.target_agent_role, null, j.updated_at);
    if (j.status === "failed") {
      recordAt(j.id, "status:failed", "codex-build-2", "codex", { error: j.error_message }, j.updated_at);
      recordAt(j.id, "retried", "system", "system", { attempt: 2, max_retries: 2 }, j.updated_at);
      recordAt(j.id, "status:failed", "codex-build-2", "codex", { error: j.error_message }, j.updated_at);
    }
    if (j.status === "rejected") recordAt(j.id, "rejected", "joe-laptop", "human", { reason: "ops-console-1 may come back after the office move." }, j.updated_at);
  });

  // ---------------- Store + pub/sub ----------------
  const listeners = new Set();
  const toastListeners = new Set();
  function notify() { listeners.forEach((fn) => fn()); }
  function toast(kind, title, body) { toastListeners.forEach((fn) => fn({ id: uuid(), kind, title, body })); }

  function find(id) { return jobs.find((j) => j.id === id); }
  function touch(j, status) { j.status = status; j.updated_at = now(); }

  function unlockDependents(doneId) {
    jobs.forEach((j) => {
      if (j.status !== "waiting") return;
      const blocked = j.depends_on.some((d) => { const dep = find(d); return dep && dep.status !== "completed"; });
      if (!blocked) {
        const next = j.requires_approval ? "needs_approval" : "pending";
        touch(j, next);
        record(j.id, "status:" + next, "system", "system", { unlocked_by: doneId });
        if (next === "needs_approval") toast("approval", "Approval needed", j.title);
      }
    });
  }

  // ---------------- Public actions ----------------
  const store = {
    getJobs: () => jobs.slice(),
    getAgents: () => agents.slice(),
    getEvents: (jobId) => events.filter((e) => e.job_id === jobId).slice(),
    getAllEvents: (limit) => events.slice(-1 * (limit || 12)).reverse(),
    subscribe: (fn) => { listeners.add(fn); return () => listeners.delete(fn); },
    onToast: (fn) => { toastListeners.add(fn); return () => toastListeners.delete(fn); },

    approve(jobId, actor) {
      const j = find(jobId); if (!j || j.status !== "needs_approval") return;
      touch(j, "pending"); j.approved_by = actor;
      record(jobId, "approved", actor, "human");
      toast("ok", "Approved", j.title + " is now ready to run.");
      notify();
    },
    reject(jobId, actor, reason) {
      const j = find(jobId); if (!j || j.status !== "needs_approval") return;
      touch(j, "rejected"); j.approved_by = actor;
      j.error_message = "Rejected by " + actor + ": " + (reason || "no reason given");
      record(jobId, "rejected", actor, "human", reason ? { reason } : null);
      toast("err", "Rejected", j.title);
      notify();
    },
    retryNow(jobId) {
      const j = find(jobId); if (!j || j.status !== "failed") return;
      j.retry_count = 0; j.error_message = null; touch(j, "pending");
      record(jobId, "retried", "joe-laptop", "human", { manual: true });
      toast("ok", "Re-queued", j.title);
      notify();
    },
    createJob(payload) {
      const requires = !!payload.requires_approval;
      const status = (payload.depends_on || []).length ? "waiting" : (requires ? "needs_approval" : "pending");
      const j = mkJob(Object.assign({}, payload, { status, source_agent_id: "joe-laptop", source_agent_role: "human" }));
      jobs.unshift(j);
      record(j.id, "created", "joe-laptop", "human", { status, target_agent_role: j.target_agent_role });
      toast(requires ? "approval" : "ok", "Job created", j.title);
      notify();
      return j;
    },
    submitWorkflow(name, steps) {
      // steps: [{tmpId, role, title, instructions, depends_on:[tmpIds], requires_approval, max_retries, escalate_to_role}]
      const idMap = {};
      steps.forEach((s) => {
        const deps = (s.depends_on || []).map((d) => idMap[d]).filter(Boolean);
        const blocked = deps.length > 0;
        const status = blocked ? "waiting" : (s.requires_approval ? "needs_approval" : "pending");
        const j = mkJob({
          title: s.title, description: s.instructions || "", target_agent_role: s.role,
          depends_on: deps, requires_approval: !!s.requires_approval,
          max_retries: s.max_retries || 0, escalate_to_role: s.escalate_to_role || null,
          status, workflow: name,
        });
        jobs.unshift(j);
        record(j.id, "created", "joe-laptop", "human", { status, workflow: name });
        idMap[s.tmpId] = j.id;
      });
      toast("ok", "Workflow submitted", name + " — " + steps.length + " steps queued.");
      notify();
      return idMap;
    },
    seedDemoPipeline() {
      const name = "jde-demo-live-pipeline";
      const run = uuid().slice(0, 12);
      const steps = [
        { tmpId: "plan", role: "claude", title: "Demo pipeline: plan the customer change", instructions: "Read the pilot brief and hand Codex a scoped build plan.", depends_on: [] },
        { tmpId: "build", role: "codex", title: "Demo pipeline: build the approved slice", instructions: "Implement the planned change and return files plus verification output.", depends_on: ["plan"], max_retries: 1 },
        { tmpId: "review", role: "reviewer", title: "Demo pipeline: test and sign off", instructions: "Review the branch, run tests, and approve or return findings.", depends_on: ["build"] },
      ];
      const idMap = {};
      steps.forEach((s) => {
        const deps = (s.depends_on || []).map((d) => idMap[d]).filter(Boolean);
        const j = mkJob({
          title: s.title, description: s.instructions, target_agent_role: s.role,
          depends_on: deps, status: deps.length ? "waiting" : "pending",
          max_retries: s.max_retries || 0, workflow: name,
          input_payload: { prompt: s.instructions, workflow: { name, run, step: s.tmpId } },
        });
        jobs.unshift(j);
        record(j.id, "created", "joe-laptop", "human", { status: j.status, workflow: name });
        idMap[s.tmpId] = j.id;
      });
      toast("ok", "Demo pipeline running", "Claude -> Codex -> reviewer is now visible on the board.");
      notify();
      return { success: true, workflow: name, run, jobs: idMap };
    },
    exportEvidencePack(payload) {
      const start = (payload || {}).start_date || null;
      const end = (payload || {}).end_date || null;
      const inRange = (e) => {
        const ts = e.created_at || "";
        if (start && ts && ts < start) return false;
        if (end && ts && ts > end + "T23:59:59") return false;
        return true;
      };
      const auditEvents = events.filter(inRange).map((e) => {
        const j = find(e.job_id);
        return Object.assign({}, e, {
          job_title: j && j.title,
          job_status: j && j.status,
          target_agent_role: j && j.target_agent_role,
        });
      });
      const pending = jobs.filter((j) => j.status === "needs_approval").map((j) => ({
        id: j.id, title: j.title, target_agent_role: j.target_agent_role, created_at: j.created_at,
      }));
      const decisions = auditEvents.filter((e) => e.event === "approved" || e.event === "rejected");
      const audit = {
        exported_at: now(),
        requested_by: "joe-laptop",
        org_id: "default",
        range: { start, end },
        regulatory_basis: {
          eu_ai_act_article_12: "Record-keeping: preserve system event logs and job lifecycle audit data.",
          eu_ai_act_article_14: "Human oversight: preserve approval requests and operator decisions.",
        },
        summary: { audit_events: auditEvents.length, pending_approvals: pending.length, decisions: decisions.length },
        pending_approvals: pending,
        decision_history: decisions,
        audit_events: auditEvents,
      };
      return {
        success: true,
        generated_at: audit.exported_at,
        summary: audit.summary,
        files: [
          { filename: "cover.pdf", mime: "application/pdf", base64: "JVBERi0xLjQK" },
          { filename: "audit-trail.json", mime: "application/json", text: JSON.stringify(audit, null, 2) },
        ],
      };
    },
  };

  // ---------------- Live simulation ----------------
  const backlog = [
    ["Refresh embeddings for support docs", "claude"],
    ["Compress old job payloads", "codex"],
    ["Smoke-test staging gateway", "gemini"],
    ["Translate changelog to Spanish", "claude"],
    ["Rebuild MCP schema map", "codex"],
  ];
  let backlogIdx = 0;
  let tickCount = 0;

  function tick() {
    tickCount += 1;
    let changed = false;

    // Heartbeats
    agents.forEach((a) => { if (a.status === "online") a.last_seen_at = now(); });

    // Progress one active-ish job per tick
    const pending = jobs.filter((j) => j.status === "pending");
    const leased = jobs.filter((j) => j.status === "leased");
    const running = jobs.filter((j) => j.status === "in_progress");

    if (running.length && tickCount % 3 === 0) {
      const j = running[0];
      const fail = j.max_retries > 0 && j.retry_count < j.max_retries && Math.random() < 0.18;
      if (fail) {
        j.retry_count += 1; j.error_message = "Executor exited with code 1 (transient).";
        touch(j, "pending");
        record(j.id, "status:failed", j.leased_by_instance_id, j.target_agent_role, { error: j.error_message });
        record(j.id, "retried", "system", "system", { attempt: j.retry_count, max_retries: j.max_retries });
        toast("err", "Retrying", j.title + " — attempt " + (j.retry_count + 1));
      } else {
        touch(j, "completed"); j.output_payload = { ok: true };
        record(j.id, "status:completed", j.leased_by_instance_id, j.target_agent_role, null);
        toast("ok", "Completed", j.title);
        unlockDependents(j.id);
      }
      changed = true;
    } else if (leased.length) {
      const j = leased[0]; touch(j, "in_progress");
      record(j.id, "status:in_progress", j.leased_by_instance_id, j.target_agent_role, null);
      changed = true;
    } else if (pending.length && tickCount % 2 === 0) {
      const j = pending[0];
      const candidates = agents.filter((a) => a.role === j.target_agent_role && a.status === "online");
      if (candidates.length) {
        j.leased_by_instance_id = candidates[tickCount % candidates.length].instance_id;
        touch(j, "leased");
        record(j.id, "leased", j.leased_by_instance_id, j.target_agent_role, null);
        changed = true;
      }
    }

    // Occasionally a new job arrives from an agent
    if (tickCount % 9 === 0 && backlogIdx < backlog.length) {
      const [title, role] = backlog[backlogIdx++];
      const j = mkJob({ title, target_agent_role: role, source_agent_id: "claude-research-1", source_agent_role: "claude" });
      jobs.unshift(j);
      record(j.id, "created", j.source_agent_id, j.source_agent_role, { status: "pending" });
      toast("info", "New job", title);
      changed = true;
    }

    if (changed) notify();
  }

  let timer = null;
  store.startSim = () => { if (!timer) timer = setInterval(tick, 2600); };
  store.stopSim = () => { clearInterval(timer); timer = null; };

  window.BitCadenceStore = store;
})();
