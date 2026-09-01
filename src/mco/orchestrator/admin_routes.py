"""
Admin API - everything the CLI can do, over REST, for the Control Panel UI.

Three routers:
    /api/agents     (manage)  register / reset-token / edit / delete agents
    /api/settings   (admin)   read + write the operator-tunable configuration
    /api/workflows  (write)   submit a workflow DAG (the `mco workflow` parity)

Design rules:
- Settings writes go through a strict whitelist (SETTING_GROUPS). Unknown
  keys are rejected: the web surface must never become "write arbitrary env
  vars over HTTP". Secrets are write-only - reads return set/unset, never
  the value.
- Tokens are generated server-side, returned exactly once, and stored only
  as SHA-256 hashes - same contract as `mco register`.
- Tenant isolation: a non-default-org caller can only manage agents inside
  its own org.
"""

import base64
import hashlib
import json
import logging
import re
import secrets as _secrets
import uuid
from datetime import datetime, time, timezone

from fastapi import APIRouter, Depends, HTTPException

from mco.config import get_config
from mco.editions import edition_summary
from mco.orchestrator import llm_connections
from mco.secret_vault import (
    SecretNotFoundError,
    SecretRef,
    VaultError,
    build_secret_vault,
)
from mco.orchestrator.auth import KNOWN_SCOPES, normalize_scopes, require_scopes

logger = logging.getLogger("mco.orchestrator.admin")

# instance_id / role are rendered into the dashboard (and into shell-free
# argv elsewhere); constrain them to a safe identifier charset so a crafted
# value can never break out of an HTML/JS context in the Control Panel.
_IDENT_RE = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")

agents_admin_router = APIRouter(prefix="/api/agents")
settings_router = APIRouter(prefix="/api/settings")
workflows_router = APIRouter(prefix="/api/workflows")
governance_router = APIRouter(prefix="/api/governance")
llm_connections_router = APIRouter(prefix="/api/llm-connections")


def _db():
    from mco.orchestrator.routes import get_db_client
    db_client = get_db_client()
    if not db_client:
        raise HTTPException(status_code=400, detail="Database not configured")
    return db_client


def _caller_org(agent: dict) -> str:
    return agent.get("org_id") or "default"


def allowed_orgs() -> list:
    """The configured org allowlist: 'default' plus MCO_ORGS (comma-separated).

    Orgs are tenant boundaries, so they are minted deliberately by an admin
    in Settings - never implicitly by whatever string arrives at
    registration (that is how a typo becomes an isolated tenant)."""
    raw = get_config().get("MCO_ORGS") or ""
    orgs = {"default"}
    orgs.update(o.strip() for o in str(raw).split(",") if o.strip())
    return sorted(orgs)


def _generate_token() -> tuple:
    token = "mco_tok_" + _secrets.token_hex(24)
    return token, hashlib.sha256(token.encode("utf-8")).hexdigest()


def _get_agent_row(db, instance_id: str, caller: dict) -> dict:
    res = db.table("agent_registry").select("*").eq("instance_id", instance_id).execute()
    rows = res.data or []
    if not rows or (rows[0].get("org_id") or "default") != _caller_org(caller):
        raise HTTPException(status_code=404, detail=f"Agent '{instance_id}' not found")
    return rows[0]


def _public(row: dict) -> dict:
    return {k: v for k, v in row.items() if k != "auth_token_hash"}


# ── Agent management ─────────────────────────────────────────────────────────

@agents_admin_router.get("/orgs")
async def list_orgs(caller: dict = Depends(require_scopes("agents:read"))):
    """Orgs available for registration (powers the Control Panel dropdown).

    Host operators see the configured allowlist plus any orgs already in use
    (so grandfathered tenants stay visible); org-scoped callers see only
    their own org - it is the only one they may register into anyway."""
    org = _caller_org(caller)
    if org != "default":
        return {"orgs": [org], "host_operator": False}
    in_use = set()
    try:
        res = _db().table("agent_registry").select("*").execute()
        in_use = {r.get("org_id") or "default" for r in (res.data or [])}
    except Exception as e:
        logger.debug(f"Org in-use scan skipped: {e}")
    return {
        "orgs": allowed_orgs(),
        "in_use": sorted(in_use),
        "host_operator": True,
    }


