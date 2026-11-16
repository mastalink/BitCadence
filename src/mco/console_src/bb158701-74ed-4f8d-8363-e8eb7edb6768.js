// BitCadence — "Ask for something" (design/redesign-v1/05-ask.html).
// A plain-language request, a drawn plan, one Approve, and only light tweaks:
// remove a step, always ask me, make it repeat. The gateway draws the plan with
// the same planner as `bitcadence ask`, so the console and the terminal agree.
// Workflow YAML files still run through `bitcadence ask --file` and `mco workflow`.
const { useState: useStateK, useRef: useRefK } = React;

const ASK_EXAMPLES = [
  ["Summarize my week", "Look at what happened this week, then write a short summary"],
  ["Check my website daily", "Check my website, then tell me if anything looks wrong"],
];
const ASK_REPEATS = [
  ["every day at 9 AM", "Every day at 9 AM"],
  ["every weekday at 9 AM", "Every weekday at 9 AM"],
  ["every Friday at 9 AM", "Every Friday at 9 AM"],
];
const ASK_STYLE = `
 .ask-view{max-width:760px;margin:0 auto;line-height:1.6}.ask-view h1{margin:0 0 6px}.ask-view .sub{color:var(--text-2);margin:0 0 18px}
 .ask-card{background:var(--surface);color:var(--text);border:1px solid var(--border);border-radius:14px;padding:20px;margin-bottom:16px;min-width:0;overflow-wrap:anywhere}
 .ask-view label{display:block;font-weight:600;margin-bottom:6px}
 .ask-view textarea{width:100%;min-height:110px;padding:12px;border:1px solid var(--border-strong);border-radius:10px;background:var(--surface);color:var(--text);font:inherit;resize:vertical}
 .ask-view button,.ask-view select{min-height:48px;min-width:48px;padding:10px 18px;border:1px solid var(--border-strong);border-radius:10px;background:var(--surface);color:var(--text);font:inherit;cursor:pointer}
 .ask-view textarea,.ask-view select{box-sizing:border-box;max-width:100%;min-width:0}.ask-view button{max-width:100%;white-space:normal}.ask-view button[disabled]{opacity:.55;cursor:default}
 .ask-primary{background:var(--accent)!important;border-color:var(--accent-strong)!important;color:#fff!important;font-weight:600}
 .ask-good{border-width:2px!important;font-weight:600}
 .ask-view button[aria-pressed="true"]{border-width:2px;font-weight:600}
 .ask-row{display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin-top:12px}
 .ask-view{min-width:0}.ask-flow{display:flex;flex-direction:column;align-items:stretch}
 .ask-node{display:flex;flex-wrap:wrap;gap:12px;overflow-wrap:anywhere;align-items:center;justify-content:space-between;border:1px solid var(--border-strong);border-radius:12px;padding:10px 14px;min-height:48px;background:var(--surface-2)}
 .ask-node[data-gate="true"]{border-style:dashed;border-width:2px}
 .ask-node>span{flex:1 1 180px;min-width:0}
 .ask-edge{margin:2px 0 2px 22px;padding-left:14px;border-left:3px solid var(--border-strong);min-height:20px;color:var(--text-2);font-size:13px}
 .ask-hint{color:var(--text-2);font-size:14px}.ask-error{border:2px solid #b42318;border-radius:10px;padding:10px 14px;color:var(--text)}
 .ask-view :focus-visible{outline:3px solid var(--accent);outline-offset:3px}
 @media(prefers-reduced-motion:reduce){.ask-view *{animation:none!important;transition:none!important}}
`;

function askMessage(e) {
  // The console API wraps errors as "HTTP 400 — detail"; people only need the detail.
  const text = String((e && e.message) || e || "");
  return text.replace(/^HTTP \d+\s*—\s*/, "") || "That didn't work. Try again.";
}

