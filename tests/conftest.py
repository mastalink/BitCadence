"""Shared fixtures and fakes for BitCadence tests.

Provides reusable FakeDB and FakeConfig implementations that all test files
can import. This eliminates duplication across test files and ensures
consistent mock behavior.
"""

from typing import Any, Dict, List, Optional

import pytest


# ══════════════════════════════════════════════════════════════════════════
# FakeConfig
# ══════════════════════════════════════════════════════════════════════════

class FakeConfig:
    """In-memory config dictionary that mirrors get_config().

    Usage::

        monkeypatch.setattr(some_module, "get_config", lambda: FakeConfig())

    or inject specific values::

        monkeypatch.setattr(
            that_mod, "get_config",
            lambda: FakeConfig(MCO_METRICS_TOKEN="s3cret"),
        )
    """

    def __init__(self, **overrides):
        self._store: Dict[str, Any] = {
            "MCO_LOCAL_TOKEN": "",
            "MCO_METRICS_TOKEN": "",
            "MCO_KILL_SWITCH": "",
            "MCO_TRUSTED_HEADER_AUTH": "",
            "MCO_WEBHOOK_SECRET": "",
            "MCO_ESCALATION_CONNECTOR": "",
            "SUPABASE_URL": "",
            "SUPABASE_KEY": "",
            **overrides,
        }

    def get(self, key: str, default: Any = None) -> Any:
        return self._store.get(key, default)

    def __getitem__(self, key: str) -> Any:
        return self._store[key]

    def __contains__(self, key: str) -> bool:
        return key in self._store


# ══════════════════════════════════════════════════════════════════════════
# FakeDB (stateful in-memory Supabase-like client)
# ══════════════════════════════════════════════════════════════════════════