@agents_admin_router.post("")
async def register_agent(payload: dict, caller: dict = Depends(require_scopes("agents:manage"))):
    """Register a new agent and return its access token - shown exactly once.

    Stricter than `mco register`: an existing instance_id is a 409, never a
    silent token rotation. Use /reset-token for that, deliberately.
    """
    db = _db()
    instance_id = (payload.get("instance_id") or payload.get("name") or "").strip()
    role = (payload.get("role") or "").strip()
    if not instance_id or not role:
        raise HTTPException(status_code=400, detail="instance_id and role are required")
    if not _IDENT_RE.match(instance_id) or not _IDENT_RE.match(role):
        raise HTTPException(
            status_code=400,
            detail="instance_id and role may contain only letters, digits, and . _ : - (max 64 chars)",
        )

    scopes = normalize_scopes(payload.get("scopes"))
    unknown = [s for s in scopes if s not in KNOWN_SCOPES]
    if unknown:
        raise HTTPException(status_code=400,
                            detail=f"Unknown scope(s): {', '.join(unknown)}. "
                                   f"Valid: {', '.join(sorted(KNOWN_SCOPES))}")

    existing = db.table("agent_registry").select("*").eq("instance_id", instance_id).execute()
    if existing.data:
        raise HTTPException(status_code=409,
                            detail=f"Agent '{instance_id}' already exists. Use reset-token to rotate its token.")

    # Host operators (default org) may stamp any ALLOWED org; org admins stay
    # home. Unknown orgs are rejected, not silently created.
    org = _caller_org(caller)
    if org == "default":
        org = (payload.get("org") or "default").strip() or "default"
        if org not in allowed_orgs():
            raise HTTPException(
                status_code=400,
                detail=(f"Org '{org}' is not configured. Allowed: "
                        f"{', '.join(allowed_orgs())}. Add it in Settings -> "
                        f"Tenancy (MCO_ORGS) first - orgs are isolation "
                        f"boundaries, minted deliberately."))

    token, token_hash = _generate_token()
    data = {"instance_id": instance_id, "role": role, "status": "offline",
            "auth_token_hash": token_hash}
    if org != "default":
        data["org_id"] = org
    if scopes:
        data["scopes"] = scopes
    try:
        res = db.table("agent_registry").insert(data).execute()
    except Exception:
        if scopes:
            # Pre-migration database without the scopes column.
            data.pop("scopes", None)
            res = db.table("agent_registry").insert(data).execute()
        else:
            raise
    if not res.data:
        raise HTTPException(status_code=500, detail="Registration failed to persist")
    logger.info(f"Agent '{instance_id}' registered via API by {caller.get('instance_id')}")
    return {"success": True, "agent": _public(res.data[0]), "token": token}


@agents_admin_router.post("/{instance_id}/reset-token")
async def reset_agent_token(instance_id: str, caller: dict = Depends(require_scopes("agents:manage"))):
    """Rotate an agent's access token. The old token stops working immediately;
    the new one is returned exactly once."""
    db = _db()
    _get_agent_row(db, instance_id, caller)
    token, token_hash = _generate_token()
    res = db.table("agent_registry").update({"auth_token_hash": token_hash})\
        .eq("instance_id", instance_id).execute()
    if not res.data:
        raise HTTPException(status_code=500, detail="Token reset failed to persist")
    logger.info(f"Token rotated for '{instance_id}' by {caller.get('instance_id')}")
    return {"success": True, "instance_id": instance_id, "token": token}


@agents_admin_router.patch("/{instance_id}")
async def update_agent(instance_id: str, payload: dict,
                       caller: dict = Depends(require_scopes("agents:manage"))):
    """Edit an agent's role, scopes, or status."""
    db = _db()
    _get_agent_row(db, instance_id, caller)
    update = {}
    if payload.get("role"):
        update["role"] = str(payload["role"]).strip()
    if "scopes" in payload:
        scopes = normalize_scopes(payload.get("scopes"))
        unknown = [s for s in scopes if s not in KNOWN_SCOPES]
        if unknown:
            raise HTTPException(status_code=400, detail=f"Unknown scope(s): {', '.join(unknown)}")
        update["scopes"] = scopes or None  # empty list -> role-derived defaults
    if payload.get("status") in ("online", "offline", "disabled"):
        update["status"] = payload["status"]
    if payload.get("org"):
        # Host operators may move an agent from their own org into any allowed
        # org. One-way by design: after the move the agent belongs to the
        # target tenant and is managed from there (strict org isolation).
        if _caller_org(caller) != "default":
            raise HTTPException(status_code=403,
                                detail="Only the host operator may move agents between orgs")
        target = str(payload["org"]).strip()
        if target not in allowed_orgs():
            raise HTTPException(status_code=400,
                                detail=f"Org '{target}' is not configured. Allowed: {', '.join(allowed_orgs())}")
        update["org_id"] = None if target == "default" else target
    if not update:
        raise HTTPException(status_code=400, detail="Nothing to update (role, scopes, status, org)")
    res = db.table("agent_registry").update(update).eq("instance_id", instance_id).execute()
    if not res.data:
        raise HTTPException(status_code=500, detail="Update failed to persist")
    return {"success": True, "agent": _public(res.data[0])}


