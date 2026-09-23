"""
Drumline Agent Exchange - a governed, non-authoritative discussion lane.

Agents and operators ask, answer, propose, block, decide and hand off inside a
thread tied to a job or workflow run. Nothing written here is ever recalled or
injected into a worker prompt: the automatic Drumline injection path reads only
`agent_context`. An exchange becomes durable context solely through an explicit
promotion (`promote()`), which needs its own scope and writes through the same
`remember()` sanitation and tenant rules as every other context entry.

Storage is two append-only tables (`agent_exchanges`,
`agent_exchange_promotions`). State such as "resolved" or "superseded" is a
projection over later rows; original rows are never updated.
See docs/DRUMLINE-AGENT-EXCHANGE-DESIGN.md.
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
import os
import re
import threading
import time
import uuid
from collections import deque
from hashlib import sha256
from typing import Any, Callable, Dict, List, Optional, Tuple

from mco.orchestrator.drumline import remember
from mco.orchestrator.store_context import transaction

logger = logging.getLogger("mco.orchestrator.agent_exchange")

EXCHANGES_TABLE = "agent_exchanges"
PROMOTIONS_TABLE = "agent_exchange_promotions"

KINDS = ("question", "proposal", "blocker", "reply", "decision", "handoff",
         "resolution", "supersession")
# Exchange kinds that may be promoted, and the context kinds they may become.
PROMOTABLE_SOURCE_KINDS = ("decision", "handoff")
PROMOTION_TARGET_KINDS = ("decision", "lesson", "handoff")

MAX_BODY_CHARS = 8000
MAX_REQUEST_BYTES = 64 * 1024
MAX_IDEMPOTENCY_KEY = 128
MAX_TITLE_CHARS = 300
DEFAULT_PAGE = 50
MAX_PAGE = 100
FETCH_WINDOW = 5000
WORKFLOW_SCAN_WINDOW = 2000

PROVENANCE_KEYS = ("source", "client_request_id", "score_digest", "score_run",
                   "score_task", "score_attempt")
_PROVENANCE_MAX_CHARS = 200

# Fixed application namespace for deterministic promotion ids. Independent of
# every other UUID5 recipe in the codebase.
_PROMOTION_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "batoncadence:drumline:agent-exchange-promotion")

COMPOSE_LIMIT_PER_MINUTE = 30


class ExchangeError(Exception):
    """A stable, safe-to-return failure (never carries bodies or SQL)."""

    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


# ── Feature flag ──────────────────────────────────────────────────────────────

def is_enabled() -> bool:
    """MCO_AGENT_EXCHANGE gates the whole surface; off by default for one release."""
    raw = os.environ.get("MCO_AGENT_EXCHANGE")
    if raw is None:
        try:
            from mco.config import get_config
            raw = get_config().get("MCO_AGENT_EXCHANGE")
        except Exception:
            raw = None
    return str(raw or "").strip().lower() in ("1", "true", "on", "yes")


# ── Sanitization ──────────────────────────────────────────────────────────────

_SECRET_PATTERNS = (
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)", re.S),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=\-]{16,}"),
    re.compile(r"\b(?:sk|pk|rk)[-_](?:live|test|ant|proj)?[-_]?[A-Za-z0-9_\-]{20,}"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{10,}"),
    re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}"),
    re.compile(r"(?i)\b(authorization|cookie|set-cookie)\s*:\s*\S.*"),
    re.compile(r"(?i)\b(password|passwd|secret|api[_-]?key|token)\s*[=:]\s*[^\s\"']{8,}"),
)


def redact_secrets(text: str) -> str:
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub("[REDACTED]", text)
    return text


def sanitize_text(text: str, max_chars: int) -> str:
    """Defang prompt-injection syntax and redact secrets, keeping the content.

    Same neutralization as Drumline `sanitize_content` (tool-call markers
    dropped, fences broken, angle brackets defanged) without its 2,000
    character cap, plus known-secret redaction and control-character removal.
    """
    text = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    text = "".join(ch for ch in text if ch in "\n\t" or ord(ch) >= 32)
    text = re.sub(r"!function_call:.*", "", text)
    text = redact_secrets(text)
    text = text.replace("```", "'''")
    text = text.replace("<", "‹").replace(">", "›")
    return text.strip()[:max_chars]


def _sha(text: str) -> str:
    return sha256(text.encode("utf-8")).hexdigest()


def _now() -> str:
    from mco.localstore import _now_iso
    return _now_iso()


def _org_of(row: dict) -> str:
    return (row or {}).get("org_id") or "default"


# ── Compose budget ────────────────────────────────────────────────────────────

_budget_lock = threading.Lock()
_budget: Dict[Tuple[str, str], deque] = {}


def reset_compose_budget() -> None:
    with _budget_lock:
        _budget.clear()


def _charge_compose_budget(org: str, instance_id: str) -> None:
    now = time.monotonic()
    with _budget_lock:
        window = _budget.setdefault((org, instance_id), deque())
        while window and now - window[0] > 60:
            window.popleft()
        if len(window) >= COMPOSE_LIMIT_PER_MINUTE:
            raise ExchangeError(429, "Too many exchange messages; slow down and retry")
        window.append(now)


# ── Live-event publisher ──────────────────────────────────────────────────────

_publisher: Optional[Callable[[dict], Any]] = None


def register_publisher(callback: Optional[Callable[[dict], Any]]) -> None:
    """Register the async callback that fans `exchange.created` events out."""
    global _publisher
    _publisher = callback


def get_publisher() -> Optional[Callable[[dict], Any]]:
    return _publisher


def event_for(row: dict) -> dict:
    """Sanitized live-event hint: identifiers only, never the body."""
    return {
        "event": "exchange.created",
        "org_id": _org_of(row),
        "exchange": {k: row.get(k) for k in (
            "id", "thread_id", "kind", "job_id", "workflow_name", "workflow_run",
            "workflow_step", "created_at")},
    }


# ── Validation helpers ────────────────────────────────────────────────────────

def _clean_id(value: Any, label: str) -> Optional[str]:
    if value in (None, ""):
        return None
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, AttributeError, TypeError):
        raise ExchangeError(400, f"{label} must be a UUID")


def _clean_label(value: Any, label: str) -> Optional[str]:
    if value in (None, ""):
        return None
    text = str(value).strip()
    if not text or len(text) > 200:
        raise ExchangeError(400, f"{label} must be 1-200 characters")
    return text


def _clean_provenance(raw: Any) -> dict:
    if raw in (None, ""):
        raw = {}
    if not isinstance(raw, dict):
        raise ExchangeError(400, "provenance must be an object")
    unknown = sorted(set(raw) - set(PROVENANCE_KEYS))
    if unknown:
        raise ExchangeError(400, f"provenance keys not allowed: {', '.join(unknown)[:120]}")
    out: dict = {}
    for key, value in raw.items():
        if isinstance(value, bool) or not isinstance(value, (str, int)):
            raise ExchangeError(400, f"provenance.{key} must be a string or integer")
        text = redact_secrets(str(value))
        if len(text) > _PROVENANCE_MAX_CHARS:
            raise ExchangeError(400, f"provenance.{key} is too long")
        out[key] = value if isinstance(value, int) else text
    out.setdefault("source", "api")
    return out


def _load_exchange(db: Any, org: str, exchange_id: str) -> Optional[dict]:
    rows = (db.table(EXCHANGES_TABLE).select("*").eq("id", exchange_id)
            .eq("org_id", org).execute().data or [])
    for row in rows:
        if _org_of(row) == org:
            return row
    return None


def _job_in_org(db: Any, org: str, job_id: str) -> bool:
    rows = db.table("agent_jobs").select("id,org_id").eq("id", job_id).execute().data or []
    return any(_org_of(r) == org for r in rows)


def _workflow_in_org(db: Any, org: str, name: str, run: str, step: str) -> bool:
    rows = (db.table("agent_jobs").select("id,org_id,input_payload")
            .order("created_at", desc=True).limit(WORKFLOW_SCAN_WINDOW).execute().data or [])
    for row in rows:
        if _org_of(row) != org:
            continue
        wf = (row.get("input_payload") or {}).get("workflow") or {}
        if (str(wf.get("name") or "") == name and str(wf.get("run") or "") == run
                and str(wf.get("step") or "") == step):
            return True
    return False


# ── Append ────────────────────────────────────────────────────────────────────

def _normalize_request(payload: dict) -> dict:
    """Validate and normalize one append request (no storage access)."""
    if not isinstance(payload, dict):
        raise ExchangeError(400, "request body must be an object")
    if len(json.dumps(payload, default=str).encode("utf-8")) > MAX_REQUEST_BYTES:
        raise ExchangeError(413, "request body exceeds 64 KiB")
    kind = str(payload.get("kind") or "").strip().lower()
    if kind not in KINDS:
        raise ExchangeError(400, f"kind must be one of: {', '.join(KINDS)}")
    raw_body = payload.get("body")
    if not isinstance(raw_body, str) or not raw_body.strip():
        raise ExchangeError(400, "body is required")
    if len(raw_body) > MAX_BODY_CHARS:
        raise ExchangeError(400, f"body exceeds {MAX_BODY_CHARS} characters")
    body = sanitize_text(raw_body, MAX_BODY_CHARS)
    if not body:
        raise ExchangeError(400, "body is empty after sanitization")
    key = payload.get("idempotency_key")
    if not isinstance(key, str) or not key.strip():
        raise ExchangeError(400, "idempotency_key is required")
    key = key.strip()
    if len(key) > MAX_IDEMPOTENCY_KEY:
        raise ExchangeError(400, f"idempotency_key exceeds {MAX_IDEMPOTENCY_KEY} characters")
    wf = (_clean_label(payload.get("workflow_name"), "workflow_name"),
          _clean_label(payload.get("workflow_run"), "workflow_run"),
          _clean_label(payload.get("workflow_step"), "workflow_step"))
    if any(wf) and not all(wf):
        raise ExchangeError(400, "workflow_name, workflow_run and workflow_step must be given together")
    return {
        "kind": kind,
        "body": body,
        "idempotency_key": key,
        "job_id": _clean_id(payload.get("job_id"), "job_id"),
        "workflow_name": wf[0], "workflow_run": wf[1], "workflow_step": wf[2],
        "reply_to_id": _clean_id(payload.get("reply_to_id"), "reply_to_id"),
        "resolves_exchange_id": _clean_id(payload.get("resolves_exchange_id"), "resolves_exchange_id"),
        "supersedes_exchange_id": _clean_id(payload.get("supersedes_exchange_id"), "supersedes_exchange_id"),
        "provenance": _clean_provenance(payload.get("provenance")),
    }


def _request_hash(req: dict) -> str:
    canonical = {k: req[k] for k in sorted(req) if k != "idempotency_key"}
    return _sha(json.dumps(canonical, sort_keys=True, separators=(",", ":")))


def append_exchange(db: Any, agent: dict, payload: dict) -> Tuple[dict, bool]:
    """Append one exchange row. Returns (row, created).

    Author, org and time come only from the authenticated principal and the
    server clock; any such field in `payload` is ignored.
    """
    if db is None:
        raise ExchangeError(503, "Agent Exchange storage is unavailable")
    org = agent.get("org_id") or "default"
    author = str(agent.get("instance_id") or "")
    if not author:
        raise ExchangeError(403, "Authenticated principal has no identity")
    req = _normalize_request(payload)
    req_hash = _request_hash(req)

    with transaction(db):
        prior = (db.table(EXCHANGES_TABLE).select("*").eq("org_id", org)
                 .eq("author_instance_id", author)
                 .eq("idempotency_key", req["idempotency_key"]).execute().data or [])
        for row in prior:
            if _org_of(row) == org and row.get("author_instance_id") == author:
                if row.get("request_sha256") == req_hash:
                    return row, False
                raise ExchangeError(409, "idempotency_key was already used with different content")

        _charge_compose_budget(org, author)
        parent = _resolve_parent(db, org, req)
        linkage = _resolve_linkage(db, org, req, parent)

        new_id = str(uuid.uuid4())
        row = {
            "id": new_id,
            "org_id": org,
            "kind": req["kind"],
            "body": req["body"],
            "body_sha256": _sha(req["body"]),
            "author_instance_id": author,
            "author_role": agent.get("role"),
            "author_subject": agent.get("user_id"),
            "created_at": _now(),
            "thread_id": parent["thread_id"] if parent else new_id,
            "reply_to_id": req["reply_to_id"],
            "job_id": linkage["job_id"],
            "workflow_name": linkage["workflow_name"],
            "workflow_run": linkage["workflow_run"],
            "workflow_step": linkage["workflow_step"],
            "resolves_exchange_id": req["resolves_exchange_id"],
            "supersedes_exchange_id": req["supersedes_exchange_id"],
            "provenance": req["provenance"],
            "idempotency_key": req["idempotency_key"],
            "request_sha256": req_hash,
        }
        try:
            stored = db.table(EXCHANGES_TABLE).insert(row).execute().data
        except Exception as exc:
            logger.warning("agent exchange insert failed: %s", type(exc).__name__)
            raise ExchangeError(503, "Could not store the exchange; retry with the same idempotency_key")
        if not stored:
            raise ExchangeError(503, "Could not store the exchange; retry with the same idempotency_key")
    logger.info("agent exchange appended id=%s org=%s kind=%s hash=%s",
                new_id, org, req["kind"], row["body_sha256"][:12])
    return stored[0], True


def _resolve_parent(db: Any, org: str, req: dict) -> Optional[dict]:
    """The row this one attaches to; it must exist in the caller's org."""
    kind = req["kind"]
    wanted = {
        "reply": ("reply_to_id", "reply_to_id"),
        "resolution": ("resolves_exchange_id", "resolves_exchange_id"),
        "supersession": ("supersedes_exchange_id", "supersedes_exchange_id"),
    }
    if kind in wanted and not req[wanted[kind][0]]:
        raise ExchangeError(400, f"{kind} requires {wanted[kind][0]}")
    if kind != "resolution" and req["resolves_exchange_id"]:
        raise ExchangeError(400, "resolves_exchange_id is only valid on a resolution")
    if kind != "supersession" and req["supersedes_exchange_id"]:
        raise ExchangeError(400, "supersedes_exchange_id is only valid on a supersession")

    targets = [req[f] for f in ("reply_to_id", "resolves_exchange_id", "supersedes_exchange_id") if req[f]]
    parents = []
    for target_id in targets:
        found = _load_exchange(db, org, target_id)
        if not found:
            raise ExchangeError(404, "Referenced exchange not found")
        parents.append(found)
    if not parents:
        return None
    if len({p["thread_id"] for p in parents}) > 1:
        raise ExchangeError(400, "Referenced exchanges belong to different threads")
    return parents[0]


