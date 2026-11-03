// BitCadence — app shell: sidebar nav, topbar, routing, tweaks.
const { useState: useStateA, useEffect: useEffectA } = React;

const NAV = [
  { id: "overview", label: "Home", icon: "M3 3h7v7H3zM14 3h7v4h-7zM14 11h7v10h-7zM3 14h7v7H3z" },
  { id: "portfolio", label: "Portfolio", icon: "M3 7h7l2 2h9v10H3zM3 7V5h7l2 2" },
  { id: "jobs", label: "Job Board", icon: "M4 6h16M4 12h16M4 18h10" },
  { id: "approvals", label: "Approvals", icon: "M9 12l2 2 4-5M12 21a9 9 0 1 1 0-18 9 9 0 0 1 0 18z" },
  { id: "governance", label: "Governance", icon: "M12 3l8 4v5c0 5-3.4 8.7-8 10-4.6-1.3-8-5-8-10V7l8-4zM9 12l2 2 4-5" },
  { id: "ask", label: "Ask for something", icon: "M5 7a2 2 0 1 0 0-4 2 2 0 0 0 0 4zM19 14a2 2 0 1 0 0-4 2 2 0 0 0 0 4zM5 21a2 2 0 1 0 0-4 2 2 0 0 0 0 4zM7 5h10M7 19h10M19 12H7" },
  { id: "agents", label: "Helpers", icon: "M12 8a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM5 22a7 7 0 0 1 14 0M19 8a2.5 2.5 0 1 0-4 0M9 8a2.5 2.5 0 1 1-4 0" },
  { id: "connect", label: "Connect an AI", icon: "M9 3v5M15 3v5M7 8h10v4a5 5 0 0 1-10 0V8zM12 17v4" },
  { id: "schedules", label: "Schedules", icon: "M12 21a9 9 0 1 1 0-18 9 9 0 0 1 0 18zM12 7v5l3 2" },
  { id: "memory", label: "Drumline", icon: "M21 5c0 1.66-4.03 3-9 3S3 6.66 3 5s4.03-3 9-3 9 1.34 9 3zM3 5v14c0 1.66 4.03 3 9 3s9-1.34 9-3V5M3 12c0 1.66 4.03 3 9 3s9-1.34 9-3" },
  { id: "activity", label: "Activity", icon: "M22 12h-4l-3 9L9 3l-3 9H2" },
  { id: "settings", label: "Settings", icon: "M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM19 12a7 7 0 0 0-.1-1.2l2-1.6-2-3.4-2.4 1a7 7 0 0 0-2-1.2L14 3h-4l-.5 2.6a7 7 0 0 0-2 1.2l-2.4-1-2 3.4 2 1.6a7 7 0 0 0 0 2.4l-2 1.6 2 3.4 2.4-1a7 7 0 0 0 2 1.2L10 21h4l.5-2.6a7 7 0 0 0 2-1.2l2.4 1 2-3.4-2-1.6c.06-.4.1-.8.1-1.2z" },
];

function NavIcon({ d }) {
  return (
    <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
      <path d={d}></path>
    </svg>
  );
}

const BRANDS = {
  BitCadence: { tag: "bitcadence.ai", mark: "sticks" },
  Cadence: { tag: "Agent Orchestration", mark: "sticks" },
  DrumTight: { tag: "Tight-Ship Ops", mark: "drum" },
  Echelon: { tag: "Decentralized Command", mark: "chevrons" },
};

function BrandMark({ brand = "Cadence", size = 26 }) {
  const kind = (BRANDS[brand] || BRANDS.Cadence).mark;
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" fill="none">
      <rect x="1" y="1" width="30" height="30" rx="8" fill="var(--accent)"></rect>
      {kind === "sticks" ? (
        <g stroke="#fff" strokeWidth="2.6" strokeLinecap="round">
          <path d="M9 23L21.5 10.5"></path>
          <path d="M23 23L10.5 10.5"></path>
          <circle cx="22.5" cy="9.5" r="2.6" fill="#fff" stroke="none"></circle>
          <circle cx="9.5" cy="9.5" r="2.6" fill="#fff" stroke="none"></circle>
        </g>
      ) : kind === "drum" ? (
        <g stroke="#fff" strokeWidth="2.2" strokeLinecap="round">
          <ellipse cx="16" cy="12" rx="9" ry="4"></ellipse>
          <path d="M7 12v8c0 2.2 4 4 9 4s9-1.8 9-4v-8"></path>
          <path d="M7 14l18 4M25 14L7 18" strokeWidth="1.4"></path>
        </g>
      ) : (
        <g stroke="#fff" strokeWidth="2.8" strokeLinecap="round" strokeLinejoin="round" fill="none">
          <path d="M9 12l7-5 7 5"></path>
          <path d="M9 19l7-5 7 5"></path>
          <path d="M9 26l7-5 7 5"></path>
        </g>
      )}
    </svg>
  );
}