@agents_admin_router.delete("/{instance_id}")
async def delete_agent(instance_id: str, caller: dict = Depends(require_scopes("agents:manage"))):
    """Remove an agent registration. Its token stops working immediately."""
    db = _db()
    _get_agent_row(db, instance_id, caller)
    db.table("agent_registry").delete().eq("instance_id", instance_id).execute()
    logger.info(f"Agent '{instance_id}' deleted by {caller.get('instance_id')}")
    return {"success": True, "instance_id": instance_id}


# ── Settings (the Control Panel back end) ────────────────────────────────────
#
# Every entry: type bool|text|secret|choice, a label, and the group it renders
# under. This metadata is served to the UI so the panel always matches the
# whitelist - one source of truth.

SETTING_GROUPS = {
    "governance": {
        "MCO_KILL_SWITCH": {"type": "bool", "label": "Kill switch (pause all new jobs and leases)"},
        "MCO_APPROVER_ROLES": {"type": "text", "label": "Approver roles (comma-separated)",
                               "placeholder": "human,admin,operator"},
        "MCO_POLICY_GATED_ROLES": {"type": "text", "label": "Always-gated roles (jobs to these pause for a human)",
                                   "placeholder": "servicenow,dynatrace"},
        "MCO_ESCALATION_CONNECTOR": {"type": "text", "label": "Escalation connector (mirror terminal failures to ITSM)",
                                     "placeholder": "servicenow"},
    },
    "memory": {
        "MCO_DRUMLINE_DISTILL": {"type": "bool", "label": "Distill completed jobs into shared memory", "default": True},
        "MCO_DRUMLINE_INJECT": {"type": "bool", "label": "Inject shared memory into worker prompts", "default": True},
    },
    "presence": {
        "MCO_AGENT_OFFLINE_AFTER": {"type": "text",
                                    "label": "Mark an agent offline after no contact for (seconds)",
                                    "placeholder": "300"},
    },
    "observability": {
        "MCO_METRICS_TOKEN": {"type": "secret",
                              "label": "Protect /metrics with a bearer token (blank = open, like /healthz)"},
        "MCO_LOG_JSON": {"type": "bool",
                         "label": "Structured JSON logs (restart to apply)"},
    },
    "tenancy": {
        "MCO_ORGS": {"type": "text",
                     "label": "Allowed orgs (comma-separated; 'default' always exists). "
                              "Orgs are isolation boundaries - agents only see jobs and "
                              "memory inside their own org.",
                     "placeholder": "acme, beta-team"},
    },
    "edition": {
        "MCO_EDITION": {"type": "choice", "label": "Edition (blank = infer from configuration)",
                        "choices": ["", "community", "team", "enterprise"]},
    },
    "security": {
        "MCO_TRUSTED_HEADER_AUTH": {"type": "bool", "label": "Trusted-header SSO (only behind an auth proxy!)"},
        "MCO_TRUSTED_HEADER_USER": {"type": "text", "label": "Identity header",
                                    "placeholder": "X-Forwarded-User"},
        "MCO_TRUSTED_HEADER_ROLE": {"type": "text", "label": "Role header",
                                    "placeholder": "X-Forwarded-Role"},
        "MCO_TRUSTED_HEADER_DEFAULT_ROLE": {"type": "text", "label": "Default role for SSO users",
                                            "placeholder": "human"},
        "MCO_TRUSTED_HEADER_SECRET": {"type": "secret", "label": "Proxy shared secret (X-MCO-Proxy-Secret)"},
        "MCO_WEBHOOK_SECRET": {"type": "secret", "label": "Inbound webhook secret (enables /webhook ingestion)"},
    },
    "notifications": {
        "NTFY_SERVER": {"type": "text", "label": "ntfy server", "placeholder": "https://ntfy.sh"},
        "NTFY_TOPIC": {"type": "text", "label": "ntfy topic (blank = notifications off)"},
    },
    "connectors": {
        "SERVICENOW_INSTANCE_URL": {"type": "text", "label": "ServiceNow instance URL",
                                    "placeholder": "https://devXXXXXX.service-now.com"},
        "SERVICENOW_USERNAME": {"type": "text", "label": "ServiceNow username", "placeholder": "admin"},
        "SERVICENOW_PASSWORD": {"type": "secret", "label": "ServiceNow password"},
        "DYNATRACE_BASE_URL": {"type": "text", "label": "Dynatrace URL",
                               "placeholder": "https://abc12345.live.dynatrace.com"},
        "DYNATRACE_API_TOKEN": {"type": "secret", "label": "Dynatrace API token (problems.read + problems.write)"},
    },
}

