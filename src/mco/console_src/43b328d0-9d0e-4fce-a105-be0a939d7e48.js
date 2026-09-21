// BitCadence — Overview (mission control) + Approvals inbox.
const { useState: useStateH, useMemo: useMemoH, useEffect: useEffectH } = React;

// ----- Overview -----
function StatCard({ label, value, sub, kind, onClick }) {
  return (
    <button onClick={onClick} style={{
      textAlign: "left", background: "var(--surface)", border: "1px solid var(--border)",
      borderRadius: "var(--radius-l)", boxShadow: "var(--shadow-s)", padding: "var(--card-pad)",
      cursor: onClick ? "pointer" : "default", display: "block", width: "100%",
      transition: "border-color .12s, box-shadow .12s",
    }}
      onMouseEnter={(e) => { if (onClick) e.currentTarget.style.boxShadow = "var(--shadow-m)"; }}
      onMouseLeave={(e) => e.currentTarget.style.boxShadow = "var(--shadow-s)"}>
      <div style={{ fontSize: 12.5, fontWeight: 600, color: "var(--text-3)", display: "flex", alignItems: "center", gap: 7 }}>
        {kind ? <span style={{ width: 8, height: 8, borderRadius: 99, background: `var(--st-${kind}-dot)` }}></span> : null}
        {label}
      </div>
      <div style={{ fontSize: 30, fontWeight: 650, letterSpacing: "-0.02em", margin: "6px 0 2px", fontVariantNumeric: "tabular-nums" }}>{value}</div>
      <div style={{ fontSize: 12.5, color: "var(--text-3)" }}>{sub}</div>
    </button>
  );
}

function WorkflowStrip({ jobs, tone, onOpen }) {
  // Group jobs by workflow, show each as a stepper
  const groups = useMemoH(() => {
    const g = {};
    jobs.forEach((j) => { if (j.workflow) (g[j.workflow] = g[j.workflow] || []).push(j); });
    return Object.entries(g).map(([name, list]) => {
      // order by dependency depth: roots first, then their dependents
      const depth = {};
      const d = (id, seen) => {
        if (depth[id] != null) return depth[id];
        if (seen.has(id)) return 0;
        seen.add(id);
        const job = list.find((x) => x.id === id);
        if (!job) return 0;
        const deps = (job.depends_on || []).filter((x) => list.some((y) => y.id === x));
        depth[id] = deps.length ? 1 + Math.max(...deps.map((x) => d(x, seen))) : 0;
        return depth[id];
      };
      list.forEach((j) => d(j.id, new Set()));
      const sorted = list.slice().sort((a, b) => (depth[a.id] || 0) - (depth[b.id] || 0));
      return { name, steps: sorted };
    });
  }, [jobs]);
  if (!groups.length) return null;

  return groups.map((g) => (
    <Card key={g.name} style={{ marginBottom: 14 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 14 }}>
        <div style={{ fontWeight: 650, fontSize: 14.5 }}>{g.name}</div>
        <span style={{ fontSize: 12, color: "var(--text-3)" }}>{g.steps.filter((s) => s.status === "completed").length} of {g.steps.length} steps done</span>
      </div>
      <div style={{ display: "flex", alignItems: "stretch", gap: 0, overflowX: "auto", paddingBottom: 2 }}>
        {g.steps.map((s, i) => (
          <React.Fragment key={s.id}>
            {i > 0 ? <div style={{ alignSelf: "center", width: 26, flex: "none", height: 2, background: s.status === "waiting" ? "var(--border)" : "var(--st-done-dot)", opacity: s.status === "waiting" ? 1 : 0.5 }}></div> : null}
            <button onClick={() => onOpen(s.id)} style={{
              flex: "1 0 150px", minWidth: 150, textAlign: "left", cursor: "pointer",
              background: s.status === "in_progress" ? "var(--accent-soft)" : "var(--surface-2)",
              border: s.status === "in_progress" ? "1.5px solid var(--accent)" : "1px solid var(--border)",
              borderRadius: "var(--radius-m)", padding: "10px 12px",
            }}>
              <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 6 }}>
                <RoleChip role={s.target_agent_role} size={18} />
                <StatusBadge status={s.status} tone={tone} />
              </div>
              <div style={{ fontSize: 12.5, fontWeight: 600, lineHeight: 1.3 }}>{s.title}</div>
              {s.requires_approval && (s.status === "waiting" || s.status === "needs_approval") ? (
                <div style={{ fontSize: 11, color: "var(--st-approval-fg)", marginTop: 4 }}>⚠ needs a human OK</div>
              ) : null}
            </button>
          </React.Fragment>
        ))}
      </div>
    </Card>
  ));
}

