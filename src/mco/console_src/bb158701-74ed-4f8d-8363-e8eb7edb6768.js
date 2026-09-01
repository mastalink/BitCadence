// BitCadence — Visual workflow builder + live DAG viewer. No YAML required;
// YAML preview available in Advanced mode (matches the mco workflow DSL).
const { useState: useStateW, useRef: useRefW, useLayoutEffect, useMemo: useMemoW, useEffect: useEffectW } = React;

// ---- shared DAG layout: items = [{id, title, role, status?, deps:[], gate, retries, escalate}] ----
function levelsOf(items) {
  const byId = {}; items.forEach((s) => byId[s.id] = s);
  const memo = {};
  const lvl = (id, seen) => {
    if (memo[id] != null) return memo[id];
    if (seen.has(id)) return 0;
    seen.add(id);
    const s = byId[id]; if (!s) return 0;
    const deps = (s.deps || []).filter((d) => byId[d]);
    const v = deps.length ? 1 + Math.max(...deps.map((d) => lvl(d, seen))) : 0;
    memo[id] = v; return v;
  };
  items.forEach((s) => lvl(s.id, new Set()));
  const cols = [];
  items.forEach((s) => { const l = memo[s.id] || 0; (cols[l] = cols[l] || []).push(s); });
  return cols;
}

function DagCanvas({ items, tone, renderCard, highlight }) {
  const wrapRef = useRefW(null);
  const [lines, setLines] = useStateW([]);
  const cols = useMemoW(() => levelsOf(items), [items]);

  useLayoutEffect(() => {
    const wrap = wrapRef.current; if (!wrap) return;
    const w = wrap.getBoundingClientRect();
    const out = [];
    items.forEach((s) => {
      (s.deps || []).forEach((d) => {
        const from = wrap.querySelector(`[data-node="${d}"]`);
        const to = wrap.querySelector(`[data-node="${s.id}"]`);
        if (!from || !to) return;
        const a = from.getBoundingClientRect(), b = to.getBoundingClientRect();
        out.push({
          key: d + "-" + s.id,
          x1: a.right - w.left, y1: a.top + a.height / 2 - w.top,
          x2: b.left - w.left, y2: b.top + b.height / 2 - w.top,
          active: highlight === s.id || highlight === d,
        });
      });
    });
    setLines(out);
  }, [items, highlight]);

  return (
    <div ref={wrapRef} style={{ position: "relative", display: "flex", gap: 48, alignItems: "flex-start", overflowX: "auto", padding: "8px 4px 16px", minHeight: 180 }}>
      <svg style={{ position: "absolute", inset: 0, width: "100%", height: "100%", pointerEvents: "none", overflow: "visible" }}>
        {lines.map((l) => {
          const mx = (l.x1 + l.x2) / 2;
          return (
            <g key={l.key}>
              <path d={`M ${l.x1} ${l.y1} C ${mx} ${l.y1}, ${mx} ${l.y2}, ${l.x2} ${l.y2}`}
                fill="none" stroke={l.active ? "var(--accent)" : "var(--border-strong)"} strokeWidth={l.active ? 2 : 1.5} />
              <circle cx={l.x2} cy={l.y2} r="3" fill={l.active ? "var(--accent)" : "var(--border-strong)"} />
            </g>
          );
        })}
      </svg>
      {cols.map((col, i) => (
        <div key={i} style={{ display: "flex", flexDirection: "column", gap: 14, flex: "none", width: 230, position: "relative", zIndex: 1 }}>
          <div style={{ fontSize: 11, fontWeight: 600, color: "var(--text-3)", textTransform: "uppercase", letterSpacing: "0.05em" }}>
            {i === 0 ? (tone === "plain" ? "First" : "Stage 1") : (tone === "plain" ? "Then" : "Stage " + (i + 1))}
          </div>
          {col.map((s) => <div key={s.id} data-node={s.id}>{renderCard(s)}</div>)}
        </div>
      ))}
    </div>
  );
}

// ---- Builder ----
let tmpSeq = 0;
const newTmpId = () => "step-" + (++tmpSeq);