# Saving any of these means the live connector registry was built from stale
# credentials; rebuild it on the next call so a "Test connection" reflects the
# values just entered.
_CONNECTOR_KEYS = frozenset(SETTING_GROUPS["connectors"])

_ALL_SETTINGS = {k: (g, meta) for g, keys in SETTING_GROUPS.items() for k, meta in keys.items()}

_TRUTHY = ("1", "true", "on", "yes")


@settings_router.get("")
async def get_settings(caller: dict = Depends(require_scopes("admin"))):
    """Current settings (secrets masked to set/unset), UI metadata, the
    edition matrix, and the scope vocabulary - everything the Control Panel
    needs in one call."""
    config = get_config()
    groups = {}
    for group, keys in SETTING_GROUPS.items():
        groups[group] = []
        for key, meta in keys.items():
            raw = config.get(key)
            if meta["type"] == "secret":
                value = bool(raw)  # never echo the secret itself
            elif meta["type"] == "bool":
                value = str(raw or meta.get("default", False)).lower() in _TRUTHY \
                    if raw is not None else bool(meta.get("default", False))
            else:
                value = raw or ""
            groups[group].append({"key": key, "value": value, **meta})
    return {
        "groups": groups,
        "edition": edition_summary(),
        "known_scopes": sorted(KNOWN_SCOPES),
    }


@settings_router.put("")
async def put_settings(payload: dict, caller: dict = Depends(require_scopes("admin"))):
    """Apply settings changes. Only whitelisted keys; values persist to the
    global config home (~/.mco/.env) and take effect immediately in-process.
    Empty string clears a key back to its default."""
    if not isinstance(payload, dict) or not payload:
        raise HTTPException(status_code=400, detail="Provide a {KEY: value} object")
    unknown = [k for k in payload if k not in _ALL_SETTINGS]
    if unknown:
        raise HTTPException(status_code=400,
                            detail=f"Not settable via API: {', '.join(unknown)}")
    config = get_config()
    applied = {}
    touched_connector = False
    for key, value in payload.items():
        meta = _ALL_SETTINGS[key][1]
        if meta["type"] == "bool":
            value = "true" if (value is True or str(value).lower() in _TRUTHY) else "false"
        elif meta["type"] == "choice":
            if str(value) not in meta["choices"]:
                raise HTTPException(status_code=400,
                                    detail=f"{key}: must be one of {meta['choices']}")
            value = str(value)
        else:
            value = str(value or "")
        if value == "":
            config.delete(key)
            applied[key] = None
        else:
            # A setting labelled secret must never silently downgrade to
            # plaintext because the local vault happens to be locked.
            try:
                config.set(key, value, encrypt=meta["type"] == "secret")
            except RuntimeError as exc:
                raise HTTPException(
                    status_code=503,
                    detail=(
                        "The encrypted secret store is locked. Unlock it in "
                        "`mco setup --menu` before saving credentials."
                    ),
                ) from exc
            applied[key] = True if meta["type"] == "secret" else value
        if key in _CONNECTOR_KEYS:
            touched_connector = True
        logger.info(f"Setting {key} changed via API by {caller.get('instance_id')}")
    if touched_connector:
        from mco.connectors import reset_connectors
        reset_connectors()  # next health check rebuilds from the new credentials
    return {"success": True, "applied": applied}


@settings_router.post("/test-connector")
async def test_connector(payload: dict, caller: dict = Depends(require_scopes("admin"))):
    """Rebuild connectors from the saved credentials and report one's health -
    the same reachability/auth probe the terminal setup wizard runs, surfaced
    in the console so an operator can set up and verify without a shell."""
    name = str((payload or {}).get("name", "")).strip().lower()
    if name not in ("servicenow", "dynatrace"):
        raise HTTPException(status_code=400, detail="name must be 'servicenow' or 'dynatrace'")
    from mco.connectors import build_connectors, get_connector, reset_connectors
    reset_connectors()
    build_connectors(force=True)
    conn = get_connector(name)
    if not conn:
        return {"ok": False, "detail": f"{name} is not configured yet - fill in its fields and Save first."}
    try:
        health = conn.health()
    except Exception as e:  # a connector probe should never 500 the panel
        return {"ok": False, "detail": str(e)}
    return {"ok": bool(health.get("ok")), "detail": health.get("detail", "")}