function ActivityFeed({ jobs, tone, onOpen }) {
  const evts = window.BitCadenceStore.getAllEvents(9);
  const titleOf = (jobId) => { const j = jobs.find((x) => x.id === jobId); return j ? j.title : shortId(jobId); };
  return (
    <Card pad={false}>
      <div style={{ padding: "0 4px" }}>
        {evts.map((e, i) => (
          <button key={e.id} onClick={() => onOpen(e.job_id)} style={{
            display: "flex", width: "100%", textAlign: "left", gap: 10, alignItems: "center",
            padding: "9px 14px", background: "transparent", border: "none", cursor: "pointer",
            borderBottom: i < evts.length - 1 ? "1px solid var(--border)" : "none", fontSize: 13,
          }}
            onMouseEnter={(ev) => ev.currentTarget.style.background = "var(--surface-2)"}
            onMouseLeave={(ev) => ev.currentTarget.style.background = "transparent"}>
            <span style={{ width: 8, height: 8, flex: "none", borderRadius: 99, background: `var(--st-${eventKindH(e.event)}-dot)` }}></span>
            <span style={{ flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
              <b>{eventLabel(e.event, tone)}</b>
              <span style={{ color: "var(--text-2)" }}> · {titleOf(e.job_id)}</span>
            </span>
            <span style={{ color: "var(--text-3)", fontSize: 11.5, flex: "none" }}>{timeAgo(e.created_at)}</span>
          </button>
        ))}
        {!evts.length ? <EmptyState icon="≡" title="Quiet so far" body="Activity will appear as agents pick up work." /> : null}
      </div>
    </Card>
  );
}
function eventKindH(ev) {
  if (ev === "created") return "pending";
  if (ev === "leased" || ev === "status:in_progress") return "active";
  if (ev === "approved" || ev === "status:completed") return "done";
  if (ev === "rejected" || ev.indexOf("failed") >= 0) return "failed";
  return "waiting";
}

function DemoLaunchPanel({ tone, onNav }) {
  const [busy, setBusy] = useStateH(false);
  const [lastRun, setLastRun] = useStateH(null);
  async function runDemo() {
    setBusy(true);
    try {
      const res = await window.BitCadenceStore.seedDemoPipeline();
      if (res) {
        setLastRun(res.run || "created");
        onNav("overview");
      }
    } finally {
      setBusy(false);
    }
  }
  return (
    <Card style={{
      marginBottom: 18,
      borderColor: "color-mix(in srgb, var(--accent) 32%, var(--border))",
      background: "linear-gradient(135deg, color-mix(in srgb, var(--accent-soft) 72%, var(--surface)), var(--surface))",
    }}>
      <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 1.4fr) auto", gap: 18, alignItems: "center" }}>
        <div>
          <div style={{ fontSize: 11.5, fontWeight: 750, letterSpacing: "0.08em", textTransform: "uppercase", color: "var(--accent-text)", marginBottom: 6 }}>Pilot demo</div>
          <h2 style={{ margin: "0 0 6px", fontSize: 22, lineHeight: 1.15, letterSpacing: "-0.01em" }}>
            Launch Claude -> Codex -> reviewer in the live console
          </h2>
          <p style={{ margin: 0, color: "var(--text-2)", fontSize: 13.5, maxWidth: 680 }}>
            {tone === "plain"
              ? "Seeds a three-job workflow and lets the activity stream show work arriving, waiting, and moving."
              : "Creates a stamped workflow run through the same job intake path as production: dependencies, audit rows, and broadcasts included."}
          </p>
          {lastRun ? <div style={{ marginTop: 9, fontSize: 12, color: "var(--text-3)" }}>Last demo run: <Mono>{lastRun}</Mono></div> : null}
        </div>
        <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap", justifyContent: "flex-end" }}>
          <Btn kind="primary" disabled={busy} onClick={runDemo}>{busy ? "Seeding..." : "Run demo pipeline"}</Btn>
          <Btn kind="ghost" onClick={() => onNav("governance")}>Governance tab</Btn>
        </div>
      </div>
    </Card>
  );
}

function AutonomyLiveLookModal({ open, onClose, autonomy, onRefresh, tone, onOpenJob }) {
  if (!open) return null;
  const store = window.BitCadenceStore;
  const [busy, setBusy] = useStateH(false);
  const data = autonomy || {};
  const isPaused = !!data.sweep_paused;
  const isActive = !!data.conductor_active;
  const runs = data.live_runs || [];
  const telemetry = data.telemetry || {};

  const handlePauseResume = async () => {
    setBusy(true);
    try {
      if (isPaused) await store.resumeAutonomy();
      else await store.pauseAutonomy();
      if (onRefresh) await onRefresh();
    } finally { setBusy(false); }
  };

  const handleTick = async () => {
    setBusy(true);
    try {
      await store.tickAutonomy();
      if (onRefresh) await onRefresh();
    } finally { setBusy(false); }
  };

  const handleAbort = async (runId) => {
    const reason = window.prompt(`Enter reason for aborting run ${runId}:`, "Operator manual intervention");
    if (reason === null) return;
    setBusy(true);
    try {
      await store.abortRun(runId, reason);
      if (onRefresh) await onRefresh();
    } finally { setBusy(false); }
  };

  return (
    <div style={{ position: "fixed", inset: 0, zIndex: 70, display: "flex", alignItems: "center", justifyContent: "center" }}>
      <div onClick={onClose} style={{ position: "absolute", inset: 0, background: "rgba(18,18,24,0.6)", backdropFilter: "blur(4px)" }}></div>
      <div style={{
        position: "relative", zIndex: 71, width: 860, maxWidth: "calc(100vw - 32px)", maxHeight: "calc(100vh - 40px)",
        background: "var(--surface)", border: "1px solid var(--border)", borderRadius: "var(--radius-l)",
        boxShadow: "0 20px 50px rgba(0,0,0,0.35)", display: "flex", flexDirection: "column", overflow: "hidden",
      }}>
        {/* Header */}
        <div style={{ padding: "18px 24px", borderBottom: "1px solid var(--border)", display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
            <span style={{
              width: 12, height: 12, borderRadius: 99,
              background: !isActive ? "var(--text-3)" : isPaused ? "var(--st-approval-dot)" : "var(--st-done-dot)",
              boxShadow: isActive && !isPaused ? "0 0 10px var(--st-done-dot)" : "none",
            }}></span>
            <div>
              <h2 style={{ margin: 0, fontSize: 18, fontWeight: 700, letterSpacing: "-0.01em" }}>
                Autonomy Status &amp; Live Look
              </h2>
              <div style={{ fontSize: 12, color: "var(--text-3)", marginTop: 2 }}>
                Conductor Oversight · Real-Time Telemetry · Steering Controls
              </div>
            </div>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <Btn small onClick={onRefresh} disabled={busy}>↻ Refresh</Btn>
            <Btn small kind="ghost" onClick={onClose} style={{ fontSize: 16 }}>×</Btn>
          </div>
        </div>

        {/* Body */}
        <div style={{ padding: "20px 24px", overflowY: "auto", flex: 1 }}>
          {/* Status & Conductor Info */}
          <div style={{
            display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(170px, 1fr))", gap: 12, marginBottom: 20,
            padding: 14, background: "var(--surface-2)", borderRadius: "var(--radius-m)", border: "1px solid var(--border)",
          }}>
            <div>
              <div style={{ fontSize: 11, color: "var(--text-3)", textTransform: "uppercase", fontWeight: 600 }}>In Charge Status</div>
              <div style={{ fontSize: 15, fontWeight: 700, color: !isActive ? "var(--text-3)" : isPaused ? "var(--st-approval-fg)" : "var(--st-done-fg)", marginTop: 4 }}>
                {!isActive ? "STANDBY" : isPaused ? "PAUSED" : "ACTIVE & LEADING"}
              </div>
            </div>
            <div>
              <div style={{ fontSize: 11, color: "var(--text-3)", textTransform: "uppercase", fontWeight: 600 }}>Conductor Leader</div>
              <div style={{ fontSize: 14, fontWeight: 600, marginTop: 4 }}>
                {data.conductor_leader ? "Primary Leader" : "Follower / Replica"}
              </div>
            </div>
            <div>
              <div style={{ fontSize: 11, color: "var(--text-3)", textTransform: "uppercase", fontWeight: 600 }}>Sweep Interval</div>
              <div style={{ fontSize: 14, fontWeight: 600, marginTop: 4 }}>
                {data.sweep_interval_sec ? `${data.sweep_interval_sec}s cadence` : "Event-driven"}
              </div>
            </div>
            <div>
              <div style={{ fontSize: 11, color: "var(--text-3)", textTransform: "uppercase", fontWeight: 600 }}>Active Workers</div>
              <div style={{ fontSize: 14, fontWeight: 600, marginTop: 4 }}>
                {(data.active_workers || []).length} connected
              </div>
            </div>
          </div>

          {/* Steering Controls Bar */}
          <div style={{
            display: "flex", alignItems: "center", gap: 10, padding: "12px 16px", marginBottom: 20,
            background: isPaused ? "var(--st-approval-bg)" : "var(--accent-soft)",
            border: `1px solid ${isPaused ? "var(--st-approval-dot)" : "var(--accent)"}`,
            borderRadius: "var(--radius-m)",
          }}>
            <span style={{ fontSize: 13, fontWeight: 600, color: isPaused ? "var(--st-approval-fg)" : "var(--accent-text)" }}>
              {isPaused ? "Autonomy is currently PAUSED — background sweeps are suspended." : "Autonomous Conductor is running active passes."}
            </span>
            <div style={{ flex: 1 }}></div>
            <Btn small kind={isPaused ? "primary" : "default"} disabled={busy} onClick={handlePauseResume}>
              {isPaused ? "▶ Resume Autonomy" : "❚❚ Pause Autonomy"}
            </Btn>
            <Btn small disabled={busy} onClick={handleTick}>
              ⚡ Tick / Sweep Now
            </Btn>
          </div>

          {/* Telemetry */}
          <div style={{ marginBottom: 20 }}>
            <SectionTitle>Conductor Telemetry</SectionTitle>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 10 }}>
              <div style={{ background: "var(--surface)", border: "1px solid var(--border)", borderRadius: 8, padding: "10px 14px" }}>
                <div style={{ fontSize: 11, color: "var(--text-3)" }}>Total Sweeps</div>
                <div style={{ fontSize: 20, fontWeight: 700, marginTop: 2 }}>{telemetry.total_sweeps ?? 0}</div>
              </div>
              <div style={{ background: "var(--surface)", border: "1px solid var(--border)", borderRadius: 8, padding: "10px 14px" }}>
                <div style={{ fontSize: 11, color: "var(--text-3)" }}>Tasks Dispatched</div>
                <div style={{ fontSize: 20, fontWeight: 700, marginTop: 2 }}>{telemetry.tasks_dispatched ?? 0}</div>
              </div>
              <div style={{ background: "var(--surface)", border: "1px solid var(--border)", borderRadius: 8, padding: "10px 14px" }}>
                <div style={{ fontSize: 11, color: "var(--text-3)" }}>Failures / Blockers</div>
                <div style={{ fontSize: 20, fontWeight: 700, marginTop: 2, color: (telemetry.failures || 0) > 0 ? "var(--st-failed-fg)" : "inherit" }}>
                  {telemetry.failures ?? 0}
                </div>
              </div>
              <div style={{ background: "var(--surface)", border: "1px solid var(--border)", borderRadius: 8, padding: "10px 14px" }}>
                <div style={{ fontSize: 11, color: "var(--text-3)" }}>Pass Rate</div>
                <div style={{ fontSize: 20, fontWeight: 700, marginTop: 2, color: "var(--st-done-fg)" }}>
                  {telemetry.pass_rate != null ? `${Math.round(telemetry.pass_rate * 100)}%` : "100%"}
                </div>
              </div>
            </div>
          </div>

          {/* Active Runs */}
          <div>
            <SectionTitle>Live Autonomous Runs ({runs.length})</SectionTitle>
            <Card pad={false}>
              {runs.length === 0 ? (
                <div style={{ padding: "24px 16px", textAlign: "center", color: "var(--text-3)", fontSize: 13 }}>
                  No autonomous score runs in flight. Conductor is standing by for new assignments.
                </div>
              ) : (
                <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
                  <THead cols={["Run ID", "Target Agent", "Stage / Action", "Lease Left", "Progress", "Intervene"]} />
                  <tbody>
                    {runs.map((r) => (
                      <tr key={r.run_id} style={{ borderBottom: "1px solid var(--border)" }}>
                        <Td><Mono style={{ fontWeight: 600 }}>{shortId(r.run_id)}</Mono></Td>
                        <Td>
                          <RoleChip role={r.target_agent || "codex"} size={18} />
                          <span style={{ marginLeft: 6 }}>{r.target_agent}</span>
                        </Td>
                        <Td><span style={{ fontWeight: 500 }}>{r.stage || "executing"}</span></Td>
                        <Td><span style={{ color: "var(--text-3)" }}>{r.remaining_sec ? `${r.remaining_sec}s` : "active"}</span></Td>
                        <Td>
                          <div style={{ width: 90, height: 6, background: "var(--surface-2)", borderRadius: 99, overflow: "hidden" }}>
                            <div style={{ width: `${Math.round((r.score_progress || 0.5) * 100)}%`, height: "100%", background: "var(--accent)" }}></div>
                          </div>
                        </Td>
                        <Td>
                          <Btn small kind="danger" disabled={busy} onClick={() => handleAbort(r.run_id)}>Abort</Btn>
                        </Td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </Card>
          </div>
        </div>
      </div>
    </div>
  );
}

function AutonomyControlCard({ tone, onOpenLiveLook }) {
  const store = window.BitCadenceStore;
  const [autonomy, setAutonomy] = useStateH(null);
  const [busy, setBusy] = useStateH(false);

  const load = async () => {
    try {
      const data = await store.getAutonomy();
      setAutonomy(data);
    } catch (e) {
      // ignore
    }
  };

  useEffectH(() => {
    load();
    const interval = setInterval(load, 6000);
    return () => clearInterval(interval);
  }, []);

  const isPaused = autonomy && autonomy.sweep_paused;
  const isActive = autonomy && autonomy.conductor_active;

  const handlePauseResume = async (e) => {
    e.stopPropagation();
    setBusy(true);
    try {
      if (isPaused) await store.resumeAutonomy();
      else await store.pauseAutonomy();
      await load();
    } finally { setBusy(false); }
  };

  const handleTick = async (e) => {
    e.stopPropagation();
    setBusy(true);
    try {
      await store.tickAutonomy();
      await load();
    } finally { setBusy(false); }
  };

  return (
    <Card style={{
      marginBottom: 18,
      borderColor: isPaused ? "var(--st-approval-dot)" : "color-mix(in srgb, var(--accent) 36%, var(--border))",
      background: isPaused
        ? "linear-gradient(135deg, var(--st-approval-bg), var(--surface))"
        : "linear-gradient(135deg, color-mix(in srgb, var(--accent-soft) 85%, var(--surface)), var(--surface))",
    }}>
      <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 1.3fr) auto", gap: 16, alignItems: "center" }}>
        <div>
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6 }}>
            <span style={{
              width: 9, height: 9, borderRadius: 99,
              background: !isActive ? "var(--text-3)" : isPaused ? "var(--st-approval-dot)" : "var(--st-done-dot)",
              animation: isActive && !isPaused ? "cadence-pulse 2s infinite" : "none",
            }}></span>
            <span style={{ fontSize: 11.5, fontWeight: 750, letterSpacing: "0.08em", textTransform: "uppercase", color: isPaused ? "var(--st-approval-fg)" : "var(--accent-text)" }}>
              {!isActive ? "Autonomy Standby" : isPaused ? "Autonomy In Charge: Paused" : "Autonomy In Charge: Active"}
            </span>
            {autonomy && autonomy.sweep_interval_sec ? (
              <span style={{ fontSize: 11, color: "var(--text-3)", background: "var(--surface)", border: "1px solid var(--border)", borderRadius: 6, padding: "1px 6px" }}>
                {autonomy.sweep_interval_sec}s cadence
              </span>
            ) : null}
          </div>
          <h3 style={{ margin: "0 0 4px", fontSize: 16, fontWeight: 700, letterSpacing: "-0.01em" }}>
            {isPaused ? "Autonomous Conductor is Paused" : "Autonomous Conductor Oversight"}
          </h3>
          <p style={{ margin: 0, color: "var(--text-2)", fontSize: 13, maxWidth: 640 }}>
            {isPaused
              ? "Background sweep execution is currently paused. Work queues will not advance automatically until resumed."
              : "The background sweep engine is actively evaluating jobs, dispatching worker packets, and monitoring autonomous score runs."}
          </p>
          <div style={{ display: "flex", gap: 14, marginTop: 8, fontSize: 12, color: "var(--text-3)" }}>
            <span>Live runs: <b style={{ color: "var(--text)" }}>{(autonomy && autonomy.live_runs_count) || 0}</b></span>
            <span>Active workers: <b style={{ color: "var(--text)" }}>{(autonomy && autonomy.active_workers && autonomy.active_workers.length) || 0}</b></span>
            {autonomy && autonomy.telemetry ? <span>Sweeps: <b style={{ color: "var(--text)" }}>{autonomy.telemetry.total_sweeps || 0}</b></span> : null}
          </div>
        </div>
        <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap", justifyContent: "flex-end" }}>
          <Btn small kind={isPaused ? "primary" : "default"} disabled={busy} onClick={handlePauseResume}>
            {isPaused ? "▶ Resume Autonomy" : "❚❚ Pause Autonomy"}
          </Btn>
          <Btn small disabled={busy} onClick={handleTick}>
            ⚡ Sweep Once
          </Btn>
          <Btn small kind="primary" onClick={() => onOpenLiveLook(autonomy)}>
            Live Look &amp; Status Window →
          </Btn>
        </div>
      </div>
    </Card>
  );
}

function Overview({ jobs, agents, tone, advanced, onNav, onOpen }) {
  const active = jobs.filter((j) => ["pending", "leased", "in_progress"].includes(j.status)).length;
  const gates = jobs.filter((j) => j.status === "needs_approval").length;
  const doneToday = jobs.filter((j) => j.status === "completed").length;
  const problems = jobs.filter((j) => ["failed"].includes(j.status)).length;
  const online = agents.filter((a) => a.status === "online").length;

  const [liveLookModalOpen, setLiveLookModalOpen] = useStateH(false);
  const [autonomyData, setAutonomyData] = useStateH(null);

  return (
    <div>
      <DemoLaunchPanel tone={tone} onNav={onNav} />
      <AutonomyControlCard tone={tone} onOpenLiveLook={(data) => { setAutonomyData(data); setLiveLookModalOpen(true); }} />

      <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 14, marginBottom: 22 }}>
        <StatCard label={tone === "plain" ? "Working now" : "Active jobs"} value={active} sub={tone === "plain" ? "jobs moving through the pipeline" : "pending · leased · in progress"} kind="active" onClick={() => onNav("jobs")} />
        <StatCard label={tone === "plain" ? "Needs your OK" : "Approval gates"} value={gates} sub={gates ? (tone === "plain" ? "paused until you decide" : "paused at needs_approval") : "all clear"} kind="approval" onClick={() => onNav("approvals")} />
        <StatCard label={tone === "plain" ? "Finished" : "Completed"} value={doneToday} sub="in the last 24 hours" kind="done" onClick={() => onNav("jobs")} />
        <StatCard label="Agents online" value={online + " / " + agents.length} sub={problems ? problems + (tone === "plain" ? " job needs attention" : " failed job") : "fleet healthy"} kind={problems ? "failed" : "done"} onClick={() => onNav("agents")} />
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 1.7fr) minmax(0, 1fr)", gap: 22, alignItems: "start" }}>
        <div>
          <SectionTitle action={<Btn small kind="ghost" onClick={() => onNav("workflows")}>Open builder →</Btn>}>{tone === "plain" ? "Running flows" : "Workflows in flight"}</SectionTitle>
          <WorkflowStrip jobs={jobs} tone={tone} onOpen={onOpen} />
          {gates > 0 ? (
            <div style={{ marginTop: 8 }}>
              <SectionTitle action={<Btn small kind="ghost" onClick={() => onNav("approvals")}>Review all →</Btn>}>{tone === "plain" ? "Waiting on you" : "Approval queue"}</SectionTitle>
              {jobs.filter((j) => j.status === "needs_approval").slice(0, 2).map((j) => (
                <Card key={j.id} style={{ marginBottom: 10, padding: 14 }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
                    <RoleChip role={j.target_agent_role} />
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ fontWeight: 600, fontSize: 13.5 }}>{j.title}</div>
                      <div style={{ fontSize: 12, color: "var(--text-3)" }}>from {j.source_agent_id} · {timeAgo(j.created_at)}</div>
                    </div>
                    <Btn kind="ok" small onClick={() => window.BitCadenceStore.approve(j.id, "joe-laptop")}>✓ Approve</Btn>
                    <Btn small onClick={() => onOpen(j.id)}>Review</Btn>
                  </div>
                </Card>
              ))}
            </div>
          ) : null}
        </div>
        <div>
          <SectionTitle>{tone === "plain" ? "What just happened" : "Live activity"}</SectionTitle>
          <ActivityFeed jobs={jobs} tone={tone} onOpen={onOpen} />
        </div>
      </div>

      <AutonomyLiveLookModal
        open={liveLookModalOpen}
        onClose={() => setLiveLookModalOpen(false)}
        autonomy={autonomyData}
        onRefresh={async () => {
          const d = await window.BitCadenceStore.getAutonomy();
          setAutonomyData(d);
        }}
        tone={tone}
        onOpenJob={onOpen}
      />
    </div>
  );
}