function AskPage({ onNav }) {
  const store = window.BitCadenceStore;
  const agents = store.getAgents() || [];
  const online = agents.filter((a) => (a.effective_status || a.status) === "online" && a.role && !["operator", "human"].includes(a.role));
  const role = (online[0] || agents.find((a) => a.role && !["operator", "human"].includes(a.role)) || {}).role || "claude";

  const [text, setText] = useStateK("");
  const [plan, setPlan] = useStateK(null);
  const [removed, setRemoved] = useStateK([]);
  const [askEnd, setAskEnd] = useStateK(false);
  const [repeat, setRepeat] = useStateK("");
  const [pickRepeat, setPickRepeat] = useStateK(false);
  const [removing, setRemoving] = useStateK(false);
  const [busy, setBusy] = useStateK(false);
  const [error, setError] = useStateK("");
  const [started, setStarted] = useStateK(null);
  const status = useRefK(null);

  const body = (over) => Object.assign({ request: text, role, remove: removed, ask_at_end: askEnd, repeat }, over || {});
  const run = async (fn) => {
    setBusy(true); setError("");
    try { return await fn(); } catch (e) { setError(askMessage(e)); return null; } finally { setBusy(false); }
  };
  const redraw = async (over) => {
    const next = await run(() => store.draftAsk(body(over)));
    if (next) setPlan(next);
    return next;
  };
  const draft = async () => {
    setRemoved([]); setAskEnd(false); setRepeat(""); setPickRepeat(false); setRemoving(false);
    await redraw({ remove: [], ask_at_end: false, repeat: "" });
  };
  const removeStep = async (id) => {
    const list = removed.concat(id);
    if (await redraw({ remove: list })) setRemoved(list);
  };
  const toggleAskEnd = async () => {
    if (await redraw({ ask_at_end: !askEnd })) setAskEnd(!askEnd);
  };
  const chooseRepeat = async (phrase) => {
    if (await redraw({ repeat: phrase })) { setRepeat(phrase); setPickRepeat(false); }
  };
  const approve = async () => {
    const res = await run(() => store.startAsk(body()));
    if (res) setStarted(res);
  };
  const again = () => { setPlan(null); setStarted(null); setText(""); setError(""); };

  if (started) {
    return (
      <div className="ask-view"><style>{ASK_STYLE}</style>
        <h1>Approved. Starting now.</h1>
        <p className="sub">{started.repeat ? "It will also repeat: " + started.repeat + "." : "Your helpers have the plan."}</p>
        {started.repeat_error ? <p role="alert" className="ask-error">{started.repeat_error}</p> : null}
        <div className="ask-row">
          <button className="ask-primary" onClick={() => onNav("overview")}>Watch it on Home</button>
          <button onClick={again}>Ask for something else</button>
        </div>
      </div>
    );
  }

  return (
    <div className="ask-view"><style>{ASK_STYLE}</style>
      <p className="sub">Say it the way you'd tell a person. We'll draw the plan and you check it.</p>
      {error ? <p role="alert" className="ask-error">{error}</p> : null}
      {!plan ? (
        <div className="ask-card">
          <label htmlFor="ask-request">What should happen?</label>
          <textarea id="ask-request" value={text} onChange={(e) => setText(e.target.value)}
            placeholder="For example: Research open pull requests, run the tests, then ask me to publish." />
          <div className="ask-row">
            {ASK_EXAMPLES.map(([label, example]) => <button key={label} onClick={() => setText(example)}>{label}</button>)}
          </div>
          <div className="ask-row">
            <button className="ask-primary" disabled={busy || !text.trim()} onClick={draft}>{busy ? "Drawing the plan…" : "Draft a plan"}</button>
          </div>
        </div>
      ) : (
        <div>
          <div className="ask-card">
            <h2 style={{ marginTop: 0 }}>Here's the plan</h2>
            <div className="ask-flow" role="list" aria-label="The plan, in order">
              {plan.steps.map((step, i) => (
                <React.Fragment key={step.id}>
                  {i > 0 ? <div className="ask-edge" aria-hidden={step.note ? undefined : "true"}>{step.note || ""}</div> : null}
                  <div className="ask-node" role="listitem" data-gate={step.gate ? "true" : "false"}>
                    <span><span aria-hidden="true">{step.icon} </span>{step.label}{step.gate ? <span> (you decide)</span> : null}</span>
                    {removing && plan.steps.length > 1 ? (
                      <button disabled={busy} aria-label={"Remove this step: " + step.label} onClick={() => removeStep(step.id)}>Remove</button>
                    ) : null}
                  </div>
                </React.Fragment>
              ))}
            </div>
            {plan.repeat ? <p className="ask-hint">Repeats: {plan.repeat.words}</p> : null}
            <p className="ask-hint">Light tweaks only. To change the plan itself, edit your request and draft again.</p>
            <div className="ask-row">
              <button aria-pressed={removing} disabled={busy || plan.steps.length < 2} onClick={() => setRemoving(!removing)}>Remove a step</button>
              <button aria-pressed={askEnd} disabled={busy} onClick={toggleAskEnd}>{askEnd ? "Asking you at the end: on" : "Always ask me at the end"}</button>
              <button aria-pressed={!!repeat} aria-expanded={pickRepeat} disabled={busy} onClick={() => setPickRepeat(!pickRepeat)}>{repeat ? "Repeats: " + plan.repeat.words : "Make it repeat"}</button>
            </div>
            {pickRepeat ? (
              <div className="ask-row" role="group" aria-label="How often">
                {ASK_REPEATS.map(([phrase, label]) => <button key={phrase} aria-pressed={repeat === phrase} disabled={busy} onClick={() => chooseRepeat(phrase)}>{label}</button>)}
                {repeat ? <button disabled={busy} onClick={() => chooseRepeat("")}>Don't repeat</button> : null}
              </div>
            ) : null}
          </div>
          <div className="ask-row">
            <button className="ask-primary ask-good" disabled={busy} onClick={approve}>{busy ? "Working…" : "Approve and start"}</button>
            <button disabled={busy} onClick={() => { setPlan(null); setError(""); }}>Change my request</button>
          </div>
        </div>
      )}
    </div>
  );
}

Object.assign(window, { AskPage });
