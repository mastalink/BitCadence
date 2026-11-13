// BitCadence — Agent Fleet + Settings screens.
const { useState: useStateO, useEffect: useEffectO } = React;

function gatewayTarget(store = window.BitCadenceStore) {
  try {
    const base = (store.config && store.config().url) || window.location.href;
    return new URL(base, window.location.href).host;
  } catch (_) { return "your connected gateway"; }
}

// ----- Register agent panel -----
function RegisterAgentPanel({ tone, advanced, onDone, onCancel }) {
  const store = window.BitCadenceStore;
  const [instanceId, setInstanceId] = useStateO("");
  const [role, setRole] = useStateO("");
  const [org, setOrg] = useStateO("");
  const [busy, setBusy] = useStateO(false);
  const [err, setErr] = useStateO(null);
  const [result, setResult] = useStateO(null); // { agent, token }
  const [copied, setCopied] = useStateO(false);
  const inputStyle = { border: "1px solid var(--border-strong)", borderRadius: 8, padding: "7px 12px", fontSize: 13, fontFamily: "var(--font-mono)", width: 240, background: "var(--surface)", color: "var(--text)" };

  async function submit() {
    setBusy(true); setErr(null);
    try {
      const payload = { instance_id: instanceId.trim(), role: role.trim() };
      if (advanced && org.trim()) payload.org = org.trim();
      const res = await store.registerAgent(payload);
      setResult(res);
      if (onDone) onDone();
    } catch (e) { setErr(e.message); }
    setBusy(false);
  }

  function copyToken() {
    if (!result || !result.token) return;
    navigator.clipboard.writeText(result.token).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1600);
    });
  }

  return (
    <Card style={{ marginBottom: 16 }}>
      <SectionTitle>{tone === "plain" ? "Add a new agent" : "Register agent"}</SectionTitle>
      {result ? (
        <div>
          <p style={{ fontSize: 12.5, color: "var(--text-2)", margin: "0 0 10px" }}>
            {tone === "plain"
              ? `"${result.agent.instance_id}" is registered.`
              : `Agent "${result.agent.instance_id}" registered with role "${result.agent.role}".`}
          </p>
          <div style={{
            display: "flex", alignItems: "center", gap: 10, padding: "10px 12px",
            background: "var(--surface-2)", border: "1px solid var(--st-approval-dot)", borderRadius: 8,
          }}>
            <Mono style={{ fontSize: 12.5, color: "var(--text)", flex: 1, wordBreak: "break-all" }}>{result.token}</Mono>
            <Btn small onClick={copyToken}>{copied ? "Copied" : "Copy"}</Btn>
          </div>
          <p style={{ fontSize: 12, color: "var(--st-failed-fg)", fontWeight: 600, margin: "8px 0 0" }}>
            {tone === "plain" ? "Copy this now — you won't see it again." : "This token is shown once — store it now."}
          </p>
          <div style={{ paddingTop: 14 }}>
            <Btn onClick={onCancel}>{tone === "plain" ? "Done" : "Close"}</Btn>
          </div>
        </div>
      ) : (
        <div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 12, alignItems: "flex-end" }}>
            <div>
              <div style={{ fontSize: 11.5, color: "var(--text-3)", marginBottom: 4 }}>Instance ID</div>
              <input value={instanceId} onChange={(e) => setInstanceId(e.target.value)} placeholder="e.g. claude-worker-3" style={inputStyle} />
            </div>
            <div>
              <div style={{ fontSize: 11.5, color: "var(--text-3)", marginBottom: 4 }}>Role</div>
              <input value={role} onChange={(e) => setRole(e.target.value)} placeholder="e.g. claude" style={inputStyle} />
            </div>
            {advanced ? (
              <div>
                <div style={{ fontSize: 11.5, color: "var(--text-3)", marginBottom: 4 }}>Org (optional)</div>
                <input value={org} onChange={(e) => setOrg(e.target.value)} placeholder="default" style={inputStyle} />
              </div>
            ) : null}
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 12, paddingTop: 14 }}>
            <Btn kind="primary" disabled={busy || !instanceId.trim() || !role.trim()} onClick={submit}>
              {busy ? (tone === "plain" ? "Adding…" : "Registering…") : (tone === "plain" ? "Add agent" : "Register")}
            </Btn>
            <Btn onClick={onCancel}>Cancel</Btn>
            {err ? <span style={{ fontSize: 12.5, color: "var(--st-failed-fg)" }}>{err}</span> : null}
          </div>
        </div>
      )}
    </Card>
  );
}

// ----- Per-agent row admin actions (live only) -----
function AgentRowActions({ agent, tone, orgs }) {
  const store = window.BitCadenceStore;
  const [busy, setBusy] = useStateO(false);
  const [reset, setReset] = useStateO(null); // { token } | { error }
  const [err, setErr] = useStateO(null);
  const [moving, setMoving] = useStateO(false);
  const [target, setTarget] = useStateO("");
  const [moveErr, setMoveErr] = useStateO(null);

  async function doReset() {
    const msg = tone === "plain"
      ? `Give "${agent.instance_id}" a brand new token? The old one stops working right away.`
      : `Reset token for "${agent.instance_id}"? The current token is invalidated immediately.`;
    if (!window.confirm(msg)) return;
    setBusy(true); setErr(null);
    try {
      const res = await store.resetToken(agent.instance_id);
      setReset(res);
    } catch (e) { setErr(e.message); }
    setBusy(false);
  }

  async function doDelete() {
    if (!window.confirm(`This agent's token stops working immediately. Remove ${agent.instance_id}?`)) return;
    setBusy(true); setErr(null);
    try { await store.deleteAgent(agent.instance_id); }
    catch (e) { setErr(e.message); setBusy(false); }
  }

  const currentOrg = agent.org_id || "default";
  const moveTargets = orgs && orgs.orgs ? orgs.orgs.filter((o) => o !== currentOrg) : [];
  const canMove = !!(orgs && orgs.host_operator && orgs.orgs && orgs.orgs.length > 1);

  async function doMove() {
    if (!target) return;
    const msg = tone === "plain"
      ? `Move "${agent.instance_id}" to ${target}? You'll manage it from that org afterward.`
      : `Move "${agent.instance_id}" to org ${target}? Strict isolation: it will no longer be manageable from the default org.`;
    if (!window.confirm(msg)) return;
    setBusy(true); setMoveErr(null);
    try {
      await store.updateAgent(agent.instance_id, { org: target });
      setMoving(false); setTarget("");
    } catch (e) { setMoveErr(e.message); }
    setBusy(false);
  }

  return (
    <div>
      <div style={{ display: "flex", alignItems: "center", gap: 6, marginTop: 4, paddingLeft: 16 }}>
        <Btn small kind="ghost" disabled={busy} onClick={doReset} style={{ fontSize: 11.5, padding: "3px 8px", color: "var(--text-3)" }}>
          {tone === "plain" ? "New token" : "Reset token"}
        </Btn>
        <Btn small kind="ghost" disabled={busy} onClick={doDelete} style={{ fontSize: 11.5, padding: "3px 8px", color: "var(--text-3)" }}>
          {tone === "plain" ? "Remove" : "Remove"}
        </Btn>
        {canMove ? (
          <Btn small kind="ghost" disabled={busy} onClick={() => { setMoving((v) => !v); setMoveErr(null); }} style={{ fontSize: 11.5, padding: "3px 8px", color: "var(--text-3)" }}>
            {tone === "plain" ? "Move…" : "Move…"}
          </Btn>
        ) : null}
      </div>
      {err ? <div style={{ fontSize: 11.5, color: "var(--st-failed-fg)", paddingLeft: 16, marginTop: 4 }}>{err}</div> : null}
      {reset && reset.token ? (
        <div style={{
          display: "flex", alignItems: "center", gap: 8, margin: "6px 0 0 16px", padding: "8px 10px",
          background: "var(--surface-2)", border: "1px solid var(--st-approval-dot)", borderRadius: 8,
        }}>
          <Mono style={{ fontSize: 11.5, color: "var(--text)", flex: 1, wordBreak: "break-all" }}>{reset.token}</Mono>
          <Btn small onClick={() => navigator.clipboard.writeText(reset.token)}>Copy</Btn>
          <span style={{ fontSize: 11, color: "var(--st-failed-fg)", fontWeight: 600, whiteSpace: "nowrap" }}>
            {tone === "plain" ? "Won't show again" : "Shown once"}
          </span>
        </div>
      ) : null}
      {moving ? (
        <div style={{
          display: "flex", alignItems: "center", gap: 8, margin: "6px 0 0 16px", padding: "8px 10px",
          background: "var(--surface-2)", border: "1px solid var(--border)", borderRadius: 8, flexWrap: "wrap",
        }}>
          <span style={{ fontSize: 11.5, color: "var(--text-2)" }}>{tone === "plain" ? "Move to:" : "Target org:"}</span>
          <select value={target} onChange={(e) => setTarget(e.target.value)} style={{
            border: "1px solid var(--border-strong)", borderRadius: 6, padding: "4px 8px", fontSize: 12,
            fontFamily: "var(--font-mono)", background: "var(--surface)", color: "var(--text)",
          }}>
            <option value="">{tone === "plain" ? "choose an org…" : "select org…"}</option>
            {moveTargets.map((o) => <option key={o} value={o}>{o}</option>)}
          </select>
          <Btn small kind="primary" disabled={busy || !target} onClick={doMove}>{busy ? "Moving…" : "Confirm"}</Btn>
          <Btn small onClick={() => { setMoving(false); setTarget(""); setMoveErr(null); }}>Cancel</Btn>
          {moveErr ? <span style={{ fontSize: 11.5, color: "var(--st-failed-fg)" }}>{moveErr}</span> : null}
        </div>
      ) : null}
    </div>
  );
}