DEMO_WORKFLOW_NAME = "jde-demo-live-pipeline"

DEMO_PIPELINE_STEPS = [
    {
        "id": "plan",
        "role": "claude",
        "title": "Demo pipeline: plan the customer change",
        "instructions": (
            "Read the pilot brief, identify the fastest credible implementation "
            "path, and hand Codex a scoped build plan."
        ),
        "depends_on": [],
    },
    {
        "id": "build",
        "role": "codex",
        "title": "Demo pipeline: build the approved slice",
        "instructions": (
            "Implement the planned change, keep the blast radius small, and "
            "return files changed plus verification output."
        ),
        "depends_on": ["plan"],
    },
    {
        "id": "review",
        "role": "reviewer",
        "title": "Demo pipeline: test and sign off",
        "instructions": (
            "Review the branch, run the requested tests, and approve or return "
            "findings with concrete reproduction notes."
        ),
        "depends_on": ["build"],
    },
]


@workflows_router.post("/demo-pipeline")
async def seed_demo_pipeline(caller: dict = Depends(require_scopes("jobs:write"))):
    """Seed the three-step live sales demo pipeline."""
    from mco.orchestrator.routes import create_job

    run_id = uuid.uuid4().hex[:12]
    job_ids = {}
    for step in DEMO_PIPELINE_STEPS:
        deps = [job_ids[d] for d in step["depends_on"]]
        payload = {
            "title": step["title"],
            "description": step["instructions"],
            "target_agent_role": step["role"],
            "depends_on": deps,
            "input_payload": {
                "prompt": step["instructions"],
                "workflow": {
                    "name": DEMO_WORKFLOW_NAME,
                    "run": run_id,
                    "step": step["id"],
                },
                "demo": {
                    "kind": "pilot-sales-demo",
                    "sequence": ["claude plans", "codex builds", "reviewer tests"],
                },
            },
            "max_retries": 1 if step["id"] == "build" else 0,
        }
        res = await create_job(payload, caller)
        job = (res or {}).get("job") or {}
        if not res.get("success") or not job.get("id"):
            raise HTTPException(
                status_code=500,
                detail=f"Demo step '{step['id']}' failed to submit (created so far: {job_ids})",
            )
        job_ids[step["id"]] = job["id"]
    return {
        "success": True,
        "workflow": DEMO_WORKFLOW_NAME,
        "run": run_id,
        "jobs": job_ids,
        "message": "Seeded claude plans -> codex builds -> reviewer tests.",
    }


# ── Workflows (mco workflow parity) ──────────────────────────────────────────

def _parse_iso(value, *, end_of_day: bool = False):
    if not value:
        return None
    try:
        text = str(value).strip()
        if len(text) == 10:
            if end_of_day:
                return datetime.combine(datetime.fromisoformat(text), time.max, tzinfo=timezone.utc)
            text += "T00:00:00+00:00"
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail=f"Invalid ISO date/time: {value}")


def _event_time(ev: dict):
    return _parse_iso(ev.get("created_at")) if ev.get("created_at") else None


def _pdf_escape(text: str) -> str:
    return str(text).replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _make_cover_pdf(lines: list[str]) -> bytes:
    content_lines = ["BT", "/F1 12 Tf", "72 760 Td"]
    for i, line in enumerate(lines[:28]):
        if i:
            content_lines.append("0 -18 Td")
        content_lines.append(f"({_pdf_escape(line)}) Tj")
    content_lines.append("ET")
    stream = "\n".join(content_lines)
    objects = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        "/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        f"<< /Length {len(stream.encode('utf-8'))} >>\nstream\n{stream}\nendstream",
    ]
    parts = [b"%PDF-1.4\n"]
    offsets = [0]
    for idx, obj in enumerate(objects, start=1):
        offsets.append(sum(len(p) for p in parts))
        parts.append(f"{idx} 0 obj\n{obj}\nendobj\n".encode("utf-8"))
    xref_at = sum(len(p) for p in parts)
    xref = ["xref", f"0 {len(objects) + 1}", "0000000000 65535 f "]
    xref.extend(f"{off:010d} 00000 n " for off in offsets[1:])
    trailer = [
        *xref,
        "trailer",
        f"<< /Size {len(objects) + 1} /Root 1 0 R >>",
        "startxref",
        str(xref_at),
        "%%EOF",
    ]
    parts.append(("\n".join(trailer) + "\n").encode("utf-8"))
    return b"".join(parts)