const FLOW_TEMPLATES = [
  {
    name: "release-pipeline", label: "Release pipeline",
    steps: [
      { title: "Research the change", role: "claude", instructions: "Summarize the open issues for the release." },
      { title: "Implement the change", role: "codex", instructions: "Apply the fixes identified by the research step.", after: [0], retries: 2, escalate: "human" },
      { title: "Run regression tests", role: "gemini", instructions: "Execute the regression suite.", after: [1] },
      { title: "Tag and publish", role: "codex", instructions: "Tag the release and publish artifacts.", after: [2], gate: true },
    ],
  },
  {
    name: "content-refresh", label: "Content refresh",
    steps: [
      { title: "Audit existing docs", role: "claude", instructions: "List stale pages and missing topics." },
      { title: "Draft updates", role: "claude", instructions: "Rewrite the stale pages.", after: [0] },
      { title: "Publish to site", role: "codex", instructions: "Commit and deploy the docs site.", after: [1], gate: true },
    ],
  },
];

function makeSteps(tpl) {
  const ids = tpl.steps.map(() => newTmpId());
  return tpl.steps.map((s, i) => ({
    id: ids[i], title: s.title, role: s.role, instructions: s.instructions || "",
    deps: (s.after || []).map((x) => ids[x]), gate: !!s.gate, retries: s.retries || 0, escalate: s.escalate || "",
  }));
}

function toYaml(name, steps) {
  const lines = ["name: " + (name || "untitled-flow"), "steps:"];
  steps.forEach((s) => {
    lines.push("  - id: " + s.id);
    lines.push("    role: " + s.role);
    lines.push("    title: " + s.title);
    if (s.instructions) lines.push("    instructions: " + s.instructions);
    if (s.deps.length) lines.push("    depends_on: [" + s.deps.join(", ") + "]");
    if (s.gate) lines.push("    requires_approval: true");
    if (s.retries) lines.push("    max_retries: " + s.retries);
    if (s.escalate) lines.push("    escalate_to_role: " + s.escalate);
  });
  return lines.join("\n");
}

function StepCard({ step, steps, tone, advanced, selected, onSelect, onChange, onRemove, dragHandlers }) {
  return (
    <div {...dragHandlers}
      onClick={() => onSelect(step.id)}
      style={{
        background: selected ? "var(--accent-soft)" : "var(--surface)",
        border: selected ? "1.5px solid var(--accent)" : "1px solid var(--border)",
        borderRadius: "var(--radius-m)", padding: 12, cursor: "grab", boxShadow: "var(--shadow-s)",
        userSelect: "none",
      }}>
      <div style={{ display: "flex", alignItems: "center", gap: 7, marginBottom: 7 }}>
        <RoleChip role={step.role} size={20} />
        <span style={{ fontSize: 11.5, fontWeight: 600, color: "var(--text-3)" }}>{step.role}</span>
        <span style={{ flex: 1 }}></span>
        {step.gate ? <span title="Approval gate" style={{ fontSize: 11, color: "var(--st-approval-fg)", background: "var(--st-approval-bg)", borderRadius: 99, padding: "2px 7px", fontWeight: 600 }}>✋ gate</span> : null}
        <button onClick={(e) => { e.stopPropagation(); onRemove(step.id); }} title="Remove step" style={{ border: "none", background: "transparent", color: "var(--text-3)", cursor: "pointer", fontSize: 14, padding: 0, lineHeight: 1 }}>×</button>
      </div>
      <div style={{ fontWeight: 600, fontSize: 13, lineHeight: 1.3 }}>{step.title || <span style={{ color: "var(--text-3)" }}>Untitled step</span>}</div>
      {step.deps.length ? (
        <div style={{ fontSize: 11, color: "var(--text-3)", marginTop: 5 }}>
          runs after {step.deps.map((d) => { const dep = steps.find((x) => x.id === d); return dep ? dep.title.split(" ").slice(0, 3).join(" ") : d; }).join(", ")}
        </div>
      ) : <div style={{ fontSize: 11, color: "var(--text-3)", marginTop: 5 }}>{tone === "plain" ? "starts right away" : "no dependencies"}</div>}
    </div>
  );
}

