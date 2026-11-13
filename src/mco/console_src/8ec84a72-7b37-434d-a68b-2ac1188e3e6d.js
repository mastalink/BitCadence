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

function administrationHref(gateway, section) {
  try {
    const url = new URL(gateway);
    if (!["http:", "https:"].includes(url.protocol) || url.username || url.password) return null;
    url.search = ""; url.hash = "";
    url.pathname = url.pathname.replace(/\/+$/, "") + "/dashboard";
    url.hash = ["ops", "agents", "models", "workflows", "settings"].includes(section) ? section : "ops";
    return url.toString();
  } catch (e) { return null; }
}

function Administration({ agents, jobs, tone, onNav }) {
  const store = window.BitCadenceStore;
  const cfg = store.config ? store.config() : null;
  const gateway = cfg && cfg.url || window.location.origin;
  const host = (() => { try { return new URL(gateway).host; } catch (e) { return "configured server"; } })();
  return <div>
    <h2>One place for administration</h2>
    <p>Manage helper credentials here. Specialist controls open on <strong>{host}</strong>. Existing permissions still apply; opening a page grants no access.</p>
    <div style={{ display: "grid", gridTemplateColumns: "repeat(3,minmax(0,1fr))", gap: 12, marginBottom: 22 }}>
      {[["models", "Model connections", "Configure and explicitly test providers."], ["workflows", "Saved workflow files", "Validate and run a versioned YAML plan."], ["settings", "Server administration", "Review server settings, connectors and edition availability."]].map(([id, title, body]) => {
        const href = administrationHref(gateway, id);
        return <Card key={id}><h3 style={{ marginTop: 0 }}>{title}</h3><p style={{ fontSize: 13 }}>{body}</p>{href ? <a href={href} target="_blank" rel="noopener noreferrer">Open {title.toLowerCase()} ↗</a> : <p role="alert">Set a valid HTTP or HTTPS server address in Settings.</p>}</Card>;
      })}
    </div>
    <p style={{ fontSize: 13, color: "var(--text-3)" }}>Administration keeps its sign-in check. Tokens are never put in links. The existing /dashboard address remains supported.</p>
    <SectionTitle action={<Btn small kind="ghost" onClick={() => onNav("agents")}>Back to Helpers</Btn>}>Helper credentials</SectionTitle>
    <AgentFleet agents={agents} jobs={jobs} tone={tone} advanced={true} />
    <p><a href="https://bitcadence.ai/wiki/gui-reference" target="_blank" rel="noopener noreferrer">Administration instructions ↗</a></p>
  </div>;
}

const SHELL_STYLE = `
.bc-sidebar{overflow-y:auto;overscroll-behavior:contain;scrollbar-width:thin}
.bc-menu-toggle,.bc-nav-backdrop{display:none}
.bc-main-content{overflow-wrap:anywhere}
.bc-main-content input,.bc-main-content select,.bc-main-content textarea{max-width:100%;box-sizing:border-box}
.bc-skip{position:fixed;left:12px;top:-100px;z-index:100;background:var(--surface);padding:12px}
.bc-skip:focus{top:8px}
:focus-visible{outline:3px solid var(--accent);outline-offset:3px}
@media(max-width:760px){
nav.bc-sidebar{display:none!important;position:fixed!important;inset:0 auto 0 0!important;width:min(290px,85vw)!important;height:100dvh!important;z-index:55}
nav.bc-sidebar[data-open=true]{display:flex!important}
.bc-menu-toggle{display:inline-flex;min-height:38px;border:1px solid var(--border);border-radius:8px;background:var(--surface);color:var(--text);align-items:center;padding:6px 10px}
.bc-nav-backdrop[data-open=true]{display:block;position:fixed;inset:0;z-index:50;background:#10182866}
.bc-topbar{padding:10px 14px!important;gap:8px!important;flex-wrap:wrap}
.bc-topbar h1{font-size:16px!important}.bc-operator{display:none!important}
.bc-main-content{padding:18px 14px 35px!important}
.bc-main-content [style*="grid-template-columns"]{grid-template-columns:minmax(0,1fr)!important}
.bc-main-content table{font-size:12px!important}.bc-main-content th,.bc-main-content td{padding:8px!important;overflow-wrap:anywhere}
}
@media(prefers-reduced-motion:reduce){*,*::before,*::after{animation:none!important;transition:none!important}}
`;

// Keep names consistent across the menu, page headings and copy tones.
for (const tone of ["plain", "expert"]) Object.assign(PAGE_TITLES[tone], { jobs: "Job Board", approvals: "Approvals", agents: "Helpers", activity: "Activity", administration: "Administration" });
NAV.push({ id: "administration", label: "Administration", icon: "M4 5h16v14H4zM8 9h8M8 13h8" });