def _hash_token(token: str) -> str:
    import hashlib
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class FakeDB:
    """Stateful fake Supabase client for route / drumline integration tests.

    Maintains in-memory tables (agents, jobs, events, context) and supports
    a chainable Supabase-like query builder::

        db = FakeDB()
        db.add_agent("a1", "codex", "tok")
        db.add_job(id="j1", title="test")

        res = db.table("agent_jobs").select("*").eq("org_id", "default").execute()
        assert len(res.data) == 1
    """

    def __init__(self):
        self._jobs: Dict[str, dict] = {}
        self._agents: List[dict] = []
        self._events: List[dict] = []
        self._context: List[dict] = []
        self._rpc_result: bool = True
        self._next_id = 1
        self._q_table: Optional[str] = None
        self._q_op: Optional[str] = None
        self._q_conds: Dict[str, Any] = {}
        self._q_in_conds: Dict[str, list] = {}
        self._q_insert_data: Optional[dict] = None
        self._q_update_data: Optional[dict] = None

    # ── Convenience helpers ────────────────────────────────────────────────

    def add_agent(self, instance_id: str, role: str, token: str,
                  status: str = "online") -> "FakeDB":
        self._agents.append({
            "instance_id": instance_id,
            "role": role,
            "status": status,
            "last_seen_at": "2026-01-01T00:00:00Z",
            "auth_token_hash": _hash_token(token),
        })
        return self

    def add_job(self, **kwargs) -> str:
        jid = kwargs.setdefault("id", f"job-{self._next_id}")
        kwargs.setdefault("org_id", "default")
        self._next_id += 1
        self._jobs[jid] = dict(kwargs)
        return jid

    def set_rpc(self, result: bool) -> "FakeDB":
        self._rpc_result = result
        return self

    # ── Chainable Supabase-like API ─────────────────────────────────────────

    def table(self, name: str) -> "FakeDB":
        self._q_table = name
        self._q_op = None
        self._q_conds = {}
        self._q_in_conds = {}
        self._q_insert_data = None
        self._q_update_data = None
        return self

    def select(self, *_args: str) -> "FakeDB":
        self._q_op = "select"
        self._q_tag_filter: Optional[str] = None
        return self

    def eq(self, field: str, value: Any) -> "FakeDB":
        self._q_conds[field] = value
        return self

    def neq(self, field: str, value: Any) -> "FakeDB":
        self._q_conds[f"{field}__neq"] = value
        return self

    def _matches_cond(self, item: dict, field: str, value: Any) -> bool:
        if field.endswith("__neq"):
            return item.get(field[:-5]) != value
        # Special handling: `is None` for content_hash
        if value is None:
            return field not in item or item[field] is None
        return item.get(field) == value

    def filter(self, field: str, op: str, value: Any) -> "FakeDB":
        self._q_in_conds[field] = {"op": op, "value": value}
        return self

    def in_(self, field: str, values: list) -> "FakeDB":
        self._q_in_conds[field] = values
        return self

    def order(self, field: str, desc: bool = False) -> "FakeDB":
        self._q_order = (field, desc)
        return self

    def limit(self, n: int) -> "FakeDB":
        self._q_limit = n
        return self

    def range(self, start: int, end: int) -> "FakeDB":
        self._q_range = (start, end)
        return self

    def insert(self, data: dict) -> "FakeDB":
        self._q_op = "insert"
        self._q_insert_data = data
        return self

    def update(self, data: dict) -> "FakeDB":
        self._q_op = "update"
        self._q_update_data = data
        return self

    def delete(self) -> "FakeDB":
        self._q_op = "delete"
        return self

    def single(self) -> "FakeDB":
        self._q_single = True
        return self

    def execute(self) -> "FakeDB.Result":
        if self._q_op == "select":
            return self._execute_select()
        elif self._q_op == "insert":
            return self._execute_insert()
        elif self._q_op == "update":
            return self._execute_update()
        elif self._q_op == "delete":
            return self._execute_delete()
        raise RuntimeError(f"Unknown query operation: {self._q_op}")

    def _execute_select(self) -> "FakeDB.Result":
        rows = self._select_rows()
        # Apply order
        order_attr = getattr(self, "_q_order", None)
        if order_attr:
            field, desc = order_attr
            rows = sorted(rows, key=lambda r: r.get(field, ""), reverse=desc)
        # Apply limit
        limit_attr = getattr(self, "_q_limit", None)
        if limit_attr:
            rows = rows[:limit_attr]
        return self.Result(data=rows)

    def _select_rows(self) -> List[dict]:
        table = self._q_table or ""
        if table == "agent_jobs":
            rows = list(self._jobs.values())
        elif table in ("agent_registry",):
            rows = list(self._agents)
        elif table in ("agent_job_events",):
            rows = list(self._events)
        elif table in ("agent_context",):
            rows = list(self._context)
        else:
            rows = []
        # Filter by eq conditions
        for field, value in self._q_conds.items():
            rows = [r for r in rows if self._matches_cond(r, field, value)]
        # We don't apply `in_` or `filter` generically; callers extend if needed
        return rows

    def _execute_insert(self) -> "FakeDB.Result":
        data = dict(self._q_insert_data or {})
        record_id = data.get("id", str(self._next_id))
        self._next_id += 1
        data["id"] = record_id
        table = self._q_table or ""
        if table in ("agent_jobs",):
            self._jobs[record_id] = data
        elif table in ("agent_job_events",):
            data["id"] = len(self._events) + 1
            self._events.append(data)
        elif table in ("agent_context",):
            self._context.append(data)
        return self.Result(data=[data])

    def _execute_update(self) -> "FakeDB.Result":
        data = self._q_update_data or {}
        table = self._q_table or ""
        updated = []
        if table in ("agent_jobs",):
            for jid, job in self._jobs.items():
                if all(self._matches_cond(job, f, v) for f, v in self._q_conds.items()):
                    job.update(data)
                    updated.append(job)
            # Rebuild dict to keep IDs aligned
            self._jobs = {j["id"]: j for j in self._jobs.values()}
        return self.Result(data=updated)

    def _execute_delete(self) -> "FakeDB.Result":
        table = self._q_table or ""
        deleted = []
        if table in ("agent_jobs",):
            remaining = {}
            for jid, job in self._jobs.items():
                if all(self._matches_cond(job, f, v) for f, v in self._q_conds.items()):
                    deleted.append(job)
                else:
                    remaining[jid] = job
            self._jobs = remaining
        return self.Result(data=deleted)

    def rpc(self, name: str, params: Optional[dict] = None) -> "FakeDB":
        self._q_op = "rpc"
        self._q_rpc_name = name
        self._q_rpc_params = params
        return self

    # ── Nested result type ────────────────────────────────────────────────

    class Result:
        def __init__(self, data: Optional[List[dict]] = None):
            self.data = data or []

        def execute(self) -> "FakeDB.Result":
            return self