// ----- Approvals inbox -----
function Approvals({ jobs, tone, advanced, onOpen }) {
  const queue = jobs.filter((j) => j.status === "needs_approval");
  const decided = jobs.filter((j) => j.approved_by && j.status !== "needs_approval").slice(0, 6);
  const [sel, setSel] = useStateH(null);
  const [reason, setReason] = useStateH("");
  const [selectedIds, setSelectedIds] = useStateH(new Set());
  const [batchBusy, setBatchBusy] = useStateH(false);
  const selected = queue.find((j) => j.id === sel) || queue[0];

  const allSelected = queue.length > 0 && queue.every((j) => selectedIds.has(j.id));
  const toggleSelectAll = () => {
    if (allSelected) setSelectedIds(new Set());
    else setSelectedIds(new Set(queue.map((j) => j.id)));
  };

  const toggleSelect = (id, e) => {
    if (e) e.stopPropagation();
    const next = new Set(selectedIds);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    setSelectedIds(next);
  };

  const handleBatchApprove = async () => {
    const ids = Array.from(selectedIds);
    if (!ids.length) return;
    setBatchBusy(true);
    try {
      await window.BitCadenceStore.batchAction("approve", ids);
      setSelectedIds(new Set());
    } finally { setBatchBusy(false); }
  };

  const handleBatchReject = async () => {
    const ids = Array.from(selectedIds);
    if (!ids.length) return;
    const r = window.prompt("Reason for batch rejection:", reason || "");
    if (r === null) return;
    setBatchBusy(true);
    try {
      await window.BitCadenceStore.batchAction("reject", ids, { reason: r });
      setSelectedIds(new Set());
    } finally { setBatchBusy(false); }
  };

  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 16, marginBottom: 16, flexWrap: "wrap" }}>
        <p style={{ margin: 0, color: "var(--text-2)", fontSize: 13.5, maxWidth: 560 }}>
          {tone === "plain"
            ? "These jobs are paused and waiting for a person to say go. Nothing runs until you decide."
            : "Jobs flagged requires_approval pause at needs_approval until an approver role decides. Decisions are recorded in the immutable audit trail."}
        </p>
        {queue.length > 0 ? (
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <label style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 12.5, cursor: "pointer", fontWeight: 500 }}>
              <input type="checkbox" checked={allSelected} onChange={toggleSelectAll} style={{ cursor: "pointer" }} />
              Select all ({queue.length})
            </label>
            {selectedIds.size > 0 ? (
              <div style={{ display: "flex", gap: 6 }}>
                <Btn small kind="ok" disabled={batchBusy} onClick={handleBatchApprove}>
                  ✓ Approve ({selectedIds.size})
                </Btn>
                <Btn small kind="danger" disabled={batchBusy} onClick={handleBatchReject}>
                  Reject ({selectedIds.size})
                </Btn>
              </div>
            ) : null}
          </div>
        ) : null}
      </div>

      {queue.length === 0 ? (
        <Card><EmptyState icon="✓" title={tone === "plain" ? "Nothing needs your OK" : "Approval queue is empty"} body="New approval requests will appear here and notify you." /></Card>
      ) : (
        <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 1fr) minmax(0, 1.4fr)", gap: 18, alignItems: "start" }}>
          <Card pad={false}>
            {queue.map((j) => {
              const isSel = selected && selected.id === j.id;
              const isChecked = selectedIds.has(j.id);
              return (
                <button key={j.id} onClick={() => { setSel(j.id); setReason(""); }} style={{
                  display: "flex", width: "100%", textAlign: "left", gap: 10, alignItems: "center",
                  padding: "12px 14px", cursor: "pointer",
                  background: isSel ? "var(--accent-soft)" : isChecked ? "var(--surface-2)" : "transparent",
                  border: "none", borderBottom: "1px solid var(--border)",
                  borderLeftStyle: "solid", borderLeftWidth: 3, borderLeftColor: isSel ? "var(--accent)" : "transparent",
                }}>
                  <input type="checkbox" checked={isChecked} onChange={(e) => toggleSelect(j.id, e)} onClick={(e) => e.stopPropagation()} style={{ cursor: "pointer", flex: "none" }} />
                  <RoleChip role={j.target_agent_role} />
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ fontWeight: 600, fontSize: 13.5, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{j.title}</div>
                    <div style={{ fontSize: 12, color: "var(--text-3)" }}>from {j.source_agent_id} · {timeAgo(j.created_at)}</div>
                  </div>
                </button>
              );
            })}
          </Card>
          {selected ? (
            <Card>
              <div style={{ display: "flex", gap: 8, marginBottom: 10 }}>
                <StatusBadge status="needs_approval" tone={tone} />
                {selected.workflow ? <span style={{ fontSize: 12, color: "var(--text-3)", alignSelf: "center" }}>part of {selected.workflow}</span> : null}
              </div>
              <h2 style={{ margin: "0 0 6px", fontSize: 18, fontWeight: 650, letterSpacing: "-0.01em" }}>{selected.title}</h2>
              <p style={{ margin: "0 0 14px", color: "var(--text-2)", fontSize: 13.5 }}>{selected.description || "No further instructions were attached."}</p>
              <div style={{ display: "flex", gap: 20, fontSize: 13, color: "var(--text-2)", borderTop: "1px solid var(--border)", borderBottom: "1px solid var(--border)", padding: "10px 0", marginBottom: 16 }}>
                <span>Will run on: <b style={{ color: "var(--text)" }}>{selected.target_agent_role}</b></span>
                <span>Requested by: <Mono>{selected.source_agent_id}</Mono></span>
                {advanced ? <span>ID: <Mono>{shortId(selected.id)}</Mono></span> : null}
              </div>
              <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
                <Btn kind="primary" onClick={() => window.BitCadenceStore.approve(selected.id, "joe-laptop")}>✓ Approve &amp; run</Btn>
                <input value={reason} onChange={(e) => setReason(e.target.value)} placeholder={tone === "plain" ? "Reason for saying no…" : "Rejection reason (audited)…"} style={{ flex: 1, minWidth: 160, border: "1px solid var(--border-strong)", borderRadius: 8, padding: "8px 12px", fontSize: 13 }} />
                <Btn kind="danger" onClick={() => window.BitCadenceStore.reject(selected.id, "joe-laptop", reason)}>Reject</Btn>
                <Btn kind="ghost" onClick={() => onOpen(selected.id)}>Full details →</Btn>
              </div>
            </Card>
          ) : null}
        </div>
      )}

      {decided.length ? (
        <div style={{ marginTop: 24 }}>
          <SectionTitle>Recent decisions</SectionTitle>
          <Card pad={false}>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13.5 }}>
              <THead cols={["Job", "Decision", "Decided by", "When"]} />
              <tbody>
                {decided.map((j) => (
                  <tr key={j.id} onClick={() => onOpen(j.id)} style={{ cursor: "pointer" }}
                    onMouseEnter={(e) => e.currentTarget.style.background = "var(--surface-2)"}
                    onMouseLeave={(e) => e.currentTarget.style.background = "transparent"}>
                    <Td><span style={{ fontWeight: 600 }}>{j.title}</span></Td>
                    <Td><StatusBadge status={j.status === "rejected" ? "rejected" : "completed"} tone={tone} /></Td>
                    <Td><Mono>{j.approved_by}</Mono></Td>
                    <Td><span style={{ color: "var(--text-3)", fontSize: 12.5 }}>{timeAgo(j.updated_at)}</span></Td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>
        </div>
      ) : null}
    </div>
  );
}

