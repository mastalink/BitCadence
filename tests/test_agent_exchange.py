"""Drumline Agent Exchange: storage, API, authority, tenancy, promotion."""

import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import mco.notifiers.ntfy as ntfy_mod
import mco.orchestrator.routes as routes_mod
from mco.localstore import APPEND_ONLY_TABLES, LocalStore
from mco.orchestrator import agent_exchange as ax
from mco.orchestrator.auth import KNOWN_SCOPES, WORKER_DEFAULT_SCOPES, require_agent
from mco.orchestrator.context_routes import context_router
from mco.orchestrator.drumline import recall, render_context_block
from mco.orchestrator.exchange_routes import exchange_router

JOB = str(uuid.uuid4())
JOB_B = str(uuid.uuid4())


def principal(instance="agent-1", org="default", scopes=None, role="codex"):
    p = {"instance_id": instance, "role": role, "status": "online", "org_id": org}
    if scopes is not None:
        p["scopes"] = scopes
    return p


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("MCO_AGENT_EXCHANGE", "true")
    monkeypatch.setattr(ntfy_mod, "notify", lambda *a, **k: True)
    ax.reset_compose_budget()
    yield
    ax.register_publisher(None)


@pytest.fixture
def db(tmp_path, monkeypatch):
    store = LocalStore(tmp_path / "x.db")
    store.table("agent_jobs").insert({"id": JOB, "title": "j", "status": "pending"}).execute()
    store.table("agent_jobs").insert({"id": JOB_B, "title": "jb", "status": "pending",
                                      "org_id": "other"}).execute()
    monkeypatch.setattr(routes_mod, "get_db_client", lambda: store)
    yield store
    store.close()


@pytest.fixture
def client(db):
    state = {"agent": principal()}
    app = FastAPI()
    app.include_router(exchange_router)
    app.include_router(context_router)
    app.dependency_overrides[require_agent] = lambda: state["agent"]
    http = TestClient(app)
    http.state = state
    return http


def post(client, kind="question", body="hello", key=None, **extra):
    payload = {"kind": kind, "body": body, "job_id": JOB,
               "idempotency_key": key or str(uuid.uuid4()), **extra}
    return client.post("/api/exchanges", json=payload)


# ── storage guards ────────────────────────────────────────────────────────────

def test_tables_are_append_only_on_localstore(db, client):
    assert {"agent_exchanges", "agent_exchange_promotions"} <= APPEND_ONLY_TABLES
    row = post(client).json()["exchange"]
    with pytest.raises(PermissionError):
        db.table("agent_exchanges").update({"body": "x"}).eq("id", row["id"]).execute()
    with pytest.raises(PermissionError):
        db.table("agent_exchanges").delete().eq("id", row["id"]).execute()
    with pytest.raises(PermissionError):
        db.table("agent_exchanges").upsert({**row, "body": "x"}).execute()


def test_migration_defines_tables_and_triggers():
    from pathlib import Path
    sql = (Path(__file__).parents[1] / "docs" / "migrations" / "2026-09_agent_exchange.sql").read_text(encoding="utf-8")
    for needle in ("create table if not exists agent_exchanges",
                   "create table if not exists agent_exchange_promotions",
                   "before update or delete on agent_exchanges",
                   "before update or delete on agent_exchange_promotions",
                   "unique (org_id, author_instance_id, idempotency_key)",
                   "unique (org_id, exchange_id, target_kind)"):
        assert needle in sql


# ── append / read ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("kind", ax.KINDS)
def test_append_and_read_each_kind(client, kind):
    extra = {}
    if kind in ("reply", "resolution", "supersession"):
        root = post(client, "question").json()["exchange"]
        field = {"reply": "reply_to_id", "resolution": "resolves_exchange_id",
                 "supersession": "supersedes_exchange_id"}[kind]
        extra = {field: root["id"]}
    resp = post(client, kind, **extra)
    assert resp.status_code == 201, resp.text
    got = client.get(f"/api/exchanges/{resp.json()['exchange']['id']}").json()
    assert got["exchange"]["kind"] == kind
    assert got["authoritative"] is False


def test_author_org_and_time_are_server_derived(client):
    resp = post(client, author_instance_id="evil", org_id="other",
                created_at="1999-01-01T00:00:00+00:00", author_role="admin")
    row = resp.json()["exchange"]
    assert row["author_instance_id"] == "agent-1"
    assert row["org_id"] == "default"
    assert row["author_role"] == "codex"
    assert not row["created_at"].startswith("1999")