# ══════════════════════════════════════════════════════════════════════════
# Pytest fixtures
# ══════════════════════════════════════════════════════════════════════════

@pytest.fixture
def fake_db() -> FakeDB:
    """Return a fresh FakeDB instance."""
    return FakeDB()


@pytest.fixture
def fake_db_with_agent(fake_db: FakeDB) -> FakeDB:
    """Return a FakeDB pre-seeded with a default agent."""
    fake_db.add_agent("agent-1", "codex", "test-token")
    return fake_db


@pytest.fixture
def fake_config() -> FakeConfig:
    """Return a default FakeConfig."""
    return FakeConfig()


@pytest.fixture
def isolated_db(fake_db: FakeDB) -> FakeDB:
    """Return a FakeDB seeded with org-isolated data for tenant tests."""
    fake_db.add_job(id="org-a-job", org_id="org-a", title="Org A job")
    fake_db.add_job(id="org-b-job", org_id="org-b", title="Org B job")
    fake_db.add_job(id="default-job", title="default org job")
    return fake_db

# ══════════════════════════════════════════════════════════════════════════
# Machine protection: tests must never touch the operator's real secrets
# ══════════════════════════════════════════════════════════════════════════

@pytest.fixture(autouse=True)
def _isolate_operator_secrets(monkeypatch, tmp_path_factory):
    """Keep every test off the operator's real secret store, two ways.

    A test on a developer machine silently orphaned the operator's real secret
    store at ~/.mco/secrets.enc - the store existed but nothing could unlock it
    again, surfacing as a mystery warning on every CLI run. It happened
    repeatedly on the machine this project is developed on.

    There are TWO paths to the real store, and both must be closed:

    1. The KEY, in Windows Credential Manager. A test calling the real
       `WindowsCredentialProvider.store_key()` overwrites the master key for
       the real store. Closed by swapping in an in-memory credential vault.

    2. The FILE, at the default path ~/.mco/secrets.enc. Any test that reaches
       for the store WITHOUT an explicit path - `get_secret_store()`,
       `SecretStore()`, or `ConfigManager()` with no store_path - binds to the
       real file and can re-initialize it with a throwaway key that never
       reaches (the real) Credential Manager, orphaning it. The first guard
       alone did not catch this: it protected the key, not the file. Closed by
       redirecting the default path to a temp dir and resetting the process
       singleton so no default store is ever the real one.

    A test that genuinely needs the real store must opt out explicitly - and
    should think hard first.
    """
    import mco.security as security_mod

    vault: Dict[str, bytes] = {}
    real_provider = security_mod.WindowsCredentialProvider

    class _InMemoryCredMan(real_provider):  # keeps isinstance() checks working
        @classmethod
        def store_key(cls, key: bytes) -> None:
            vault["key"] = key

        def get_key(self) -> bytes:
            if "key" not in vault:
                raise RuntimeError("no key stored (in-memory test vault)")
            return vault["key"]

    monkeypatch.setattr(security_mod, "WindowsCredentialProvider", _InMemoryCredMan)

    # Redirect the default store path away from ~/.mco/secrets.enc, and reset
    # the global singleton so a test that grabs a default store gets a fresh
    # temp one - never the operator's.
    temp_store = tmp_path_factory.mktemp("secret-store") / "secrets.enc"
    monkeypatch.setattr(security_mod, "DEFAULT_STORE_PATH", temp_store)
    monkeypatch.setattr(security_mod, "_store", None)
    yield
    # Leave no singleton pointing at a now-deleted temp path for the next test.
    monkeypatch.setattr(security_mod, "_store", None, raising=False)
