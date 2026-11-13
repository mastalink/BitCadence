// BitCadence — Job Board screen + Job detail drawer + New Job composer.
const { useState: useStateJ, useMemo: useMemoJ, useEffect: useEffectJ, useRef: useRefJ } = React;

// Mirrors orchestrator/contracts.py. The server remains authoritative at mutation time.
function jobActionEligible(action, job) {
  const states = {
    approve: ["needs_approval"], reject: ["needs_approval"],
    retry: ["failed", "rejected", "halted"],
    cancel: ["waiting", "needs_approval", "pending", "leased", "in_progress"],
    archive: ["completed", "failed", "rejected", "cancelled", "halted"],
    reassign: ["failed", "rejected", "cancelled", "halted"],
  };
  return !!job && (states[action] || []).includes(job.status);
}

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

// ----- Portfolio -----
const PORTFOLIO_HEALTH = {
  green: { label: "On track", color: "var(--st-done-dot)", bg: "var(--st-done-bg)", fg: "var(--st-done-fg)" },
  amber: { label: "Watch", color: "var(--st-approval-dot)", bg: "var(--st-approval-bg)", fg: "var(--st-approval-fg)" },
  red: { label: "Blocked", color: "var(--st-failed-dot)", bg: "var(--st-failed-bg)", fg: "var(--st-failed-fg)" },
};

function PortfolioHealth({ value }) {
  const health = PORTFOLIO_HEALTH[value] || PORTFOLIO_HEALTH.amber;
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 6, borderRadius: 99, padding: "3px 9px", background: health.bg, color: health.fg, fontSize: 11.5, fontWeight: 650 }}>
      <span style={{ width: 7, height: 7, borderRadius: 99, background: health.color }}></span>
      {health.label}
    </span>
  );
}

function PortfolioCapacity({ snapshot }) {
  const gpu = (snapshot.queues && snapshot.queues.gpu) || {};
  const cpu = (snapshot.queues && snapshot.queues.cpu) || { running: [], pending: [] };
  const workers = snapshot.workers || { counts: {} };
  const segments = [
    {
      label: "GPU queue",
      value: gpu.available ? `${gpu.running || 0} running · ${gpu.queued || 0} waiting` : "Unavailable",
      note: gpu.holder ? `Held by ${gpu.holder}` : gpu.idle ? "Idle" : "Status unknown",
      kind: gpu.idle && gpu.queued ? "red" : "green",
    },
    {
      label: "CPU queue",
      value: `${(cpu.running || []).length} running · ${(cpu.pending || []).length} waiting`,
      note: `${Object.keys(cpu.by_worker || {}).length} workers carrying jobs`,
      kind: (cpu.pending || []).length ? "amber" : "green",
    },
    {
      label: "Workers",
      value: `${(workers.counts && workers.counts.online) || 0} online`,
      note: `${(workers.counts && workers.counts.broken) || 0} broken · ${(workers.counts && workers.counts.away) || 0} away`,
      kind: workers.counts && workers.counts.broken ? "red" : "green",
    },
  ];
  return (
    <section aria-label="Shared capacity" className="portfolio-capacity" style={{ display: "grid", gridTemplateColumns: "repeat(3, minmax(0, 1fr))", border: "1px solid var(--border)", borderRadius: 10, background: "var(--surface)", overflow: "hidden", marginBottom: 18 }}>
      {segments.map((item, index) => (
        <div key={item.label} style={{ padding: "13px 16px", borderLeft: index ? "1px solid var(--border)" : "none", minWidth: 0 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 7, color: "var(--text-3)", fontSize: 11.5, fontWeight: 650 }}>
            <span style={{ width: 7, height: 7, borderRadius: 99, background: PORTFOLIO_HEALTH[item.kind].color }}></span>
            {item.label}
          </div>
          <div style={{ marginTop: 4, fontSize: 14, fontWeight: 680 }}>{item.value}</div>
          <div style={{ color: "var(--text-3)", fontSize: 11.5, marginTop: 2 }}>{item.note}</div>
        </div>
      ))}
    </section>
  );
}

function PortfolioMilestone({ milestone }) {
  const marks = { done: "✓", next: "●", blocked: "!" };
  const colors = { done: "var(--st-done-fg)", next: "var(--accent-text)", blocked: "var(--st-failed-fg)" };
  return (
    <li style={{ display: "grid", gridTemplateColumns: "22px minmax(0,1fr) auto", alignItems: "center", gap: 7, padding: "7px 0", borderBottom: "1px solid var(--border)" }}>
      <span aria-hidden="true" style={{ width: 19, height: 19, borderRadius: 99, display: "grid", placeItems: "center", background: "var(--surface-2)", color: colors[milestone.status], fontSize: 11, fontWeight: 750 }}>{marks[milestone.status]}</span>
      <span style={{ fontSize: 12.5, fontWeight: milestone.status === "next" ? 650 : 500 }}>{milestone.title}</span>
      <span style={{ color: colors[milestone.status], fontSize: 11.5 }}>{milestone.status}</span>
    </li>
  );
}