function WorkflowBuilder({ jobs, tone, advanced, onOpen }) {
  const [tab, setTab] = useStateW("running");
  const [steps, setSteps] = useStateW([]);
  const [name, setName] = useStateW("");
  const [sel, setSel] = useStateW(null);
  const [dragId, setDragId] = useStateW(null);
  const [dropTarget, setDropTarget] = useStateW(null);
  const selected = steps.find((s) => s.id === sel);

  const running = useMemoW(() => {
    const g = {};
    jobs.forEach((j) => { if (j.workflow) (g[j.workflow] = g[j.workflow] || []).push(j); });
    return Object.entries(g);
  }, [jobs]);

  const update = (id, patch) => setSteps((prev) => prev.map((s) => s.id === id ? Object.assign({}, s, patch) : s));
  const remove = (id) => { setSteps((prev) => prev.filter((s) => s.id !== id).map((s) => Object.assign({}, s, { deps: s.deps.filter((d) => d !== id) }))); if (sel === id) setSel(null); };
  const addStep = () => {
    const s = { id: newTmpId(), title: "", role: "codex", instructions: "", deps: [], gate: false, retries: 0, escalate: "" };
    setSteps((prev) => [...prev, s]); setSel(s.id);
  };
  const wouldCycle = (childId, parentId) => {
    // true if parentId is downstream of childId
    const downstream = (id, seen) => {
      if (seen.has(id)) return false; seen.add(id);
      const kids = steps.filter((s) => s.deps.includes(id));
      return kids.some((k) => k.id === parentId || downstream(k.id, seen));
    };
    return childId === parentId || downstream(childId, new Set());
  };

  const dragHandlersFor = (s) => ({
    draggable: true,
    onDragStart: (e) => { setDragId(s.id); e.dataTransfer.effectAllowed = "link"; },
    onDragEnd: () => { setDragId(null); setDropTarget(null); },
    onDragOver: (e) => { if (dragId && dragId !== s.id) { e.preventDefault(); setDropTarget(s.id); } },
    onDragLeave: () => { if (dropTarget === s.id) setDropTarget(null); },
    onDrop: (e) => {
      e.preventDefault();
      if (dragId && dragId !== s.id && !wouldCycle(dragId, s.id)) {
        // dragged card now runs after the drop target
        update(dragId, { deps: Array.from(new Set([...(steps.find((x) => x.id === dragId).deps), s.id])) });
        setSel(dragId);
      }
      setDragId(null); setDropTarget(null);
    },
  });

  const valid = steps.length > 0 && steps.every((s) => s.title.trim());
  const inputStyle = { width: "100%", border: "1px solid var(--border-strong)", borderRadius: 8, padding: "7px 11px", fontSize: 13, background: "var(--surface)", color: "var(--text)", outline: "none" };
  const lbl = { display: "block", fontSize: 12, fontWeight: 600, color: "var(--text-2)", margin: "12px 0 4px" };

  return (
    <div>
      <div style={{ display: "flex", gap: 2, background: "var(--surface)", border: "1px solid var(--border)", borderRadius: 8, padding: 3, width: "fit-content", marginBottom: 16 }}>
        {[["running", tone === "plain" ? "Running flows" : "In flight"], ["new", tone === "plain" ? "Build a flow" : "New workflow"]].map(([id, label]) => (
          <button key={id} onClick={() => setTab(id)} style={{
            border: "none", cursor: "pointer", borderRadius: 6, padding: "6px 14px", fontSize: 13, fontWeight: 600,
            background: tab === id ? "var(--accent-soft)" : "transparent", color: tab === id ? "var(--accent-text)" : "var(--text-2)",
          }}>{label}</button>
        ))}
      </div>

      {tab === "running" ? (
        running.length ? running.map(([wfName, list]) => {
          const items = list.map((j) => ({ id: j.id, title: j.title, role: j.target_agent_role, status: j.status, deps: j.depends_on, gate: j.requires_approval }));
          return (
            <Card key={wfName} style={{ marginBottom: 16 }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 6 }}>
                <div style={{ fontWeight: 650, fontSize: 14.5 }}>{wfName}</div>
                <span style={{ fontSize: 12, color: "var(--text-3)" }}>{list.filter((j) => j.status === "completed").length}/{list.length} done</span>
              </div>
              <DagCanvas items={items} tone={tone} renderCard={(s) => (
                <button onClick={() => onOpen(s.id)} style={{
                  display: "block", width: "100%", textAlign: "left", cursor: "pointer",
                  background: s.status === "in_progress" ? "var(--accent-soft)" : "var(--surface)",
                  border: s.status === "in_progress" ? "1.5px solid var(--accent)" : "1px solid var(--border)",
                  borderRadius: "var(--radius-m)", padding: 12, boxShadow: "var(--shadow-s)",
                }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 7 }}>
                    <RoleChip role={s.role} size={20} />
                    <StatusBadge status={s.status} tone={tone} />
                  </div>
                  <div style={{ fontWeight: 600, fontSize: 13, lineHeight: 1.3 }}>{s.title}</div>
                  {s.gate ? <div style={{ fontSize: 11, color: "var(--st-approval-fg)", marginTop: 4 }}>✋ approval gate</div> : null}
                </button>
              )} />
            </Card>
          );
        }) : <Card><EmptyState icon="⧗" title="No flows running" body="Build a flow and submit it to see it move through the pipeline here." /></Card>
      ) : (
        <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 1fr) 300px", gap: 18, alignItems: "start" }}>
          <div>
            <Card style={{ marginBottom: 14 }}>
              <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
                <input value={name} onChange={(e) => setName(e.target.value)} placeholder={tone === "plain" ? "Name this flow…" : "workflow name (kebab-case)"} style={Object.assign({}, inputStyle, { width: 240 })} />
                <Btn small onClick={addStep}>+ Add step</Btn>
                <span style={{ fontSize: 12, color: "var(--text-3)" }}>or start from:</span>
                {FLOW_TEMPLATES.map((t) => (
                  <Btn key={t.name} small kind="ghost" onClick={() => { setSteps(makeSteps(t)); setName(t.name); setSel(null); }}>{t.label}</Btn>
                ))}
              </div>
              {steps.length ? (
                <div style={{ marginTop: 8 }}>
                  <div style={{ fontSize: 12, color: "var(--text-3)", margin: "8px 0 2px" }}>
                    {tone === "plain" ? "Drag one step onto another to say “run this after that.” Click a step to edit it." : "Drag a step onto another to add a dependency edge. Click to edit."}
                  </div>
                  <DagCanvas items={steps} tone={tone} highlight={dropTarget || sel} renderCard={(s) => (
                    <div style={{ outline: dropTarget === s.id ? "2px dashed var(--accent)" : "none", outlineOffset: 3, borderRadius: "var(--radius-m)", opacity: dragId === s.id ? 0.5 : 1 }}>
                      <StepCard step={s} steps={steps} tone={tone} advanced={advanced} selected={sel === s.id}
                        onSelect={setSel} onChange={update} onRemove={remove} dragHandlers={dragHandlersFor(s)} />
                    </div>
                  )} />
                </div>
              ) : (
                <EmptyState icon="⊕" title={tone === "plain" ? "Start your flow" : "Empty workflow"} body={tone === "plain" ? "Add a step, or pick a template above." : "Add steps or load a template; each step becomes one job."} />
              )}
            </Card>
            {steps.length ? (
              <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
                <Btn kind="primary" disabled={!valid} onClick={() => {
                  window.BitCadenceStore.submitWorkflow(name || "untitled-flow", steps.map((s) => ({
                    tmpId: s.id, role: s.role, title: s.title, instructions: s.instructions,
                    depends_on: s.deps, requires_approval: s.gate, max_retries: s.retries, escalate_to_role: s.escalate || null,
                  })));
                  setSteps([]); setName(""); setSel(null); setTab("running");
                }}>▶ Submit flow</Btn>
                {!valid ? <span style={{ fontSize: 12.5, color: "var(--text-3)" }}>Every step needs a title before you can submit.</span>
                  : <span style={{ fontSize: 12.5, color: "var(--text-3)" }}>{steps.length} step{steps.length > 1 ? "s" : ""} · {steps.filter((s) => s.gate).length} approval gate{steps.filter((s) => s.gate).length === 1 ? "" : "s"}</span>}
              </div>
            ) : null}
            {advanced && steps.length ? (
              <Card style={{ marginTop: 14 }}>
                <SectionTitle>YAML (mco workflow DSL)</SectionTitle>
                <pre style={{ margin: 0, fontFamily: "var(--font-mono)", fontSize: 12, color: "var(--text-2)", whiteSpace: "pre-wrap" }}>{toYaml(name, steps)}</pre>
              </Card>
            ) : null}
          </div>

          <Card style={{ position: "sticky", top: 70 }}>
            {selected ? (
              <div>
                <SectionTitle>Edit step</SectionTitle>
                <label style={lbl}>What happens?</label>
                <input value={selected.title} onChange={(e) => update(selected.id, { title: e.target.value })} placeholder="e.g. Run the tests" style={inputStyle} />
                <label style={lbl}>{tone === "plain" ? "Instructions for the agent" : "Instructions"}</label>
                <textarea value={selected.instructions} onChange={(e) => update(selected.id, { instructions: e.target.value })} rows={3} style={Object.assign({}, inputStyle, { resize: "vertical" })}></textarea>
                <label style={lbl}>Who does it?</label>
                <div style={{ display: "flex", gap: 6 }}>
                  {["codex", "claude", "gemini"].map((r) => (
                    <button key={r} onClick={() => update(selected.id, { role: r })} style={{
                      flex: 1, padding: "7px 4px", borderRadius: 7, cursor: "pointer", fontSize: 12, fontWeight: 600,
                      border: selected.role === r ? "1.5px solid var(--accent)" : "1px solid var(--border-strong)",
                      background: selected.role === r ? "var(--accent-soft)" : "var(--surface)",
                      color: selected.role === r ? "var(--accent-text)" : "var(--text-2)",
                    }}>{r}</button>
                  ))}
                </div>
                <label style={lbl}>{tone === "plain" ? "Runs after" : "Depends on"}</label>
                <div style={{ display: "flex", flexDirection: "column", gap: 5 }}>
                  {steps.filter((s) => s.id !== selected.id && !wouldCycle(selected.id, s.id) || selected.deps.includes(s.id)).filter((s) => s.id !== selected.id).map((s) => {
                    const on = selected.deps.includes(s.id);
                    return (
                      <label key={s.id} style={{ display: "flex", gap: 8, alignItems: "center", fontSize: 12.5, cursor: "pointer" }}>
                        <input type="checkbox" checked={on} style={{ accentColor: "var(--accent)" }}
                          onChange={() => update(selected.id, { deps: on ? selected.deps.filter((d) => d !== s.id) : [...selected.deps, s.id] })} />
                        <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{s.title || "Untitled step"}</span>
                      </label>
                    );
                  })}
                  {steps.length < 2 ? <span style={{ fontSize: 12, color: "var(--text-3)" }}>Add more steps to chain them.</span> : null}
                </div>
                <label style={{ display: "flex", gap: 9, alignItems: "center", margin: "14px 0 0", cursor: "pointer", fontSize: 13 }}>
                  <input type="checkbox" checked={selected.gate} onChange={(e) => update(selected.id, { gate: e.target.checked })} style={{ width: 15, height: 15, accentColor: "var(--accent)" }} />
                  <span><b>Ask me first</b> <span style={{ color: "var(--text-3)" }}>{tone === "plain" ? "— pause here until approved" : "(requires_approval)"}</span></span>
                </label>
                {advanced ? (
                  <div style={{ borderTop: "1px dashed var(--border)", marginTop: 14, paddingTop: 2 }}>
                    <label style={lbl}>Retry budget</label>
                    <input type="number" min={0} max={5} value={selected.retries} onChange={(e) => update(selected.id, { retries: +e.target.value })} style={Object.assign({}, inputStyle, { width: 80 })} />
                    <label style={lbl}>Escalate to role</label>
                    <input value={selected.escalate} onChange={(e) => update(selected.id, { escalate: e.target.value })} placeholder="human" style={Object.assign({}, inputStyle, { width: 140 })} />
                  </div>
                ) : null}
              </div>
            ) : (
              <EmptyState icon="✎" title="Nothing selected" body={tone === "plain" ? "Click a step on the left to edit it." : "Select a step to edit its role, instructions, and governance."} />
            )}
          </Card>
        </div>
      )}
    </div>
  );
}

Object.assign(window, { WorkflowBuilder, DagCanvas });