function AgentFleet({ agents, jobs, tone, advanced }) {
  const store = window.BitCadenceStore;
  const live = (store.mode ? store.mode() : "demo") === "live";
  const [showRegister, setShowRegister] = useStateO(false);
  const [orgs, setOrgs] = useStateO(null); // { orgs, in_use, host_operator } | null
  const byRole = {};
  agents.forEach((a) => { (byRole[a.role] = byRole[a.role] || []).push(a); });

  useEffectO(() => {
    if (!live) { setOrgs(null); return; }
    let alive = true;
    store.getOrgs().then((d) => { if (alive) setOrgs(d); }).catch(() => { if (alive) setOrgs(null); });
    return () => { alive = false; };
  }, [live]);

  const workingOn = (a) => jobs.find((j) => j.leased_by_instance_id === a.instance_id && ["leased", "in_progress"].includes(j.status));
  const doneBy = (a) => jobs.filter((j) => j.leased_by_instance_id === a.instance_id && j.status === "completed").length;

  return (
    <div>
      <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 16, marginBottom: 16 }}>
        <p style={{ margin: 0, color: "var(--text-2)", fontSize: 13.5, maxWidth: 560 }}>
          {tone === "plain"
            ? "Every machine or program signed up to do work shows up here, grouped by what it can do."
            : "Registered agent instances from agent_registry, grouped by role, with live presence heartbeats."}
        </p>
        {live ? (
          <Btn kind="primary" small onClick={() => setShowRegister((v) => !v)}>
            {showRegister ? "Cancel" : (tone === "plain" ? "Add agent" : "Register agent")}
          </Btn>
        ) : null}
      </div>

      {live && showRegister ? (
        <RegisterAgentPanel tone={tone} advanced={advanced}
          onDone={() => { /* keep panel open to show the token */ }}
          onCancel={() => setShowRegister(false)} />
      ) : null}

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(310px, 1fr))", gap: 16 }}>
        {Object.entries(byRole).map(([role, list]) => (
          <Card key={role} pad={false}>
            <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "14px 16px", borderBottom: "1px solid var(--border)", background: "var(--surface-2)" }}>
              <RoleChip role={role} size={26} />
              <div style={{ flex: 1 }}>
                <div style={{ fontWeight: 650, fontSize: 14 }}>{role}</div>
                <div style={{ fontSize: 11.5, color: "var(--text-3)" }}>{list.filter((a) => a.status === "online").length} of {list.length} online</div>
              </div>
            </div>
            {list.map((a, i) => {
              const job = workingOn(a);
              return (
                <div key={a.instance_id} style={{ padding: "11px 16px", borderBottom: i < list.length - 1 ? "1px solid var(--border)" : "none" }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                    <span style={{
                      width: 8, height: 8, borderRadius: 99, flex: "none",
                      background: a.status === "online" ? "var(--st-done-dot)" : "var(--st-rejected-dot)",
                      animation: a.status === "online" ? "cadence-pulse 2.2s ease-in-out infinite" : "none",
                    }}></span>
                    <Mono style={{ fontWeight: 600, color: "var(--text)", fontSize: 13 }}>{a.instance_id}</Mono>
                    <span style={{ flex: 1 }}></span>
                    <span style={{ fontSize: 11.5, color: "var(--text-3)" }}>{a.status === "online" ? "seen " + timeAgo(a.last_seen_at) : "last seen " + timeAgo(a.last_seen_at)}</span>
                  </div>
                  <div style={{ fontSize: 12.5, color: "var(--text-2)", marginTop: 4, paddingLeft: 16 }}>
                    {job
                      ? <span>{tone === "plain" ? "Working on: " : "Executing: "}<b>{job.title}</b></span>
                      : a.status === "online"
                        ? <span style={{ color: "var(--text-3)" }}>{tone === "plain" ? "Idle — waiting for work" : "Idle — polling job board"}</span>
                        : <span style={{ color: "var(--text-3)" }}>{tone === "plain" ? "Not connected" : "No heartbeat"}</span>}
                    {advanced ? <span style={{ color: "var(--text-3)" }}> · {doneBy(a)} completed</span> : null}
                  </div>
                  {live ? <AgentRowActions agent={a} tone={tone} orgs={orgs} /> : null}
                </div>
              );
            })}
          </Card>
        ))}
      </div>
    </div>
  );
}