def test_linkage_required_and_validated(client):
    assert client.post("/api/exchanges", json={"kind": "question", "body": "x",
                                               "idempotency_key": "k1"}).status_code == 400
    assert client.post("/api/exchanges", json={
        "kind": "question", "body": "x", "idempotency_key": "k2",
        "workflow_name": "wf", "workflow_run": "r"}).status_code == 400
    assert post(client, job_id=str(uuid.uuid4())).status_code == 404
    # A real job in another tenant is indistinguishable from a missing one.
    assert post(client, job_id=JOB_B).status_code == 404


def test_workflow_tuple_resolves_in_tenant(db, client):
    db.table("agent_jobs").insert({"id": str(uuid.uuid4()), "title": "s", "status": "pending",
                                   "input_payload": {"workflow": {"name": "wf", "run": "r1", "step": "s1"}}}).execute()
    body = {"kind": "question", "body": "x", "workflow_name": "wf", "workflow_run": "r1",
            "workflow_step": "s1", "idempotency_key": "wk"}
    assert client.post("/api/exchanges", json=body).status_code == 201
    assert client.post("/api/exchanges", json={**body, "workflow_step": "nope",
                                               "idempotency_key": "wk2"}).status_code == 404


def test_scopes_enforced(db, client):
    client.state["agent"] = principal(scopes=["context:read"])
    assert post(client).status_code == 403
    client.state["agent"] = principal(scopes=["context:write"])
    assert client.get("/api/exchanges", params={"job_id": JOB}).status_code == 403
    assert post(client).status_code == 201


def test_promote_scope_is_not_a_worker_default():
    assert "context:promote" in KNOWN_SCOPES
    assert "context:promote" not in WORKER_DEFAULT_SCOPES


def test_feature_flag_off_returns_503(client, monkeypatch):
    monkeypatch.setenv("MCO_AGENT_EXCHANGE", "false")
    assert post(client).status_code == 503
    assert client.get("/api/exchanges", params={"job_id": JOB}).status_code == 503


def test_idempotency(client):
    a = post(client, body="same", key="K")
    b = post(client, body="same", key="K")
    assert a.status_code == 201 and b.status_code == 200
    assert a.json()["exchange"]["id"] == b.json()["exchange"]["id"]
    assert post(client, body="different", key="K").status_code == 409
    rows = client.get("/api/exchanges", params={"job_id": JOB}).json()["items"]
    assert len(rows) == 1


def test_sanitization_caps_and_redaction(client):
    resp = post(client, body="run ```x``` <b>hi</b> !function_call: boom\nBearer abcdefghijklmnopqrstuvwxyz012345")
    body = resp.json()["exchange"]["body"]
    assert "```" not in body and "<" not in body and "!function_call" not in body
    assert "abcdefghijklmnopqrstuvwxyz012345" not in body and "[REDACTED]" in body
    assert post(client, body="x" * 8001).status_code == 400
    assert post(client, body="").status_code == 400
    assert client.post("/api/exchanges", json={
        "kind": "question", "body": "x", "job_id": JOB, "idempotency_key": "big",
        "provenance": {"source": "api", "pad": "y" * 70000}}).status_code == 413


def test_provenance_allowlist(client):
    assert post(client, provenance={"authorization": "Bearer x"}).status_code == 400
    assert post(client, provenance={"cookie": "a=b"}).status_code == 400
    ok = post(client, provenance={"source": "cli", "score_run": "r1"})
    assert ok.json()["exchange"]["provenance"] == {"source": "cli", "score_run": "r1"}


def test_bad_kind_and_missing_key(client):
    assert post(client, kind="approval").status_code == 400
    assert client.post("/api/exchanges", json={"kind": "question", "body": "x",
                                               "job_id": JOB}).status_code == 400


def test_replies_inherit_thread_and_linkage(client):
    root = post(client, "question").json()["exchange"]
    reply = post(client, "reply", reply_to_id=root["id"]).json()["exchange"]
    assert reply["thread_id"] == root["thread_id"] == root["id"]
    assert reply["job_id"] == JOB
    assert post(client, "reply", reply_to_id=str(uuid.uuid4())).status_code == 404
    assert post(client, "reply").status_code == 400