function Governance({ jobs, tone, advanced, onOpen }) {
  const store = window.BitCadenceStore;
  const live = (store.mode ? store.mode() : "demo") === "live";
  const [events, setEvents] = useStateH([]);
  const [err, setErr] = useStateH(null);
  const [start, setStart] = useStateH("");
  const [end, setEnd] = useStateH("");
  const [exporting, setExporting] = useStateH(false);
  const [pack, setPack] = useStateH(null);
  const pending = jobs.filter((j) => j.status === "needs_approval");
  const inputStyle = { border: "1px solid var(--border-strong)", borderRadius: 8, padding: "7px 10px", fontSize: 13, background: "var(--surface)", color: "var(--text)" };

  async function loadEvents() {
    setErr(null);
    try {
      if (live && store.getRecentEvents) setEvents(await store.getRecentEvents({ limit: 150 }) || []);
      else setEvents(store.getAllEvents ? store.getAllEvents(150) : []);
    } catch (e) {
      setErr(e.message);
      setEvents([]);
    }
  }
  useEffectH(() => { loadEvents(); }, [live, jobs.length]);

  async function exportPack() {
    setExporting(true);
    setErr(null);
    try {
      const res = await store.exportEvidencePack({ start_date: start || null, end_date: end || null });
      setPack(res);
      const blob = new Blob([JSON.stringify(res, null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "bitcadence-evidence-pack.json";
      a.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (e) { setErr(e.message); }
    setExporting(false);
  }

  const decisions = (events || [])
    .filter((e) => e.event === "approved" || e.event === "rejected")
    .slice(0, 12);
  const approvalEvents = (events || [])
    .filter((e) => String(e.event || "").indexOf("approv") >= 0 || e.event === "rejected")
    .slice(0, 18);

  return (
    <div>
      <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 1.25fr) minmax(320px, .75fr)", gap: 18, alignItems: "start" }}>
        <div>
          <SectionTitle action={<Btn small kind="ghost" onClick={loadEvents}>Refresh</Btn>}>
            {tone === "plain" ? "Pending approvals" : "Human oversight queue"}
          </SectionTitle>
          <Card pad={false} style={{ marginBottom: 18 }}>
            {pending.length ? (
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13.5 }}>
                <THead cols={["Job", "Target", "Requested", ""]} />
                <tbody>
                  {pending.map((j) => (
                    <tr key={j.id}>
                      <Td><span style={{ fontWeight: 600 }}>{j.title}</span><br /><span style={{ color: "var(--text-3)", fontSize: 12 }}>{shortId(j.id)}</span></Td>
                      <Td><RoleChip role={j.target_agent_role} /></Td>
                      <Td><span style={{ color: "var(--text-3)", fontSize: 12.5 }}>{timeAgo(j.created_at)}</span></Td>
                      <Td><Btn small onClick={() => onOpen(j.id)}>Review</Btn></Td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <EmptyState icon="OK" title={tone === "plain" ? "No approvals waiting" : "No pending approval gates"} body="New human oversight gates appear here as jobs pause." />
            )}
          </Card>

          <SectionTitle>{tone === "plain" ? "Decision history" : "Approval decision history"}</SectionTitle>
          <Card pad={false}>
            {decisions.length ? (
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13.5 }}>
                <THead cols={advanced ? ["Decision", "Job", "Actor", "When", "Detail"] : ["Decision", "Job", "Actor", "When"]} />
                <tbody>
                  {decisions.map((e) => (
                    <tr key={e.id || e.job_id + e.created_at}>
                      <Td><span style={{ fontWeight: 700, color: e.event === "approved" ? "var(--st-done-fg)" : "var(--st-failed-fg)" }}>{eventLabel(e.event, tone)}</span></Td>
                      <Td>{e.job_title || shortId(e.job_id)}</Td>
                      <Td><Mono>{e.actor_id || "-"}</Mono></Td>
                      <Td><span style={{ color: "var(--text-3)", fontSize: 12.5 }}>{timeAgo(e.created_at)}</span></Td>
                      {advanced ? <Td><span style={{ color: "var(--text-3)", fontSize: 12 }}>{JSON.stringify(e.detail || {})}</span></Td> : null}
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <EmptyState icon="--" title={tone === "plain" ? "No decisions in the feed" : "No approved/rejected events loaded"} body="Approval decisions will appear once a human acts on a gate." />
            )}
          </Card>
        </div>

        <div>
          <SectionTitle>Evidence export</SectionTitle>
          <Card style={{ marginBottom: 18 }}>
            <div style={{ fontSize: 13.5, color: "var(--text-2)", marginBottom: 14 }}>
              PDF cover page plus JSON audit trail for EU AI Act Art. 12 record-keeping and Art. 14 human oversight.
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10, marginBottom: 12 }}>
              <label style={{ fontSize: 12, color: "var(--text-3)" }}>Start<br /><input type="date" value={start} onChange={(e) => setStart(e.target.value)} style={Object.assign({}, inputStyle, { width: "100%" })} /></label>
              <label style={{ fontSize: 12, color: "var(--text-3)" }}>End<br /><input type="date" value={end} onChange={(e) => setEnd(e.target.value)} style={Object.assign({}, inputStyle, { width: "100%" })} /></label>
            </div>
            <Btn kind="primary" disabled={exporting} onClick={exportPack}>
              {exporting ? "Exporting..." : "Export compliance evidence pack"}
            </Btn>
            {pack ? (
              <div style={{ marginTop: 14, fontSize: 12.5, color: "var(--text-2)" }}>
                Exported {pack.summary.audit_events} audit events, {pack.summary.pending_approvals} pending approvals, {pack.summary.decisions} decisions.
                <div style={{ marginTop: 8, display: "flex", gap: 6, flexWrap: "wrap" }}>
                  {(pack.files || []).map((f) => <span key={f.filename} style={{ fontFamily: "var(--font-mono)", color: "var(--text-3)", background: "var(--surface-2)", padding: "2px 7px", borderRadius: 6 }}>{f.filename}</span>)}
                </div>
              </div>
            ) : null}
            {err ? <div style={{ marginTop: 12, color: "var(--st-failed-fg)", fontSize: 12.5 }}>{err}</div> : null}
          </Card>

          <SectionTitle>{tone === "plain" ? "Oversight trail" : "Approval-related events"}</SectionTitle>
          <Card pad={false}>
            {approvalEvents.length ? approvalEvents.map((e, i) => (
              <button key={e.id || i} onClick={() => e.job_id && onOpen(e.job_id)} style={{
                display: "flex", width: "100%", textAlign: "left", gap: 10, alignItems: "center",
                padding: "10px 14px", border: "none", borderBottom: i < approvalEvents.length - 1 ? "1px solid var(--border)" : "none",
                background: "transparent", cursor: "pointer", fontSize: 13,
              }}>
                <span style={{ width: 8, height: 8, borderRadius: 99, background: `var(--st-${eventKindH(e.event)}-dot)`, flex: "none" }}></span>
                <span style={{ flex: 1, minWidth: 0 }}>{eventLabel(e.event, tone)}<br /><span style={{ color: "var(--text-3)", fontSize: 12 }}>{e.job_title || shortId(e.job_id)}</span></span>
                <span style={{ color: "var(--text-3)", fontSize: 11.5 }}>{timeAgo(e.created_at)}</span>
              </button>
            )) : <EmptyState icon="--" title="No oversight events loaded" body="Refresh after approvals, rejections, or demo activity." />}
          </Card>
        </div>
      </div>
    </div>
  );
}

// ----- Drumline shared memory -----
// Browse/search the collective agent memory (/api/context) and add entries.
// Recall scoring happens server-side; this screen just asks and renders.

const KIND_STYLE = {
  fact:     { label: "Fact",     hue: 200 },
  decision: { label: "Decision", hue: 270 },
  lesson:   { label: "Lesson",   hue: 22 },
  handoff:  { label: "Handoff",  hue: 150 },
  artifact: { label: "Artifact", hue: 0 },
};

function KindChip({ kind }) {
  const k = KIND_STYLE[kind] || KIND_STYLE.fact;
  return (
    <span style={{
      fontSize: 10.5, fontWeight: 700, letterSpacing: "0.04em", textTransform: "uppercase",
      color: `oklch(0.45 0.09 ${k.hue})`, background: `oklch(0.95 0.03 ${k.hue})`,
      padding: "2px 8px", borderRadius: 999, flex: "none",
    }}>{k.label}</span>
  );
}

function MemoryDetailDrawer({ entry, onClose, onOpenJob, tone, advanced }) {
  if (!entry) return null;
  const [copied, setCopied] = useStateH(false);

  const copyContent = () => {
    navigator.clipboard.writeText(entry.content || "").then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1600);
    });
  };

  return (
    <div style={{ position: "fixed", inset: 0, zIndex: 65 }}>
      <div onClick={onClose} style={{ position: "absolute", inset: 0, background: "rgba(23,23,28,0.38)", backdropFilter: "blur(2px)" }}></div>
      <div style={{
        position: "absolute", top: 10, right: 10, bottom: 10, width: 620, maxWidth: "calc(100vw - 30px)",
        background: "var(--surface)", border: "1px solid var(--border)", borderRadius: "var(--radius-l)",
        boxShadow: "0 18px 48px rgba(0,0,0,0.3)", display: "flex", flexDirection: "column", overflow: "hidden", zIndex: 66,
      }}>
        {/* Header */}
        <div style={{ padding: "18px 22px 14px", borderBottom: "1px solid var(--border)" }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 12 }}>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 8, flexWrap: "wrap" }}>
                <KindChip kind={entry.kind} />
                {entry.role ? <RoleChip role={entry.role} size={18} /> : null}
                <span style={{ fontSize: 11.5, color: "var(--text-3)" }}>{timeAgo(entry.created_at)}</span>
              </div>
              <h2 style={{ margin: 0, fontSize: 18, fontWeight: 700, letterSpacing: "-0.01em", wordBreak: "break-word" }}>
                {entry.title}
              </h2>
            </div>
            <Btn kind="ghost" small onClick={onClose} style={{ fontSize: 18, lineHeight: 1 }}>×</Btn>
          </div>
        </div>

        {/* Content body */}
        <div style={{ flex: 1, overflowY: "auto", padding: "18px 22px" }}>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 8 }}>
            <span style={{ fontSize: 12, fontWeight: 600, color: "var(--text-3)", textTransform: "uppercase", letterSpacing: "0.04em" }}>
              Full Memory Content
            </span>
            <Btn small onClick={copyContent}>{copied ? "✓ Copied" : "Copy text"}</Btn>
          </div>

          <div style={{
            background: "var(--surface-2)", border: "1px solid var(--border)", borderRadius: "var(--radius-m)",
            padding: "16px 18px", fontSize: 13.5, lineHeight: 1.6, color: "var(--text)",
            whiteSpace: "pre-wrap", wordBreak: "break-word", fontFamily: "inherit",
            marginBottom: 20, userSelect: "text",
          }}>
            {entry.content}
          </div>

          <SectionTitle>Metadata &amp; Provenance</SectionTitle>
          <div style={{ display: "grid", gridTemplateColumns: "130px 1fr", gap: 10, fontSize: 13, borderBottom: "1px solid var(--border)", paddingBottom: 14, marginBottom: 14 }}>
            <span style={{ color: "var(--text-3)" }}>Author / Agent</span>
            <Mono>{entry.created_by || "system"}</Mono>

            <span style={{ color: "var(--text-3)" }}>Created</span>
            <span>{entry.created_at ? new Date(entry.created_at).toLocaleString() : "—"}</span>

            <span style={{ color: "var(--text-3)" }}>Tags</span>
            <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
              {(entry.tags || []).length ? (entry.tags || []).map((t) => (
                <span key={t} style={{ fontSize: 11, fontFamily: "var(--font-mono)", color: "var(--text-2)", background: "var(--surface-2)", border: "1px solid var(--border)", padding: "2px 8px", borderRadius: 6 }}>
                  {t}
                </span>
              )) : <span style={{ color: "var(--text-3)" }}>None</span>}
            </div>

            {entry.source_job_id ? (
              <React.Fragment>
                <span style={{ color: "var(--text-3)" }}>Source Job</span>
                <div>
                  <Mono style={{ fontSize: 12 }}>{entry.source_job_id}</Mono>
                  {onOpenJob ? (
                    <div style={{ marginTop: 6 }}>
                      <Btn small onClick={() => { onClose(); onOpenJob(entry.source_job_id); }}>
                        Open Source Job ({shortId(entry.source_job_id)}) →
                      </Btn>
                    </div>
                  ) : null}
                </div>
              </React.Fragment>
            ) : null}

            {advanced && entry.weight !== undefined ? (
              <React.Fragment>
                <span style={{ color: "var(--text-3)" }}>Recall Weight</span>
                <span>{entry.weight}</span>
              </React.Fragment>
            ) : null}

            {entry.org_id ? (
              <React.Fragment>
                <span style={{ color: "var(--text-3)" }}>Org / Tenant</span>
                <Mono>{entry.org_id}</Mono>
              </React.Fragment>
            ) : null}
          </div>
        </div>
      </div>
    </div>
  );
}