@governance_router.post("/evidence-pack")
async def export_evidence_pack(payload: dict = None,
                               caller: dict = Depends(require_scopes("jobs:read"))):
    """Return a PDF/JSON evidence bundle for approval and audit history."""
    from mco.orchestrator.routes import agent_org, job_org, get_db_client

    body = payload or {}
    start = _parse_iso(body.get("start_date") or body.get("start"))
    end = _parse_iso(body.get("end_date") or body.get("end"), end_of_day=True)
    if start and end and start > end:
        raise HTTPException(status_code=400, detail="start_date must be before end_date")

    db = get_db_client()
    if not db:
        raise HTTPException(status_code=400, detail="Database not configured")

    events = db.table("agent_job_events").select("*").order("created_at", desc=False).execute().data or []
    jobs = db.table("agent_jobs").select("*").order("created_at", desc=True).limit(500).execute().data or []
    jobs_by_id = {j.get("id"): j for j in jobs}
    caller_org = agent_org(caller)

    filtered = []
    for ev in events:
        job = jobs_by_id.get(ev.get("job_id"))
        if caller_org != "default" and (not job or job_org(job) != caller_org):
            continue
        ts = _event_time(ev)
        if start and ts and ts < start:
            continue
        if end and ts and ts > end:
            continue
        row = dict(ev)
        if job:
            row["job_title"] = job.get("title")
            row["job_status"] = job.get("status")
            row["target_agent_role"] = job.get("target_agent_role")
        filtered.append(row)

    pending_approvals = [
        {
            "id": j.get("id"),
            "title": j.get("title"),
            "target_agent_role": j.get("target_agent_role"),
            "created_at": j.get("created_at"),
        }
        for j in jobs
        if (caller_org == "default" or job_org(j) == caller_org) and j.get("status") == "needs_approval"
    ]
    decisions = [e for e in filtered if e.get("event") in ("approved", "rejected")]
    exported_at = datetime.now(timezone.utc).isoformat()
    audit_json = {
        "exported_at": exported_at,
        "requested_by": caller.get("instance_id"),
        "org_id": caller_org,
        "range": {
            "start": start.isoformat() if start else None,
            "end": end.isoformat() if end else None,
        },
        "regulatory_basis": {
            "eu_ai_act_article_12": "Record-keeping: preserve system event logs and job lifecycle audit data.",
            "eu_ai_act_article_14": "Human oversight: preserve approval requests and operator decisions.",
        },
        "summary": {
            "audit_events": len(filtered),
            "pending_approvals": len(pending_approvals),
            "decisions": len(decisions),
        },
        "pending_approvals": pending_approvals,
        "decision_history": decisions,
        "audit_events": filtered,
    }
    cover_pdf = _make_cover_pdf([
        "BitCadence Compliance Evidence Pack",
        f"Exported at: {exported_at}",
        f"Requested by: {caller.get('instance_id')} ({caller.get('role')})",
        f"Org: {caller_org}",
        f"Range start: {audit_json['range']['start'] or 'beginning of record'}",
        f"Range end: {audit_json['range']['end'] or 'latest event'}",
        "",
        "EU AI Act Article 12 - record-keeping",
        "This pack preserves job lifecycle and audit event records.",
        "",
        "EU AI Act Article 14 - human oversight",
        "This pack preserves pending approvals and human decisions.",
        "",
        f"Audit events: {audit_json['summary']['audit_events']}",
        f"Pending approvals: {audit_json['summary']['pending_approvals']}",
        f"Approval decisions: {audit_json['summary']['decisions']}",
    ])
    return {
        "success": True,
        "generated_at": exported_at,
        "summary": audit_json["summary"],
        "files": [
            {
                "filename": "cover.pdf",
                "mime": "application/pdf",
                "base64": base64.b64encode(cover_pdf).decode("ascii"),
            },
            {
                "filename": "audit-trail.json",
                "mime": "application/json",
                "text": json.dumps(audit_json, indent=2, default=str),
            },
        ],
    }


