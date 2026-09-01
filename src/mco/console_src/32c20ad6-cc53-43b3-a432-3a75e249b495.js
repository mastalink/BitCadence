// BitCadence — shared UI primitives (badges, cards, tables, drawer, toasts).
const { useState, useEffect, useRef, useMemo } = React;

// ----- Copy tone: expert jargon vs plain language -----
const STATUS_COPY = {
  expert: {
    waiting: "Waiting", needs_approval: "Needs approval", pending: "Pending",
    leased: "Leased", in_progress: "In progress", completed: "Completed",
    failed: "Failed", rejected: "Rejected", online: "Online", offline: "Offline",
  },
  plain: {
    waiting: "Waiting its turn", needs_approval: "Needs your OK", pending: "Ready to start",
    leased: "Picked up", in_progress: "Working…", completed: "Done",
    failed: "Hit a problem", rejected: "Declined", online: "Online", offline: "Away",
  },
};
const STATUS_KIND = {
  waiting: "waiting", needs_approval: "approval", pending: "pending",
  leased: "active", in_progress: "active", completed: "done",
  failed: "failed", rejected: "rejected", online: "done", offline: "rejected",
};

function StatusBadge({ status, tone = "expert", pulse }) {
  const kind = STATUS_KIND[status] || "rejected";
  const label = (STATUS_COPY[tone] || STATUS_COPY.expert)[status] || status;
  return (
    <span style={{
      display: "inline-flex", alignItems: "center", gap: 6,
      background: `var(--st-${kind}-bg)`, color: `var(--st-${kind}-fg)`,
      borderRadius: 999, padding: "3px 10px 3px 8px", fontSize: 12, fontWeight: 600, whiteSpace: "nowrap",
    }}>
      <span style={{
        width: 7, height: 7, borderRadius: 999, background: `var(--st-${kind}-dot)`,
        animation: (pulse || status === "in_progress" || status === "leased") ? "cadence-pulse 1.6s ease-in-out infinite" : "none",
      }}></span>
      {label}
    </span>
  );
}

const ROLE_GLYPH = { codex: "Cₓ", claude: "Cl", gemini: "G", human: "H", operator: "Op", system: "S" };
const ROLE_HUE = { codex: 230, claude: 22, gemini: 200, human: 150, operator: 270, system: 0 };
function RoleChip({ role, size = 22 }) {
  const hue = ROLE_HUE[role] != null ? ROLE_HUE[role] : 0;
  const sat = ROLE_HUE[role] != null ? 0.07 : 0;
  return (
    <span title={role} style={{
      display: "inline-flex", alignItems: "center", justifyContent: "center",
      width: size, height: size, borderRadius: 6, flex: "none",
      background: `oklch(0.95 ${sat} ${hue})`, color: `oklch(0.45 ${sat * 2.2} ${hue})`,
      border: `1px solid oklch(0.88 ${sat} ${hue})`,
      fontSize: size * 0.42, fontWeight: 700, letterSpacing: "-0.02em",
    }}>{ROLE_GLYPH[role] || role.slice(0, 2)}</span>
  );
}

function Card({ children, style, pad = true }) {
  return (
    <div style={Object.assign({
      background: "var(--surface)", border: "1px solid var(--border)",
      borderRadius: "var(--radius-l)", boxShadow: "var(--shadow-s)",
      padding: pad ? "var(--card-pad)" : 0, overflow: "hidden",
    }, style)}>{children}</div>
  );
}

function SectionTitle({ children, action }) {
  return (
    <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 12 }}>
      <h2 style={{ font: "600 13px/1 var(--font-sans)", letterSpacing: "0.02em", textTransform: "uppercase", color: "var(--text-3)", margin: 0 }}>{children}</h2>
      {action || null}
    </div>
  );
}