def _resolve_linkage(db: Any, org: str, req: dict, parent: Optional[dict]) -> dict:
    wf = (req["workflow_name"], req["workflow_run"], req["workflow_step"])
    if parent:
        inherited = {k: parent.get(k) for k in ("job_id", "workflow_name", "workflow_run", "workflow_step")}
        supplied = {"job_id": req["job_id"], "workflow_name": wf[0],
                    "workflow_run": wf[1], "workflow_step": wf[2]}
        for key, value in supplied.items():
            if value and value != inherited.get(key):
                raise ExchangeError(400, "Linkage must match the thread being continued")
        return inherited
    if not req["job_id"] and not all(wf):
        raise ExchangeError(400, "A job_id or a complete workflow tuple is required")
    if req["job_id"] and not _job_in_org(db, org, req["job_id"]):
        raise ExchangeError(404, "Linked job not found")
    if all(wf) and not _workflow_in_org(db, org, *wf):
        raise ExchangeError(404, "Linked workflow run not found")
    return {"job_id": req["job_id"], "workflow_name": wf[0], "workflow_run": wf[1],
            "workflow_step": wf[2]}


# ── Read ──────────────────────────────────────────────────────────────────────

def _sort_key(row: dict) -> Tuple[str, str]:
    return (str(row.get("created_at") or ""), str(row.get("id") or ""))


