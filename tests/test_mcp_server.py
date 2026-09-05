"""Tests for the MCO MCP server tools — verify delegation to GatewayClient."""

import mco.mcp_server as mcp_mod
from mco.mcp_server import (
    mco_agents,
    mco_complete,
    mco_fail,
    mco_inbox,
    mco_lease,
    mco_send,
)


# ── Fake GatewayClient ────────────────────────────────────────────────────────

class FakeGatewayClient:
    """Records every call made through MCP tool delegation."""

    def __init__(self, **responses):
        self.calls: list = []
        self._responses = responses

    def inbox(self):
        self.calls.append(("inbox",))
        return self._responses.get("inbox", [])

    def flush_reports(self):
        self.calls.append(("flush_reports",))
        return 0

    def lease(self, task_id: str):
        self.calls.append(("lease", task_id))
        return self._responses.get("lease", {"success": True})

    def complete(self, task_id: str, output: str, handoff=None):
        self.calls.append(("complete", task_id, output, handoff))
        return self._responses.get("complete", {"success": True})

    def fail(self, task_id: str, error: str):
        self.calls.append(("fail", task_id, error))
        return self._responses.get("fail", {"success": True})

    def send(self, to_role: str, title: str, instructions: str, to_instance=None,
             depends_on=None, requires_approval=False, max_retries=0, escalate_to_role=None):
        self.calls.append(("send", to_role, title, instructions, to_instance))
        return self._responses.get("send", {"success": True})

    def approve(self, task_id: str):
        self.calls.append(("approve", task_id))
        return self._responses.get("approve", {"success": True})

    def reject(self, task_id: str, reason: str = ""):
        self.calls.append(("reject", task_id, reason))
        return self._responses.get("reject", {"success": True})

    def events(self, task_id: str):
        self.calls.append(("events", task_id))
        return self._responses.get("events", [])

    def agents(self):
        self.calls.append(("agents",))
        return self._responses.get("agents", [])


def _fake(monkeypatch, **responses) -> FakeGatewayClient:
    client = FakeGatewayClient(**responses)
    monkeypatch.setattr(mcp_mod, "_client", lambda: client)
    return client


# ── Tool delegation tests ─────────────────────────────────────────────────────

def test_mco_inbox_delegates_and_returns_payload(monkeypatch):
    jobs = [{"id": "j1", "title": "do thing", "input_payload": {"prompt": "do X"}}]
    fake = _fake(monkeypatch, inbox=jobs)
    result = mco_inbox()
    assert result == jobs
    assert fake.calls == [("flush_reports",), ("inbox",)]

def test_mco_inbox_preserves_input_payload_shape(monkeypatch):
    jobs = [{"id": "j1", "input_payload": {"prompt": "run tests"}, "status": "pending"}]
    fake = _fake(monkeypatch, inbox=jobs)
    result = mco_inbox()
    assert result[0]["input_payload"]["prompt"] == "run tests"

def test_mco_lease_delegates_task_id(monkeypatch):
    fake = _fake(monkeypatch, lease={"success": True})
    result = mco_lease("task-123")
    assert result == {"success": True}
    assert fake.calls == [("lease", "task-123")]

def test_mco_complete_delegates_task_and_output(monkeypatch):
    fake = _fake(monkeypatch, complete={"success": True})
    result = mco_complete("task-456", "all done")
    assert result == {"success": True}
    assert fake.calls == [("complete", "task-456", "all done", None)]

def test_mco_complete_builds_structured_handoff(monkeypatch):
    fake = _fake(monkeypatch, complete={"success": True})
    mco_complete("task-456", "all done",
                 summary="Shipped the fix.",
                 decisions="used psutil",
                 files="src/mco/cli.py\ntests/test_cli.py",
                 gotchas="",  # empty fields are dropped
                 follow_ups="add --timeout")
    handoff = fake.calls[0][3]
    assert handoff == {
        "summary": "Shipped the fix.",
        "decisions": "used psutil",
        "files": "src/mco/cli.py\ntests/test_cli.py",
        "follow_ups": "add --timeout",
    }

def test_mco_fail_delegates_task_and_error(monkeypatch):
    fake = _fake(monkeypatch, fail={"success": True})
    result = mco_fail("task-789", "something broke")
    assert result == {"success": True}
    assert fake.calls == [("fail", "task-789", "something broke")]