function Btn({ children, kind = "default", small, onClick, disabled, style }) {
  const base = {
    border: "1px solid var(--border-strong)", background: "var(--surface)", color: "var(--text)",
    borderRadius: "var(--radius-s)", padding: small ? "5px 10px" : "7px 14px",
    fontSize: small ? 12.5 : 13.5, fontWeight: 500, cursor: disabled ? "default" : "pointer",
    opacity: disabled ? 0.5 : 1, boxShadow: "var(--shadow-s)", transition: "background .12s, border-color .12s",
  };
  const kinds = {
    primary: { background: "var(--accent)", borderColor: "var(--accent-strong)", color: "#fff" },
    ok: { background: "var(--st-done-bg)", borderColor: "var(--st-done-dot)", color: "var(--st-done-fg)" },
    danger: { background: "var(--surface)", borderColor: "var(--st-failed-dot)", color: "var(--st-failed-fg)" },
    ghost: { background: "transparent", borderColor: "transparent", boxShadow: "none", color: "var(--text-2)" },
  };
  return (
    <button disabled={disabled} onClick={onClick}
      style={Object.assign(base, kinds[kind] || {}, style)}
      onMouseEnter={(e) => { if (!disabled && (kind === "default" || kind === "ghost")) e.currentTarget.style.background = "var(--surface-2)"; }}
      onMouseLeave={(e) => { if (kind === "default") e.currentTarget.style.background = "var(--surface)"; if (kind === "ghost") e.currentTarget.style.background = "transparent"; }}
    >{children}</button>
  );
}

function timeAgo(iso) {
  if (!iso) return "—";
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 5) return "just now";
  if (s < 60) return Math.floor(s) + "s ago";
  if (s < 3600) return Math.floor(s / 60) + "m ago";
  if (s < 86400) return Math.floor(s / 3600) + "h ago";
  return Math.floor(s / 86400) + "d ago";
}
function shortId(id) { return String(id || "").slice(0, 8); }

function Mono({ children, style }) {
  return <span style={Object.assign({ fontFamily: "var(--font-mono)", fontSize: "0.92em", color: "var(--text-2)" }, style)}>{children}</span>;
}

// ----- Table primitives -----
function THead({ cols }) {
  return (
    <thead>
      <tr>
        {cols.map((c, i) => (
          <th key={i} style={{
            textAlign: "left", padding: "8px 14px", fontSize: 11.5, fontWeight: 600,
            color: "var(--text-3)", textTransform: "uppercase", letterSpacing: "0.04em",
            borderBottom: "1px solid var(--border)", background: "var(--surface-2)", whiteSpace: "nowrap",
          }}>{c}</th>
        ))}
      </tr>
    </thead>
  );
}
function Td({ children, style, onClick }) {
  return <td onClick={onClick} style={Object.assign({ padding: "var(--row-pad) 14px", borderBottom: "1px solid var(--border)", verticalAlign: "middle" }, style)}>{children}</td>;
}

function EmptyState({ icon, title, body }) {
  return (
    <div style={{ padding: "36px 20px", textAlign: "center", color: "var(--text-3)" }}>
      <div style={{ fontSize: 26, marginBottom: 8, opacity: 0.7 }}>{icon}</div>
      <div style={{ fontWeight: 600, color: "var(--text-2)", marginBottom: 3 }}>{title}</div>
      <div style={{ fontSize: 13 }}>{body}</div>
    </div>
  );
}

// ----- Drawer -----
function Drawer({ open, onClose, children, width = 480 }) {
  if (!open) return null;
  return (
    <div style={{ position: "fixed", inset: 0, zIndex: 60 }}>
      <div onClick={onClose} style={{ position: "absolute", inset: 0, background: "rgba(23,23,28,0.28)" }}></div>
      <div style={{
        position: "absolute", top: 10, right: 10, bottom: 10, width, maxWidth: "calc(100vw - 40px)",
        background: "var(--surface)", borderRadius: "var(--radius-l)", border: "1px solid var(--border)",
        boxShadow: "var(--shadow-l)", display: "flex", flexDirection: "column", overflow: "hidden",
        animation: "cadence-drawer-in .18s ease-out",
      }}>{children}</div>
    </div>
  );
}