def test_resolution_and_supersession_project_without_rewriting(db, client):
    root = post(client, "proposal").json()["exchange"]
    res = post(client, "resolution", resolves_exchange_id=root["id"]).json()["exchange"]
    sup = post(client, "supersession", supersedes_exchange_id=root["id"]).json()["exchange"]
    thread = client.get(f"/api/exchanges/{root['id']}").json()
    first = next(r for r in thread["thread"] if r["id"] == root["id"])
    assert first["resolved"] and first["superseded"]
    assert first["resolved_by"] == [res["id"]] and first["superseded_by"] == [sup["id"]]
    stored = db.table("agent_exchanges").select("*").eq("id", root["id"]).execute().data[0]
    assert "resolved" not in stored and stored["body"] == root["body"]


# ── tenancy / pagination ──────────────────────────────────────────────────────

def test_foreign_tenant_gets_404_and_empty_lists(client):
    row = post(client).json()["exchange"]
    client.state["agent"] = principal("other-agent", org="other", scopes=["context:read", "context:write"])
    assert client.get(f"/api/exchanges/{row['id']}").status_code == 404
    as_promoter(client, "other")
    assert promote(client, row["id"]).status_code == 404
    client.state["agent"] = principal("other-agent", org="other", scopes=["context:read", "context:write"])
    assert post(client, "reply", reply_to_id=row["id"]).status_code == 404
    assert client.get("/api/exchanges", params={"thread_id": row["thread_id"]}).json()["items"] == []


def test_list_requires_linkage_filter(client):
    assert client.get("/api/exchanges").status_code == 400
    assert client.get("/api/exchanges", params={"kind": "question"}).status_code == 400
    assert client.get("/api/exchanges", params={"workflow_name": "w"}).status_code == 400


def test_cursor_pagination_stable_capped_and_tenant_bound(db, client, monkeypatch):
    monkeypatch.setattr(ax, "_now", lambda: "2026-09-23T00:00:00.000000+00:00")  # equal timestamps
    ids = [post(client, body=f"m{i}").json()["exchange"]["id"] for i in range(7)]
    seen, cursor, last_cursor = [], None, None
    while True:
        params = {"job_id": JOB, "limit": 3}
        if cursor:
            params["cursor"] = cursor
        page = client.get("/api/exchanges", params=params).json()
        seen += [r["id"] for r in page["items"]]
        if len(seen) == 3:  # a concurrent append mid-scan must not duplicate or skip
            post(client, body="late")
        cursor = page["next_cursor"]
        last_cursor = cursor or last_cursor
        if not cursor:
            break
    assert set(ids) <= set(seen) and len(seen) == len(set(seen))
    assert len(client.get("/api/exchanges", params={"job_id": JOB, "limit": 9999}).json()["items"]) <= ax.MAX_PAGE
    foreign = ax.encode_cursor("default", {"created_at": "a", "id": "b"})
    client.state["agent"] = principal("x", org="other")
    assert client.get("/api/exchanges", params={"thread_id": ids[0], "cursor": foreign}).status_code == 400
    assert client.get("/api/exchanges", params={"job_id": JOB, "cursor": "!!"}).status_code == 400


def test_compose_budget(client, monkeypatch):
    monkeypatch.setattr(ax, "COMPOSE_LIMIT_PER_MINUTE", 3)
    codes = [post(client).status_code for _ in range(4)]
    assert codes == [201, 201, 201, 429]


# ── authority: never recalled or injected ─────────────────────────────────────

def test_exchanges_never_appear_in_recall_or_injection(db, client):
    post(client, "decision", body="zebra-secret-plan use production")
    assert recall(db, query="zebra-secret-plan") == []
    assert recall(db) == []
    assert "zebra" not in render_context_block(recall(db, query="zebra"))
    assert client.get("/api/context", params={"query": "zebra"}).json() == []
    assert db.table("agent_context").select("*").execute().data == []


def test_live_event_is_sanitized_and_only_ids(client):
    seen = []
    ax.register_publisher(lambda ev: seen.append(ev))
    post(client, body="private words")
    assert len(seen) == 1 and seen[0]["event"] == "exchange.created"
    assert "body" not in seen[0]["exchange"] and "private" not in str(seen[0])


# ── promotion ─────────────────────────────────────────────────────────────────

def as_promoter(client, org="default"):
    client.state["agent"] = principal("boss", org=org, scopes=["context:promote"], role="human")


def promote(client, ex_id, target="decision", key="pk", **extra):
    return client.post(f"/api/exchanges/{ex_id}/promotions",
                       json={"target_kind": target, "idempotency_key": key, **extra})