// ----- Helpers page (design/redesign-v1/06-helpers.html) -----
// Friendly names, a health light with a word, what each helper is doing, "Add a helper"
// (same path as `bitcadence helpers add`) and "Fix it" (dry run first, then a confirm).
// Colour is never the only signal: every light also has a shape and its word.
const HELPERS_STYLE = `
 .hp-view{max-width:980px;margin:0 auto;line-height:1.6;min-width:0}.hp-view h1{margin:0 0 6px}.hp-view .sub{color:var(--text-2);margin:0 0 18px}
 .hp-view button,.hp-view select,.hp-view input{min-height:48px;min-width:48px;padding:10px 18px;border:1px solid var(--border-strong);border-radius:10px;background:var(--surface);color:var(--text);font:inherit;cursor:pointer}
 .hp-view input{cursor:text;width:100%;max-width:420px}.hp-view button[disabled]{opacity:.55;cursor:default}
 .hp-primary{background:var(--accent)!important;border-color:var(--accent-strong)!important;color:#fff!important;font-weight:600}
 .hp-card{background:var(--surface);color:var(--text);border:1px solid var(--border);border-radius:14px;padding:18px;margin-bottom:14px;overflow-wrap:anywhere}
 .hp-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:14px}.hp-grid .hp-card{margin:0}
 .hp-top{display:flex;gap:12px;align-items:center;flex-wrap:wrap}.hp-top .hp-who{flex:1;min-width:140px}
 .hp-avatar{width:40px;height:40px;border-radius:99px;display:flex;align-items:center;justify-content:center;font-weight:700;background:var(--surface-2);border:1px solid var(--border-strong)}
 .hp-pill{display:inline-flex;gap:6px;align-items:center;border:2px solid var(--border-strong);border-radius:99px;padding:4px 12px;font-weight:600;min-height:32px}
 .hp-pill[data-light="red"]{border-color:#b42318}.hp-pill[data-light="green"]{border-color:#1a7f37}
 .hp-hint{color:var(--text-2);font-size:14px;margin:6px 0 0}.hp-label{display:block;font-weight:600;margin:12px 0 6px}
 .hp-row{display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin-top:12px}
 .hp-error{border:2px solid #b42318;border-radius:10px;padding:10px 14px}
 .hp-view :focus-visible{outline:3px solid var(--accent);outline-offset:3px}
 @media(prefers-reduced-motion:reduce){.hp-view *{animation:none!important;transition:none!important}}
`;
const HP_MARK = { green: "●", red: "▲", grey: "○" };

function helperMessage(e) {
  const text = String((e && e.message) || e || "");
  return text.replace(/^HTTP \d+\s*—\s*/, "") || "That didn't work. Try again.";
}

function HelpersPage() {
  const store = window.BitCadenceStore;
  const live = (store.mode ? store.mode() : "demo") === "live";
  const [data, setData] = useStateO(null);
  const [error, setError] = useStateO("");
  const [adding, setAdding] = useStateO(false);
  const [name, setName] = useStateO("");
  const [role, setRole] = useStateO("claude");
  const [busy, setBusy] = useStateO(false);
  const [note, setNote] = useStateO("");
  const [plan, setPlan] = useStateO(null);

  async function load() {
    try { setData(await store.helpersList()); setError(""); }
    catch (e) { setError(helperMessage(e)); }
  }
  useEffectO(() => {
    if (!live) return undefined;
    load();
    const timer = setInterval(load, 15000);
    return () => clearInterval(timer);
  }, [live]);

  async function add() {
    setBusy(true); setNote(""); setError("");
    try {
      const res = await store.addHelper({ name: name.trim(), role });
      setNote(res.helper.name + " is added. Its sign-in is saved on the gateway computer.");
      setName(""); setAdding(false);
      await load();
    } catch (e) { setError(helperMessage(e)); }
    setBusy(false);
  }
  async function lookFirst() {
    setBusy(true); setNote(""); setError("");
    try {
      const res = await store.fixHelpers(false);
      if (!res.problems.length) setNote("Every helper looks fine.");
      else setPlan(res);
    } catch (e) { setError(helperMessage(e)); }
    setBusy(false);
  }
  async function confirmFix() {
    setBusy(true); setError("");
    try {
      const res = await store.fixHelpers(true);
      setNote(res.results.join(" "));
      setPlan(null);
      await load();
    } catch (e) { setError(helperMessage(e)); }
    setBusy(false);
  }

  const list = (data && data.helpers) || [];
  const roles = (data && data.roles) || [{ role: "claude", label: "Claude" }];
  return (
    <div className="hp-view">
      <style>{HELPERS_STYLE}</style>
      <h1>Your helpers</h1>
      <p className="sub">Helpers do the work. Each has a light and a word: Working and Ready are fine, Stuck needs a fix.</p>
      {!live ? <div className="hp-card">Connect to your BitCadence in Settings first, then your helpers show up here.</div> : null}
      {error ? <div className="hp-error" role="alert" style={{ marginBottom: 14 }}>{error}</div> : null}
      <div role="status" aria-live="polite">{note ? <div className="hp-card">{note}</div> : null}</div>
      {live ? (
        <div className="hp-row" style={{ marginBottom: 14 }}>
          <button className="hp-primary" aria-expanded={adding} onClick={() => setAdding((v) => !v)}>{adding ? "Cancel" : "+ Add a helper"}</button>
        </div>
      ) : null}
      {adding ? (
        <div className="hp-card">
          <h2 style={{ marginTop: 0 }}>Add a helper</h2>
          <label className="hp-label" htmlFor="hp-ai">Which AI should power it?</label>
          <select id="hp-ai" value={role} onChange={(e) => setRole(e.target.value)}>
            {roles.map((r) => <option key={r.role} value={r.role}>{r.label}</option>)}
          </select>
          <label className="hp-label" htmlFor="hp-name">Name</label>
          <input id="hp-name" value={name} onChange={(e) => setName(e.target.value)} placeholder="For example: Penny" />
          <div className="hp-row"><button className="hp-primary" disabled={busy || !name.trim()} onClick={add}>{busy ? "Adding…" : "Add helper"}</button></div>
        </div>
      ) : null}
      {plan ? (
        <div className="hp-card" role="group" aria-label="Fix it">
          <h2 style={{ marginTop: 0 }}>Here's what I found</h2>
          {plan.problems.map((p, i) => <p key={i}><b>{p.summary}</b><br />{p.would}</p>)}
          <p className="hp-hint">Nothing has changed yet.</p>
          <div className="hp-row">
            <button className="hp-primary" disabled={busy} onClick={confirmFix}>{busy ? "Fixing…" : "Yes, fix it"}</button>
            <button disabled={busy} onClick={() => setPlan(null)}>Not now</button>
          </div>
        </div>
      ) : null}
      {live && data && !list.length ? <div className="hp-card">No helpers yet. Choose "+ Add a helper" to start.</div> : null}
      <div className="hp-grid">
        {list.map((h) => (
          <div className="hp-card" key={h.id}>
            <div className="hp-top">
              <div className="hp-avatar" aria-hidden="true">{h.name.charAt(0)}</div>
              <div className="hp-who"><b>{h.name}</b><div className="hp-hint" style={{ margin: 0 }}>{h.doing}</div></div>
              <span className="hp-pill" data-light={h.light}><span aria-hidden="true">{HP_MARK[h.light] || "○"}</span>{h.word}</span>
            </div>
            <p className="hp-hint">Last heard from: {h.last_heard}</p>
            {h.can_fix ? <button className="hp-primary" style={{ width: "100%" }} disabled={busy} onClick={lookFirst}>Fix it</button> : null}
          </div>
        ))}
      </div>
    </div>
  );
}


