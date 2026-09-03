// BitCadence — Job Board screen + Job detail drawer + New Job composer.
const { useState: useStateJ, useMemo: useMemoJ, useEffect: useEffectJ } = React;

const JOB_FILTERS = [
  { id: "all", label: "All" },
  { id: "active", label: "Active", match: ["pending", "leased", "in_progress"] },
  { id: "needs_approval", label: "Needs approval", match: ["needs_approval"] },
  { id: "waiting", label: "Waiting", match: ["waiting"] },
  { id: "done", label: "Done", match: ["completed"] },
  { id: "problems", label: "Problems", match: ["failed", "rejected"] },
];

function JobBoard({ jobs, tone, advanced, onOpen, onCompose }) {
  const [filter, setFilter] = useStateJ("all");
  const [query, setQuery] = useStateJ("");

  const visible = useMemoJ(() => {
    const f = JOB_FILTERS.find((x) => x.id === filter);
    let list = jobs;
    if (f && f.match) list = list.filter((j) => f.match.includes(j.status));
    if (query) {
      const q = query.toLowerCase();
      list = list.filter((j) => (j.title + " " + j.target_agent_role + " " + (j.workflow || "")).toLowerCase().includes(q));
    }
    return list;
  }, [jobs, filter, query]);

  const counts = useMemoJ(() => {
    const c = {};
    JOB_FILTERS.forEach((f) => { c[f.id] = f.match ? jobs.filter((j) => f.match.includes(j.status)).length : jobs.length; });
    return c;
  }, [jobs]);

  return (
    <div>
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 14, flexWrap: "wrap" }}>
        <div style={{ display: "flex", gap: 2, background: "var(--surface)", border: "1px solid var(--border)", borderRadius: 8, padding: 3 }}>
          {JOB_FILTERS.map((f) => (
            <button key={f.id} onClick={() => setFilter(f.id)} style={{
              border: "none", cursor: "pointer", borderRadius: 6, padding: "5px 11px",
              fontSize: 12.5, fontWeight: 600,
              background: filter === f.id ? "var(--accent-soft)" : "transparent",
              color: filter === f.id ? "var(--accent-text)" : "var(--text-2)",
            }}>
              {f.label}
              <span style={{ marginLeft: 5, fontSize: 11, opacity: 0.65 }}>{counts[f.id]}</span>
            </button>
          ))}
        </div>
        <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search jobs…" style={{
          border: "1px solid var(--border)", borderRadius: 8, padding: "7px 12px", fontSize: 13,
          background: "var(--surface)", color: "var(--text)", width: 200, outline: "none",
        }} />
        <div style={{ flex: 1 }}></div>
        <Btn kind="primary" onClick={onCompose}>+ New job</Btn>
      </div>

      <Card pad={false}>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13.5 }}>
          <THead cols={["Job", "Status", "Assigned to", "From", advanced ? "Retries" : "Workflow", "Updated"]} />
          <tbody>
            {visible.length === 0 ? (
              <tr><Td style={{ borderBottom: "none" }} ><EmptyState icon="○" title="No jobs here" body="Try another filter, or create a new job." /></Td></tr>
            ) : visible.map((j) => (
              <tr key={j.id} onClick={() => onOpen(j.id)} style={{ cursor: "pointer" }}
                onMouseEnter={(e) => e.currentTarget.style.background = "var(--surface-2)"}
                onMouseLeave={(e) => e.currentTarget.style.background = "transparent"}>
                <Td>
                  <div style={{ fontWeight: 600 }}>{j.title}</div>
                  <div style={{ fontSize: 11.5, color: "var(--text-3)", marginTop: 1 }}>
                    <Mono style={{ fontSize: 11 }}>{shortId(j.id)}</Mono>
                    {j.workflow ? <span> · {j.workflow}</span> : null}
                  </div>
                </Td>
                <Td><StatusBadge status={j.status} tone={tone} /></Td>
                <Td>
                  <span style={{ display: "inline-flex", alignItems: "center", gap: 7 }}>
                    <RoleChip role={j.target_agent_role} size={20} />
                    <span>{j.leased_by_instance_id || j.target_agent_role}</span>
                  </span>
                </Td>
                <Td><span style={{ color: "var(--text-2)" }}>{j.source_agent_id}</span></Td>
                <Td>
                  {advanced
                    ? <span style={{ color: "var(--text-2)" }}>{j.max_retries ? `${j.retry_count}/${j.max_retries}` : "—"}{j.escalate_to_role ? ` → ${j.escalate_to_role}` : ""}</span>
                    : <span style={{ color: "var(--text-2)" }}>{j.workflow || "—"}</span>}
                </Td>
                <Td><span style={{ color: "var(--text-3)", fontSize: 12.5, whiteSpace: "nowrap" }}>{timeAgo(j.updated_at)}</span></Td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
    </div>
  );
}

// ----- Job detail drawer -----
function MetaRow({ label, children }) {
  return (
    <div style={{ display: "grid", gridTemplateColumns: "120px 1fr", gap: 8, padding: "6px 0", fontSize: 13 }}>
      <span style={{ color: "var(--text-3)" }}>{label}</span>
      <span style={{ minWidth: 0 }}>{children}</span>
    </div>
  );
}

function JobDetail({ jobId, jobs, tone, advanced, onClose, onOpen }) {
  const j = jobs.find((x) => x.id === jobId);
  const [reason, setReason] = useStateJ("");
  const [rejecting, setRejecting] = useStateJ(false);
  const [cancelling, setCancelling] = useStateJ(false);
  const [cancelReason, setCancelReason] = useStateJ("");
  const [reassigning, setReassigning] = useStateJ(false);
  const [toRole, setToRole] = useStateJ("");
  if (!j) return null;
  // Cancel and reassign apply to anything the board has not finished with.
  // Terminal jobs are history and must not offer actions that would 409.
  const TERMINAL = ["completed", "failed", "rejected", "cancelled"];
  const live = TERMINAL.indexOf(j.status) < 0;
  const roles = Array.from(new Set((window.BitCadenceStore.getAgents() || []).map((a) => a.role))).sort();
  const deps = (j.depends_on || []).map((d) => jobs.find((x) => x.id === d)).filter(Boolean);
  const dependents = jobs.filter((x) => (x.depends_on || []).includes(j.id));

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%" }}>
      <div style={{ padding: "18px 22px 14px", borderBottom: "1px solid var(--border)" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 12 }}>
          <div>
            <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 8 }}>
              <StatusBadge status={j.status} tone={tone} />
              {j.requires_approval ? <span style={{ fontSize: 11.5, fontWeight: 600, color: "var(--st-approval-fg)", background: "var(--st-approval-bg)", borderRadius: 999, padding: "3px 9px" }}>Approval gate</span> : null}
            </div>
            <h2 style={{ margin: 0, fontSize: 17, fontWeight: 650, letterSpacing: "-0.01em" }}>{j.title}</h2>
          </div>
          <Btn kind="ghost" small onClick={onClose} style={{ fontSize: 16, lineHeight: 1 }}>×</Btn>
        </div>
      </div>

      <div style={{ flex: 1, overflowY: "auto", padding: "16px 22px" }}>
        {j.status === "needs_approval" ? (
          <div style={{ background: "var(--st-approval-bg)", border: "1px solid var(--st-approval-dot)", borderRadius: "var(--radius-m)", padding: 14, marginBottom: 16 }}>
            <div style={{ fontWeight: 600, color: "var(--st-approval-fg)", marginBottom: 8 }}>
              {tone === "plain" ? "This job is waiting for your decision." : "Paused at human-in-the-loop approval gate."}
            </div>
            {!rejecting ? (
              <div style={{ display: "flex", gap: 8 }}>
                <Btn kind="ok" small onClick={() => window.BitCadenceStore.approve(j.id, "joe-laptop")}>✓ Approve &amp; run</Btn>
                <Btn kind="danger" small onClick={() => setRejecting(true)}>Reject…</Btn>
              </div>
            ) : (
              <div style={{ display: "flex", gap: 8 }}>
                <input autoFocus value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Why? (recorded in the audit trail)" style={{ flex: 1, border: "1px solid var(--border-strong)", borderRadius: 6, padding: "5px 10px", fontSize: 12.5 }} />
                <Btn kind="danger" small onClick={() => { window.BitCadenceStore.reject(j.id, "joe-laptop", reason); setRejecting(false); }}>Reject</Btn>
                <Btn kind="ghost" small onClick={() => setRejecting(false)}>Cancel</Btn>
              </div>
            )}
          </div>
        ) : null}

        {j.status === "failed" ? (
          <div style={{ background: "var(--st-failed-bg)", border: "1px solid var(--st-failed-dot)", borderRadius: "var(--radius-m)", padding: 14, marginBottom: 16 }}>
            <div style={{ fontWeight: 600, color: "var(--st-failed-fg)", marginBottom: 4 }}>{tone === "plain" ? "This job hit a problem." : "Execution failed."}</div>
            <div style={{ fontSize: 12.5, color: "var(--st-failed-fg)", marginBottom: 10 }}>{j.error_message}</div>
            <Btn small onClick={() => window.BitCadenceStore.retryNow(j.id)}>↻ Try again</Btn>
          </div>
        ) : null}

        {live ? (
          <div style={{ border: "1px solid var(--border)", borderRadius: "var(--radius-m)", padding: 14, marginBottom: 16 }}>
            <div style={{ fontWeight: 600, marginBottom: 8, fontSize: 13 }}>
              {tone === "plain" ? "Change this job" : "Job control"}
            </div>

            {!cancelling && !reassigning ? (
              <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                <Btn small onClick={() => setReassigning(true)}>&#8644; Reassign&#8230;</Btn>
                <Btn kind="danger" small onClick={() => setCancelling(true)}>&#10005; Call it off&#8230;</Btn>
              </div>
            ) : null}

            {reassigning ? (
              <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
                <select value={toRole} onChange={(e) => setToRole(e.target.value)}
                        style={{ border: "1px solid var(--border-strong)", borderRadius: 6, padding: "5px 10px", fontSize: 12.5 }}>
                  <option value="">Send to role&#8230;</option>
                  {roles.map((r) => <option key={r} value={r}>{r}</option>)}
                </select>
                <Btn kind="primary" small disabled={!toRole}
                     onClick={() => { window.BitCadenceStore.reassignJob(j.id, toRole); setReassigning(false); setToRole(""); }}>Reassign</Btn>
                <Btn kind="ghost" small onClick={() => { setReassigning(false); setToRole(""); }}>Back</Btn>
                <span style={{ fontSize: 12, color: "var(--text-3)" }}>
                  {tone === "plain" ? "currently waiting on " : "target_agent_role: "}<Mono>{j.target_agent_role}</Mono>
                </span>
              </div>
            ) : null}

            {cancelling ? (
              <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
                <input autoFocus value={cancelReason} onChange={(e) => setCancelReason(e.target.value)}
                       placeholder="Why? (recorded in the audit trail)"
                       style={{ flex: 1, minWidth: 200, border: "1px solid var(--border-strong)", borderRadius: 6, padding: "5px 10px", fontSize: 12.5 }} />
                <Btn kind="danger" small
                     onClick={() => { window.BitCadenceStore.cancelJob(j.id, cancelReason); setCancelling(false); setCancelReason(""); }}>Call it off</Btn>
                <Btn kind="ghost" small onClick={() => { setCancelling(false); setCancelReason(""); }}>Back</Btn>
              </div>
            ) : null}
          </div>
        ) : null}

        {j.description ? <p style={{ margin: "0 0 14px", color: "var(--text-2)", fontSize: 13.5 }}>{j.description}</p> : null}

        <div style={{ borderTop: "1px solid var(--border)", paddingTop: 8 }}>
          <MetaRow label="Assigned to"><span style={{ display: "inline-flex", gap: 7, alignItems: "center" }}><RoleChip role={j.target_agent_role} size={18} />{j.leased_by_instance_id || (tone === "plain" ? "any " + j.target_agent_role + " agent" : j.target_agent_role + " (role)")}</span></MetaRow>
          <MetaRow label="Requested by"><Mono>{j.source_agent_id}</Mono></MetaRow>
          <MetaRow label="Created">{timeAgo(j.created_at)}</MetaRow>
          {j.workflow ? <MetaRow label="Workflow">{j.workflow}</MetaRow> : null}
          {j.approved_by ? <MetaRow label="Decided by"><Mono>{j.approved_by}</Mono></MetaRow> : null}
          {advanced ? <MetaRow label="Job ID"><Mono style={{ fontSize: 11.5 }}>{j.id}</Mono></MetaRow> : null}
          {advanced && j.max_retries ? <MetaRow label="Retry budget">{j.retry_count} of {j.max_retries} used{j.escalate_to_role ? <span style={{ color: "var(--text-3)" }}> · escalates to {j.escalate_to_role}</span> : null}</MetaRow> : null}
        </div>

        {deps.length || dependents.length ? (
          <div style={{ marginTop: 14 }}>
            <SectionTitle>{tone === "plain" ? "Connected steps" : "Dependencies"}</SectionTitle>
            {deps.map((d) => (
              <button key={d.id} onClick={() => onOpen(d.id)} style={{ display: "flex", width: "100%", textAlign: "left", alignItems: "center", gap: 8, background: "var(--surface-2)", border: "1px solid var(--border)", borderRadius: 8, padding: "7px 10px", marginBottom: 6, cursor: "pointer", fontSize: 13 }}>
                <span style={{ color: "var(--text-3)", fontSize: 11 }}>↑ after</span>
                <span style={{ fontWeight: 600, flex: 1 }}>{d.title}</span>
                <StatusBadge status={d.status} tone={tone} />
              </button>
            ))}
            {dependents.map((d) => (
              <button key={d.id} onClick={() => onOpen(d.id)} style={{ display: "flex", width: "100%", textAlign: "left", alignItems: "center", gap: 8, background: "var(--surface-2)", border: "1px solid var(--border)", borderRadius: 8, padding: "7px 10px", marginBottom: 6, cursor: "pointer", fontSize: 13 }}>
                <span style={{ color: "var(--text-3)", fontSize: 11 }}>↓ then</span>
                <span style={{ fontWeight: 600, flex: 1 }}>{d.title}</span>
                <StatusBadge status={d.status} tone={tone} />
              </button>
            ))}
          </div>
        ) : null}

        {advanced && (j.output_payload || Object.keys(j.input_payload || {}).length) ? (
          <div style={{ marginTop: 14 }}>
            <SectionTitle>Payloads</SectionTitle>
            {Object.keys(j.input_payload || {}).length ? <pre style={{ fontFamily: "var(--font-mono)", fontSize: 11.5, background: "var(--surface-2)", border: "1px solid var(--border)", borderRadius: 8, padding: 10, margin: "0 0 8px", whiteSpace: "pre-wrap" }}>{"// input\n" + JSON.stringify(j.input_payload, null, 2)}</pre> : null}
            {j.output_payload ? <pre style={{ fontFamily: "var(--font-mono)", fontSize: 11.5, background: "var(--surface-2)", border: "1px solid var(--border)", borderRadius: 8, padding: 10, margin: 0, whiteSpace: "pre-wrap" }}>{"// output\n" + JSON.stringify(j.output_payload, null, 2)}</pre> : null}
          </div>
        ) : null}

        <div style={{ marginTop: 16 }}>
          <SectionTitle>{tone === "plain" ? "History" : "Audit trail"}</SectionTitle>
          <AuditTrail jobId={j.id} tone={tone} advanced={advanced} />
        </div>
      </div>
    </div>
  );
}