const TWEAK_DEFAULTS = /*EDITMODE-BEGIN*/{
  "brand": "Cadence",
  "accent": "#5b5bd6",
  "density": "comfortable",
  "tone": "plain",
  "simulate": true
}/*EDITMODE-END*/;

const PAGE_TITLES = {
  expert: { overview: "Home", portfolio: "Portfolio", jobs: "Job Board", approvals: "Approval Queue", governance: "Governance", ask: "Ask for something", agents: "Agent Fleet", connect: "Connect an AI", schedules: "Schedules", memory: "Drumline", activity: "Audit Trail", settings: "Settings", scoreLive: "Live view" },
  plain: { overview: "Home", portfolio: "Portfolio", jobs: "All work", approvals: "Needs your OK", governance: "Governance", ask: "Ask for something", agents: "Your helpers", connect: "Connect an AI", schedules: "Schedules", memory: "Drumline", activity: "What happened", settings: "Settings", scoreLive: "Live view" },
};

function App() {
  const [t, setTweak] = useTweaks(TWEAK_DEFAULTS);
  const [page, setPage] = useStateA(((p) => p === "workflows" ? "ask" : p === "projects" ? "portfolio" : p)(localStorage.getItem("bitcadence_page") || localStorage.getItem("baton_page") || "overview"));
  const [advanced, setAdvanced] = useStateA((localStorage.getItem("bitcadence_adv") ?? localStorage.getItem("baton_adv")) === "1");
  const [openJob, setOpenJob] = useStateA(null);
  const [watchRun, setWatchRun] = useStateA(null);
  const [composing, setComposing] = useStateA(false);
  const [, force] = useStateA(0);

  useEffectA(() => window.BitCadenceStore.subscribe(() => force((x) => x + 1)), []);
  useEffectA(() => { localStorage.setItem("bitcadence_page", page === "scoreLive" ? "overview" : page); }, [page]);
  useEffectA(() => { localStorage.setItem("bitcadence_adv", advanced ? "1" : "0"); }, [advanced]);
  useEffectA(() => {
    if (t.simulate) window.BitCadenceStore.startSim(); else window.BitCadenceStore.stopSim();
    return () => window.BitCadenceStore.stopSim();
  }, [t.simulate]);
  useEffectA(() => {
    const r = document.documentElement.style;
    r.setProperty("--accent", t.accent);
    r.setProperty("--accent-strong", `oklch(from ${t.accent} calc(l - 0.07) c h)`);
    r.setProperty("--accent-soft", `oklch(from ${t.accent} 0.95 calc(c * 0.25) h)`);
    r.setProperty("--accent-text", `oklch(from ${t.accent} 0.45 c h)`);
    r.setProperty("--row-pad", t.density === "compact" ? "6px" : "10px");
    r.setProperty("--card-pad", t.density === "compact" ? "14px" : "20px");
  }, [t.accent, t.density]);
  // Refresh "time ago" labels even when nothing changes
  useEffectA(() => { const i = setInterval(() => force((x) => x + 1), 10000); return () => clearInterval(i); }, []);

  const jobs = window.BitCadenceStore.getJobs();
  const projectJobs = window.BitCadenceStore.getProjectJobs ? window.BitCadenceStore.getProjectJobs() : jobs;
  const projectCoverage = window.BitCadenceStore.getProjectCoverage ? window.BitCadenceStore.getProjectCoverage() : { count: projectJobs.length, truncated: false };
  const storeMode = window.BitCadenceStore.mode ? window.BitCadenceStore.mode() : "demo";
  const agents = window.BitCadenceStore.getAgents();
  const portfolio = window.BitCadenceStore.getPortfolio ? window.BitCadenceStore.getPortfolio() : null;
  const portfolioError = window.BitCadenceStore.getPortfolioError ? window.BitCadenceStore.getPortfolioError() : null;
  const tone = t.tone;
  const gates = jobs.filter((j) => j.status === "needs_approval").length;

  useEffectA(() => {
    if (page !== "portfolio" || !window.BitCadenceStore.refreshPortfolio) return;
    window.BitCadenceStore.refreshPortfolio(false).catch(() => {});
    const timer = setInterval(() => window.BitCadenceStore.refreshPortfolio(true).catch(() => {}), 30000);
    return () => clearInterval(timer);
  }, [page, storeMode]);

  const portfolioJobs = portfolio && portfolio.projects
    ? portfolio.projects.flatMap((project) => project.recent_jobs || []) : [];

  const screen = {
    overview: <Home jobs={jobs} agents={agents} onNav={setPage} onOpen={setOpenJob} onWatch={(id) => {setWatchRun(id);setPage("scoreLive");}} />,
    scoreLive: <ScoreLive runId={watchRun} jobs={jobs} agents={agents} onBack={() => setPage("overview")} />,
    portfolio: <PortfolioPage snapshot={portfolio} error={portfolioError} mode={storeMode} onRefresh={() => window.BitCadenceStore.refreshPortfolio(true)} onOpen={setOpenJob} />,
    jobs: <JobBoard jobs={jobs} tone={tone} advanced={advanced} onOpen={setOpenJob} onCompose={() => setComposing(true)} />,
    approvals: <Approvals jobs={jobs} tone={tone} advanced={advanced} onOpen={setOpenJob} />,
    governance: <Governance jobs={jobs} tone={tone} advanced={advanced} onOpen={setOpenJob} />,
    ask: <AskPage onNav={setPage} />,
    agents: tone === "plain" ? <HelpersPage /> : <AgentFleet agents={agents} jobs={jobs} tone={tone} advanced={advanced} />,
    connect: <ConnectPage />,
    schedules: <SchedulesPage />,
    memory: <DrumlineMemory tone={tone} advanced={advanced} onOpen={setOpenJob} />,
    activity: <ActivityFeedScreen jobs={jobs} tone={tone} advanced={advanced} onOpen={setOpenJob} />,
    settings: <Settings tone={tone} advanced={advanced} setAdvanced={setAdvanced} />,
  }[page];

  return (
    <div style={{ display: "flex", minHeight: "100vh" }}>
      {/* Sidebar */}
      <nav data-screen-label="Sidebar" style={{
        width: 216, flex: "none", background: "var(--surface)", borderRight: "1px solid var(--border)",
        display: "flex", flexDirection: "column", position: "sticky", top: 0, height: "100vh",
      }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "16px 16px 14px" }}>
          <BrandMark brand="BitCadence" />
          <div>
            <div style={{ fontWeight: 700, fontSize: 14.5, letterSpacing: "-0.01em" }}>Bit<span style={{ color: "var(--accent-text)" }}>Cadence</span></div>
            <div style={{ fontSize: 10.5, color: "var(--text-3)", letterSpacing: "0.06em", textTransform: "uppercase" }}>Agent Orchestration</div>
          </div>
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 2, padding: "4px 10px" }}>
          {NAV.map((n) => {
            const active = page === n.id;
            return (
              <button key={n.id} aria-label={n.label} onClick={() => setPage(n.id)} style={{
                display: "flex", alignItems: "center", gap: 10, padding: "8px 10px", minHeight: 48, borderRadius: 8,
                border: "none", cursor: "pointer", fontSize: 13.5, fontWeight: active ? 600 : 500, textAlign: "left",
                background: active ? "var(--accent-soft)" : "transparent",
                color: active ? "var(--accent-text)" : "var(--text-2)",
              }}
                onMouseEnter={(e) => { if (!active) e.currentTarget.style.background = "var(--surface-2)"; }}
                onMouseLeave={(e) => { if (!active) e.currentTarget.style.background = "transparent"; }}>
                <NavIcon d={n.icon} />
                <span style={{ flex: 1 }}>{(PAGE_TITLES[tone] || PAGE_TITLES.expert)[n.id] === undefined ? n.label : n.label}</span>
                {n.id === "approvals" && gates > 0 ? (
                  <span style={{ background: "var(--st-approval-dot)", color: "#fff", borderRadius: 99, fontSize: 10.5, fontWeight: 700, padding: "1px 7px" }}>{gates}</span>
                ) : null}
              </button>
            );
          })}
        </div>
        <div style={{ flex: 1 }}></div>
        <div style={{ padding: "12px 16px", borderTop: "1px solid var(--border)", fontSize: 12, color: "var(--text-3)", display: "flex", alignItems: "center", gap: 8 }}>
          {(() => {
            const mode = window.BitCadenceStore.mode ? window.BitCadenceStore.mode() : "demo";
            const host = (window.BitCadenceStore.config ? (window.BitCadenceStore.config().url || "") : "").replace(/^https?:\/\//, "");
            return (
              <React.Fragment>
                <span style={{ width: 7, height: 7, borderRadius: 99, flex: "none", background: mode === "live" ? "var(--st-done-dot)" : "var(--st-approval-dot)", animation: "cadence-pulse 2.2s infinite" }}></span>
                <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {mode === "live" ? "Live" : mode === "connecting" ? "Connecting\u2026" : mode === "offline" ? "Offline \u00B7 connection failed" : "Demo \u00B7 simulated data"}
                </span>
              </React.Fragment>
            );
          })()}
        </div>
      </nav>

      {/* Main */}
      <main style={{ flex: 1, minWidth: 0 }}>
        <header style={{
          position: "sticky", top: 0, zIndex: 40, background: "color-mix(in srgb, var(--bg) 82%, transparent)",
          backdropFilter: "blur(8px)", borderBottom: "1px solid var(--border)",
          display: "flex", alignItems: "center", gap: 14, padding: "12px 28px",
        }}>
          <h1 style={{ margin: 0, fontSize: 17, fontWeight: 650, letterSpacing: "-0.01em", flex: 1 }}>
            {(PAGE_TITLES[tone] || PAGE_TITLES.expert)[page]}
          </h1>
          <label style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12.5, color: "var(--text-2)", cursor: "pointer", fontWeight: 500 }}>
            Advanced
            <Toggle on={advanced} onChange={setAdvanced} />
          </label>
          <div style={{ display: "flex", alignItems: "center", gap: 8, paddingLeft: 14, borderLeft: "1px solid var(--border)" }}>
            <span style={{
              width: 28, height: 28, borderRadius: 99, background: "var(--accent-soft)", color: "var(--accent-text)",
              display: "inline-flex", alignItems: "center", justifyContent: "center", fontWeight: 700, fontSize: 12.5,
            }}>BC</span>
            <div style={{ lineHeight: 1.2 }}>
              <div style={{ fontSize: 12.5, fontWeight: 600 }}>Operator console</div>
              <div style={{ fontSize: 10.5, color: "var(--text-3)" }}>BitCadence</div>
            </div>
          </div>
        </header>
        <div data-screen-label={page} style={{ padding: "22px 28px 48px", maxWidth: 1240 }}>
          {screen}
        </div>
      </main>

      <Drawer open={!!openJob} onClose={() => setOpenJob(null)}>
        <JobDetail jobId={openJob} jobs={page === "portfolio" ? portfolioJobs : jobs} tone={tone} advanced={advanced} onClose={() => setOpenJob(null)} onOpen={setOpenJob} />
      </Drawer>
      <Drawer open={composing} onClose={() => setComposing(false)} width={420}>
        <NewJobForm tone={tone} advanced={advanced} onClose={() => setComposing(false)} />
      </Drawer>
      <ToastHost />

      <TweaksPanel>
        <TweakSection label="Brand" />
        <TweakColor label="Accent" value={t.accent}
          options={["#5b5bd6", "#0f766e", "#b3540f", "#3b62c4"]}
          onChange={(v) => setTweak("accent", v)} />
        <TweakSection label="Experience" />
        <TweakRadio label="Density" value={t.density} options={["comfortable", "compact"]}
          onChange={(v) => setTweak("density", v)} />
        <TweakRadio label="Copy tone" value={t.tone} options={["plain", "expert"]}
          onChange={(v) => setTweak("tone", v)} />
        <TweakToggle label="Live simulation" value={t.simulate} onChange={(v) => setTweak("simulate", v)} />
      </TweaksPanel>
    </div>
  );
}

ReactDOM.createRoot(document.getElementById("root")).render(<App />);