@workflows_router.post("")
async def submit_workflow_api(payload: dict, caller: dict = Depends(require_scopes("jobs:write"))):
    """Submit a workflow DAG: {"yaml": "..."} or {"name": ..., "steps": [...]}.

    Validates (ids, deps, cycles), then creates every step through the same
    create_job path the REST API uses - governance, audit, broadcast, and
    Context Exchange run-stamping all included. Returns {step_id: job_id}.
    """
    from mco.orchestrator.workflows import WorkflowError, load_workflow, topo_order
    from mco.orchestrator.routes import create_job

    source = payload.get("yaml") if isinstance(payload, dict) and payload.get("yaml") else payload
    try:
        workflow = load_workflow(source)
    except WorkflowError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not parse workflow: {e}")

    name = workflow["name"]
    run_id = uuid.uuid4().hex[:12]
    job_ids = {}
    for step in topo_order(workflow["steps"]):
        step_id = step["id"]
        instructions = step.get("instructions") or step.get("title") or ""
        job_payload = {
            "title": step.get("title") or f"{name}:{step_id}",
            "description": instructions,
            "target_agent_role": step["role"],
            "target_agent_id": step.get("instance"),
            "depends_on": [job_ids[d] for d in (step.get("depends_on") or [])],
            "input_payload": {
                "prompt": instructions,
                "workflow": {"name": name, "run": run_id, "step": step_id},
            },
            "requires_approval": bool(step.get("requires_approval")),
            "max_retries": int(step.get("max_retries") or 0),
            "escalate_to_role": step.get("escalate_to_role"),
        }
        res = await create_job(job_payload, caller)
        job = (res or {}).get("job") or {}
        if not res.get("success") or not job.get("id"):
            raise HTTPException(status_code=500,
                                detail=f"Step '{step_id}' failed to submit (created so far: {job_ids})")
        job_ids[step_id] = job["id"]
    return {"success": True, "workflow": name, "run": run_id, "jobs": job_ids}
# ── LLM Provider Connections ("Model Connections" in the Control Panel) ──────
#
# Named, testable connections to LLM providers. See llm_connections.py for
# why the API key is never stored in the llm_connections table itself.

def _llm_public(row: dict, key_set: bool) -> dict:
    return {
        "id": row.get("id"),
        "name": row.get("name"),
        "provider": row.get("provider"),
        "base_url": row.get("base_url"),
        "model": row.get("model"),
        "org_id": row.get("org_id") or "default",
        "created_at": row.get("created_at"),
        "key_set": key_set,
    }


def _get_llm_row(db, conn_id: str, caller: dict) -> dict:
    res = db.table("llm_connections").select("*").eq("id", conn_id).execute()
    rows = res.data or []
    if not rows or (rows[0].get("org_id") or "default") != _caller_org(caller):
        raise HTTPException(status_code=404, detail=f"Connection '{conn_id}' not found")
    return rows[0]


def _llm_secret_ref(conn_id: str, caller: dict) -> SecretRef:
    return SecretRef(
        org_id=_caller_org(caller),
        scope=conn_id,
        name="api_key",
        legacy_config_key=llm_connections.config_key_for(conn_id),
    )


def _llm_vault(db):
    return build_secret_vault(get_config(), db)


def _vault_http_error(exc: VaultError) -> HTTPException:
    return HTTPException(status_code=503, detail=str(exc))


@llm_connections_router.get("/providers")
async def list_llm_providers(caller: dict = Depends(require_scopes("admin"))):
    """Provider metadata for the Add Connection form."""
    return {p: {"label": m["label"], "base_url_editable": m["base_url"] is None}
            for p, m in llm_connections.PROVIDERS.items()}


@llm_connections_router.get("")
async def list_llm_connections(caller: dict = Depends(require_scopes("admin"))):
    db = _db()
    res = db.table("llm_connections").select("*").execute()
    rows = [r for r in (res.data or []) if (r.get("org_id") or "default") == _caller_org(caller)]
    try:
        vault = _llm_vault(db)
        return [_llm_public(r, vault.exists(_llm_secret_ref(r["id"], caller)))
                for r in rows]
    except VaultError as exc:
        raise _vault_http_error(exc)