function PortfolioPage({ snapshot, error, mode, onRefresh, onOpen }) {
  const [expanded, setExpanded] = useStateJ(null);
  if (error) {
    return <Card><EmptyState icon="!" title="Portfolio could not be refreshed" body={error} /><div style={{ textAlign: "center" }}><Btn onClick={onRefresh}>Try again</Btn></div></Card>;
  }
  if (!snapshot) {
    return <Card><EmptyState icon="◇" title={mode === "live" ? "Loading portfolio" : "Portfolio is not configured"} body={mode === "live" ? "Reading local roadmaps and live queues…" : "Connect to the gateway, then add a local portfolio configuration."} /></Card>;
  }
  if (!snapshot.configured) {
    return <Card><EmptyState icon="◇" title="Portfolio is not configured" body="Copy configs/portfolio.example.yaml to your local BitCadence config and add your projects. Real project data stays outside this repository." /></Card>;
  }
  const projects = snapshot.projects || [];
  const flags = snapshot.flags || [];
  return (
    <div>
      <style>{`@media(max-width:820px){.portfolio-ledger{grid-template-columns:1fr!important}.portfolio-capacity{grid-template-columns:1fr!important}}`}</style>
      <div style={{ display: "flex", alignItems: "flex-start", gap: 16, marginBottom: 16 }}>
        <div style={{ flex: 1 }}>
          <h2 style={{ margin: "0 0 5px", fontSize: 20, fontWeight: 700 }}>Every project, one run sheet</h2>
          <p style={{ margin: 0, color: "var(--text-2)", fontSize: 13.5, maxWidth: 700 }}>Roadmaps come from local configuration. Queues, workers, jobs, and flow warnings are live and read-only.</p>
        </div>
        <Btn onClick={onRefresh}>Refresh</Btn>
      </div>

      {flags.length ? (
        <section aria-label="Portfolio flow flags" style={{ background: "var(--st-failed-bg)", border: "1px solid var(--st-failed-dot)", borderRadius: 10, marginBottom: 16, overflow: "hidden" }}>
          <div style={{ padding: "10px 13px", color: "var(--st-failed-fg)", fontSize: 12.5, fontWeight: 700, borderBottom: "1px solid color-mix(in srgb, var(--st-failed-dot) 35%, transparent)" }}>{flags.length} flow {flags.length === 1 ? "flag" : "flags"}</div>
          {flags.map((flag) => (
            <div key={flag.id} style={{ display: "grid", gridTemplateColumns: "minmax(150px,.7fr) minmax(0,1.6fr)", gap: 12, padding: "9px 13px", color: "var(--st-failed-fg)", fontSize: 12.5, borderBottom: "1px solid color-mix(in srgb, var(--st-failed-dot) 20%, transparent)" }}>
              <strong>{flag.title}</strong><span>{flag.detail}</span>
            </div>
          ))}
        </section>
      ) : null}

      <PortfolioCapacity snapshot={snapshot} />

      {projects.length === 0 ? <Card><EmptyState icon="◇" title="No projects are configured" body="Add at least one project and milestone to the local portfolio file." /></Card> : (
        <section aria-label="Project portfolio" className="portfolio-ledger" style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(390px, 1fr))", gap: 13 }}>
          {projects.map((project) => {
            const open = expanded === project.id;
            const health = PORTFOLIO_HEALTH[project.health] || PORTFOLIO_HEALTH.amber;
            return (
              <article key={project.id} style={{ position: "relative", border: "1px solid var(--border)", borderRadius: 10, background: "var(--surface)", overflow: "hidden", boxShadow: open ? "0 8px 24px rgba(36,35,58,.08)" : "none" }}>
                <div aria-hidden="true" style={{ position: "absolute", inset: "0 auto 0 0", width: 5, background: health.color }}></div>
                <button type="button" aria-expanded={open} onClick={() => setExpanded(open ? null : project.id)} style={{ display: "block", width: "100%", border: 0, background: "transparent", color: "var(--text)", textAlign: "left", cursor: "pointer", padding: "15px 16px 14px 20px" }}>
                  <div style={{ display: "flex", alignItems: "flex-start", gap: 12 }}>
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
                        <span style={{ color: "var(--text-3)", fontSize: 11.5, fontWeight: 750 }}>P{project.priority}</span>
                        <h3 style={{ margin: 0, fontSize: 16, fontWeight: 700 }}>{project.name}</h3>
                        <span style={{ color: project.status === "active" ? "var(--st-done-fg)" : "var(--text-3)", fontSize: 11.5 }}>{project.status}</span>
                      </div>
                      <p style={{ margin: "7px 0 0", color: "var(--text-2)", fontSize: 12.5, lineHeight: 1.45 }}>{project.done_definition}</p>
                    </div>
                    <PortfolioHealth value={project.health} />
                  </div>
                  <div style={{ display: "grid", gridTemplateColumns: "minmax(0,1fr) auto", gap: 12, alignItems: "center", marginTop: 13 }}>
                    <div aria-label={`${project.progress.percent}% along the roadmap`} style={{ height: 7, borderRadius: 99, overflow: "hidden", background: "var(--surface-2)" }}><div style={{ width: project.progress.percent + "%", height: "100%", background: health.color }}></div></div>
                    <span style={{ color: "var(--text-3)", fontSize: 11.5 }}>{project.progress.done}/{project.progress.total}</span>
                  </div>
                  <div style={{ display: "grid", gridTemplateColumns: "1fr auto", gap: 12, marginTop: 10, fontSize: 12.5 }}>
                    <span><span style={{ color: "var(--text-3)" }}>Next: </span><strong>{project.next_milestone || "Roadmap complete"}</strong></span>
                    <span style={{ color: "var(--text-3)" }}>{project.last_activity ? timeAgo(project.last_activity) : "No activity"}</span>
                  </div>
                </button>

                {open ? (
                  <div style={{ borderTop: "1px solid var(--border)", padding: "4px 16px 16px 20px" }}>
                    <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))", gap: 18 }}>
                      <div>
                        <h4 style={{ margin: "12px 0 4px", fontSize: 12, fontWeight: 700 }}>Roadmap</h4>
                        <ol style={{ listStyle: "none", margin: 0, padding: 0 }}>{project.milestones.map((milestone, index) => <PortfolioMilestone key={`${milestone.title}-${index}`} milestone={milestone} />)}</ol>
                      </div>
                      <div>
                        <h4 style={{ margin: "12px 0 4px", fontSize: 12, fontWeight: 700 }}>Recent board jobs</h4>
                        {project.recent_jobs.length ? project.recent_jobs.map((job) => (
                          <button key={job.id} onClick={() => onOpen(job.id)} style={{ display: "grid", gridTemplateColumns: "minmax(0,1fr) auto", gap: 8, width: "100%", border: 0, borderBottom: "1px solid var(--border)", background: "transparent", color: "var(--text)", padding: "8px 0", textAlign: "left", cursor: "pointer", fontSize: 12.5 }}><span>{job.title}</span><span style={{ color: "var(--text-3)" }}>{job.status}</span></button>
                        )) : <div style={{ color: "var(--text-3)", fontSize: 12, padding: "8px 0" }}>No matching jobs.</div>}
                        <h4 style={{ margin: "14px 0 4px", fontSize: 12, fontWeight: 700 }}>Open pull requests</h4>
                        {project.repository && project.repository.warning ? <div style={{ color: "var(--st-waiting-fg)", fontSize: 12, padding: "8px 0" }}>{project.repository.warning}</div> : project.open_prs.length ? project.open_prs.map((pr) => <a key={pr.url} href={pr.url} target="_blank" rel="noreferrer" style={{ display: "block", padding: "6px 0", color: "var(--accent-text)", fontSize: 12.5 }}>{pr.number ? `#${pr.number} ` : ""}{pr.title}</a>) : <div style={{ color: "var(--text-3)", fontSize: 12, padding: "8px 0" }}>No open pull requests.</div>}
                      </div>
                    </div>
                    {/* TODO(owner-controls): wire priority/pause writes after the owner-override API lands. */}
                    <div style={{ marginTop: 13, paddingTop: 10, borderTop: "1px solid var(--border)", color: "var(--text-3)", fontSize: 11.5 }}>Priority and pause controls · Read-only in v1</div>
                  </div>
                ) : null}
              </article>
            );
          })}
        </section>
      )}
      <div style={{ marginTop: 14, color: "var(--text-3)", fontSize: 11.5 }}>Updated {timeAgo(snapshot.generated_at)} · local roadmap + live board</div>
    </div>
  );
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
  const [selected, setSelected] = useStateJ(() => new Set(Object.keys((window.BitCadenceStore.getMutationResults?.() || [])[0]?.failed || {})));
  const [batchBusy, setBatchBusy] = useStateJ(false);
  const [reassignRole, setReassignRole] = useStateJ("");
  const [batchResult, setBatchResult] = useStateJ(() => (window.BitCadenceStore.getMutationResults?.() || [])[0] || null);
  const batchLock = useRefJ(false);

  const setSort = (s) => {
    setSortState(s);
    try {
      localStorage.setItem("bitcadence_job_sort", s);
      localStorage.setItem("mco_job_sort", s);
    } catch (e) {}
  };

  const roles = useMemoJ(() => {
    const set = new Set([...jobs.map((j) => j.target_agent_role), ...(window.BitCadenceStore.getAgents?.() || []).filter(agent => !agent.disabled).map(agent => agent.role)].filter(Boolean));
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
      setSelected(new Set(Array.from(selected).filter(id => !visible.some(job => job.id === id))));
    } else {
      setSelected(new Set([...selected, ...visible.map((j) => j.id)]));
    }
  };

  const toggleSelect = (id) => {
    const next = new Set(selected);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    setSelected(next);
  };

  const selectedJobs = useMemoJ(() => visible.filter((j) => selected.has(j.id)), [visible, selected]);
  const hiddenSelected = selected.size - selectedJobs.length;
  const eligibleCount = action => selectedJobs.filter(job => jobActionEligible(action, job)).length;
  const canApprove = eligibleCount("approve") > 0;
  const canReject = eligibleCount("reject") > 0;
  const canRetry = eligibleCount("retry") > 0;
  const canCancel = eligibleCount("cancel") > 0;
  const canArchive = eligibleCount("archive") > 0;
  const canReassign = eligibleCount("reassign") > 0;

  const runBatch = async (action, extra = {}) => {
    if (batchLock.current) return;
    const ids = selectedJobs.filter(job => jobActionEligible(action, job)).map(job => job.id);
    if (!ids.length) return;
    const skipped = Array.from(selected).filter(id => !ids.includes(id));
    if (action === "reject" || action === "cancel") {
      const reason = window.prompt(`Enter reason for batch ${action}:`, "");
      if (reason === null) return;
      extra.reason = reason;
    }
    batchLock.current = true; setBatchBusy(true);
    try {
      const result = await window.BitCadenceStore.batchAction(action, ids, extra);
      setBatchResult({ ...result, action, skipped });
      setSelected(current => new Set(Array.from(current).filter(id => !(result.succeeded || []).includes(id))));
      if (action === "reassign" && !Object.keys(result.failed || {}).length) setReassignRole("");
    } catch (e) {
      setBatchResult({ action, succeeded: [], failed: Object.fromEntries(ids.map(id => [id, e.message])), skipped });
    } finally {
      batchLock.current = false; setBatchBusy(false);
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
        <input aria-label="Search loaded jobs" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search loaded jobs…" style={{
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

      <p style={{ fontSize: 12, color: "var(--text-3)" }}>Showing {visible.length} of {jobs.length} loaded jobs, from the latest window of up to 200. Filters and counts apply only to this window; older or archived work may be absent.</p>

      {batchResult ? <div role="status" aria-live="polite" style={{ border: "1px solid var(--border)", borderRadius: 8, padding: 14, marginBottom: 14 }}>
        <b>Last {batchResult.action} result: {(batchResult.succeeded || []).length} succeeded, {Object.keys(batchResult.failed || {}).length} failed, {(batchResult.skipped || []).length} skipped.</b>
        <div>Failed and skipped jobs stay selected. A retry applies only to currently visible, eligible selections.</div>
        <ul>{Object.entries(batchResult.failed || {}).map(([id, why]) => <li key={id}><button onClick={() => onOpen(id)}>History for {id}</button>: {typeof why === "string" ? why : JSON.stringify(why)}</li>)}</ul>
        {(batchResult.succeeded || []).length ? <ul>{batchResult.succeeded.map(id => <li key={id}>{id}: {batchResult.action === "reassign" ? <span>Replacement {batchResult.replacements?.[id] ? <button onClick={() => onOpen(batchResult.replacements[id])}>{batchResult.replacements[id]}</button> : <span>created; its ID could not be loaded. Open <button onClick={() => onOpen(id)}>original History</button> to inspect the replacement link.</span>}</span> : "succeeded"}</li>)}</ul> : null}
        {(batchResult.skipped || []).length ? <div>Skipped (hidden or ineligible): {batchResult.skipped.join(", ")}</div> : null}
      </div> : null}

      {selected.size > 0 ? (
        <div style={{
          display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap",
          padding: "10px 16px", marginBottom: 14,
          background: "var(--accent-soft)", border: "1px solid var(--accent)", borderRadius: "var(--radius-m)",
          boxShadow: "var(--shadow-s)",
        }}>
          <span style={{ fontWeight: 650, fontSize: 13, color: "var(--accent-text)", display: "inline-flex", alignItems: "center", gap: 6 }}>
            <span style={{ width: 8, height: 8, borderRadius: 99, background: "var(--accent)" }}></span>
            {selected.size} job{selected.size > 1 ? "s" : ""} selected · {hiddenSelected} hidden
          </span>
          <div style={{ height: 16, width: 1, background: "var(--border)" }}></div>
          <span style={{ fontSize: 12 }}>Each action uses its eligible visible count below; hidden and ineligible jobs are skipped.</span>
          <div style={{ width: "100%", fontSize: 12 }}>
            {["approve", "reject", "retry", "cancel", "archive", "reassign"].filter(action => eligibleCount(action) > 0).map(action => <span key={action} style={{ marginRight: 12 }}>{action}: {eligibleCount(action)} eligible / {selected.size - eligibleCount(action)} skipped</span>)}
          </div>
          {canApprove ? <Btn small kind="ok" disabled={batchBusy} onClick={() => runBatch("approve")}>✓ Approve ({selectedJobs.filter((j) => j.status === "needs_approval").length})</Btn> : null}
          {canReject ? <Btn small kind="danger" disabled={batchBusy} onClick={() => runBatch("reject")}>Reject ({selectedJobs.filter((j) => j.status === "needs_approval").length})</Btn> : null}
          {canRetry ? <Btn small disabled={batchBusy} onClick={() => runBatch("retry")}>Retry ({selectedJobs.filter((j) => ["failed", "rejected", "halted"].includes(j.status)).length})</Btn> : null}
          {canCancel ? <Btn small disabled={batchBusy} onClick={() => runBatch("cancel")}>Cancel ({eligibleCount("cancel")})</Btn> : null}
          {canArchive ? <Btn small disabled={batchBusy} onClick={() => runBatch("archive")}>Archive ({eligibleCount("archive")})</Btn> : null}
          {canReassign ? (
            <div style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
              <select aria-label="Replacement target role" disabled={batchBusy} value={reassignRole} onChange={(e) => setReassignRole(e.target.value)}
                style={{ border: "1px solid var(--border)", borderRadius: 6, padding: "4px 8px", fontSize: 12, background: "var(--surface)", color: "var(--text)" }}>
                <option value="">Reassign to role…</option>
                {roles.map((r) => <option key={r} value={r}>{r}</option>)}
              </select>
              {reassignRole ? <Btn small disabled={batchBusy} onClick={() => runBatch("reassign", { target_agent_role: reassignRole })}>Create replacements ({eligibleCount("reassign")})</Btn> : null}
            </div>
          ) : null}
          <div style={{ flex: 1 }}></div>
          <Btn small kind="ghost" disabled={batchBusy} onClick={() => setSelected(new Set())}>Clear selection</Btn>
        </div>
      ) : null}

      <Card pad={false}>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13.5 }}>
          <thead>
            <tr>
              <th style={{ width: 38, padding: "8px 10px 8px 14px", borderBottom: "1px solid var(--border)", background: "var(--surface-2)", textAlign: "center" }}>
                <input type="checkbox" aria-label="Select all visible jobs" disabled={batchBusy} checked={allVisibleSelected} onChange={toggleSelectAll} style={{ cursor: "pointer" }} />
              </th>
              <th aria-sort={sort === "title" ? "ascending" : "none"} style={{
                textAlign: "left", padding: "8px 14px", fontSize: 11.5, fontWeight: 600,
                color: sort === "title" ? "var(--accent-text)" : "var(--text-3)", textTransform: "uppercase", letterSpacing: "0.04em",
                borderBottom: "1px solid var(--border)", background: "var(--surface-2)", cursor: "pointer", userSelect: "none",
              }}>
                <button aria-label="Sort by job" onClick={() => onHeaderSort("job")} style={{ border: 0, background: "transparent", color: "inherit", font: "inherit", cursor: "pointer" }}>Job {sort === "title" ? "▲" : ""}</button>
              </th>
              <th aria-sort={sort === "status" ? "ascending" : "none"} style={{
                textAlign: "left", padding: "8px 14px", fontSize: 11.5, fontWeight: 600,
                color: sort === "status" ? "var(--accent-text)" : "var(--text-3)", textTransform: "uppercase", letterSpacing: "0.04em",
                borderBottom: "1px solid var(--border)", background: "var(--surface-2)", cursor: "pointer", userSelect: "none",
              }}>
                <button aria-label="Sort by status" onClick={() => onHeaderSort("status")} style={{ border: 0, background: "transparent", color: "inherit", font: "inherit", cursor: "pointer" }}>Status {sort === "status" ? "▲" : ""}</button>
              </th>
              <th aria-sort={sort === "role" ? "ascending" : "none"} style={{
                textAlign: "left", padding: "8px 14px", fontSize: 11.5, fontWeight: 600,
                color: sort === "role" ? "var(--accent-text)" : "var(--text-3)", textTransform: "uppercase", letterSpacing: "0.04em",
                borderBottom: "1px solid var(--border)", background: "var(--surface-2)", cursor: "pointer", userSelect: "none",
              }}>
                <button aria-label="Sort by assigned role" onClick={() => onHeaderSort("role")} style={{ border: 0, background: "transparent", color: "inherit", font: "inherit", cursor: "pointer" }}>Assigned to {sort === "role" ? "▲" : ""}</button>
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
              <th aria-sort={sort === "newest" ? "descending" : sort === "oldest" ? "ascending" : "none"} style={{
                textAlign: "left", padding: "8px 14px", fontSize: 11.5, fontWeight: 600,
                color: (sort === "newest" || sort === "oldest") ? "var(--accent-text)" : "var(--text-3)", textTransform: "uppercase", letterSpacing: "0.04em",
                borderBottom: "1px solid var(--border)", background: "var(--surface-2)", cursor: "pointer", userSelect: "none",
              }}>
                <button aria-label="Sort by updated time" onClick={() => onHeaderSort("time")} style={{ border: 0, background: "transparent", color: "inherit", font: "inherit", cursor: "pointer" }}>Updated {sort === "newest" ? "▼" : sort === "oldest" ? "▲" : ""}</button>
              </th>
            </tr>
          </thead>
          <tbody>
            {visible.length === 0 ? (
              <tr><td colSpan={7} style={{ padding: "36px 20px" }}><EmptyState icon="○" title="No matches in the loaded window" body="Only the latest loaded jobs are searched. Clear filters or use a job's History to inspect older work." /></td></tr>
            ) : visible.map((j) => {
              const isSelected = selected.has(j.id);
              return (
                <tr key={j.id} onClick={() => onOpen(j.id)} style={{ cursor: "pointer", background: isSelected ? "var(--accent-soft)" : "transparent" }}
                  onMouseEnter={(e) => { if (!isSelected) e.currentTarget.style.background = "var(--surface-2)"; }}
                  onMouseLeave={(e) => { if (!isSelected) e.currentTarget.style.background = "transparent"; }}>
                  <td style={{ width: 38, padding: "var(--row-pad) 10px var(--row-pad) 14px", borderBottom: "1px solid var(--border)", textAlign: "center" }}
                    onClick={(e) => e.stopPropagation()}>
                    <input type="checkbox" aria-label={`Select job ${j.title}`} disabled={batchBusy} checked={isSelected} onChange={() => toggleSelect(j.id)} style={{ cursor: "pointer" }} />
                  </td>
                  <Td>
                    <button aria-label={`Open job ${j.title}`} onClick={e => { e.stopPropagation(); onOpen(j.id); }} style={{ fontWeight: 600, border: 0, background: "transparent", color: "var(--text)", font: "inherit", textAlign: "left", cursor: "pointer", padding: 0 }}>{j.title}</button>
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
  const [fetchedJob, setFetchedJob] = useStateJ(null);
  const [fetchError, setFetchError] = useStateJ("");
  const j = jobs.find((x) => x.id === jobId) || (fetchedJob?.id === jobId ? fetchedJob : null);
  useEffectJ(() => {
    let stopped = false;
    setFetchError("");
    if (!jobs.some(job => job.id === jobId) && window.BitCadenceStore.fetchJob) {
      window.BitCadenceStore.fetchJob(jobId).then(job => {
        if (!stopped) { setFetchedJob(job); if (!job) setFetchError("This job is unavailable with the current connection."); }
      }).catch(error => { if (!stopped) setFetchError(error.message); });
    }
    return () => { stopped = true; };
  }, [jobId, jobs]);
  const [reason, setReason] = useStateJ("");
  const [rejecting, setRejecting] = useStateJ(false);
  const [cancelling, setCancelling] = useStateJ(false);
  const [cancelReason, setCancelReason] = useStateJ("");
  const [reassigning, setReassigning] = useStateJ(false);
  const [toRole, setToRole] = useStateJ("");
  const [mutationBusy, setMutationBusy] = useStateJ("");
  const [mutationError, setMutationError] = useStateJ("");
  const [mutationNote, setMutationNote] = useStateJ("");
  const mutationLock = useRefJ(false);
  const mutate = async (action, request, after) => {
    if (mutationLock.current) return;
    mutationLock.current = true; setMutationBusy(action); setMutationError(""); setMutationNote("");
    try {
      const result = await request();
      if (after) after(result);
      setMutationNote(action === "reassign" ? "Replacement " + (result?.job?.id || "(see History)") + " created. The original attempt stays in History." : "Action recorded. Review this job's History for its current state.");
    } catch (error) { setMutationError(error.message || String(error)); }
    finally { mutationLock.current = false; setMutationBusy(""); }
  };
  if (!j) return <div role={fetchError ? "alert" : "status"} style={{ padding: 22 }}>{fetchError || "Loading this job outside the current board window…"}<Btn onClick={onClose}>Close</Btn></div>;
  const live = jobActionEligible("cancel", j);
  const canReassign = jobActionEligible("reassign", j);
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
        {mutationBusy ? <div role="status">Saving {mutationBusy}…</div> : null}
        {mutationError ? <div role="alert" style={{ color: "var(--st-failed-fg)", marginBottom: 12 }}>{mutationError} Your draft is retained; review the reason before trying again.</div> : null}
        {mutationNote ? <div role="status" style={{ marginBottom: 12 }}>{mutationNote}</div> : null}
        {j.status === "needs_approval" ? (
          <div style={{ background: "var(--st-approval-bg)", border: "1px solid var(--st-approval-dot)", borderRadius: "var(--radius-m)", padding: 14, marginBottom: 16 }}>
            <div style={{ fontWeight: 600, color: "var(--st-approval-fg)", marginBottom: 8 }}>
              {tone === "plain" ? "This job is waiting for your decision." : "Paused at human-in-the-loop approval gate."}
            </div>
            {!rejecting ? (
              <div style={{ display: "flex", gap: 8 }}>
                <Btn kind="ok" small disabled={!!mutationBusy} onClick={() => mutate("approve", () => window.BitCadenceStore.approve(j.id, "owner-laptop"))}>✓ Approve</Btn>
                <Btn kind="danger" small disabled={!!mutationBusy} onClick={() => setRejecting(true)}>Reject…</Btn>
              </div>
            ) : (
              <div style={{ display: "flex", gap: 8 }}>
                <input aria-label="Rejection reason" disabled={!!mutationBusy} autoFocus value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Why? (recorded in the audit trail)" style={{ flex: 1, border: "1px solid var(--border-strong)", borderRadius: 6, padding: "5px 10px", fontSize: 12.5 }} />
                <Btn kind="danger" small disabled={!!mutationBusy} onClick={() => mutate("reject", () => window.BitCadenceStore.reject(j.id, "owner-laptop", reason), () => setRejecting(false))}>Reject</Btn>
                <Btn kind="ghost" small disabled={!!mutationBusy} onClick={() => setRejecting(false)}>Cancel</Btn>
              </div>
            )}
          </div>
        ) : null}

        {["failed", "rejected", "halted"].includes(j.status) ? (
          <div style={{ background: "var(--st-failed-bg)", border: "1px solid var(--st-failed-dot)", borderRadius: "var(--radius-m)", padding: 14, marginBottom: 16 }}>
            <div style={{ fontWeight: 600, color: "var(--st-failed-fg)", marginBottom: 4 }}>{tone === "plain" ? "This job hit a problem." : "Execution failed."}</div>
            <div style={{ fontSize: 12.5, color: "var(--st-failed-fg)", marginBottom: 10 }}>{j.error_message}</div>
            <Btn small disabled={!!mutationBusy} onClick={() => mutate("retry", () => window.BitCadenceStore.retryNow(j.id))}>↻ Try again</Btn>
          </div>
        ) : null}

        {live || canReassign ? (
          <div style={{ border: "1px solid var(--border)", borderRadius: "var(--radius-m)", padding: 14, marginBottom: 16 }}>
            <div style={{ fontWeight: 600, marginBottom: 8, fontSize: 13 }}>
              {tone === "plain" ? "Change this job" : "Job control"}
            </div>

            {!cancelling && !reassigning ? (
              <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                {canReassign ? <Btn small disabled={!!mutationBusy} onClick={() => setReassigning(true)}>&#8644; Reassign&#8230;</Btn> : null}
                {live ? <Btn kind="danger" small disabled={!!mutationBusy} onClick={() => setCancelling(true)}>&#10005; Call it off&#8230;</Btn> : null}
              </div>
            ) : null}

            {reassigning ? (
              <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
                <select aria-label="Replacement job target role" disabled={!!mutationBusy} value={toRole} onChange={(e) => setToRole(e.target.value)}
                        style={{ border: "1px solid var(--border-strong)", borderRadius: 6, padding: "5px 10px", fontSize: 12.5 }}>
                  <option value="">Send to role&#8230;</option>
                  {roles.map((r) => <option key={r} value={r}>{r}</option>)}
                </select>
                <Btn kind="primary" small disabled={!toRole || !!mutationBusy}
                     onClick={() => mutate("reassign", () => window.BitCadenceStore.reassignJob(j.id, toRole), () => { setReassigning(false); setToRole(""); })}>Create replacement</Btn>
                <Btn kind="ghost" small disabled={!!mutationBusy} onClick={() => { setReassigning(false); setToRole(""); }}>Back</Btn>
                <span style={{ fontSize: 12, color: "var(--text-3)" }}>
                  Creates a new job, links both attempts and archives this unsuccessful attempt. Existing target: <Mono>{j.target_agent_role}</Mono>
                </span>
              </div>
            ) : null}

            {cancelling ? (
              <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
                <input aria-label="Cancellation reason" disabled={!!mutationBusy} autoFocus value={cancelReason} onChange={(e) => setCancelReason(e.target.value)}
                       placeholder="Why? (recorded in the audit trail)"
                       style={{ flex: 1, minWidth: 200, border: "1px solid var(--border-strong)", borderRadius: 6, padding: "5px 10px", fontSize: 12.5 }} />
                <Btn kind="danger" small disabled={!!mutationBusy}
                     onClick={() => mutate("cancel", () => window.BitCadenceStore.cancelJob(j.id, cancelReason), () => { setCancelling(false); setCancelReason(""); })}>Call it off</Btn>
                <Btn kind="ghost" small disabled={!!mutationBusy} onClick={() => { setCancelling(false); setCancelReason(""); }}>Back</Btn>
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
          {j.reassigned_to_job_id ? <MetaRow label="Replacement job"><button onClick={() => onOpen(j.reassigned_to_job_id)}>{j.reassigned_to_job_id}</button></MetaRow> : null}
          {j.reassigned_from_job_id ? <MetaRow label="Original attempt"><button onClick={() => onOpen(j.reassigned_from_job_id)}>{j.reassigned_from_job_id}</button></MetaRow> : null}
          {j.approved_by ? <MetaRow label="Decided by"><Mono>{j.approved_by}</Mono></MetaRow> : null}
          {advanced ? <MetaRow label="Job ID"><Mono style={{ fontSize: 11.5 }}>{j.id}</Mono></MetaRow> : null}
          {advanced && j.max_retries ? <MetaRow label="Retry budget">{j.retry_count || 0} of {j.max_retries} used{j.escalate_to_role ? <span style={{ color: "var(--text-3)" }}> · escalates to {j.escalate_to_role}</span> : null}</MetaRow> : null}
        </div>

        {(j.depends_on || []).length || dependents.length ? (
          <div style={{ marginTop: 14 }}>
            <SectionTitle>{tone === "plain" ? "Connected steps" : "Dependencies"}</SectionTitle>
            {deps.map((d) => (
              <button key={d.id} onClick={() => onOpen(d.id)} style={{ display: "flex", width: "100%", textAlign: "left", alignItems: "center", gap: 8, background: "var(--surface-2)", border: "1px solid var(--border)", borderRadius: 8, padding: "7px 10px", marginBottom: 6, cursor: "pointer", fontSize: 13 }}>
                <span style={{ color: "var(--text-3)", fontSize: 11 }}>↑ after</span>
                <span style={{ fontWeight: 600, flex: 1 }}>{d.title}</span>
                <StatusBadge status={d.status} tone={tone} />
              </button>
            ))}
            {(j.depends_on || []).filter(id => !jobs.some(job => job.id === id)).map(id => <div key={id}><button onClick={() => onOpen(id)}>Open dependency {id}</button> · outside the loaded board window; its state is not inferred here.</div>)}
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
  const submitLock = useRefJ(false);
  const inputStyle = { width: "100%", border: "1px solid var(--border-strong)", borderRadius: 8, padding: "8px 12px", fontSize: 13.5, background: "var(--surface)", color: "var(--text)", outline: "none" };
  const label = { display: "block", fontSize: 12.5, fontWeight: 600, color: "var(--text-2)", margin: "14px 0 5px" };

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%" }}>
      <div style={{ padding: "18px 22px 14px", borderBottom: "1px solid var(--border)", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <h2 style={{ margin: 0, fontSize: 17, fontWeight: 650 }}>New job</h2>
        <Btn kind="ghost" small disabled={busy} onClick={onClose} style={{ fontSize: 16, lineHeight: 1 }}>×</Btn>
      </div>
      <div style={{ flex: 1, overflowY: "auto", padding: "6px 22px 16px" }}>
        <label htmlFor="bc-job-title" style={label}>What needs to happen?</label>
        <input id="bc-job-title" disabled={busy} autoFocus value={title} onChange={(e) => setTitle(e.target.value)} placeholder="e.g. Deploy the staging branch" style={inputStyle} />
        <label htmlFor="bc-job-details" style={label}>Details {tone === "plain" ? "(the agent reads this)" : "(instructions)"}</label>
        <textarea id="bc-job-details" disabled={busy} value={desc} onChange={(e) => setDesc(e.target.value)} rows={3} placeholder="Anything the agent should know…" style={Object.assign({}, inputStyle, { resize: "vertical" })}></textarea>
        <label style={label}>Who should do it?</label>
        {!roles.length ? <p>Register a worker in Agent Fleet before creating work.</p> : null}
        <div style={{ display: "flex", gap: 8 }}>
          {roles.map((r) => (
            <button key={r} disabled={busy} aria-pressed={role === r} onClick={() => setRole(r)} style={{
              flex: 1, display: "flex", alignItems: "center", gap: 8, justifyContent: "center",
              border: role === r ? "1.5px solid var(--accent)" : "1px solid var(--border-strong)",
              background: role === r ? "var(--accent-soft)" : "var(--surface)",
              borderRadius: 8, padding: "9px 10px", cursor: "pointer", fontSize: 13, fontWeight: 600,
              color: role === r ? "var(--accent-text)" : "var(--text-2)",
            }}><RoleChip role={r} size={18} />{r}</button>
          ))}
        </div>
        <label style={{ display: "flex", gap: 10, alignItems: "center", margin: "18px 0 0", cursor: "pointer", fontSize: 13.5 }}>
          <input type="checkbox" disabled={busy} checked={gate} onChange={(e) => setGate(e.target.checked)} style={{ width: 16, height: 16, accentColor: "var(--accent)" }} />
          <span><b>Ask me before it runs</b><span style={{ color: "var(--text-3)" }}> — {tone === "plain" ? "the job pauses until you approve it" : "requires_approval: pauses at needs_approval"}</span></span>
        </label>
        {advanced ? (
          <div style={{ marginTop: 16, borderTop: "1px dashed var(--border)", paddingTop: 4 }}>
            <label htmlFor="bc-job-retries" style={label}>Retry budget</label>
            <input id="bc-job-retries" disabled={busy} type="number" min={0} max={5} value={retries} onChange={(e) => setRetries(+e.target.value)} style={Object.assign({}, inputStyle, { width: 90 })} />
            <label htmlFor="bc-job-escalate" style={label}>Escalate to role (when retries run out)</label>
            <input id="bc-job-escalate" disabled={busy} value={escalate} onChange={(e) => setEscalate(e.target.value)} placeholder="e.g. human" style={Object.assign({}, inputStyle, { width: 200 })} />
          </div>
        ) : null}
      </div>
      <div style={{ padding: "14px 22px", borderTop: "1px solid var(--border)", display: "flex", gap: 8, justifyContent: "flex-end" }}>
        <Btn disabled={busy} onClick={onClose}>Cancel</Btn>
        {error ? <span role="alert">{error}</span> : null}
        <Btn kind="primary" disabled={busy || !title.trim() || !role} onClick={async () => {
          if (submitLock.current) return;
          submitLock.current = true;
          setBusy(true); setError("");
          try {
            const result = await window.BitCadenceStore.createJob({ title: title.trim(), description: desc.trim(), target_agent_role: role, requires_approval: gate, max_retries: retries || 0, escalate_to_role: escalate.trim() || null });
            if (!result) throw new Error("Job was not created. Check the connection and try again.");
            onClose();
          } catch(e) { setError(e.message); }
          finally { submitLock.current = false; setBusy(false); }
        }}>{busy ? "Creating…" : "Create job"}</Btn>
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
            ? "Recent recorded activity, newest first. Search and counts cover the loaded window only (up to 500 events)."
            : "A bounded window of agent_job_events, newest first (up to 500); not a complete audit export."}
        </p>
        <Card>
          <EmptyState icon="≡" title={tone === "plain" ? "The activity feed lives on your server" : "Activity feed requires a live gateway"}
            body={tone === "plain"
              ? "Connect in Settings to read recent events. Open a job's History to inspect that attempt."
              : "Connect in Settings to query /api/events — the cross-job audit trail runs server-side."} />
        </Card>
      </div>
    );
  }

  return (
    <div>
      <p style={{ margin: "0 0 16px", color: "var(--text-2)", fontSize: 13.5, maxWidth: 560 }}>
        {tone === "plain"
          ? "Recent recorded activity, newest first. Search and counts cover the loaded window only (up to 500 events)."
          : "A bounded window of agent_job_events, newest first (up to 500); not a complete audit export."}
      </p>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 14, marginBottom: 18 }}>
        <StatCard label={tone === "plain" ? "Events (last day)" : "Events (24h)"} value={stats.events24h} sub="within loaded event window" kind="active" />
        <StatCard label="Failure rate" value={stats.failureRate} sub="of finished jobs in loaded board window" kind={stats.failureRate !== "—" && parseInt(stats.failureRate) > 0 ? "failed" : "done"} />
        <StatCard label={tone === "plain" ? "Typical wait for OK" : "Median approval wait"} value={stats.medianWait} sub="paired decisions in loaded event window" kind="approval" />
        <StatCard label={tone === "plain" ? "Agents working (24h)" : "Active agents (24h)"} value={stats.activeAgents} sub="distinct actors in loaded event window" kind="done" />
      </div>

      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 14, flexWrap: "wrap" }}>
        <input aria-label="Search loaded activity" value={query} onChange={(e) => setQuery(e.target.value)}
          placeholder={tone === "plain" ? "Search activity…" : "Filter event / job / actor…"}
          style={Object.assign({}, inputStyle, { flex: 1, minWidth: 220 })} />
        <select aria-label="Filter loaded activity by event type" value={typeFilter} onChange={(e) => setTypeFilter(e.target.value)} style={Object.assign({}, inputStyle, { width: 200 })}>
          <option value="all">{tone === "plain" ? "All event types" : "All events"}</option>
          {eventTypes.map((t) => <option key={t} value={t}>{eventLabel(t, tone)}</option>)}
        </select>
        <Btn small disabled={loading} onClick={() => load(limit)}>{loading ? "Refreshing…" : "Refresh"}</Btn>
      </div>

      {events ? <p style={{ color: "var(--text-3)", fontSize: 12 }}>Showing {filtered.length} of {events.length} loaded events (requested latest {limit}, maximum 500). {events.length ? "Loaded dates: " + events.map(event => event.created_at).filter(Boolean).sort()[0] + " to " + events.map(event => event.created_at).filter(Boolean).sort().slice(-1)[0] + "." : "No events returned in this window."} Older or inaccessible events may be absent.</p> : null}

      {err ? <Card><div style={{ fontSize: 12.5, color: "var(--st-failed-fg)" }}>{err}</div></Card> : null}

      {events === null && !err ? <p style={{ fontSize: 12.5, color: "var(--text-3)" }}>Loading…</p> : null}

      {events && events.length === 0 && !err ? (
        <Card><EmptyState icon="≡" title="No events in the loaded window" body="This result does not establish that no earlier activity exists. Open a specific job's History for that attempt." /></Card>
      ) : null}

      {events && events.length > 0 ? (
        <React.Fragment>
          <Card pad={false}>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13.5 }}>
              <THead cols={advanced ? ["When", "Event", "Job", "Actor", "Detail"] : ["When", "Event", "Job", "Actor"]} />
              <tbody>
                {filtered.length === 0 ? (
                  <tr><td colSpan={advanced ? 5 : 4}><EmptyState icon="○" title="No matches in the loaded window" body="Search applies to loaded events only. Try another filter or load a larger recent window." /></td></tr>
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
                        <button aria-label={`Open job ${e.job_title || titleOf(e.job_id)}`} onClick={ev => { ev.stopPropagation(); onOpen(e.job_id); }} style={{ fontWeight: 600, background: "transparent", color: "var(--text)", border: 0, padding: 0, cursor: "pointer", textAlign: "left" }}>{e.job_title || titleOf(e.job_id)}</button>
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

Object.assign(window, { PortfolioPage, ProjectDashboard, JobBoard, JobDetail, NewJobForm, ActivityFeedScreen });