function DrumlineMemory({ tone, advanced, onOpen }) {
  const store = window.BitCadenceStore;
  const live = (store.mode ? store.mode() : "demo") === "live";
  const [query, setQuery] = useStateH("");
  const [tags, setTags] = useStateH("");
  const [entries, setEntries] = useStateH(null); // null = not loaded yet
  const [err, setErr] = useStateH(null);
  const [composing, setComposing] = useStateH(false);
  const [form, setForm] = useStateH({ title: "", content: "", kind: "fact", tags: "" });
  const [saving, setSaving] = useStateH(false);
  const [saveErr, setSaveErr] = useStateH(null);
  const [selectedMemory, setSelectedMemory] = useStateH(null);
  const [expandedIds, setExpandedIds] = useStateH(new Set());
  const inputStyle = { border: "1px solid var(--border-strong)", borderRadius: 8, padding: "8px 12px", fontSize: 13, background: "var(--surface)", color: "var(--text)" };

  async function load(q, tg) {
    setErr(null);
    try {
      const r = await store.getContext({ query: q !== undefined ? q : query, tags: tg !== undefined ? tg : tags, limit: 25 });
      setEntries(r || []);
    } catch (e) { setErr(e.message); setEntries([]); }
  }
  useEffectH(() => { if (live) load("", ""); }, [live]);

  if (!live) {
    return (
      <Card>
        <EmptyState icon="◎" title={tone === "plain" ? "Shared memory lives on your server" : "Drumline requires a live gateway"}
          body={tone === "plain"
            ? "Connect to your orchestrator in Settings to browse what your agents remember and teach them new facts."
            : "Connect in Settings to query /api/context — recall scoring, tag filters, and writes all run server-side."} />
      </Card>
    );
  }

  async function save() {
    if (!form.title.trim() || !form.content.trim()) { setSaveErr("Title and content are required."); return; }
    setSaving(true); setSaveErr(null);
    try {
      await store.addContext({
        title: form.title.trim(), content: form.content.trim(), kind: form.kind,
        tags: form.tags.split(",").map((t) => t.trim()).filter(Boolean),
      });
      setForm({ title: "", content: "", kind: "fact", tags: "" });
      setComposing(false);
      await load();
    } catch (e) { setSaveErr(e.message); }
    setSaving(false);
  }

  return (
    <div>
      <p style={{ margin: "0 0 16px", color: "var(--text-2)", fontSize: 13.5, maxWidth: 560 }}>
        {tone === "plain"
          ? "Everything your agents chose to remember — decisions, lessons, handoffs. Anything you add here is recalled by every agent on its next job. Click any memory entry to drill down into the full context."
          : "The Drumline shared context (agent_context): explicit writes via mco_remember plus auto-distilled job handoffs. Entries below are ranked by the server's recall scorer. Click any entry to inspect provenance and full text."}
      </p>

      <form onSubmit={(e) => { e.preventDefault(); setEntries(null); load(); }}
        style={{ display: "flex", gap: 10, marginBottom: 16, flexWrap: "wrap", alignItems: "center" }}>
        <input value={query} onChange={(e) => setQuery(e.target.value)}
          placeholder={tone === "plain" ? "Search memory…" : "Recall query (soft-scored)…"}
          style={Object.assign({}, inputStyle, { flex: 1, minWidth: 220 })} />
        <input value={tags} onChange={(e) => setTags(e.target.value)}
          placeholder={tone === "plain" ? "tags, comma-separated" : "tag filter (hard)"}
          style={Object.assign({}, inputStyle, { width: 180, fontFamily: "var(--font-mono)", fontSize: 12.5 })} />
        <Btn small onClick={() => { setEntries(null); load(); }}>{tone === "plain" ? "Search" : "Recall"}</Btn>
        <span style={{ flex: 1 }}></span>
        <Btn small kind="primary" onClick={() => { setComposing(!composing); setSaveErr(null); }}>
          {composing ? "Cancel" : (tone === "plain" ? "+ Add memory" : "+ Remember")}
        </Btn>
      </form>

      {composing ? (
        <Card style={{ marginBottom: 16 }}>
          <SectionTitle>{tone === "plain" ? "Teach your agents something" : "New context entry"}</SectionTitle>
          <div style={{ display: "flex", gap: 10, margin: "12px 0 10px", flexWrap: "wrap" }}>
            <input value={form.title} onChange={(e) => setForm(Object.assign({}, form, { title: e.target.value }))}
              placeholder="Title" style={Object.assign({}, inputStyle, { flex: 2, minWidth: 220 })} />
            <select value={form.kind} onChange={(e) => setForm(Object.assign({}, form, { kind: e.target.value }))}
              style={Object.assign({}, inputStyle, { width: 130 })}>
              {Object.keys(KIND_STYLE).map((k) => <option key={k} value={k}>{KIND_STYLE[k].label}</option>)}
            </select>
            <input value={form.tags} onChange={(e) => setForm(Object.assign({}, form, { tags: e.target.value }))}
              placeholder="tags, comma-separated" style={Object.assign({}, inputStyle, { width: 200, fontFamily: "var(--font-mono)", fontSize: 12.5 })} />
          </div>
          <textarea value={form.content} onChange={(e) => setForm(Object.assign({}, form, { content: e.target.value }))}
            placeholder={tone === "plain" ? "What should every agent know?" : "Content (sanitized + capped server-side; recalled into agent prompts)"}
            rows={4} style={Object.assign({}, inputStyle, { width: "100%", boxSizing: "border-box", resize: "vertical", fontFamily: "inherit" })} />
          <div style={{ display: "flex", alignItems: "center", gap: 12, paddingTop: 12 }}>
            <Btn kind="primary" disabled={saving} onClick={save}>{saving ? "Saving…" : (tone === "plain" ? "Save memory" : "Remember")}</Btn>
            {saveErr ? <span style={{ fontSize: 12.5, color: "var(--st-failed-fg)" }}>{saveErr}</span> : null}
          </div>
        </Card>
      ) : null}

      {err ? <Card style={{ marginBottom: 16 }}><div style={{ fontSize: 12.5, color: "var(--st-failed-fg)" }}>{err}</div></Card> : null}
      {entries === null && !err ? <p style={{ fontSize: 12.5, color: "var(--text-3)" }}>Loading…</p> : null}

      {entries && entries.length === 0 && !err ? (
        <Card><EmptyState icon="◎" title={tone === "plain" ? "Nothing remembered yet" : "No matching context"}
          body={tone === "plain"
            ? "As agents finish jobs, what they learned lands here automatically. Or add the first memory yourself."
            : "Completed jobs auto-distill into handoffs; mco remember / the composer above write entries directly."} /></Card>
      ) : null}

      {entries && entries.length > 0 ? (
        <Card pad={false}>
          {entries.map((e, i) => {
            const eid = e.id || String(i);
            const isExpanded = expandedIds.has(eid);
            const toggleExpand = (ev) => {
              ev.stopPropagation();
              const next = new Set(expandedIds);
              if (next.has(eid)) next.delete(eid);
              else next.add(eid);
              setExpandedIds(next);
            };
            return (
              <div key={eid} onClick={() => setSelectedMemory(e)} style={{
                padding: "12px 16px", borderBottom: i < entries.length - 1 ? "1px solid var(--border)" : "none",
                cursor: "pointer", transition: "background .12s",
              }}
                onMouseEnter={(ev) => ev.currentTarget.style.background = "var(--surface-2)"}
                onMouseLeave={(ev) => ev.currentTarget.style.background = "transparent"}>
                <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                  <KindChip kind={e.kind} />
                  <span style={{ fontWeight: 600, fontSize: 13.5, flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{e.title}</span>
                  {e.role ? <RoleChip role={e.role} size={18} /> : null}
                  <span style={{ fontSize: 11.5, color: "var(--text-3)", flex: "none" }}>{timeAgo(e.created_at)}</span>
                </div>
                <div style={Object.assign({
                  fontSize: 12.5, color: "var(--text-2)", marginTop: 5, whiteSpace: "pre-wrap", overflow: "hidden",
                }, isExpanded ? { display: "block" } : { display: "-webkit-box", WebkitLineClamp: 3, WebkitBoxOrient: "vertical" })}>
                  {e.content}
                </div>
                <div style={{ display: "flex", alignItems: "center", gap: 10, marginTop: 6, flexWrap: "wrap" }}>
                  {(e.tags || []).map((t) => (
                    <span key={t} style={{ fontSize: 10.5, fontFamily: "var(--font-mono)", color: "var(--text-3)", background: "var(--surface-2)", padding: "1px 7px", borderRadius: 6 }}>{t}</span>
                  ))}
                  <button onClick={toggleExpand} style={{ background: "none", border: "none", color: "var(--accent-text)", fontSize: 11.5, fontWeight: 600, padding: "2px 0", cursor: "pointer" }}>
                    {isExpanded ? "▲ Collapse" : "▼ Show more"}
                  </button>
                  <button onClick={(ev) => { ev.stopPropagation(); setSelectedMemory(e); }} style={{ background: "none", border: "none", color: "var(--accent-text)", fontSize: 11.5, fontWeight: 600, padding: "2px 0", cursor: "pointer" }}>
                    Drill down →
                  </button>
                  <span style={{ flex: 1 }}></span>
                  <span style={{ fontSize: 11, color: "var(--text-3)" }}>
                    by <Mono style={{ fontSize: 11 }}>{e.created_by || "system"}</Mono>
                    {e.source_job_id ? <span> · from job <Mono style={{ fontSize: 11 }}>{shortId(e.source_job_id)}</Mono></span> : null}
                    {advanced && e.weight !== undefined ? <span> · w={e.weight}</span> : null}
                  </span>
                </div>
              </div>
            );
          })}
        </Card>
      ) : null}

      <MemoryDetailDrawer
        entry={selectedMemory}
        onClose={() => setSelectedMemory(null)}
        onOpenJob={onOpen}
        tone={tone}
        advanced={advanced}
      />
    </div>
  );
}

Object.assign(window, { Overview, Approvals, Governance, StatCard, DrumlineMemory, KindChip, MemoryDetailDrawer, AutonomyControlCard, AutonomyLiveLookModal });