// ----- Connect an AI page (design/redesign-v1/02-connect-ai.html) -----
// One tap per app. The gateway finds the app and writes its connection settings after keeping a backup
// (same code as `bitcadence connect`). No file, path or code is shown, except under "Another app".
function ConnectPage() {
  const store = window.BitCadenceStore;
  const live = (store.mode ? store.mode() : "demo") === "live";
  const targetHost = gatewayTarget(store);
  const [apps, setApps] = useStateO(null);
  const [error, setError] = useStateO("");
  const [note, setNote] = useStateO("");
  const [busy, setBusy] = useStateO("");
  const [snippet, setSnippet] = useStateO("");
  const [copied, setCopied] = useStateO(false);

  async function load() {
    try { setApps((await store.connectList()).apps); setError(""); }
    catch (e) { setError(helperMessage(e)); }
  }
  useEffectO(() => { if (live) load(); }, [live]);

  async function act(app, verb) {
    setBusy(app + verb); setNote(""); setError("");
    try {
      const res = await store.connectAction(app, verb);
      setNote(res.message);
      await load();
    } catch (e) { setError(helperMessage(e)); }
    setBusy("");
  }
  async function showOther() {
    setError("");
    try { setSnippet((await store.connectOther()).snippet); }
    catch (e) { setError(helperMessage(e)); }
  }
  async function copy() {
    try { await navigator.clipboard.writeText(snippet); setCopied(true); } catch (e) { setCopied(false); }
  }

  const list = apps || [];
  return (
    <div className="hp-view">
      <style>{HELPERS_STYLE}</style>
      <h1>Connect an AI</h1>
      <p className="sub">Apps on {targetHost}. Changes apply to the gateway computer, which may be different from this browser's computer.</p>
      {!live ? <div className="hp-card">Connect to your BitCadence in Settings first, then you can connect your AI apps here.</div> : null}
      {error ? <div className="hp-error" role="alert" style={{ marginBottom: 14 }}>{error}</div> : null}
      <div role="status" aria-live="polite">{note ? <div className="hp-card">{note}</div> : null}</div>
      <div className="hp-grid">
        {list.map((a) => (
          <div className="hp-card" key={a.app}>
            <div className="hp-top">
              <div className="hp-avatar" aria-hidden="true">{a.name.charAt(0)}</div>
              <div className="hp-who"><b>{a.name}</b><div className="hp-hint" style={{ margin: 0 }}>{a.hint}</div></div>
              <span className="hp-pill" data-light={a.connected ? "green" : "grey"}>
                <span aria-hidden="true">{a.connected ? "●" : "○"}</span>{a.connected ? "Connected" : (a.found ? "Not connected" : "Not found")}
              </span>
            </div>
            {!a.found ? <p className="hp-hint">We couldn't find {a.name} on the gateway computer ({targetHost}). Install it there, then come back.</p> : null}
            <div className="hp-row">
              {a.connected ? (
                <>
                  <button disabled={!!busy} onClick={() => act(a.app, "test")}>{busy === a.app + "test" ? "Checking…" : "Send a test"}</button>
                  <button disabled={!!busy} onClick={() => act(a.app, "disconnect")}>{busy === a.app + "disconnect" ? "Disconnecting…" : "Disconnect"}</button>
                </>
              ) : (
                <button className="hp-primary" disabled={!!busy || !a.found} onClick={() => act(a.app, "connect")}>{busy === a.app + "connect" ? "Connecting…" : "Connect"}</button>
              )}
            </div>
          </div>
        ))}
        <div className="hp-card">
          <div className="hp-top">
            <div className="hp-avatar" aria-hidden="true">+</div>
            <div className="hp-who"><b>Another app</b><div className="hp-hint" style={{ margin: 0 }}>Anything that supports connections</div></div>
          </div>
          <div className="hp-row"><button disabled={!live} onClick={showOther}>Show me a code</button></div>
          {snippet ? (
            <div>
              <pre className="hp-hint" style={{ whiteSpace: "pre-wrap" }}>{snippet}</pre>
              <button onClick={copy}>{copied ? "Copied" : "Copy"}</button>
            </div>
          ) : null}
        </div>
      </div>
    </div>
  );
}


// ----- Schedules page (design/redesign-v1/07-schedules.html) -----
// "Every weekday at 2:00 AM" pickers. The file behind it (~/.mco/schedules.yaml) is written by
// the gateway, the same code as `bitcadence schedule add`; no YAML or cron is ever shown.
function SchedulesPage() {
  const store = window.BitCadenceStore;
  const live = (store.mode ? store.mode() : "demo") === "live";
  const [data, setData] = useStateO(null);
  const [error, setError] = useStateO("");
  const [note, setNote] = useStateO("");
  const [what, setWhat] = useStateO("");
  const [freq, setFreq] = useStateO("weekday");
  const [days, setDays] = useStateO([1, 3, 5]);
  const [time, setTime] = useStateO("02:00");
  const [words, setWords] = useStateO("");
  const [zone, setZone] = useStateO(() => {
    try { return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC"; } catch (_) { return "UTC"; }
  });
  const [preview, setPreview] = useStateO(null);
  const [busy, setBusy] = useStateO(false);
  const previewKey = JSON.stringify({ frequency: freq, time, days, timezone: zone });

  async function load() {
    try {
      const res = await store.schedulesList();
      setData(res); setError(res.problem || "");
      setWhat((w) => w || (res.can_schedule[0] ? res.can_schedule[0].id : ""));
    } catch (e) { setError(helperMessage(e)); }
  }
  useEffectO(() => { if (live) load(); }, [live]);
  useEffectO(() => {
    if (!live) return undefined;
    let current = true;
    setPreview(null);
    store.schedulePreview({ frequency: freq, time, days, timezone: zone })
      .then((r) => { if (current) { setWords(r.words); setPreview({ ...r, key: previewKey }); } })
      .catch((e) => { if (current) { setWords(helperMessage(e)); setPreview(null); } });
    return () => { current = false; };
  }, [live, freq, time, days, zone]);

  async function save() {
    if (!preview || preview.key !== previewKey) return;
    setBusy(true); setNote(""); setError("");
    try {
      const res = await store.addSchedule({ what, frequency: freq, time, days, timezone: zone });
      setNote("Saved. " + res.words + " in " + zone + "." + (res.next_words ? " Next run: " + res.next_words : ""));
      await load();
    } catch (e) { setError(helperMessage(e)); }
    setBusy(false);
  }
  async function flip(item) {
    setError(""); setNote("");
    try { await store.setScheduleOn(item.id, !item.on); await load(); }
    catch (e) { setError(helperMessage(e)); }
  }
  const toggleDay = (d) => setDays((cur) => (cur.includes(d) ? cur.filter((x) => x !== d) : [...cur, d].sort()));

  const list = (data && data.schedules) || [];
  const options = (data && data.can_schedule) || [];
  const freqs = (data && data.frequencies) || [];
  const dayList = (data && data.days) || [];
  return (
    <div className="hp-view">
      <style>{HELPERS_STYLE}</style>
      <h1>Schedules</h1>
      <p className="sub">Pick a time like you would for an alarm.</p>
      {!live ? <div className="hp-card">Connect to your BitCadence in Settings first, then your schedules show up here.</div> : null}
      {error ? <div className="hp-error" role="alert" style={{ marginBottom: 14 }}>{error}</div> : null}
      <div role="status" aria-live="polite">{note ? <div className="hp-card">{note}</div> : null}</div>
      {live && data && !list.length ? <div className="hp-card">Nothing is scheduled yet.</div> : null}
      <div className="hp-grid" style={{ marginBottom: 14 }}>
        {list.map((item) => (
          <div className="hp-card" key={item.id}>
            <b>{item.name}</b>
            <p className="hp-hint">{item.when}</p>
            <p className="hp-hint">Next run: {item.next}</p>
            <button aria-pressed={item.on} onClick={() => flip(item)}>{item.on ? "On" : "Off"}</button>
          </div>
        ))}
      </div>
      {live && data ? (
        <div className="hp-card">
          <h2 style={{ marginTop: 0 }}>New schedule</h2>
          {!options.length ? <p className="hp-hint">There's nothing to schedule yet. Ask for something first, then come back.</p> : (
            <div>
              <label className="hp-label" htmlFor="sc-what">What should run?</label>
              <select id="sc-what" value={what} onChange={(e) => setWhat(e.target.value)}>
                {options.map((o) => <option key={o.id} value={o.id}>{o.label}</option>)}
              </select>
              <div className="hp-label" id="sc-how">How often?</div>
              <div className="hp-row" role="group" aria-labelledby="sc-how" style={{ marginTop: 0 }}>
                {freqs.map((f) => <button key={f.id} aria-pressed={freq === f.id} className={freq === f.id ? "hp-primary" : ""} onClick={() => setFreq(f.id)}>{f.label}</button>)}
              </div>
              {freq === "days" ? (
                <div className="hp-row" role="group" aria-label="Which days">
                  {dayList.map((d) => <button key={d.id} aria-pressed={days.includes(d.id)} className={days.includes(d.id) ? "hp-primary" : ""} onClick={() => toggleDay(d.id)}>{d.label.slice(0, 3)}</button>)}
                </div>
              ) : null}
              {freq !== "hour" ? (
                <div>
                  <label className="hp-label" htmlFor="sc-time">At what time?</label>
                  <input id="sc-time" type="time" value={time} onChange={(e) => setTime(e.target.value)} />
                </div>
              ) : null}
              <label className="hp-label" htmlFor="sc-zone">Time zone (IANA)</label>
              <input id="sc-zone" value={zone} onChange={(e) => setZone(e.target.value)} placeholder="America/New_York or UTC" />
              <p className="hp-hint">Starts with this browser's time zone. The gateway saves the zone shown here and follows its daylight saving rules.</p>
              <div className="hp-card" style={{ marginTop: 14 }} aria-live="polite"><b>{words}</b>
                {preview && preview.key === previewKey ? <p>Time zone: {preview.timezone}<br />Next run: {preview.next_words}</p> : <p>Choose a valid time zone to preview the next run.</p>}</div>
              <div className="hp-row"><button className="hp-primary" disabled={busy || !what || !preview || preview.key !== previewKey || (freq === "days" && !days.length)} onClick={save}>{busy ? "Saving…" : "Save"}</button></div>
            </div>
          )}
        </div>
      ) : null}
    </div>
  );
}

// ----- Settings -----
function SettingRow({ title, body, control }) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 16, padding: "14px 0", borderBottom: "1px solid var(--border)" }}>
      <div style={{ flex: 1 }}>
        <div style={{ fontWeight: 600, fontSize: 13.5 }}>{title}</div>
        <div style={{ fontSize: 12.5, color: "var(--text-2)", marginTop: 2, maxWidth: 480 }}>{body}</div>
      </div>
      <div style={{ flex: "none" }}>{control}</div>
    </div>
  );
}