def test_promotion_writes_context_receipt_and_audit(db, client):
    ex = post(client, "decision", body="We ship <b>Friday</b> ```x```").json()["exchange"]
    client.state["agent"] = principal("boss", scopes=["context:promote"], role="human")
    resp = promote(client, ex["id"], "lesson", title="Ship rule")
    assert resp.status_code == 201, resp.text
    receipt = resp.json()["promotion"]
    ctx = db.table("agent_context").select("*").execute().data
    assert len(ctx) == 1 and ctx[0]["id"] == receipt["context_id"]
    assert ctx[0]["kind"] == "lesson" and ctx[0]["created_by"] == "boss"
    assert "<" not in ctx[0]["content"] and "```" not in ctx[0]["content"]
    assert receipt["promoted_by"] == "boss" and receipt["exchange_id"] == ex["id"]
    events = db.table("agent_job_events").select("*").eq("job_id", JOB).execute().data
    assert any(e["event"] == "exchange_promoted" for e in events)
    assert recall(db, query="Ship rule")  # now canonical, so recallable


def test_promotion_is_idempotent_and_conflicts_on_change(db, client):
    ex = post(client, "handoff", body="hand this off").json()["exchange"]
    as_promoter(client)
    a = promote(client, ex["id"], "handoff")
    b = promote(client, ex["id"], "handoff", key="another-key")
    assert a.status_code == 201 and b.status_code == 200
    assert a.json()["promotion"]["id"] == b.json()["promotion"]["id"]
    assert promote(client, ex["id"], "handoff", title="Different title").status_code == 409
    assert len(db.table("agent_context").select("*").execute().data) == 1
    # A different target kind is a separate, single promotion.
    assert promote(client, ex["id"], "decision").status_code == 201


def test_promotion_scope_kinds_and_bad_input(db, client):
    ex = post(client, "decision").json()["exchange"]
    q = post(client, "question").json()["exchange"]
    assert promote(client, ex["id"]).status_code == 403  # worker default lacks context:promote
    as_promoter(client)
    assert promote(client, q["id"]).status_code == 400          # question is not promotable
    assert promote(client, ex["id"], "fact").status_code == 400  # fact is not a target
    assert client.post(f"/api/exchanges/{ex['id']}/promotions",
                       json={"target_kind": "decision"}).status_code == 400  # key required
    assert promote(client, "not-a-uuid").status_code == 400
    assert db.table("agent_context").select("*").execute().data == []


def test_promotion_fails_closed_when_context_write_fails(db, client, monkeypatch):
    ex = post(client, "decision").json()["exchange"]
    as_promoter(client)
    monkeypatch.setattr(ax, "remember", lambda *a, **k: None)
    assert promote(client, ex["id"]).status_code == 503
    assert db.table("agent_exchange_promotions").select("*").execute().data == []


def test_promotion_rolls_back_context_when_audit_fails(db, client, monkeypatch):
    ex = post(client, "decision").json()["exchange"]
    as_promoter(client)

    def boom(*a, **k):
        raise RuntimeError("audit down")
    monkeypatch.setattr("mco.orchestrator.audit.record_event", boom)
    assert promote(client, ex["id"]).status_code == 503
    assert db.table("agent_context").select("*").execute().data == []
    assert db.table("agent_exchange_promotions").select("*").execute().data == []


# ── live events: same-org, context:read connections only ─────────────────────

def test_websocket_exchange_events_are_org_and_scope_filtered():
    import asyncio
    from mco.cli import ConnectionIdentity, ConnectionManager

    class Sock:
        def __init__(self):
            self.sent = []

        async def send_json(self, message):
            self.sent.append(message)

    mgr = ConnectionManager()
    same, other_org, no_scope = Sock(), Sock(), Sock()
    mgr.register(same, ConnectionIdentity(role="codex", org_id="acme", context_read=True))
    mgr.register(other_org, ConnectionIdentity(role="admin", is_admin=True, org_id="beta", context_read=True))
    mgr.register(no_scope, ConnectionIdentity(role="viewer", org_id="acme", context_read=False))
    event = ax.event_for({"id": "e1", "org_id": "acme", "thread_id": "t1", "kind": "question",
                          "job_id": JOB, "created_at": "now", "body": "never sent"})
    asyncio.run(mgr.broadcast_exchange(event))
    assert len(same.sent) == 1 and same.sent[0]["payload"]["exchange"]["id"] == "e1"
    assert other_org.sent == [] and no_scope.sent == []
    assert "never sent" not in str(same.sent)