// ----- New Job composer -----
function NewJobForm({ tone, advanced, onClose }) {
  const [title, setTitle] = useStateJ("");
  const [desc, setDesc] = useStateJ("");
  const [role, setRole] = useStateJ("codex");
  const [gate, setGate] = useStateJ(false);
  const [retries, setRetries] = useStateJ(0);
  const [escalate, setEscalate] = useStateJ("");
  const inputStyle = { width: "100%", border: "1px solid var(--border-strong)", borderRadius: 8, padding: "8px 12px", fontSize: 13.5, background: "var(--surface)", color: "var(--text)", outline: "none" };
  const label = { display: "block", fontSize: 12.5, fontWeight: 600, color: "var(--text-2)", margin: "14px 0 5px" };

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%" }}>
      <div style={{ padding: "18px 22px 14px", borderBottom: "1px solid var(--border)", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <h2 style={{ margin: 0, fontSize: 17, fontWeight: 650 }}>New job</h2>
        <Btn kind="ghost" small onClick={onClose} style={{ fontSize: 16, lineHeight: 1 }}>×</Btn>
      </div>
      <div style={{ flex: 1, overflowY: "auto", padding: "6px 22px 16px" }}>
        <label style={label}>What needs to happen?</label>
        <input autoFocus value={title} onChange={(e) => setTitle(e.target.value)} placeholder="e.g. Deploy the staging branch" style={inputStyle} />
        <label style={label}>Details {tone === "plain" ? "(the agent reads this)" : "(instructions)"}</label>
        <textarea value={desc} onChange={(e) => setDesc(e.target.value)} rows={3} placeholder="Anything the agent should know…" style={Object.assign({}, inputStyle, { resize: "vertical" })}></textarea>
        <label style={label}>Who should do it?</label>
        <div style={{ display: "flex", gap: 8 }}>
          {["codex", "claude", "gemini"].map((r) => (
            <button key={r} onClick={() => setRole(r)} style={{
              flex: 1, display: "flex", alignItems: "center", gap: 8, justifyContent: "center",
              border: role === r ? "1.5px solid var(--accent)" : "1px solid var(--border-strong)",
              background: role === r ? "var(--accent-soft)" : "var(--surface)",
              borderRadius: 8, padding: "9px 10px", cursor: "pointer", fontSize: 13, fontWeight: 600,
              color: role === r ? "var(--accent-text)" : "var(--text-2)",
            }}><RoleChip role={r} size={18} />{r}</button>
          ))}
        </div>
        <label style={{ display: "flex", gap: 10, alignItems: "center", margin: "18px 0 0", cursor: "pointer", fontSize: 13.5 }}>
          <input type="checkbox" checked={gate} onChange={(e) => setGate(e.target.checked)} style={{ width: 16, height: 16, accentColor: "var(--accent)" }} />
          <span><b>Ask me before it runs</b><span style={{ color: "var(--text-3)" }}> — {tone === "plain" ? "the job pauses until you approve it" : "requires_approval: pauses at needs_approval"}</span></span>
        </label>
        {advanced ? (
          <div style={{ marginTop: 16, borderTop: "1px dashed var(--border)", paddingTop: 4 }}>
            <label style={label}>Retry budget</label>
            <input type="number" min={0} max={5} value={retries} onChange={(e) => setRetries(+e.target.value)} style={Object.assign({}, inputStyle, { width: 90 })} />
            <label style={label}>Escalate to role (when retries run out)</label>
            <input value={escalate} onChange={(e) => setEscalate(e.target.value)} placeholder="e.g. human" style={Object.assign({}, inputStyle, { width: 200 })} />
          </div>
        ) : null}
      </div>
      <div style={{ padding: "14px 22px", borderTop: "1px solid var(--border)", display: "flex", gap: 8, justifyContent: "flex-end" }}>
        <Btn onClick={onClose}>Cancel</Btn>
        <Btn kind="primary" disabled={!title.trim()} onClick={() => {
          window.BitCadenceStore.createJob({ title: title.trim(), description: desc.trim(), target_agent_role: role, requires_approval: gate, max_retries: retries || 0, escalate_to_role: escalate.trim() || null });
          onClose();
        }}>Create job</Btn>
      </div>
    </div>
  );
}

// ----- Activity feed (cross-job audit explorer) -----
function humanDuration(ms) {
  const s = ms / 1000;
  if (s < 90) return Math.round(s) + "s";
  const m = s / 60;
  if (m < 90) return Math.round(m) + "m";
  const h = m / 60;
  if (h < 48) return (h < 10 ? h.toFixed(1) : Math.round(h)) + "h";
  const d = h / 24;
  return (d < 10 ? d.toFixed(1) : Math.round(d)) + "d";
}

function computeFeedStats(events, jobs) {
  const now = Date.now();
  const dayMs = 24 * 60 * 60 * 1000;
  const last24h = events.filter((e) => now - new Date(e.created_at).getTime() <= dayMs);

  const completed = jobs.filter((j) => j.status === "completed").length;
  const failed = jobs.filter((j) => j.status === "failed").length;
  const failDenom = completed + failed;
  const failureRate = failDenom > 0 ? Math.round((failed / failDenom) * 100) + "%" : "—";

  // Median approval wait: pair each job's needs_approval-ish event with the
  // next approve/reject-ish event that follows it, within the fetched window.
  const byJob = {};
  events.forEach((e) => { (byJob[e.job_id] = byJob[e.job_id] || []).push(e); });
  const waits = [];
  Object.keys(byJob).forEach((jobId) => {
    const list = byJob[jobId].slice().sort((a, b) => new Date(a.created_at) - new Date(b.created_at));
    let pendingAt = null;
    list.forEach((e) => {
      const ev = String(e.event || "").toLowerCase();
      const isApprovalAsk = ev.indexOf("needs_approval") >= 0 || ev.indexOf("approv") >= 0 && ev.indexOf("approved") < 0 && ev.indexOf("reject") < 0;
      const isDecision = ev.indexOf("approv") >= 0 || ev.indexOf("reject") >= 0;
      if (isApprovalAsk && !isDecision) { pendingAt = new Date(e.created_at).getTime(); return; }
      if (isDecision && pendingAt != null) {
        waits.push(new Date(e.created_at).getTime() - pendingAt);
        pendingAt = null;
      }
    });
  });
  let medianWait = "—";
  if (waits.length) {
    const sorted = waits.slice().sort((a, b) => a - b);
    const mid = Math.floor(sorted.length / 2);
    const med = sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
    medianWait = humanDuration(med);
  }

  const activeAgents = new Set(last24h.filter((e) => e.actor_id).map((e) => e.actor_id)).size;

  return {
    events24h: last24h.length,
    failureRate,
    medianWait,
    activeAgents,
  };
}

function ActivityFeedScreen({ jobs, tone, advanced, onOpen }) {
  const store = window.BitCadenceStore;
  const live = (store.mode ? store.mode() : "demo") === "live";
  const [events, setEvents] = useStateJ(null); // null = not loaded yet
  const [err, setErr] = useStateJ(null);
  const [limit, setLimit] = useStateJ(200);
  const [loading, setLoading] = useStateJ(false);
  const [query, setQuery] = useStateJ("");
  const [typeFilter, setTypeFilter] = useStateJ("all");
  const inputStyle = { border: "1px solid var(--border-strong)", borderRadius: 8, padding: "8px 12px", fontSize: 13, background: "var(--surface)", color: "var(--text)" };

  async function load(lim) {
    setLoading(true); setErr(null);
    try {
      const rows = await store.getRecentEvents({ limit: lim || limit });
      setEvents(rows || []);
    } catch (e) { setErr(e.message); setEvents([]); }
    setLoading(false);
  }
  useEffectJ(() => { if (live) load(limit); }, [live]);

  const jobIds = useMemoJ(() => new Set(jobs.map((j) => j.id)), [jobs]);
  const titleOf = (jobId) => { const j = jobs.find((x) => x.id === jobId); return j ? j.title : shortId(jobId); };

  const eventTypes = useMemoJ(() => {
    const set = new Set((events || []).map((e) => e.event));
    return Array.from(set).sort();
  }, [events]);

  const filtered = useMemoJ(() => {
    let list = events || [];
    if (typeFilter !== "all") list = list.filter((e) => e.event === typeFilter);
    if (query) {
      const q = query.toLowerCase();
      list = list.filter((e) => (
        (e.event || "") + " " + (e.job_title || titleOf(e.job_id) || "") + " " + (e.actor_id || "")
      ).toLowerCase().indexOf(q) >= 0);
    }
    return list;
  }, [events, typeFilter, query, jobs]);

  const stats = useMemoJ(() => computeFeedStats(events || [], jobs), [events, jobs]);

  if (!live) {
    return (
      <div>
        <p style={{ margin: "0 0 16px", color: "var(--text-2)", fontSize: 13.5, maxWidth: 560 }}>
          {tone === "plain"
            ? "Every step every agent took, newest first — the paper trail behind the job board."
            : "The immutable agent_job_events trail across all jobs, newest first. Rows are append-only and tamper-evident."}
        </p>
        <Card>
          <EmptyState icon="≡" title={tone === "plain" ? "The activity feed lives on your server" : "Activity feed requires a live gateway"}
            body={tone === "plain"
              ? "Connect to your orchestrator in Settings to see the full audit trail across every job."
              : "Connect in Settings to query /api/events — the cross-job audit trail runs server-side."} />
        </Card>
      </div>
    );
  }

  return (
    <div>
      <p style={{ margin: "0 0 16px", color: "var(--text-2)", fontSize: 13.5, maxWidth: 560 }}>
        {tone === "plain"
          ? "Every step every agent took, newest first — the paper trail behind the job board."
          : "The immutable agent_job_events trail across all jobs, newest first. Rows are append-only and tamper-evident."}
      </p>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 14, marginBottom: 18 }}>
        <StatCard label={tone === "plain" ? "Events (last day)" : "Events (24h)"} value={stats.events24h} sub={tone === "plain" ? "things that happened recently" : "rows in the fetched window"} kind="active" />
        <StatCard label="Failure rate" value={stats.failureRate} sub={tone === "plain" ? "of finished jobs" : "failed / (completed + failed)"} kind={stats.failureRate !== "—" && parseInt(stats.failureRate) > 0 ? "failed" : "done"} />
        <StatCard label={tone === "plain" ? "Typical wait for OK" : "Median approval wait"} value={stats.medianWait} sub={tone === "plain" ? "time until someone decided" : "needs_approval → decision"} kind="approval" />
        <StatCard label={tone === "plain" ? "Agents working (24h)" : "Active agents (24h)"} value={stats.activeAgents} sub={tone === "plain" ? "distinct agents seen recently" : "distinct actor_id, last 24h"} kind="done" />
      </div>

      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 14, flexWrap: "wrap" }}>
        <input value={query} onChange={(e) => setQuery(e.target.value)}
          placeholder={tone === "plain" ? "Search activity…" : "Filter event / job / actor…"}
          style={Object.assign({}, inputStyle, { flex: 1, minWidth: 220 })} />
        <select value={typeFilter} onChange={(e) => setTypeFilter(e.target.value)} style={Object.assign({}, inputStyle, { width: 200 })}>
          <option value="all">{tone === "plain" ? "All event types" : "All events"}</option>
          {eventTypes.map((t) => <option key={t} value={t}>{eventLabel(t, tone)}</option>)}
        </select>
        <Btn small onClick={() => load(limit)}>{loading ? "Refreshing…" : "Refresh"}</Btn>
      </div>

      {err ? <Card><div style={{ fontSize: 12.5, color: "var(--st-failed-fg)" }}>{err}</div></Card> : null}

      {events === null && !err ? <p style={{ fontSize: 12.5, color: "var(--text-3)" }}>Loading…</p> : null}

      {events && events.length === 0 && !err ? (
        <Card><EmptyState icon="≡" title={tone === "plain" ? "Nothing has happened yet" : "No audit events yet"} body={tone === "plain" ? "As agents pick up and finish jobs, every step will show up here." : "Job lifecycle events across the fleet will appear here as they occur."} /></Card>
      ) : null}

      {events && events.length > 0 ? (
        <React.Fragment>
          <Card pad={false}>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13.5 }}>
              <THead cols={advanced ? ["When", "Event", "Job", "Actor", "Detail"] : ["When", "Event", "Job", "Actor"]} />
              <tbody>
                {filtered.length === 0 ? (
                  <tr><td colSpan={advanced ? 5 : 4}><EmptyState icon="○" title="No matching events" body="Try a different search or event type." /></td></tr>
                ) : filtered.map((e) => {
                  const clickable = jobIds.has(e.job_id);
                  return (
                    <tr key={e.id} onClick={clickable ? () => onOpen(e.job_id) : undefined}
                      style={{ cursor: clickable ? "pointer" : "default" }}
                      onMouseEnter={(ev) => { if (clickable) ev.currentTarget.style.background = "var(--surface-2)"; }}
                      onMouseLeave={(ev) => { if (clickable) ev.currentTarget.style.background = "transparent"; }}>
                      <Td>
                        <span title={e.created_at} style={{ color: "var(--text-3)", fontSize: 12.5, whiteSpace: "nowrap" }}>{timeAgo(e.created_at)}</span>
                      </Td>
                      <Td>
                        <span style={{
                          display: "inline-flex", alignItems: "center", gap: 6, fontSize: 12, fontWeight: 600,
                          color: `var(--st-${eventKind(e.event)}-fg)`, background: `var(--st-${eventKind(e.event)}-bg)`,
                          borderRadius: 999, padding: "3px 9px",
                        }}>{eventLabel(e.event, tone)}</span>
                      </Td>
                      <Td>
                        <span style={{ fontWeight: 600 }}>{e.job_title || titleOf(e.job_id)}</span>
                        {advanced ? <div style={{ fontSize: 11, color: "var(--text-3)", marginTop: 1 }}><Mono style={{ fontSize: 11 }}>{shortId(e.job_id)}</Mono></div> : null}
                      </Td>
                      <Td>
                        <span style={{ display: "inline-flex", alignItems: "center", gap: 7 }}>
                          <Mono>{e.actor_id || "—"}</Mono>
                          {e.actor_role ? <RoleChip role={e.actor_role} size={18} /> : null}
                        </span>
                      </Td>
                      {advanced ? (
                        <Td>
                          <Mono style={{ fontSize: 11, color: "var(--text-3)" }}>
                            {e.detail ? (() => { const s = JSON.stringify(e.detail); return s.length > 80 ? s.slice(0, 80) + "…" : s; })() : "—"}
                          </Mono>
                        </Td>
                      ) : null}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </Card>
          <div style={{ display: "flex", justifyContent: "center", marginTop: 14 }}>
            <Btn small disabled={loading || limit >= 500} onClick={() => { const next = Math.min(500, limit * 2); setLimit(next); load(next); }}>
              {loading ? "Loading…" : (limit >= 500 ? (tone === "plain" ? "That's everything we fetch" : "Fetch limit reached") : "Load more")}
            </Btn>
          </div>
        </React.Fragment>
      ) : null}
    </div>
  );
}

Object.assign(window, { JobBoard, JobDetail, NewJobForm, ActivityFeedScreen });