function Toggle({ on, onChange, label }) {
  return (
    <button onClick={() => onChange(!on)} aria-label={label} aria-pressed={on} style={{
      width: 38, height: 22, borderRadius: 99, border: "none", cursor: "pointer", position: "relative",
      background: on ? "var(--accent)" : "var(--border-strong)", transition: "background .15s",
    }}>
      <span style={{
        position: "absolute", top: 2, left: on ? 18 : 2, width: 18, height: 18, borderRadius: 99,
        background: "#fff", boxShadow: "0 1px 3px rgba(0,0,0,.25)", transition: "left .15s",
      }}></span>
    </button>
  );
}

// Set up enterprise connectors and verify them — the GUI counterpart to the
// terminal `mco setup` wizard. Reads/writes the same whitelisted settings API,
// and the Test button runs the connector's real health() probe server-side.
const CONNECTORS = [
  { name: "servicenow", label: "ServiceNow", fields: ["SERVICENOW_INSTANCE_URL", "SERVICENOW_USERNAME", "SERVICENOW_PASSWORD"] },
  { name: "dynatrace", label: "Dynatrace", fields: ["DYNATRACE_BASE_URL", "DYNATRACE_API_TOKEN"] },
];

function ConnectorsCard({ tone }) {
  const store = window.BitCadenceStore;
  const live = (store.mode ? store.mode() : "demo") === "live";
  const [meta, setMeta] = useStateO(null);   // key -> setting metadata (incl. set/unset)
  const [form, setForm] = useStateO({});       // editable field values
  const [savedForm, setSavedForm] = useStateO({});
  const revision = React.useRef(0);
  const [saving, setSaving] = useStateO(false);
  const [saveMsg, setSaveMsg] = useStateO(null);
  const [test, setTest] = useStateO({});       // name -> { busy, ok, detail }
  const [loadErr, setLoadErr] = useStateO(null);
  const [health, setHealth] = useStateO(null);  // name -> { ok, detail } | null on error
  const [sync, setSync] = useStateO({});        // name -> { busy, ok, summary }
  const inputStyle = { border: "1px solid var(--border-strong)", borderRadius: 8, padding: "7px 12px", fontSize: 13, fontFamily: "var(--font-mono)", width: 320, background: "var(--surface)", color: "var(--text)" };

  // Secrets come back as set/unset (never the value), so we keep their inputs
  // blank and only send them when retyped — blank means "leave unchanged".
  const hydrate = (data) => {
    const conn = (data.groups && data.groups.connectors) || [];
    const m = {}, f = {};
    conn.forEach((s) => { m[s.key] = s; f[s.key] = s.type === "secret" ? "" : (s.value || ""); });
    setMeta(m); setForm(f); setSavedForm(f);
  };

  useEffectO(() => {
    revision.current += 1;
    setTest({}); setSync({}); setHealth(null);
    if (!live) { setMeta(null); return; }
    let alive = true;
    setLoadErr(null);
    store.getSettings().then((d) => { if (alive) hydrate(d); }).catch((e) => { if (alive) setLoadErr(e.message); });
    return () => { alive = false; };
  }, [live]);

  useEffectO(() => {
    if (!live) { setHealth(null); return; }
    let alive = true;
    store.getIntegrations()
      .then((list) => {
        if (!alive) return;
        const h = {};
        (list || []).forEach((c) => { h[c.name] = c.health || {}; });
        setHealth(h);
      })
      .catch(() => { if (alive) setHealth(null); }); // integrations may need a different scope — hide dots
    return () => { alive = false; };
  }, [live]);

  if (!live) {
    return (
      <Card style={{ marginBottom: 18 }}>
        <SectionTitle>Connectors</SectionTitle>
        <p style={{ fontSize: 12.5, color: "var(--text-2)", margin: "10px 0 0", maxWidth: 520 }}>
          {tone === "plain"
            ? "Connect to your orchestrator above, then set up ServiceNow or Dynatrace and test them right here — no terminal needed."
            : "Connect a live gateway above to configure SERVICENOW_*/DYNATRACE_* and run each connector's health() probe."}
        </p>
      </Card>
    );
  }

  const dirty = Object.keys(form).some(k => form[k] !== savedForm[k]);
  const set = (k, v) => {
    revision.current += 1;
    setForm((f) => Object.assign({}, f, { [k]: v }));
    setTest({}); setSync({}); setHealth(null); setSaveMsg(null);
  };

  async function save() {
    revision.current += 1;
    setTest({}); setSync({}); setHealth(null);
    setSaving(true); setSaveMsg(null);
    const payload = {};
    Object.keys(meta || {}).forEach((k) => {
      const v = form[k] || "";
      if (meta[k].type === "secret") { if (v) payload[k] = v; }  // blank keeps existing
      else payload[k] = v;
    });
    try {
      await store.saveSettings(payload);
      hydrate(await store.getSettings());
      setSaveMsg({ ok: true, text: "Saved." });
    } catch (e) { setSaveMsg({ ok: false, text: e.message }); }
    setSaving(false);
  }

  async function runTest(name) {
    if (dirty || saving) return;
    const testedRevision = revision.current;
    setTest((t) => Object.assign({}, t, { [name]: { busy: true } }));
    try {
      const r = await store.testConnector(name);
      if (testedRevision === revision.current) setTest((t) => Object.assign({}, t, { [name]: { busy: false, ok: !!r.ok, detail: r.detail || "", revision: testedRevision } }));
    } catch (e) {
      if (testedRevision === revision.current) setTest((t) => Object.assign({}, t, { [name]: { busy: false, ok: false, detail: e.message } }));
    }
  }

  function summarize(r) {
    if (r && typeof r === "object") {
      if (r.created != null || r.updated != null || r.count != null) {
        const parts = [];
        if (r.created != null) parts.push((Array.isArray(r.created) ? r.created.length : r.created) + " created");
        if (r.updated != null) parts.push((Array.isArray(r.updated) ? r.updated.length : r.updated) + " updated");
        if (r.skipped != null) parts.push((Array.isArray(r.skipped) ? r.skipped.length : r.skipped) + " skipped");
        if (r.count != null && !parts.length) parts.push(r.count + " items");
        if (parts.length) return parts.join(", ");
      }
    }
    const s = JSON.stringify(r);
    return s && s.length > 80 ? s.slice(0, 80) + "…" : (s || "done");
  }

  async function runSync(name) {
    if (dirty || saving || !test[name] || !test[name].ok || test[name].revision !== revision.current) return;
    setSync((s) => Object.assign({}, s, { [name]: { busy: true } }));
    try {
      const r = await store.syncConnector(name);
      setSync((s) => Object.assign({}, s, { [name]: { busy: false, ok: true, summary: summarize(r) } }));
    } catch (e) {
      setSync((s) => Object.assign({}, s, { [name]: { busy: false, ok: false, summary: e.message } }));
    }
  }

  return (
    <Card style={{ marginBottom: 18 }}>
      <SectionTitle>Connectors</SectionTitle>
      {!meta && !loadErr ? <p style={{ fontSize: 12.5, color: "var(--text-3)", margin: "10px 0 0" }}>Loading…</p> : null}
      {CONNECTORS.map((c) => (
        <div key={c.name} style={{ padding: "14px 0", borderBottom: "1px solid var(--border)" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 7, flex: 1 }}>
              <div style={{ fontWeight: 650, fontSize: 13.5 }}>{c.label}</div>
              {health && health[c.name] ? (
                <span title={health[c.name].detail || ""} style={{ display: "inline-flex", alignItems: "center", gap: 5 }}>
                  <span style={{
                    width: 7, height: 7, borderRadius: 99, flex: "none",
                    background: health[c.name].ok ? "var(--st-done-dot)" : "var(--st-failed-dot)",
                  }}></span>
                  <span style={{ fontSize: 11.5, color: "var(--text-3)" }}>{health[c.name].detail || (health[c.name].ok ? "ok" : "unreachable")}</span>
                </span>
              ) : null}
            </div>
            <Btn small disabled={dirty || saving || !meta || (test[c.name] && test[c.name].busy)} onClick={() => runTest(c.name)}>
              {test[c.name] && test[c.name].busy ? "Testing…" : "Test connection"}
            </Btn>
            {live && !dirty && !saving && test[c.name] && test[c.name].ok && test[c.name].revision === revision.current ? (
              <Btn small disabled={sync[c.name] && sync[c.name].busy} onClick={() => runSync(c.name)}>
                {sync[c.name] && sync[c.name].busy ? "Importing…" : "Import now"}
              </Btn>
            ) : null}
          </div>
          {c.fields.map((k) => (meta && meta[k]) ? (
            <div key={k} style={{ display: "flex", alignItems: "center", gap: 12, marginTop: 8 }}>
              <label htmlFor={"connector-" + k} style={{ flex: 1, fontSize: 12.5, color: "var(--text-2)" }}>{meta[k].label}</label>
              <input id={"connector-" + k} value={form[k] || ""} onChange={(e) => set(k, e.target.value)} disabled={saving}
                type={meta[k].type === "secret" ? "password" : "text"}
                placeholder={meta[k].type === "secret" ? (meta[k].value ? "•••• set — blank keeps it" : "not set") : (meta[k].placeholder || "")}
                style={inputStyle} />
            </div>
          ) : null)}
          {test[c.name] && !test[c.name].busy ? (
            <div style={{ marginTop: 8, fontSize: 12.5, fontWeight: 600, color: test[c.name].ok ? "var(--st-done-fg)" : "var(--st-failed-fg)" }}>
              {(test[c.name].ok ? "✓ " : "✗ ") + (test[c.name].detail || (test[c.name].ok ? "Connected." : "Not reachable."))}
            </div>
          ) : null}
          {sync[c.name] && !sync[c.name].busy ? (
            <div style={{ marginTop: 6, fontSize: 12, color: sync[c.name].ok ? "var(--st-done-fg)" : "var(--st-failed-fg)" }}>
              {sync[c.name].ok ? "✓ Synced — " + sync[c.name].summary : "✗ " + sync[c.name].summary}
            </div>
          ) : null}
        </div>
      ))}
      <div style={{ display: "flex", alignItems: "center", gap: 12, paddingTop: 14 }}>
        <Btn kind="primary" disabled={saving || !meta} onClick={save}>{saving ? "Saving…" : "Save connectors"}</Btn>
        {saveMsg ? <span style={{ fontSize: 12.5, color: saveMsg.ok ? "var(--st-done-fg)" : "var(--st-failed-fg)" }}>{saveMsg.text}</span> : null}
        {loadErr ? <span style={{ fontSize: 12.5, color: "var(--st-failed-fg)" }}>{loadErr}</span> : null}
      </div>
      {dirty ? <p role="status">Save changes before testing. Previous test results no longer apply.</p> : <p>Tests use saved gateway settings. Import now can create or update work.</p>}
    </Card>
  );
}

