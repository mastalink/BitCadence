// BitCadence — Job Board screen + Job detail drawer + New Job composer.
const { useState: useStateJ, useMemo: useMemoJ, useEffect: useEffectJ } = React;

const JOB_FILTERS = [
  { id: "all", label: "All" },
  { id: "active", label: "Active", match: ["pending", "leased", "in_progress"] },
  { id: "needs_approval", label: "Needs approval", match: ["needs_approval"] },
  { id: "waiting", label: "Waiting", match: ["waiting"] },
  { id: "done", label: "Done", match: ["completed"] },
  { id: "problems", label: "Problems", match: ["failed", "rejected", "halted", "cancelled"] },
];

// Workers take the first job in their inbox, so priority is scheduling, not
// decoration: it is what lets an urgent job jump an existing backlog.
const JOB_SORTS = [
  { id: "priority", label: "Priority, then oldest" },
  { id: "newest",   label: "Newest first" },
  { id: "oldest",   label: "Oldest first" },
  { id: "status",   label: "Status" },
  { id: "role",     label: "Assigned role" },
  { id: "title",    label: "Title A–Z" },
];

const jobPriority = (j) => Number(j && j.priority) || 0;
const jobTime = (j) => new Date(j.updated_at || j.created_at || 0).getTime() || 0;

// Projects are a human-facing projection of the job board, not a second source
// of truth. Prefer an explicit project stamp, fall back to the workflow stamp
// already written by workflow submission, and keep everything else visible in
// an honest Unassigned bucket. Jev may suggest stamps upstream, but this view
// never invents or persists an assignment.
const PROJECT_TERMINAL = ["completed", "failed", "rejected", "cancelled", "halted"];
const PROJECT_PROBLEMS = ["failed", "rejected", "halted"];