def test_mco_send_blank_to_instance_coerced_to_none(monkeypatch):
    fake = _fake(monkeypatch)
    mco_send("claude", "Review PR", "Please look at this", to_instance="")
    _, to_role, title, instructions, to_instance = fake.calls[0]
    assert to_role == "claude"
    assert title == "Review PR"
    assert instructions == "Please look at this"
    assert to_instance is None

def test_mco_send_explicit_instance_preserved(monkeypatch):
    fake = _fake(monkeypatch)
    mco_send("codex", "Fix bug", "Here is the issue", to_instance="coding-beast-codex")
    _, _, _, _, to_instance = fake.calls[0]
    assert to_instance == "coding-beast-codex"

def test_mco_send_no_instance_arg_passes_none(monkeypatch):
    fake = _fake(monkeypatch)
    mco_send("gemini", "Analyze", "Instructions here")
    _, _, _, _, to_instance = fake.calls[0]
    assert to_instance is None

def test_mco_agents_delegates_and_returns_list(monkeypatch):
    agents = [{"instance_id": "a1", "role": "codex", "status": "online"}]
    fake = _fake(monkeypatch, agents=agents)
    result = mco_agents()
    assert result == agents
    assert fake.calls == [("agents",)]

def test_each_tool_call_resolves_the_configured_client(monkeypatch):
    """Each invocation resolves identity; the real factory retains proofs."""
    call_count = 0

    def counting_factory():
        nonlocal call_count
        call_count += 1
        return FakeGatewayClient()

    monkeypatch.setattr(mcp_mod, "_client", counting_factory)
    mco_inbox()
    mco_agents()
    assert call_count == 2


def test_mco_approve_delegates(monkeypatch):
    from mco.mcp_server import mco_approve
    fake = _fake(monkeypatch, approve={"success": True, "job": {"status": "pending"}})
    result = mco_approve("j1")
    assert result["success"] is True
    assert fake.calls == [("approve", "j1")]


def test_mco_reject_delegates_with_reason(monkeypatch):
    from mco.mcp_server import mco_reject
    fake = _fake(monkeypatch, reject={"success": True, "job": {"status": "rejected"}})
    mco_reject("j1", "nope")
    assert fake.calls == [("reject", "j1", "nope")]


def test_mco_audit_delegates(monkeypatch):
    from mco.mcp_server import mco_audit
    events = [{"event": "created"}, {"event": "leased"}]
    fake = _fake(monkeypatch, events=events)
    assert mco_audit("j1") == events
    assert fake.calls == [("events", "j1")]


# ── SDK major compatibility (mcp 1.x and 2.x) ─────────────────────────────────

def test_server_constructs_on_whichever_sdk_major_is_installed():
    """mcp 2.0.0 removed mcp.server.fastmcp; mcp_server.py must work on both.

    The day 2.0.0 shipped, an unbounded `mcp>=1.2.0` pin meant a fresh install
    resolved to a major that no longer had FastMCP - the MCP server was dead on
    arrival, and no test caught it.
    """
    from mco import mcp_server
    assert mcp_server.mcp is not None
    assert hasattr(mcp_server.mcp, "tool")
    assert hasattr(mcp_server.mcp, "run")


def test_every_governance_tool_is_registered():
    import asyncio
    from mco import mcp_server

    tools = asyncio.run(mcp_server.mcp.list_tools())
    names = {t.name for t in tools}
    # Losing any of these silently would strand every configured worker.
    for required in ("mco_inbox", "mco_lease", "mco_complete", "mco_fail", "mco_send"):
        assert required in names, f"{required} missing from the MCP tool surface"


def test_tool_schemas_mark_the_right_args_required():
    import asyncio
    from mco import mcp_server

    tools = {t.name: t for t in asyncio.run(mcp_server.mcp.list_tools())}

    def schema(tool):
        # Tool.inputSchema (mcp 1.x) was renamed to Tool.input_schema (2.x).
        return getattr(tool, "input_schema", None) or getattr(tool, "inputSchema", None) or {}

    assert schema(tools["mco_lease"]).get("required") == ["task_id"]
    assert set(schema(tools["mco_complete"]).get("required", [])) == {"task_id", "output"}