const ORG_NAME_RE = /^[A-Za-z0-9._:-]{1,64}$/;

function TenancyCard({ tone, advanced }) {
  const store = window.BitCadenceStore;
  const live = (store.mode ? store.mode() : "demo") === "live";
  const [orgs, setOrgs] = useStateO(null);   // { orgs, in_use, host_operator } | null
  const [loadErr, setLoadErr] = useStateO(null);
  const [newOrg, setNewOrg] = useStateO("");
  const [addErr, setAddErr] = useStateO(null);
  const [adding, setAdding] = useStateO(false);
  const inputStyle = { border: "1px solid var(--border-strong)", borderRadius: 8, padding: "7px 12px", fontSize: 13, fontFamily: "var(--font-mono)", width: 220, background: "var(--surface)", color: "var(--text)" };

  function refresh() {
    setLoadErr(null);
    return store.getOrgs().then((d) => setOrgs(d)).catch((e) => setLoadErr(e.message));
  }

  useEffectO(() => {
    if (!live) { setOrgs(null); return; }
    let alive = true;
    store.getOrgs().then((d) => { if (alive) setOrgs(d); }).catch((e) => { if (alive) setLoadErr(e.message); });
    return () => { alive = false; };
  }, [live]);

  if (!live) {
    return (
      <Card style={{ marginBottom: 18 }}>
        <SectionTitle>Tenancy</SectionTitle>
        <p style={{ fontSize: 12.5, color: "var(--text-2)", margin: "10px 0 0", maxWidth: 520 }}>
          {tone === "plain"
            ? "Connect to your orchestrator above, then set up orgs to keep different teams' agents and jobs separate."
            : "Connect a live gateway above to manage MCO_ORGS and move agents between tenants."}
        </p>
      </Card>
    );
  }

  async function addOrg() {
    const name = newOrg.trim();
    setAddErr(null);
    if (!name) { setAddErr(tone === "plain" ? "Type an org name first." : "Org name is required."); return; }
    if (!ORG_NAME_RE.test(name)) {
      setAddErr(tone === "plain" ? "Use only letters, digits, and . _ : - (max 64 chars)." : "Invalid org name — letters, digits, and . _ : - only (max 64 chars).");
      return;
    }
    const existing = (orgs && orgs.orgs) || [];
    if (existing.indexOf(name) !== -1) {
      setAddErr(tone === "plain" ? `"${name}" already exists.` : `Org "${name}" is already configured.`);
      return;
    }
    setAdding(true);
    try {
      const settings = await store.getSettings();
      const tenancyGroup = (settings.groups && settings.groups.tenancy) || [];
      const item = tenancyGroup.find((s) => s.key === "MCO_ORGS");
      const current = (item && item.value) || "";
      const parts = String(current).split(",").map((s) => s.trim()).filter(Boolean);
      parts.push(name);
      await store.saveSettings({ MCO_ORGS: parts.join(", ") });
      setNewOrg("");
      await refresh();
    } catch (e) { setAddErr(e.message); }
    setAdding(false);
  }

  if (orgs && orgs.host_operator === false) {
    return (
      <Card style={{ marginBottom: 18 }}>
        <SectionTitle>Tenancy</SectionTitle>
        <p style={{ fontSize: 12.5, color: "var(--text-3)", margin: "10px 0 0" }}>
          {tone === "plain" ? "Tenants are managed by your host operator." : "Org management requires the host operator (default org)."}
        </p>
      </Card>
    );
  }

  const inUse = (orgs && orgs.in_use) || [];
  const list = (orgs && orgs.orgs) || [];

  return (
    <Card style={{ marginBottom: 18 }}>
      <SectionTitle>Tenancy</SectionTitle>
      {!orgs && !loadErr ? <p style={{ fontSize: 12.5, color: "var(--text-3)", margin: "10px 0 0" }}>Loading…</p> : null}
      {list.map((o) => (
        <div key={o} style={{
          display: "flex", alignItems: "center", gap: 10, padding: "10px 0",
          borderBottom: "1px solid var(--border)",
        }}>
          <Mono style={{ fontSize: 13, color: "var(--text)", fontWeight: 600 }}>{o}</Mono>
          {o === "default" ? <span style={{ fontSize: 11.5, color: "var(--text-3)" }}>(host)</span> : null}
          {inUse.indexOf(o) !== -1 ? (
            <span style={{
              fontSize: 11, fontWeight: 600, color: "var(--st-done-fg)", background: "var(--st-done-bg)",
              padding: "2px 9px", borderRadius: 999,
            }}>in use</span>
          ) : null}
        </div>
      ))}
      <div style={{ display: "flex", alignItems: "center", gap: 10, paddingTop: 14 }}>
        <input value={newOrg} onChange={(e) => setNewOrg(e.target.value)}
          placeholder={tone === "plain" ? "new org name" : "org name"} style={inputStyle} />
        <Btn kind="primary" small disabled={adding || !newOrg.trim()} onClick={addOrg}>
          {adding ? "Adding…" : "Add org"}
        </Btn>
        {addErr ? <span style={{ fontSize: 12, color: "var(--st-failed-fg)" }}>{addErr}</span> : null}
        {loadErr ? <span style={{ fontSize: 12, color: "var(--st-failed-fg)" }}>{loadErr}</span> : null}
      </div>
      <p style={{ fontSize: 11.5, color: "var(--text-3)", margin: "14px 0 0" }}>
        {tone === "plain"
          ? "Orgs keep teams' jobs and memory completely separate."
          : "Orgs are hard tenant boundaries — jobs, Drumline memory, and agent registrations never cross them."}
      </p>
    </Card>
  );
}