function humanizeProjectName(value) {
  const text = String(value || "").trim().replace(/^project[:/\s-]*/i, "");
  if (!text) return "";
  return text
    .replace(/[_-]+/g, " ")
    .replace(/\s+/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

function projectKey(value) {
  return String(value || "").trim().toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "project";
}

function declaredProject(job) {
  const payload = job && typeof job.input_payload === "object" && job.input_payload ? job.input_payload : {};
  const explicit = job.project || job.project_name || job.project_id || payload.project || payload.project_name || payload.project_id;
  if (explicit && typeof explicit === "object") {
    const key = explicit.id || explicit.slug || explicit.name || explicit.title;
    const label = explicit.name || explicit.title || explicit.slug || explicit.id;
    if (key) return { id: "project:" + projectKey(key), name: humanizeProjectName(label), source: "declared project" };
  } else if (explicit) {
    return { id: "project:" + projectKey(explicit), name: humanizeProjectName(explicit), source: "declared project" };
  }

  const workflow = payload.workflow || job.workflow;
  if (workflow && typeof workflow === "object" && workflow.name) {
    return { id: "workflow:" + String(workflow.name), name: humanizeProjectName(workflow.name), source: "workflow" };
  }
  if (workflow && typeof workflow === "string") {
    return { id: "workflow:" + workflow, name: humanizeProjectName(workflow), source: "workflow" };
  }
  return { id: "unassigned", name: "Unassigned work", source: "unassigned" };
}

function projectHealth(jobs) {
  const counts = {};
  jobs.forEach((j) => { counts[j.status] = (counts[j.status] || 0) + 1; });
  const completed = counts.completed || 0;
  const total = jobs.length;
  const decision = jobs.find((j) => j.status === "needs_approval");
  const problem = jobs.find((j) => PROJECT_PROBLEMS.includes(j.status));
  const working = jobs.find((j) => ["leased", "in_progress"].includes(j.status));
  const ready = jobs.find((j) => j.status === "pending");
  const waiting = jobs.find((j) => j.status === "waiting");

  if (decision) return { rank: 0, state: "Needs decision", status: "needs_approval", next: `Review “${decision.title}”` };
  if (problem) return { rank: 1, state: "Needs attention", status: "failed", next: `Resolve “${problem.title}”` };
  if (working) return { rank: 2, state: "In progress", status: "in_progress", next: `Working on “${working.title}”` };
  if (ready) return { rank: 3, state: "Ready", status: "pending", next: `Start “${ready.title}”` };
  if (waiting) return { rank: 4, state: "Waiting", status: "waiting", next: `Waiting to unlock “${waiting.title}”` };
  if (completed === total && total > 0) return { rank: 6, state: "Complete", status: "completed", next: "No action needed" };
  return { rank: 5, state: "Closed", status: "cancelled", next: "Review closed work" };
}

function projectGroups(jobs) {
  const grouped = new Map();
  jobs.forEach((job) => {
    const project = declaredProject(job);
    if (!grouped.has(project.id)) grouped.set(project.id, { ...project, jobs: [] });
    grouped.get(project.id).jobs.push(job);
  });
  return Array.from(grouped.values()).map((project) => {
    project.jobs.sort((a, b) => jobTime(b) - jobTime(a));
    const health = projectHealth(project.jobs);
    const completed = project.jobs.filter((j) => j.status === "completed").length;
    return { ...project, ...health, completed, total: project.jobs.length };
  }).sort((a, b) => a.rank - b.rank || (a.id === "unassigned" ? 1 : 0) - (b.id === "unassigned" ? 1 : 0) || a.name.localeCompare(b.name));
}

function ProjectDashboard({ jobs, coverage, tone, onOpen, onShowJobs }) {
  const [query, setQuery] = useStateJ("");
  const [filter, setFilter] = useStateJ("open");
  const projects = useMemoJ(() => projectGroups(jobs), [jobs]);
  const visible = useMemoJ(() => projects.filter((p) => {
    if (filter === "open" && ["completed", "cancelled"].includes(p.status)) return false;
    if (filter === "attention" && !["needs_approval", "failed"].includes(p.status)) return false;
    const q = query.trim().toLowerCase();
    return !q || p.name.toLowerCase().includes(q) || p.jobs.some((j) => String(j.title || "").toLowerCase().includes(q));
  }), [projects, query, filter]);
  const openCount = projects.filter((p) => !["completed", "cancelled"].includes(p.status)).length;
  const attentionCount = projects.filter((p) => ["needs_approval", "failed"].includes(p.status)).length;
  const progress = jobs.length ? Math.round((jobs.filter((j) => j.status === "completed").length / jobs.length) * 100) : 0;
  const moveJob = async (job, project) => {
    const name = window.prompt("Project name (leave blank for Unassigned):", project.source === "declared project" ? project.name : "");
    if (name !== null) await window.BitCadenceStore.assignProject(job.id, name);
  };

  return (
    <div>
      <div style={{ marginBottom: 18 }}>
        <h2 style={{ margin: "0 0 5px", fontSize: 20, fontWeight: 680 }}>Projects at a glance</h2>
        <div style={{ color: "var(--text-2)", fontSize: 13.5, maxWidth: 760 }}>
          Jobs are grouped by their declared project or workflow. Work without either stays visible as Unassigned so nothing silently disappears.
        </div>
      </div>

      {coverage && coverage.truncated ? (
        <div role="status" style={{ marginBottom: 14, padding: "10px 13px", borderRadius: 8, border: "1px solid var(--st-approval-dot)", background: "var(--st-approval-bg)", color: "var(--st-approval-fg)", fontSize: 12.5 }}>
          Showing the first {coverage.ceiling || jobs.length} jobs. Project counts are partial; narrow or archive old work before treating these totals as complete.
        </div>
      ) : null}

      <div style={{ display: "grid", gridTemplateColumns: "repeat(3, minmax(0, 1fr))", gap: 12, marginBottom: 16 }}>
        {[
          ["Open projects", openCount, "Work still moving or waiting"],
          ["Need you", attentionCount, "Decisions or problems"],
          ["Jobs complete", progress + "%", `${jobs.filter((j) => j.status === "completed").length} of ${jobs.length}`],
        ].map(([label, value, note]) => (
          <Card key={label}><div style={{ color: "var(--text-3)", fontSize: 11.5, fontWeight: 650, textTransform: "uppercase", letterSpacing: ".04em" }}>{label}</div><div style={{ fontSize: 25, fontWeight: 720, margin: "4px 0 1px" }}>{value}</div><div style={{ color: "var(--text-3)", fontSize: 12 }}>{note}</div></Card>
        ))}
      </div>

      <div style={{ display: "flex", gap: 10, alignItems: "center", marginBottom: 14, flexWrap: "wrap" }}>
        <div role="tablist" aria-label="Filter projects" style={{ display: "flex", gap: 2, background: "var(--surface)", border: "1px solid var(--border)", borderRadius: 8, padding: 3 }}>
          {[["open", "Open"], ["attention", "Needs attention"], ["all", "All"]].map(([id, label]) => (
            <button key={id} role="tab" aria-selected={filter === id} onClick={() => setFilter(id)} style={{ border: "none", cursor: "pointer", borderRadius: 6, padding: "5px 11px", fontSize: 12.5, fontWeight: 600, background: filter === id ? "var(--accent-soft)" : "transparent", color: filter === id ? "var(--accent-text)" : "var(--text-2)" }}>{label}</button>
          ))}
        </div>
        <input value={query} onChange={(e) => setQuery(e.target.value)} aria-label="Search projects" placeholder="Search projects or work…" style={{ border: "1px solid var(--border)", borderRadius: 8, padding: "7px 12px", fontSize: 13, background: "var(--surface)", color: "var(--text)", width: 240, outline: "none" }} />
        <div style={{ flex: 1 }}></div>
        <Btn onClick={onShowJobs}>Open Job Board</Btn>
      </div>

      {visible.length ? (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(360px, 1fr))", gap: 14 }}>
          {visible.map((project) => {
            const pct = project.total ? Math.round((project.completed / project.total) * 100) : 0;
            const q = query.trim().toLowerCase();
            const matchingJobs = q ? project.jobs.filter((j) => String(j.title || "").toLowerCase().includes(q)) : project.jobs;
            const shownJobs = matchingJobs.slice(0, project.id === "unassigned" ? 8 : 4);
            return (
              <Card key={project.id}>
                <div style={{ display: "flex", alignItems: "flex-start", gap: 12 }}>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
                      <h3 style={{ margin: 0, fontSize: 16, fontWeight: 670 }}>{project.name}</h3>
                      <StatusBadge status={project.status} tone={tone} />
                    </div>
                    <div style={{ color: "var(--text-3)", fontSize: 11.5, marginTop: 4 }}>{project.source === "workflow" ? "Grouped from workflow" : project.source === "unassigned" ? "No project or workflow declared" : "Declared project"}</div>
                  </div>
                  <div style={{ color: "var(--text-2)", fontSize: 12.5, whiteSpace: "nowrap" }}>{project.completed}/{project.total} done</div>
                </div>
                <div aria-label={`${pct}% complete`} style={{ height: 6, borderRadius: 99, background: "var(--surface-2)", overflow: "hidden", margin: "13px 0 12px" }}><div style={{ width: pct + "%", height: "100%", background: project.status === "failed" ? "var(--st-failed-dot)" : "var(--accent)", borderRadius: 99 }}></div></div>
                <div style={{ background: "var(--surface-2)", borderRadius: 8, padding: "9px 11px", marginBottom: 10 }}>
                  <div style={{ color: "var(--text-3)", fontSize: 10.5, fontWeight: 650, textTransform: "uppercase", letterSpacing: ".04em", marginBottom: 2 }}>Next action</div>
                  <div style={{ fontSize: 13, fontWeight: 570 }}>{project.next}</div>
                </div>
                <div style={{ display: "flex", flexDirection: "column", gap: 1 }}>
                  {shownJobs.map((job) => (
                    <div key={job.id} style={{ display: "grid", gridTemplateColumns: "minmax(0,1fr) auto auto", gap: 6, alignItems: "center" }}>
                      <button onClick={() => onOpen(job.id)} style={{ border: "none", borderRadius: 7, padding: "7px 8px", background: "transparent", color: "var(--text)", cursor: "pointer", textAlign: "left", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{job.title}</button>
                      <StatusBadge status={job.status} tone={tone} />
                      <button aria-label={`Move ${job.title} to a project`} onClick={() => moveJob(job, project)} style={{ border: "1px solid var(--border)", borderRadius: 6, padding: "3px 6px", background: "var(--surface)", color: "var(--text-3)", cursor: "pointer", fontSize: 11 }}>Move</button>
                    </div>
                  ))}
                </div>
                {matchingJobs.length > shownJobs.length ? <div style={{ color: "var(--text-3)", fontSize: 11.5, padding: "5px 8px 0" }}>+ {matchingJobs.length - shownJobs.length} more jobs · search to move one</div> : null}
              </Card>
            );
          })}
        </div>
      ) : <Card><EmptyState icon="◇" title="No projects here" body="Try All projects or clear the search." /></Card>}

      <div style={{ marginTop: 16, color: "var(--text-3)", fontSize: 11.5 }}>
        Project assignment follows job metadata. Advisory tools such as Jev can recommend organization, while reviewed job metadata remains authoritative.
      </div>
    </div>
  );
}

function JobBoard({ jobs, tone, advanced, onOpen, onCompose }) {
  const [filter, setFilter] = useStateJ("all");
  const [query, setQuery] = useStateJ("");
  const [role, setRole] = useStateJ("all");
  const [sort, setSortState] = useStateJ(() => {
    try {
      return localStorage.getItem("bitcadence_job_sort") || localStorage.getItem("mco_job_sort") || "priority";
    } catch (e) {
      return "priority";
    }
  });
  const [selected, setSelected] = useStateJ(new Set());
  const [batchBusy, setBatchBusy] = useStateJ(false);
  const [reassignRole, setReassignRole] = useStateJ("");

  const setSort = (s) => {
    setSortState(s);
    try {
      localStorage.setItem("bitcadence_job_sort", s);
      localStorage.setItem("mco_job_sort", s);
    } catch (e) {}
  };

  const roles = useMemoJ(() => {
    const set = new Set(jobs.map((j) => j.target_agent_role).filter(Boolean));
    return Array.from(set).sort();
  }, [jobs]);

  const visible = useMemoJ(() => {
    const f = JOB_FILTERS.find((x) => x.id === filter);
    let list = jobs;
    if (f && f.match) list = list.filter((j) => f.match.includes(j.status));
    if (role !== "all") list = list.filter((j) => j.target_agent_role === role);
    if (query) {
      const q = query.toLowerCase();
      list = list.filter((j) => (j.title + " " + j.target_agent_role + " " + (j.workflow || "")).toLowerCase().includes(q));
    }
    list = list.slice();
    // Every comparator falls through to newest-first so the order is stable and
    // never depends on however the store happened to return rows.
    const byNewest = (a, b) => jobTime(b) - jobTime(a);
    if (sort === "priority") {
      // Mirrors the gateway's own inbox ordering, so the board shows the queue
      // in the order workers will actually pick it up.
      list.sort((a, b) => (jobPriority(b) - jobPriority(a))
        || (new Date(a.created_at || 0) - new Date(b.created_at || 0)));
    } else if (sort === "newest") {
      list.sort(byNewest);
    } else if (sort === "oldest") {
      list.sort((a, b) => (new Date(a.created_at || 0) - new Date(b.created_at || 0)));
    } else if (sort === "status") {
      list.sort((a, b) => String(a.status).localeCompare(String(b.status)) || byNewest(a, b));
    } else if (sort === "role") {
      list.sort((a, b) => String(a.target_agent_role).localeCompare(String(b.target_agent_role)) || byNewest(a, b));
    } else if (sort === "title") {
      list.sort((a, b) => String(a.title).localeCompare(String(b.title)) || byNewest(a, b));
    }
    return list;
  }, [jobs, filter, query, role, sort]);

  const counts = useMemoJ(() => {
    const c = {};
    JOB_FILTERS.forEach((f) => { c[f.id] = f.match ? jobs.filter((j) => f.match.includes(j.status)).length : jobs.length; });
    return c;
  }, [jobs]);

  const allVisibleSelected = visible.length > 0 && visible.every((j) => selected.has(j.id));
  const someVisibleSelected = visible.some((j) => selected.has(j.id));

  const toggleSelectAll = () => {
    if (allVisibleSelected) {
      setSelected(new Set());
    } else {
      setSelected(new Set(visible.map((j) => j.id)));
    }
  };

  const toggleSelect = (id) => {
    const next = new Set(selected);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    setSelected(next);
  };

  const selectedJobs = useMemoJ(() => jobs.filter((j) => selected.has(j.id)), [jobs, selected]);
  const canApprove = selectedJobs.some((j) => j.status === "needs_approval");
  const canReject = selectedJobs.some((j) => j.status === "needs_approval");
  const canRetry = selectedJobs.some((j) => ["failed", "rejected", "halted"].includes(j.status));
  const canCancel = selectedJobs.some((j) => !["completed", "failed", "rejected", "cancelled", "halted"].includes(j.status));
  const canArchive = selectedJobs.some((j) => ["completed", "failed", "rejected", "cancelled"].includes(j.status));
  const canReassign = selectedJobs.some((j) => !["completed", "failed", "rejected", "cancelled", "halted"].includes(j.status));

  const runBatch = async (action, extra = {}) => {
    const ids = Array.from(selected);
    if (!ids.length) return;
    if (action === "reject" || action === "cancel") {
      const reason = window.prompt(`Enter reason for batch ${action}:`, "");
      if (reason === null) return;
      extra.reason = reason;
    }
    setBatchBusy(true);
    try {
      await window.BitCadenceStore.batchAction(action, ids, extra);
      setSelected(new Set());
    } catch (e) {
      // toast notification handled by store
    } finally {
      setBatchBusy(false);
    }
  };

  const onHeaderSort = (col) => {
    if (col === "job") setSort("title");
    else if (col === "status") setSort("status");
    else if (col === "role") setSort("role");
    else if (col === "time") setSort(sort === "newest" ? "oldest" : "newest");
  };

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
        <select aria-label="Filter by assigned role" value={role} onChange={(e) => setRole(e.target.value)} style={{ border: "1px solid var(--border)", borderRadius: 8, padding: "7px 10px", fontSize: 12.5,
          background: "var(--surface)", color: "var(--text)", outline: "none", cursor: "pointer" }}>
          <option value="all">All roles</option>
          {roles.map((r) => <option key={r} value={r}>{r}</option>)}
        </select>
        <div style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
          <select aria-label="Sort jobs" value={sort} onChange={(e) => setSort(e.target.value)} style={{ border: "1px solid var(--border)", borderRadius: 8, padding: "7px 10px", fontSize: 12.5,
            background: "var(--surface)", color: "var(--text)", outline: "none", cursor: "pointer" }}>
            {JOB_SORTS.map((o) => <option key={o.id} value={o.id}>{o.label}</option>)}
          </select>
          <span title="Order persisted in local settings" style={{ fontSize: 11, color: "var(--text-3)", fontWeight: 500 }}>default saved</span>
        </div>
        <div style={{ flex: 1 }}></div>
        <Btn kind="primary" onClick={onCompose}>+ New job</Btn>
      </div>

      {selected.size > 0 ? (
        <div style={{
          display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap",
          padding: "10px 16px", marginBottom: 14,
          background: "var(--accent-soft)", border: "1px solid var(--accent)", borderRadius: "var(--radius-m)",
          boxShadow: "var(--shadow-s)",
        }}>
          <span style={{ fontWeight: 650, fontSize: 13, color: "var(--accent-text)", display: "inline-flex", alignItems: "center", gap: 6 }}>
            <span style={{ width: 8, height: 8, borderRadius: 99, background: "var(--accent)" }}></span>
            {selected.size} job{selected.size > 1 ? "s" : ""} selected
          </span>
          <div style={{ height: 16, width: 1, background: "var(--border)" }}></div>
          {canApprove ? <Btn small kind="ok" disabled={batchBusy} onClick={() => runBatch("approve")}>✓ Approve ({selectedJobs.filter((j) => j.status === "needs_approval").length})</Btn> : null}
          {canReject ? <Btn small kind="danger" disabled={batchBusy} onClick={() => runBatch("reject")}>Reject ({selectedJobs.filter((j) => j.status === "needs_approval").length})</Btn> : null}
          {canRetry ? <Btn small disabled={batchBusy} onClick={() => runBatch("retry")}>Retry ({selectedJobs.filter((j) => ["failed", "rejected", "halted"].includes(j.status)).length})</Btn> : null}
          {canCancel ? <Btn small disabled={batchBusy} onClick={() => runBatch("cancel")}>Cancel ({selectedJobs.filter((j) => !["completed", "failed", "rejected", "cancelled", "halted"].includes(j.status)).length})</Btn> : null}
          {canArchive ? <Btn small disabled={batchBusy} onClick={() => runBatch("archive")}>Archive ({selectedJobs.filter((j) => ["completed", "failed", "rejected", "cancelled"].includes(j.status)).length})</Btn> : null}
          {canReassign ? (
            <div style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
              <select value={reassignRole} onChange={(e) => setReassignRole(e.target.value)}
                style={{ border: "1px solid var(--border)", borderRadius: 6, padding: "4px 8px", fontSize: 12, background: "var(--surface)", color: "var(--text)" }}>
                <option value="">Reassign to role…</option>
                {roles.map((r) => <option key={r} value={r}>{r}</option>)}
              </select>
              {reassignRole ? <Btn small disabled={batchBusy} onClick={() => { runBatch("reassign", { to_role: reassignRole }); setReassignRole(""); }}>Apply</Btn> : null}
            </div>
          ) : null}
          <div style={{ flex: 1 }}></div>
          <Btn small kind="ghost" onClick={() => setSelected(new Set())}>Clear selection</Btn>
        </div>
      ) : null}

      <Card pad={false}>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13.5 }}>
          <thead>
            <tr>
              <th style={{ width: 38, padding: "8px 10px 8px 14px", borderBottom: "1px solid var(--border)", background: "var(--surface-2)", textAlign: "center" }}>
                <input type="checkbox" aria-label="Select all jobs" checked={allVisibleSelected} onChange={toggleSelectAll} style={{ cursor: "pointer" }} />
              </th>
              <th onClick={() => onHeaderSort("job")} style={{
                textAlign: "left", padding: "8px 14px", fontSize: 11.5, fontWeight: 600,
                color: sort === "title" ? "var(--accent-text)" : "var(--text-3)", textTransform: "uppercase", letterSpacing: "0.04em",
                borderBottom: "1px solid var(--border)", background: "var(--surface-2)", cursor: "pointer", userSelect: "none",
              }}>
                Job {sort === "title" ? "▲" : ""}
              </th>
              <th onClick={() => onHeaderSort("status")} style={{
                textAlign: "left", padding: "8px 14px", fontSize: 11.5, fontWeight: 600,
                color: sort === "status" ? "var(--accent-text)" : "var(--text-3)", textTransform: "uppercase", letterSpacing: "0.04em",
                borderBottom: "1px solid var(--border)", background: "var(--surface-2)", cursor: "pointer", userSelect: "none",
              }}>
                Status {sort === "status" ? "▲" : ""}
              </th>
              <th onClick={() => onHeaderSort("role")} style={{
                textAlign: "left", padding: "8px 14px", fontSize: 11.5, fontWeight: 600,
                color: sort === "role" ? "var(--accent-text)" : "var(--text-3)", textTransform: "uppercase", letterSpacing: "0.04em",
                borderBottom: "1px solid var(--border)", background: "var(--surface-2)", cursor: "pointer", userSelect: "none",
              }}>
                Assigned to {sort === "role" ? "▲" : ""}
              </th>
              <th style={{
                textAlign: "left", padding: "8px 14px", fontSize: 11.5, fontWeight: 600,
                color: "var(--text-3)", textTransform: "uppercase", letterSpacing: "0.04em",
                borderBottom: "1px solid var(--border)", background: "var(--surface-2)",
              }}>From</th>
              <th style={{
                textAlign: "left", padding: "8px 14px", fontSize: 11.5, fontWeight: 600,
                color: "var(--text-3)", textTransform: "uppercase", letterSpacing: "0.04em",
                borderBottom: "1px solid var(--border)", background: "var(--surface-2)",
              }}>{advanced ? "Retries" : "Workflow"}</th>
              <th onClick={() => onHeaderSort("time")} style={{
                textAlign: "left", padding: "8px 14px", fontSize: 11.5, fontWeight: 600,
                color: (sort === "newest" || sort === "oldest") ? "var(--accent-text)" : "var(--text-3)", textTransform: "uppercase", letterSpacing: "0.04em",
                borderBottom: "1px solid var(--border)", background: "var(--surface-2)", cursor: "pointer", userSelect: "none",
              }}>
                Updated {sort === "newest" ? "▼" : sort === "oldest" ? "▲" : ""}
              </th>
            </tr>
          </thead>
          <tbody>
            {visible.length === 0 ? (
              <tr><td colSpan={7} style={{ padding: "36px 20px" }}><EmptyState icon="○" title="No jobs here" body={role !== "all" || query ? "Nothing matches these filters. Clear the role or search to widen it." : "Try another filter, or create a new job."} /></td></tr>
            ) : visible.map((j) => {
              const isSelected = selected.has(j.id);
              return (
                <tr key={j.id} onClick={() => onOpen(j.id)} style={{ cursor: "pointer", background: isSelected ? "var(--accent-soft)" : "transparent" }}
                  onMouseEnter={(e) => { if (!isSelected) e.currentTarget.style.background = "var(--surface-2)"; }}
                  onMouseLeave={(e) => { if (!isSelected) e.currentTarget.style.background = "transparent"; }}>
                  <td style={{ width: 38, padding: "var(--row-pad) 10px var(--row-pad) 14px", borderBottom: "1px solid var(--border)", textAlign: "center" }}
                    onClick={(e) => e.stopPropagation()}>
                    <input type="checkbox" aria-label={`Select job ${j.title}`} checked={isSelected} onChange={() => toggleSelect(j.id)} style={{ cursor: "pointer" }} />
                  </td>
                  <Td>
                    <div style={{ fontWeight: 600 }}>{j.title}</div>
                    <div style={{ fontSize: 11.5, color: "var(--text-3)", marginTop: 1 }}>
                      <Mono style={{ fontSize: 11 }}>{shortId(j.id)}</Mono>
                      {j.workflow ? <span> · {j.workflow}</span> : null}
                      {jobPriority(j) !== 0 ? (
                        <span title="Higher priority is leased first" style={{
                          marginLeft: 6, padding: "1px 6px", borderRadius: 999, fontSize: 10.5, fontWeight: 700,
                          background: jobPriority(j) > 0 ? "var(--accent-soft)" : "var(--surface-2)",
                          color: jobPriority(j) > 0 ? "var(--accent-text)" : "var(--text-3)",
                        }}>P{jobPriority(j)}</span>
                      ) : null}
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
                      ? <span style={{ color: "var(--text-2)" }}>{j.max_retries ? `${j.retry_count || 0}/${j.max_retries}` : "—"}{j.escalate_to_role ? ` → ${j.escalate_to_role}` : ""}</span>
                      : <span style={{ color: "var(--text-2)" }}>{j.workflow || "—"}</span>}
                  </Td>
                  <Td><span style={{ color: "var(--text-3)", fontSize: 12.5, whiteSpace: "nowrap" }}>{timeAgo(j.updated_at || j.completed_at || j.started_at || j.created_at)}</span></Td>
                </tr>
              );
            })}
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
  const TERMINAL = ["completed", "failed", "rejected", "cancelled", "halted"];
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
                <Btn kind="ok" small onClick={() => window.BitCadenceStore.approve(j.id, "owner-laptop")}>✓ Approve &amp; run</Btn>
                <Btn kind="danger" small onClick={() => setRejecting(true)}>Reject…</Btn>
              </div>
            ) : (
              <div style={{ display: "flex", gap: 8 }}>
                <input autoFocus value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Why? (recorded in the audit trail)" style={{ flex: 1, border: "1px solid var(--border-strong)", borderRadius: 6, padding: "5px 10px", fontSize: 12.5 }} />
                <Btn kind="danger" small onClick={() => { window.BitCadenceStore.reject(j.id, "owner-laptop", reason); setRejecting(false); }}>Reject</Btn>
                <Btn kind="ghost" small onClick={() => setRejecting(false)}>Cancel</Btn>
              </div>
            )}
          </div>
        ) : null}

        {["failed", "rejected", "halted"].includes(j.status) ? (
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
          {advanced && j.max_retries ? <MetaRow label="Retry budget">{j.retry_count || 0} of {j.max_retries} used{j.escalate_to_role ? <span style={{ color: "var(--text-3)" }}> · escalates to {j.escalate_to_role}</span> : null}</MetaRow> : null}
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
  const roles = Array.from(new Set((window.BitCadenceStore.getAgents() || []).filter(a => !a.disabled).map(a => a.role).filter(Boolean))).sort();
  const [title, setTitle] = useStateJ("");
  const [desc, setDesc] = useStateJ("");
  const [role, setRole] = useStateJ(roles[0] || "");
  const [gate, setGate] = useStateJ(false);
  const [retries, setRetries] = useStateJ(0);
  const [escalate, setEscalate] = useStateJ("");
  const [busy, setBusy] = useStateJ(false);
  const [error, setError] = useStateJ("");
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
        {!roles.length ? <p>Register a worker in Agent Fleet before creating work.</p> : null}
        <div style={{ display: "flex", gap: 8 }}>
          {roles.map((r) => (
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
        {error ? <span role="alert">{error}</span> : null}
        <Btn kind="primary" disabled={busy || !title.trim() || !role} onClick={async () => {
          setBusy(true); setError("");
          try {
            const result = await window.BitCadenceStore.createJob({ title: title.trim(), description: desc.trim(), target_agent_role: role, requires_approval: gate, max_retries: retries || 0, escalate_to_role: escalate.trim() || null });
            if (!result) throw new Error("Job was not created. Check the connection and try again.");
            onClose();
          } catch(e) { setError(e.message); }
          finally { setBusy(false); }
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

Object.assign(window, { ProjectDashboard, JobBoard, JobDetail, NewJobForm, ActivityFeedScreen });