def encode_cursor(org: str, row: dict) -> str:
    raw = json.dumps({"o": org, "c": row.get("created_at"), "i": row.get("id")},
                     separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_cursor(org: str, cursor: str) -> Tuple[str, str]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
        if data["o"] != org:
            raise ValueError("tenant mismatch")
        return str(data["c"]), str(data["i"])
    except (ValueError, KeyError, TypeError, binascii.Error, json.JSONDecodeError):
        raise ExchangeError(400, "Invalid cursor")


def list_exchanges(
    db: Any,
    agent: dict,
    *,
    job_id: Optional[str] = None,
    workflow_name: Optional[str] = None,
    workflow_run: Optional[str] = None,
    workflow_step: Optional[str] = None,
    thread_id: Optional[str] = None,
    kind: Optional[str] = None,
    limit: int = DEFAULT_PAGE,
    cursor: Optional[str] = None,
) -> dict:
    """Newest-first keyset page of exchanges under a required linkage filter."""
    if db is None:
        raise ExchangeError(503, "Agent Exchange storage is unavailable")
    org = agent.get("org_id") or "default"
    job_id = _clean_id(job_id, "job_id")
    thread_id = _clean_id(thread_id, "thread_id")
    wf = (_clean_label(workflow_name, "workflow_name"),
          _clean_label(workflow_run, "workflow_run"),
          _clean_label(workflow_step, "workflow_step"))
    if any(wf) and not all(wf):
        raise ExchangeError(400, "workflow_name, workflow_run and workflow_step must be given together")
    if not (job_id or thread_id or all(wf)):
        raise ExchangeError(400, "A job_id, thread_id or complete workflow filter is required")
    if kind and kind not in KINDS:
        raise ExchangeError(400, f"kind must be one of: {', '.join(KINDS)}")
    limit = max(1, min(int(limit or DEFAULT_PAGE), MAX_PAGE))
    after = decode_cursor(org, cursor) if cursor else None

    query = db.table(EXCHANGES_TABLE).select("*").eq("org_id", org)
    if job_id:
        query = query.eq("job_id", job_id)
    if thread_id:
        query = query.eq("thread_id", thread_id)
    if all(wf):
        query = query.eq("workflow_name", wf[0]).eq("workflow_run", wf[1]).eq("workflow_step", wf[2])
    if kind:
        query = query.eq("kind", kind)
    rows = [r for r in (query.order("created_at", desc=True).limit(FETCH_WINDOW).execute().data or [])
            if _org_of(r) == org]
    rows.sort(key=_sort_key, reverse=True)
    if after:
        rows = [r for r in rows if _sort_key(r) < after]
    page = rows[:limit]
    more = len(rows) > limit
    return {"items": page, "next_cursor": encode_cursor(org, page[-1]) if more and page else None}


def project_state(rows: List[dict]) -> List[dict]:
    """Annotate rows with derived `resolved_by` / `superseded_by` id lists."""
    resolved: Dict[str, List[str]] = {}
    superseded: Dict[str, List[str]] = {}
    for row in rows:
        if row.get("kind") == "resolution" and row.get("resolves_exchange_id"):
            resolved.setdefault(row["resolves_exchange_id"], []).append(row["id"])
        if row.get("kind") == "supersession" and row.get("supersedes_exchange_id"):
            superseded.setdefault(row["supersedes_exchange_id"], []).append(row["id"])
    return [{**row,
             "resolved_by": resolved.get(row["id"], []),
             "superseded_by": superseded.get(row["id"], []),
             "resolved": bool(resolved.get(row["id"])),
             "superseded": bool(superseded.get(row["id"]))} for row in rows]


def get_exchange(db: Any, agent: dict, exchange_id: str) -> dict:
    """One exchange plus its whole thread with derived state. Foreign org -> 404."""
    if db is None:
        raise ExchangeError(503, "Agent Exchange storage is unavailable")
    org = agent.get("org_id") or "default"
    exchange_id = _clean_id(exchange_id, "exchange id")
    row = _load_exchange(db, org, exchange_id) if exchange_id else None
    if not row:
        raise ExchangeError(404, "Exchange not found")
    thread = [r for r in (db.table(EXCHANGES_TABLE).select("*").eq("org_id", org)
                          .eq("thread_id", row["thread_id"]).execute().data or [])
              if _org_of(r) == org]
    thread.sort(key=_sort_key)
    projected = project_state(thread)
    promotions = [p for p in (db.table(PROMOTIONS_TABLE).select("*").eq("org_id", org)
                              .eq("thread_id", row["thread_id"]).execute().data or [])
                  if _org_of(p) == org]
    return {
        "exchange": next(r for r in projected if r["id"] == row["id"]),
        "thread": projected,
        "promotions": promotions,
        "authoritative": False,
    }


# ── Promotion ─────────────────────────────────────────────────────────────────

def _promotion_title(title: Any, source: dict, target_kind: str) -> str:
    if isinstance(title, str) and title.strip():
        return sanitize_text(title, MAX_TITLE_CHARS)
    first = next((ln for ln in source["body"].splitlines() if ln.strip()), "")
    return sanitize_text(f"{target_kind.title()}: {first}", MAX_TITLE_CHARS)


def promote(db: Any, agent: dict, exchange_id: str, payload: dict) -> Tuple[dict, bool]:
    """Promote one decision/handoff exchange into canonical `agent_context`.

    Returns (receipt, created). Idempotent per (org, exchange, target kind):
    a compatible repeat returns the original receipt, an incompatible one is 409.
    """
    if db is None:
        raise ExchangeError(503, "Agent Exchange storage is unavailable")
    if not isinstance(payload, dict):
        raise ExchangeError(400, "request body must be an object")
    org = agent.get("org_id") or "default"
    promoter = str(agent.get("instance_id") or "")
    target_kind = str(payload.get("target_kind") or "").strip().lower()
    if target_kind not in PROMOTION_TARGET_KINDS:
        raise ExchangeError(400, f"target_kind must be one of: {', '.join(PROMOTION_TARGET_KINDS)}")
    key = payload.get("idempotency_key")
    if not isinstance(key, str) or not key.strip() or len(key.strip()) > MAX_IDEMPOTENCY_KEY:
        raise ExchangeError(400, f"idempotency_key is required (max {MAX_IDEMPOTENCY_KEY} characters)")
    key = key.strip()
    exchange_id = _clean_id(exchange_id, "exchange id")
    source = _load_exchange(db, org, exchange_id) if exchange_id else None
    if not source:
        raise ExchangeError(404, "Exchange not found")
    if source["kind"] not in PROMOTABLE_SOURCE_KINDS:
        raise ExchangeError(400, f"Only {', '.join(PROMOTABLE_SOURCE_KINDS)} exchanges can be promoted")

    # Sanitize again at the trust boundary, then let remember() apply its own
    # (2,000 character) defanging so the canonical row follows Drumline rules.
    title = _promotion_title(payload.get("title"), source, target_kind)
    body = sanitize_text(source["body"], MAX_BODY_CHARS)
    title_hash = _sha(title)
    body_hash = _sha(body)
    promotion_id = str(uuid.uuid5(_PROMOTION_NAMESPACE, f"{org}|{exchange_id}|{target_kind}|{key}"))

    with transaction(db):
        existing = [p for p in (db.table(PROMOTIONS_TABLE).select("*").eq("org_id", org)
                                .eq("exchange_id", exchange_id).eq("target_kind", target_kind)
                                .execute().data or []) if _org_of(p) == org]
        if existing:
            receipt = existing[0]
            if receipt.get("title_sha256") == title_hash and receipt.get("body_sha256") == body_hash:
                return receipt, False
            raise ExchangeError(409, "This exchange was already promoted with different content")

        entry = remember(
            db,
            title=title,
            content=body,
            kind=target_kind,
            scope="global",
            tags=["agent-exchange", f"exchange:{exchange_id[:8]}"],
            created_by=promoter or "system",
            source_job_id=source.get("job_id"),
            org_id=org,
        )
        if not entry or not entry.get("id"):
            raise ExchangeError(503, "Could not write canonical context; retry with the same idempotency_key")

        if source.get("job_id"):
            _audit_promotion(db, agent, source, promotion_id, target_kind)

        receipt = {
            "id": promotion_id,
            "org_id": org,
            "exchange_id": exchange_id,
            "thread_id": source["thread_id"],
            "exchange_sha256": source.get("body_sha256"),
            "target_kind": target_kind,
            "title_sha256": title_hash,
            "body_sha256": body_hash,
            "context_id": entry["id"],
            "promoted_by": promoter,
            "promoted_by_role": agent.get("role"),
            "idempotency_key": key,
            "audit_correlation_id": promotion_id,
            "created_at": _now(),
        }
        try:
            stored = db.table(PROMOTIONS_TABLE).insert(receipt).execute().data
        except Exception as exc:
            logger.warning("agent exchange promotion receipt failed: %s", type(exc).__name__)
            raise ExchangeError(503, "Could not record the promotion; retry with the same idempotency_key")
        if not stored:
            raise ExchangeError(503, "Could not record the promotion; retry with the same idempotency_key")
    logger.info("agent exchange promoted exchange=%s target=%s context=%s",
                exchange_id, target_kind, entry["id"])
    return stored[0], True


def _audit_promotion(db: Any, agent: dict, source: dict, promotion_id: str, target_kind: str) -> None:
    """Chain a promotion event onto the linked job's audit trail (fails closed)."""
    from mco.orchestrator.audit import record_event
    try:
        record_event(
            db, source["job_id"], "exchange_promoted",
            agent.get("instance_id"), agent.get("role"),
            {"exchange_id": source["id"], "promotion_id": promotion_id,
             "target_kind": target_kind, "exchange_sha256": source.get("body_sha256")},
        )
    except Exception:
        raise ExchangeError(503, "Could not write the promotion audit event; retry")