function Settings({ tone, advanced, setAdvanced }) {
  const store = window.BitCadenceStore;
  const conn = store.config ? store.config() : { url: "http://127.0.0.1:18789", token: "" };
  const mode = store.mode ? store.mode() : "demo";
  const [ntfy, setNtfy] = useStateO(true);
  const [approvers, setApprovers] = useStateO("human, admin, operator");
  const [url, setUrl] = useStateO(conn.url || "http://127.0.0.1:18789");
  const [token, setToken] = useStateO(conn.token || "");
  const [busy, setBusy] = useStateO(false);
  const err = store.lastError ? store.lastError() : null;
  const inputStyle = { border: "1px solid var(--border-strong)", borderRadius: 8, padding: "7px 12px", fontSize: 13, fontFamily: "var(--font-mono)", width: 260, background: "var(--surface)", color: "var(--text)" };

  const body = (
    <div style={{ maxWidth: 720 }}>
      <Card style={{ marginBottom: 18 }}>
        <SectionTitle>Connection</SectionTitle>
        <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "12px 0", borderBottom: "1px solid var(--border)" }}>
          <span style={{
            width: 9, height: 9, borderRadius: 99, flex: "none",
            background: mode === "live" ? "var(--st-done-dot)" : mode === "connecting" ? "var(--st-waiting-dot)" : "var(--st-approval-dot)",
            animation: mode !== "demo" ? "cadence-pulse 1.6s infinite" : "none",
          }}></span>
          <div style={{ flex: 1 }}>
            <div style={{ fontWeight: 600, fontSize: 13.5 }}>
              {mode === "live" ? "Live — connected to your orchestrator" : mode === "connecting" ? "Connecting…" : mode === "offline" ? "Offline — connection unavailable" : "Demo mode — simulated data"}
            </div>
            <div style={{ fontSize: 12.5, color: "var(--text-2)", marginTop: 2 }}>
              {mode === "live"
                ? (tone === "plain" ? "Everything you see is real, updating live. Actions affect real jobs." : "Live events via /ws/broadcast, with /api polling as fallback, using your bearer token.")
                : (tone === "plain" ? "Connect to your running server to see real jobs and agents." : "Point this console at a running `mco serve` gateway to go live.")}
            </div>
          </div>
          {mode === "live" ? <Btn small kind="danger" onClick={() => store.disconnect()}>Disconnect</Btn> : null}
        </div>
        <SettingRow
          title="Gateway URL"
          body={tone === "plain" ? "Where your orchestrator server is running." : "The mco serve REST endpoint (default http://127.0.0.1:18789)."}
          control={<input value={url} onChange={(e) => setUrl(e.target.value)} disabled={mode === "live"} style={inputStyle} />} />
        <SettingRow
          title="Agent token"
          body={tone === "plain" ? "Proves it's you. Use an approver token to decide approval gates." : "Bearer token from `mco register`. Approver role (human/admin/operator) required for approve/reject."}
          control={<input value={token} onChange={(e) => setToken(e.target.value)} disabled={mode === "live"} type="password" placeholder="paste token…" style={inputStyle} />} />
        {mode !== "live" ? (
          <div style={{ display: "flex", alignItems: "center", gap: 12, paddingTop: 14 }}>
            <Btn kind="primary" disabled={busy || !url.trim() || !token.trim()} onClick={async () => {
              setBusy(true);
              await store.connect(url, token);
              setBusy(false);
            }}>{busy ? "Connecting…" : "Connect"}</Btn>
            {err ? <span style={{ fontSize: 12.5, color: "var(--st-failed-fg)" }}>{err}</span>
              : <span style={{ fontSize: 12.5, color: "var(--text-3)" }}>{tone === "plain" ? "Connect to load real jobs and settings." : "Connection is verified against GET /api/agents before switching."}</span>}
          </div>
        ) : err ? <div style={{ paddingTop: 12, fontSize: 12.5, color: "var(--st-failed-fg)" }}>Last poll error: {err}</div> : null}
      </Card>

      <Card style={{ marginBottom: 18 }}>
        <SectionTitle>Experience</SectionTitle>
        <SettingRow
          title="Technical details"
          body={tone === "plain" ? "Show the technical layer: raw IDs, payloads, retry budgets, and YAML." : "Expose payload JSON, full UUIDs, retry/escalation config, and workflow YAML."}
          control={<Toggle on={advanced} onChange={setAdvanced} />} />
      </Card>
      <LiveControlSettings />

      <ConnectorsCard tone={tone} />
      {advanced ? <TenancyCard tone={tone} advanced={advanced} /> : null}


    </div>
  );
  return tone === "plain" ? <PlainSettings>{body}</PlainSettings> : body;
}