@llm_connections_router.post("")
async def create_llm_connection(payload: dict, caller: dict = Depends(require_scopes("admin"))):
    name = (payload.get("name") or "").strip()
    provider = (payload.get("provider") or "").strip().lower()
    base_url = (payload.get("base_url") or "").strip() or None
    model = (payload.get("model") or "").strip() or None
    api_key = (payload.get("api_key") or "").strip()

    if not name or not provider:
        raise HTTPException(status_code=400, detail="name and provider are required")
    if not _IDENT_RE.match(name):
        raise HTTPException(status_code=400,
                            detail="name may contain only letters, digits, and . _ : - (max 64 chars)")
    if provider not in llm_connections.PROVIDERS:
        raise HTTPException(status_code=400,
                            detail=f"Unknown provider '{provider}'. Valid: {', '.join(sorted(llm_connections.PROVIDERS))}")
    if provider == "custom" and not base_url:
        raise HTTPException(status_code=400, detail="base_url is required for a custom connection")
    if provider != "custom":
        # Built-in providers use a fixed URL - never let the client steer an
        # outbound request an operator didn't intend (SSRF guard).
        base_url = None

    db = _db()
    row = {"name": name, "provider": provider, "base_url": base_url, "model": model}
    if _caller_org(caller) != "default":
        row["org_id"] = _caller_org(caller)
    res = db.table("llm_connections").insert(row).execute()
    if not res.data:
        raise HTTPException(status_code=500, detail="Failed to persist connection")
    saved = res.data[0]

    if api_key:
        try:
            _llm_vault(db).put(_llm_secret_ref(saved["id"], caller), api_key)
        except VaultError as exc:
            # Do not leave metadata claiming a usable connection when secret
            # custody failed (for example because the local vault is locked).
            db.table("llm_connections").delete().eq("id", saved["id"]).execute()
            raise _vault_http_error(exc)

    logger.info(f"LLM connection '{name}' ({provider}) created by {caller.get('instance_id')}")
    return {"success": True, "connection": _llm_public(saved, bool(api_key))}


@llm_connections_router.patch("/{conn_id}")
async def update_llm_connection(conn_id: str, payload: dict,
                                caller: dict = Depends(require_scopes("admin"))):
    """Edit name/model/base_url, and optionally rotate the API key. A blank
    api_key leaves the stored key untouched (mirrors the Settings pattern)."""
    db = _db()
    row = _get_llm_row(db, conn_id, caller)
    update = {}
    if "name" in payload:
        name = str(payload["name"]).strip()
        if not _IDENT_RE.match(name):
            raise HTTPException(status_code=400,
                                detail="name may contain only letters, digits, and . _ : - (max 64 chars)")
        update["name"] = name
    if "model" in payload:
        update["model"] = str(payload["model"]).strip() or None
    if row.get("provider") == "custom" and "base_url" in payload:
        base_url = str(payload["base_url"]).strip()
        if not base_url:
            raise HTTPException(status_code=400, detail="base_url is required for a custom connection")
        update["base_url"] = base_url

    api_key = str(payload.get("api_key") or "").strip()
    if api_key:
        try:
            _llm_vault(db).put(_llm_secret_ref(conn_id, caller), api_key)
        except VaultError as exc:
            raise _vault_http_error(exc)

    if not update and not api_key:
        raise HTTPException(status_code=400, detail="Nothing to update")

    if update:
        res = db.table("llm_connections").update(update).eq("id", conn_id).execute()
        if not res.data:
            raise HTTPException(status_code=500, detail="Update failed to persist")
        row = res.data[0]

    try:
        key_set = _llm_vault(db).exists(_llm_secret_ref(conn_id, caller))
    except VaultError as exc:
        raise _vault_http_error(exc)
    return {"success": True, "connection": _llm_public(row, key_set)}


@llm_connections_router.delete("/{conn_id}")
async def delete_llm_connection(conn_id: str, caller: dict = Depends(require_scopes("admin"))):
    db = _db()
    _get_llm_row(db, conn_id, caller)
    try:
        _llm_vault(db).delete(_llm_secret_ref(conn_id, caller))
    except VaultError as exc:
        raise _vault_http_error(exc)
    db.table("llm_connections").delete().eq("id", conn_id).execute()
    logger.info(f"LLM connection '{conn_id}' deleted by {caller.get('instance_id')}")
    return {"success": True, "id": conn_id}


@llm_connections_router.post("/{conn_id}/test")
async def test_llm_connection(conn_id: str, caller: dict = Depends(require_scopes("admin"))):
    """Make one cheap, real call to the provider to prove the key/base_url
    actually authenticate. Never returns the key itself."""
    db = _db()
    row = _get_llm_row(db, conn_id, caller)
    try:
        api_key = _llm_vault(db).get(_llm_secret_ref(conn_id, caller))
    except SecretNotFoundError:
        api_key = ""
    except VaultError as exc:
        raise _vault_http_error(exc)
    return llm_connections.test_connection(row.get("provider"), api_key, row.get("base_url"))