function App() {
  const [t, setTweak] = useTweaks(TWEAK_DEFAULTS);
  const [page, setPage] = useStateA(((p) => p === "workflows" ? "ask" : p === "projects" ? "portfolio" : p)(localStorage.getItem("bitcadence_page") || localStorage.getItem("baton_page") || "overview"));
  const [advanced, setAdvanced] = useStateA((localStorage.getItem("bitcadence_adv") ?? localStorage.getItem("baton_adv")) === "1");
  const [openJob, setOpenJob] = useStateA(null);
  const [watchRun, setWatchRun] = useStateA(null);
  const [composing, setComposing] = useStateA(false);
  const [menuOpen, setMenuOpen] = useStateA(false);
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

  useEffectA(() => { setMenuOpen(false); }, [page]);
  useEffectA(() => {
    const key = (e) => { if(e.key === "Escape" && menuOpen) {setMenuOpen(false);document.querySelector("[data-bc-menu]")?.focus();} };
    document.addEventListener("keydown",key);return () => document.removeEventListener("keydown",key);
  }, [menuOpen]);

  const screen = {
    overview: <Home jobs={jobs} agents={agents} onNav={setPage} onOpen={setOpenJob} onWatch={(id) => {setWatchRun(id);setPage("scoreLive");}} />,
    scoreLive: <ScoreLive runId={watchRun} jobs={jobs} agents={agents} onBack={() => setPage("overview")} />,
    portfolio: <PortfolioPage snapshot={portfolio} error={portfolioError} mode={storeMode} onRefresh={() => window.BitCadenceStore.refreshPortfolio(true)} onOpen={setOpenJob} />,
    jobs: <JobBoard jobs={jobs} tone={tone} advanced={advanced} onOpen={setOpenJob} onCompose={() => setComposing(true)} />,
    approvals: <Approvals jobs={jobs} tone={tone} advanced={advanced} onOpen={setOpenJob} />,
    governance: <Governance jobs={jobs} tone={tone} advanced={advanced} onOpen={setOpenJob} />,
    ask: <AskPage onNav={setPage} />,
    agents: <><div style={{ display: "flex", justifyContent: "flex-end", marginBottom: 12 }}><Btn small onClick={() => setPage("administration")}>Manage credentials…</Btn></div><HelpersPage /></>,
    administration: <Administration agents={agents} jobs={jobs} tone={tone} onNav={setPage} />,
    connect: <ConnectPage />,
    schedules: <SchedulesPage />,
    memory: <DrumlineMemory tone={tone} advanced={advanced} onOpen={setOpenJob} />,
    activity: <ActivityFeedScreen jobs={jobs} tone={tone} advanced={advanced} onOpen={setOpenJob} />,
    settings: <Settings tone={tone} advanced={advanced} setAdvanced={setAdvanced} />,
  }[page];

  return (
    <div style={{ display: "flex", minHeight: "100vh" }}>
      <style>{SHELL_STYLE}</style><a className="bc-skip" href="#bc-main">Skip to work</a>
      <div className="bc-nav-backdrop" data-open={menuOpen} aria-hidden="true" onClick={() => setMenuOpen(false)}></div>
      {/* Sidebar */}
      <nav className="bc-sidebar" id="bc-navigation" aria-label="Main navigation" data-open={menuOpen} data-screen-label="Sidebar" style={{
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
              <button type="button" key={n.id} aria-current={active ? "page" : undefined} aria-label={n.label} onClick={() => {setPage(n.id);setMenuOpen(false);}} style={{
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
      <main id="bc-main" tabIndex={-1} style={{ flex: 1, minWidth: 0 }}>
        <header className="bc-topbar" style={{
          position: "sticky", top: 0, zIndex: 40, background: "color-mix(in srgb, var(--bg) 82%, transparent)",
          backdropFilter: "blur(8px)", borderBottom: "1px solid var(--border)",
          display: "flex", alignItems: "center", gap: 14, padding: "12px 28px",
        }}>
          <button type="button" data-bc-menu className="bc-menu-toggle" aria-expanded={menuOpen} aria-controls="bc-navigation" onClick={() => setMenuOpen(!menuOpen)}>Menu</button>
          <h1 style={{ margin: 0, fontSize: 17, fontWeight: 650, letterSpacing: "-0.01em", flex: 1 }}>
            {(PAGE_TITLES[tone] || PAGE_TITLES.expert)[page]}
          </h1>
          <label style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12.5, color: "var(--text-2)", cursor: "pointer", fontWeight: 500 }}>
            Technical details
            <Toggle on={advanced} onChange={setAdvanced} />
          </label>
          <div className="bc-operator" style={{ display: "flex", alignItems: "center", gap: 8, paddingLeft: 14, borderLeft: "1px solid var(--border)" }}>
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
        <div className="bc-main-content" data-screen-label={page} style={{ padding: "22px 28px 48px", maxWidth: 1240 }}>
          {storeMode !== "live" && page !== "settings" ? <div role="status" style={{ padding: "10px 14px", border: "1px solid var(--border)", borderRadius: 10, marginBottom: 16, display: "flex", gap: 12, flexWrap: "wrap", alignItems: "center" }}><span>{storeMode === "offline" ? "Server unavailable. Showing previously loaded work." : "Demo — simulated work. Connect to use your real board."}</span><Btn small onClick={() => setPage("settings")}>Connect to server</Btn></div> : null}
          {screen}
        </div>
      </main>

      <Drawer open={!!openJob} title="Job details" onClose={() => setOpenJob(null)}>
        <JobDetail key={openJob} jobId={openJob} jobs={page === "portfolio" ? portfolioJobs : jobs} tone={tone} advanced={advanced} onClose={() => setOpenJob(null)} onOpen={setOpenJob} />
      </Drawer>
      <Drawer open={composing} title="New job" onClose={() => setComposing(false)} width={420}>
        <NewJobForm key={composing ? "open" : "closed"} tone={tone} advanced={advanced} onClose={() => setComposing(false)} />
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