// Settings for people (design/redesign-v1/09-settings.html): plain labels up front; addresses,
// sign-in tokens and raw gateway fields only inside "Show advanced".
function PlainSettings({ children }) {
  const store = window.BitCadenceStore;
  const live = (store.mode ? store.mode() : "demo") === "live";
  const [paused, setPaused] = useStateO(null);
  const [phone, setPhone] = useStateO(null);
  const [note, setNote] = useStateO("");
  const [error, setError] = useStateO("");
  const [busy, setBusy] = useStateO(false);
  const [confirmStop, setConfirmStop] = useStateO(false);
  async function load() {
    try {
      const rows = Object.values((await store.getSettings()).groups || {}).flat();
      const find = (k) => rows.find((r) => r.key === k);
      setPaused(!!(find("MCO_KILL_SWITCH") && find("MCO_KILL_SWITCH").value));
      setPhone(!!(find("NTFY_TOPIC") && find("NTFY_TOPIC").value));
    } catch (e) { setError(helperMessage(e)); }
  }
  useEffectO(() => { if (live) load(); }, [live]);
  async function flipPause() {
    setBusy(true); setError(""); setNote("");
    try {
      await store.saveSettings({ MCO_KILL_SWITCH: !paused });
      setNote(paused ? "New work can start again. Halted jobs need Retry." : "Active attempts halted. New work is paused.");
      setConfirmStop(false);
      await load();
    } catch (e) { setError(helperMessage(e)); }
    setBusy(false);
  }
  return (
    <div className="hp-view">
      <style>{HELPERS_STYLE}</style>
      <h1>Settings</h1>
      <p className="sub">The everyday things are on the other pages. This is the rest.</p>
      {!live ? <div className="hp-card">Connect to your BitCadence below. Actions require the permissions of your existing token.</div> : null}
      {error ? <div className="hp-error" role="alert" style={{ marginBottom: 14 }}>{error}</div> : null}
      <div role="status" aria-live="polite">{note ? <div className="hp-card">{note}</div> : null}</div>
      {live ? (
        <div className="hp-grid" style={{ marginBottom: 18 }}>
          <div className="hp-card"><b>Stop work</b>
            <p className="hp-hint">Halts active attempts and pauses new work. Resume allows new work; halted jobs need Retry.</p>
            <p className="hp-hint">{paused === null ? "Checking…" : paused ? "Paused right now." : "Running normally."}</p>
            {confirmStop && !paused ? <div role="group" aria-label="Confirm stop work">
              <p>This halts active attempts now and pauses new work. Their external effects are not undone. Nothing has changed yet.</p>
              <button disabled={busy} onClick={flipPause}>Stop work now</button>
              <button disabled={busy} onClick={() => setConfirmStop(false)}>Cancel</button>
            </div> : <button disabled={busy || paused === null} onClick={paused ? flipPause : () => setConfirmStop(true)}>{paused ? "Resume" : "Stop work…"}</button>}</div>
          <div className="hp-card"><b>Tell me on my phone</b>
            <p className="hp-hint">A message when something needs you.</p>
            <p className="hp-hint">{phone === null ? "Checking…" : phone ? "On" : "Off. Turn it on from the terminal: bitcadence settings."}</p></div>
          <div className="hp-card"><b>Memory</b>
            <p className="hp-hint">Things BitCadence remembers for you are under Drumline in the menu.</p></div>
        </div>
      ) : null}
      {!live ? children : <details className="hp-card">
        <summary style={{ minHeight: 48, cursor: "pointer", fontWeight: 600 }}>Server administration</summary>
        <p className="hp-hint">For people who like details: where BitCadence listens, the sign-in for this computer, and every raw setting.</p>
        {children}
      </details>}
    </div>
  );
}

Object.assign(window, { AgentFleet, HelpersPage, ConnectPage, SchedulesPage, Settings, PlainSettings, Toggle, SettingRow, ConnectorsCard });


function LiveControlSettings() {
  const store = window.BitCadenceStore;
  const live = store.mode() === "live";
  const [groups, setGroups] = useStateO(null);
  const [form, setForm] = useStateO({});
  const [dirty, setDirty] = useStateO({});
  const [message, setMessage] = useStateO("");
  const [busy, setBusy] = useStateO(false);
  const hydrate = (data) => {
    const selected = ["governance", "notifications", "presence", "memory"];
    const g = Object.fromEntries(selected.map(k => [k, (data.groups || {})[k] || []]));
    setGroups(g);
    setForm(Object.fromEntries(Object.values(g).flat().map(f => [f.key, f.value])));
    setDirty({});
  };
  useEffectO(() => {
    if (live) store.getSettings().then(hydrate).catch(e => setMessage(e.message));
  }, [live]);
  const change = (key, value) => {
    setForm(f => ({...f, [key]: value}));
    setDirty(d => ({...d, [key]: true}));
  };
  return <Card style={{marginBottom:18}}>
    <SectionTitle>Gateway controls</SectionTitle>
    {!live ? <p>Connect to manage work, notifications, and approval policy.</p> : null}
    {live && groups ? Object.entries(groups).map(([name, fields]) => <div key={name}>
      <h3 style={{textTransform:"capitalize",fontSize:14}}>{name}</h3>
      {fields.map(field => <SettingRow key={field.key} title={field.label}
        body={field.key === "MCO_KILL_SWITCH" ? "Stops active attempts and pauses new work. Turning it off leaves halted jobs for you to retry." : ""}
        control={field.type === "bool" ? <Toggle label={field.label} on={!!form[field.key]} onChange={v => change(field.key,v)} /> :
          field.type === "choice" ? <select aria-label={field.label} value={form[field.key] ?? ""} onChange={e => change(field.key,e.target.value)}>
            {(field.choices || []).map(value => <option key={value} value={value}>{value || "Infer from configuration"}</option>)}
          </select> :
          <input aria-label={field.label} value={form[field.key] || ""} onChange={e => change(field.key,e.target.value)}
            style={{border:"1px solid var(--border)",padding:8,borderRadius:6,width:280}} />} />)}
    </div>) : null}
    {live ? <Btn kind="primary" disabled={busy || !Object.keys(dirty).length} onClick={async () => {
      if (dirty.MCO_KILL_SWITCH && form.MCO_KILL_SWITCH && !window.confirm("Stop work now? Active attempts halt and new work pauses. Resume allows new work; halted jobs need Retry.")) return;
      setBusy(true);setMessage("");
      try {
        await store.saveSettings(Object.fromEntries(Object.keys(dirty).map(k => [k,form[k]])));
        hydrate(await store.getSettings());setMessage("Settings saved and recorded in the audit trail.");
      } catch(e) { setMessage(e.message); }
      finally { setBusy(false); }
    }}>{busy ? "Saving…" : "Save gateway settings"}</Btn> : null}
    {message ? <p role="status">{message}</p> : null}
  </Card>;
}
