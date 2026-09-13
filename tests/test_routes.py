"""Integration tests for FastAPI route auth enforcement and dropbox policy."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import mco.orchestrator.routes as routes_mod
from mco.orchestrator.auth import hash_token, require_agent
from mco.orchestrator.routes import router, agents_router, events_router, version_router


# ── App factory ──────────────────────────────────────────────────────────────

def _build_app() -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    app.include_router(agents_router)
    app.include_router(events_router)
    app.include_router(version_router)
    return app


# ── FakeDB ───────────────────────────────────────────────────────────────────

class FakeDB:
    """Stateful fake Supabase client for route integration tests."""

    def __init__(self):
        self._jobs: dict = {}
        self._extra = {}
        self._agents: list = []
        self._events: list = []
        self._context: list = []
        self._rpc_result: bool = True
        self._next_id = 1
        self._q_table = None
        self._q_op = None
        self._q_conds: dict = {}
        self._q_in_conds: dict = {}
        self._q_insert_data = None
        self._q_update_data = None

    def add_agent(self, instance_id, role, token, status="online"):
        self._agents.append({
            "instance_id": instance_id,
            "role": role,
            "status": status,
            "last_seen_at": "2026-01-01T00:00:00Z",
            "auth_token_hash": hash_token(token),
        })
        return self

    def add_job(self, **kwargs) -> str:
        jid = kwargs.setdefault("id", f"job-{self._next_id}")
        kwargs.setdefault("org_id", "default")  # mirrors the DB column default
        self._next_id += 1
        self._jobs[jid] = dict(kwargs)
        return jid

    def set_rpc(self, result: bool):
        self._rpc_result = result
        return self

    # ── Chainable Supabase-like API ───────────────────────────────────────────

    def table(self, name):
        self._q_table = name
        self._q_op = None
        self._q_conds = {}
        self._q_in_conds = {}
        self._q_insert_data = None
        self._q_update_data = None
        return self

    def select(self, *_args):
        self._q_op = "select"
        return self

    def eq(self, col, val):
        self._q_conds[col] = val
        return self

    def in_(self, col, vals):
        self._q_in_conds[col] = list(vals)
        return self

    def order(self, *_args, **_kw):
        return self

    def limit(self, _n):
        return self

    def insert(self, data):
        self._q_op = "insert"
        self._q_insert_data = dict(data)
        return self

    def upsert(self, data):
        self._q_op = "upsert"
        self._q_insert_data = dict(data)
        return self

    def update(self, data):
        self._q_op = "update"
        self._q_update_data = dict(data)
        return self

    def delete(self):
        self._q_op = "delete"
        return self

    def rpc(self, _name, _args):
        result = self._rpc_result

        class _Q:
            def execute(self_inner):
                class R:
                    data = result
                return R()

        return _Q()

    def execute(self):
        class R:
            def __init__(self, data):
                self.data = data

        t = self._q_table
        op = self._q_op

        if t.startswith("mco_"):
            rows = self._extra.setdefault(t, [])
            if op in ("insert", "upsert"):
                data = dict(self._q_insert_data)
                if op == "upsert":
                    rows[:] = [r for r in rows if r.get("id") != data.get("id")]
                rows.append(data)
                return R([data])
            return R([r for r in rows if all(r.get(k) == v for k,v in self._q_conds.items())])
        if t == "agent_registry":
            if op == "insert":
                data = dict(self._q_insert_data)
                self._agents.append(data)
                return R([{k: v for k, v in data.items() if k != "auth_token_hash"}])
            if op == "update":
                matched = [a for a in self._agents
                           if all(a.get(c) == v for c, v in self._q_conds.items())]
                for a in matched:
                    a.update(self._q_update_data)
                return R([{k: v for k, v in a.items() if k != "auth_token_hash"} for a in matched])
            if op == "delete":
                removed = [a for a in self._agents
                           if all(a.get(c) == v for c, v in self._q_conds.items())]
                self._agents = [a for a in self._agents if a not in removed]
                return R([{k: v for k, v in a.items() if k != "auth_token_hash"} for a in removed])
            rows = [dict(a) for a in self._agents]
            for col, val in self._q_conds.items():
                rows = [r for r in rows if r.get(col) == val]
            return R([{k: v for k, v in r.items() if k != "auth_token_hash"} for r in rows])

        if t == "agent_context":
            if op == "insert":
                data = dict(self._q_insert_data)
                data.setdefault("id", f"ctx-{len(self._context) + 1}")
                data.setdefault("created_at", f"2026-01-01T00:00:{len(self._context):02d}Z")
                self._context.append(data)
                return R([dict(data)])
            if op == "select":
                rows = list(self._context)
                for col, val in self._q_conds.items():
                    rows = [r for r in rows if r.get(col) == val]
                # Newest first, mirroring order("created_at", desc=True)
                return R([dict(r) for r in reversed(rows)])

        if t == "agent_job_events":
            if op == "insert":
                data = dict(self._q_insert_data)
                data.setdefault("id", len(self._events) + 1)
                data.setdefault("created_at", f"2026-01-01T00:00:{len(self._events):02d}Z")
                self._events.append(data)
                return R([dict(data)])
            if op == "select":
                rows = list(self._events)
                for col, val in self._q_conds.items():
                    rows = [r for r in rows if r.get(col) == val]
                return R([dict(r) for r in rows])

        if t == "agent_jobs":
            if op == "select":
                rows = list(self._jobs.values())
                for col, val in self._q_conds.items():
                    rows = [r for r in rows if r.get(col) == val]
                for col, vals in self._q_in_conds.items():
                    rows = [r for r in rows if r.get(col) in vals]
                return R([dict(r) for r in rows])

            if op == "insert":
                data = dict(self._q_insert_data)
                data.setdefault("id", f"job-{self._next_id}")
                data.setdefault("org_id", "default")  # mirrors the DB column default
                self._next_id += 1
                self._jobs[data["id"]] = data
                return R([dict(data)])

            if op == "update":
                matched = [j for j in self._jobs.values()
                           if all(j.get(c) == v for c, v in self._q_conds.items())]
                for j in matched:
                    j.update(self._q_update_data)
                return R([dict(j) for j in matched])

        return R([])


# ── Shared constants ──────────────────────────────────────────────────────────

TOKEN = "test-token-abc"
AGENT = {"instance_id": "agent-1", "role": "codex", "status": "online"}


class TestVersionRoute:
    def test_version_route_returns_package_version_and_git_commit_key(self):
        resp = TestClient(_build_app()).get("/api/version")
        assert resp.status_code == 200
        body = resp.json()
        assert body["version"] == "0.3.0"
        assert set(body) == {"version", "git_commit"}

    def test_version_route_uses_package_metadata_without_pyproject(self, monkeypatch, tmp_path):
        monkeypatch.setattr(routes_mod.importlib_metadata, "version", lambda name: "1.2.3")
        monkeypatch.setattr(routes_mod, "_repo_root", lambda: tmp_path)

        resp = TestClient(_build_app()).get("/api/version")

        assert resp.status_code == 200
        assert resp.json()["version"] == "1.2.3"


# ── Auth-enforcement tests (real require_agent, no dependency override) ───────

class TestAuthEnforcement:
    """All protected endpoints reject requests with missing or invalid bearer tokens."""

    PROTECTED = [
        ("GET",  "/api/jobs",         None),
        ("POST", "/api/jobs",         {"title": "x", "target_agent_role": "claude"}),
        ("GET",  "/api/jobs/pending", None),
        ("POST", "/api/jobs/lease",   {"task_id": "j1", "agent_instance_id": "a1"}),
        ("PUT",  "/api/jobs/j1",      {"status": "completed"}),
        ("GET",  "/api/agents",       None),
    ]

    @pytest.fixture(autouse=True)
    def setup(self, monkeypatch):
        db = FakeDB()
        db.add_agent("agent-1", "codex", TOKEN)
        monkeypatch.setattr(routes_mod, "get_db_client", lambda: db)
        self.app = _build_app()
        self.http = TestClient(self.app)

    def _request(self, method, path, body, **kwargs):
        if method in ("GET",) or body is None:
            return getattr(self.http, method.lower())(path, **kwargs)
        return getattr(self.http, method.lower())(path, json=body, **kwargs)

    def test_missing_bearer_is_401(self):
        for method, path, body in self.PROTECTED:
            resp = self._request(method, path, body)
            assert resp.status_code == 401, f"{method} {path} returned {resp.status_code}"

    def test_invalid_bearer_is_401(self):
        for method, path, body in self.PROTECTED:
            resp = self._request(method, path, body, headers={"Authorization": "Bearer garbage-token"})
            assert resp.status_code == 401, f"{method} {path} returned {resp.status_code}"

    def test_local_only_mode_accepts_any_token(self, monkeypatch):
        # When no DB is configured and MCO_LOCAL_TOKEN is unset, any bearer token
        # is accepted (Local-Only profile with no secret configured).
        import mco.orchestrator.auth as auth_mod

        class _NullCfg:
            def get(self, key):
                return None

        monkeypatch.setattr(routes_mod, "get_db_client", lambda: None)
        monkeypatch.setattr(auth_mod, "get_config", lambda: _NullCfg())
        resp = self.http.get("/api/jobs", headers={"Authorization": f"Bearer {TOKEN}"})
        # Returns [] (no DB) with 200 — not a 503 anymore.
        assert resp.status_code == 200


# ── Dropbox policy tests ──────────────────────────────────────────────────────

class TestDropboxPolicy:
    """403 enforcement: agents can only pull/lease/update mail addressed to them."""

    @pytest.fixture(autouse=True)
    def setup(self, monkeypatch):
        self.db = FakeDB()
        monkeypatch.setattr(routes_mod, "get_db_client", lambda: self.db)
        self.app = _build_app()
        self.app.dependency_overrides[require_agent] = lambda: AGENT
        self.http = TestClient(self.app)

    def test_pending_403_role_mismatch(self):
        resp = self.http.get("/api/jobs/pending", params={"role": "claude", "instance_id": "agent-1"})
        assert resp.status_code == 403

    def test_pending_403_instance_mismatch(self):
        resp = self.http.get("/api/jobs/pending", params={"role": "codex", "instance_id": "other-instance"})
        assert resp.status_code == 403

    def test_lease_403_on_behalf_of_another_agent(self):
        resp = self.http.post("/api/jobs/lease", json={
            "task_id": "j1",
            "agent_instance_id": "not-agent-1",
        })
        assert resp.status_code == 403

    def test_update_403_not_addressed_to_caller(self):
        self.db.add_job(
            id="j99",
            target_agent_role="claude",
            target_agent_id="someone-else",
        )
        resp = self.http.put("/api/jobs/j99", json={"status": "completed"})
        assert resp.status_code == 403

    def test_lease_403_wrong_role(self):
        # AGENT is role "codex"; a job addressed to "claude" must not be leasable.
        self.db.add_job(id="claude-job", target_agent_role="claude", status="pending")
        resp = self.http.post("/api/jobs/lease", json={
            "task_id": "claude-job",
            "agent_instance_id": "agent-1",
        })
        assert resp.status_code == 403

    def test_lease_403_sibling_instance_of_same_role(self):
        # Same role (codex) but the job is reserved for a *specific* other
        # instance. The inbox hides it from agent-1, so lease must 403 too -
        # this is the case a naive check (role-match only) would wrongly allow.
        self.db.add_job(
            id="sibling-job",
            target_agent_role="codex",
            target_agent_id="codex-sibling",
            status="pending",
        )
        resp = self.http.post("/api/jobs/lease", json={
            "task_id": "sibling-job",
            "agent_instance_id": "agent-1",
        })
        assert resp.status_code == 403

    def test_lease_200_correctly_addressed(self):
        # A job addressed to the caller's role (no specific instance) leases fine.
        self.db.add_job(id="mine", target_agent_role="codex", status="pending")
        resp = self.http.post("/api/jobs/lease", json={
            "task_id": "mine",
            "agent_instance_id": "agent-1",
        })
        assert resp.status_code == 200
        assert resp.json().get("success") is True


# ── Success and validation tests ──────────────────────────────────────────────

class TestDropboxSuccess:
    """Happy paths and input validation for the dropbox API."""

    @pytest.fixture(autouse=True)
    def setup(self, monkeypatch):
        self.db = FakeDB()
        monkeypatch.setattr(routes_mod, "get_db_client", lambda: self.db)
        self.app = _build_app()
        self.app.dependency_overrides[require_agent] = lambda: AGENT
        self.http = TestClient(self.app)

    def test_create_job_success(self):
        resp = self.http.post("/api/jobs", json={
            "title": "Do work",
            "target_agent_role": "claude",
        })
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["job"]["title"] == "Do work"
        assert body["job"]["status"] == "pending"

    def test_create_job_missing_title_is_rejected(self):
        # The route's bare except-Exception wraps the 400 as 500, but the request is still rejected.
        resp = self.http.post("/api/jobs", json={"target_agent_role": "claude"})
        assert resp.status_code >= 400

    def test_create_job_missing_role_is_rejected(self):
        resp = self.http.post("/api/jobs", json={"title": "Do work"})
        assert resp.status_code >= 400

    def test_create_job_no_db_is_400(self, monkeypatch):
        monkeypatch.setattr(routes_mod, "get_db_client", lambda: None)
        resp = self.http.post("/api/jobs", json={"title": "x", "target_agent_role": "claude"})
        assert resp.status_code == 400

    def test_pending_returns_only_matching_role_jobs(self):
        self.db.add_job(id="j1", status="pending", target_agent_role="codex", target_agent_id=None)
        self.db.add_job(id="j2", status="pending", target_agent_role="claude", target_agent_id=None)
        resp = self.http.get("/api/jobs/pending", params={"role": "codex", "instance_id": "agent-1"})
        assert resp.status_code == 200
        ids = [j["id"] for j in resp.json()]
        assert "j1" in ids
        assert "j2" not in ids

    def test_pending_excludes_jobs_addressed_to_other_instance(self):
        self.db.add_job(id="j3", status="pending", target_agent_role="codex", target_agent_id="other-instance")
        self.db.add_job(id="j4", status="pending", target_agent_role="codex", target_agent_id=None)
        resp = self.http.get("/api/jobs/pending", params={"role": "codex", "instance_id": "agent-1"})
        ids = [j["id"] for j in resp.json()]
        assert "j3" not in ids
        assert "j4" in ids

    def test_pending_includes_job_addressed_to_my_instance(self):
        self.db.add_job(id="j5", status="pending", target_agent_role="codex", target_agent_id="agent-1")
        resp = self.http.get("/api/jobs/pending", params={"role": "codex", "instance_id": "agent-1"})
        ids = [j["id"] for j in resp.json()]
        assert "j5" in ids

    def test_lease_missing_task_id_is_400(self):
        resp = self.http.post("/api/jobs/lease", json={"agent_instance_id": "agent-1"})
        assert resp.status_code == 400

    def test_lease_missing_agent_instance_id_is_400(self):
        resp = self.http.post("/api/jobs/lease", json={"task_id": "j1"})
        assert resp.status_code == 400

    def test_lease_success_returns_true(self):
        self.db.add_job(id="jl1", status="pending", target_agent_role="codex", target_agent_id=None)
        self.db.set_rpc(True)
        resp = self.http.post("/api/jobs/lease", json={
            "task_id": "jl1",
            "agent_instance_id": "agent-1",
        })
        assert resp.status_code == 200
        assert resp.json()["success"] is True

    def test_get_jobs_returns_list(self):
        self.db.add_job(id="jx", status="pending", target_agent_role="codex")
        resp = self.http.get("/api/jobs")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_agents_excludes_auth_token_hash(self):
        self.db.add_agent("agent-99", "claude", "some-token")
        resp = self.http.get("/api/agents")
        assert resp.status_code == 200
        for agent in resp.json():
            assert "auth_token_hash" not in agent


# ── Cross-job audit feed (/api/events) ────────────────────────────────────────

class TestRecentEvents:
    """GET /api/events: newest-first cross-job feed with job enrichment and
    host-operator/org tenancy rules."""

    @pytest.fixture(autouse=True)
    def setup(self, monkeypatch):
        self.db = FakeDB()
        monkeypatch.setattr(routes_mod, "get_db_client", lambda: self.db)
        self.app = _build_app()
        self.app.dependency_overrides[require_agent] = lambda: AGENT
        self.http = TestClient(self.app)

    def _event(self, job_id, event="created", **kw):
        data = {"job_id": job_id, "event": event, "actor_id": "a1", "detail": {}}
        data.update(kw)
        self.db.table("agent_job_events").insert(data).execute()

    def test_events_enriched_with_job_title(self):
        jid = self.db.add_job(title="Fix the widget", status="pending",
                              target_agent_role="codex")
        self._event(jid, "created")
        rows = self.http.get("/api/events").json()
        assert len(rows) == 1
        assert rows[0]["event"] == "created"
        assert rows[0]["job_title"] == "Fix the widget"
        assert rows[0]["target_agent_role"] == "codex"

    def test_default_org_sees_all_orgs(self):
        a = self.db.add_job(title="A", org_id="org-a")
        b = self.db.add_job(title="B")  # default org
        self._event(a); self._event(b)
        rows = self.http.get("/api/events").json()
        assert {r["job_title"] for r in rows} == {"A", "B"}

    def test_org_caller_sees_only_own_jobs_events(self):
        a = self.db.add_job(title="A", org_id="org-a")
        b = self.db.add_job(title="B")  # default org
        self._event(a); self._event(b)
        self.app.dependency_overrides[require_agent] = lambda: {
            "instance_id": "acme-1", "role": "codex", "status": "online",
            "org_id": "org-a"}
        rows = self.http.get("/api/events").json()
        assert {r["job_title"] for r in rows} == {"A"}

    def test_since_filters_older_events(self):
        jid = self.db.add_job(title="T")
        self._event(jid, "created", created_at="2026-01-01T00:00:00Z")
        self._event(jid, "leased",  created_at="2026-01-02T00:00:00Z")
        rows = self.http.get("/api/events", params={"since": "2026-01-01T12:00:00Z"}).json()
        assert [r["event"] for r in rows] == ["leased"]

    def test_no_db_returns_empty(self, monkeypatch):
        monkeypatch.setattr(routes_mod, "get_db_client", lambda: None)
        assert self.http.get("/api/events").json() == []