// ----- Toasts -----
function ToastHost() {
  const [toasts, setToasts] = useState([]);
  useEffect(() => {
    return window.BitCadenceStore.onToast((t) => {
      setToasts((prev) => [...prev.slice(-3), t]);
      setTimeout(() => setToasts((prev) => prev.filter((x) => x.id !== t.id)), 4200);
    });
  }, []);
  const KIND = {
    ok: { dot: "var(--st-done-dot)" }, err: { dot: "var(--st-failed-dot)" },
    approval: { dot: "var(--st-approval-dot)" }, info: { dot: "var(--st-active-dot)" },
  };
  return (
    <div style={{ position: "fixed", bottom: 18, right: 18, zIndex: 80, display: "flex", flexDirection: "column", gap: 8, width: 300 }}>
      {toasts.map((t) => (
        <div key={t.id} style={{
          background: "var(--surface)", border: "1px solid var(--border)", borderRadius: "var(--radius-m)",
          boxShadow: "var(--shadow-m)", padding: "10px 14px", display: "flex", gap: 10, alignItems: "flex-start",
          animation: "cadence-toast-in .2s ease-out",
        }}>
          <span style={{ width: 8, height: 8, borderRadius: 99, marginTop: 5, flex: "none", background: (KIND[t.kind] || KIND.info).dot }}></span>
          <div style={{ minWidth: 0 }}>
            <div style={{ fontWeight: 600, fontSize: 13 }}>{t.title}</div>
            {t.body ? <div style={{ fontSize: 12.5, color: "var(--text-2)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{t.body}</div> : null}
          </div>
        </div>
      ))}
    </div>
  );
}

// ----- Audit trail -----
const EVENT_COPY = {
  expert: { created: "Created", leased: "Leased", approved: "Approved", rejected: "Rejected", retried: "Retried", escalated: "Escalated" },
  plain: { created: "Created", leased: "Picked up", approved: "Approved", rejected: "Declined", retried: "Trying again", escalated: "Handed to a person" },
};
function eventLabel(ev, tone) {
  if (ev.startsWith("status:")) {
    const st = ev.slice(7);
    return (STATUS_COPY[tone] || STATUS_COPY.expert)[st] || st;
  }
  return (EVENT_COPY[tone] || EVENT_COPY.expert)[ev] || ev;
}
function eventKind(ev) {
  if (ev === "created") return "pending";
  if (ev === "leased" || ev === "status:in_progress" || ev === "status:leased") return "active";
  if (ev === "approved" || ev === "status:completed") return "done";
  if (ev === "rejected" || ev.indexOf("failed") >= 0) return "failed";
  if (ev === "retried" || ev === "escalated") return "waiting";
  return "pending";
}

function AuditTrail({ jobId, tone, advanced }) {
  const evts = window.BitCadenceStore.getEvents(jobId);
  if (!evts.length) return <EmptyState icon="≡" title="No events yet" body="Every change to this job will be recorded here." />;
  return (
    <div style={{ position: "relative", paddingLeft: 18 }}>
      <div style={{ position: "absolute", left: 5, top: 8, bottom: 8, width: 2, background: "var(--border)", borderRadius: 2 }}></div>
      {evts.map((e) => (
        <div key={e.id} style={{ position: "relative", padding: "7px 0 7px 14px" }}>
          <span style={{
            position: "absolute", left: -17, top: 12, width: 10, height: 10, borderRadius: 99,
            background: `var(--st-${eventKind(e.event)}-dot)`, border: "2px solid var(--surface)", boxShadow: "0 0 0 1px var(--border)",
          }}></span>
          <div style={{ display: "flex", justifyContent: "space-between", gap: 10, alignItems: "baseline" }}>
            <span style={{ fontWeight: 600, fontSize: 13 }}>{eventLabel(e.event, tone)}</span>
            <span style={{ fontSize: 11.5, color: "var(--text-3)", whiteSpace: "nowrap" }}>{timeAgo(e.created_at)}</span>
          </div>
          <div style={{ fontSize: 12.5, color: "var(--text-2)" }}>
            by <Mono>{e.actor_id || "—"}</Mono>{e.actor_role ? <span style={{ color: "var(--text-3)" }}> ({e.actor_role})</span> : null}
          </div>
          {advanced && e.detail ? (
            <pre style={{ margin: "4px 0 0", fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--text-2)", background: "var(--surface-2)", border: "1px solid var(--border)", borderRadius: 6, padding: "5px 8px", whiteSpace: "pre-wrap" }}>{JSON.stringify(e.detail)}</pre>
          ) : null}
        </div>
      ))}
    </div>
  );
}

Object.assign(window, {
  StatusBadge, RoleChip, Card, SectionTitle, Btn, timeAgo, shortId, Mono,
  THead, Td, EmptyState, Drawer, ToastHost, AuditTrail, STATUS_COPY, eventLabel,
});
