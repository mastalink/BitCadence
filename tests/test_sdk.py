"""Agent SDK tests: lease race, handoffs, failure reporting, context building."""

import pytest

from mco.sdk import BitCadenceAgent


class FakeClient:
    """Records calls; scriptable lease/inbox/recall responses."""

    def __init__(self, inbox=None, lease_success=True, recall_map=None):
        self.base_url = "http://test"
        self.calls = []
        self._inbox = inbox or []
        self._lease_success = lease_success
        self._recall_map = recall_map or {}

    def inbox(self):
        self.calls.append(("inbox",))
        return self._inbox

    def lease(self, task_id):
        self.calls.append(("lease", task_id))
        return {"success": self._lease_success}

    def complete(self, task_id, output, handoff=None):
        self.calls.append(("complete", task_id, output, handoff))
        return {"success": True}

    def fail(self, task_id, error):
        self.calls.append(("fail", task_id, error))
        return {"success": True}

    def recall(self, query="", tags=None, limit=5):
        self.calls.append(("recall", query, tuple(tags or []), limit))
        if tags:
            return self._recall_map.get(tuple(tags), [])
        return self._recall_map.get("general", [])

    def remember(self, title, content, kind="fact", tags=None, role=None,
                 source_job_id=None):
        self.calls.append(("remember", title, kind))
        return {"success": True}

    def send(self, to_role, title, instructions, **kwargs):
        self.calls.append(("send", to_role, title))
        return {"success": True}


def _agent(client) -> BitCadenceAgent:
    return BitCadenceAgent(role="codex", instance_id="w1", client=client)


JOB = {"id": "j1", "title": "Fix the build",
       "input_payload": {"prompt": "make it green"}}


class TestProcessJob:
    def test_completes_with_plain_string_result(self):
        client = FakeClient()
        agent = _agent(client)

        @agent.handler
        def handle(job, prompt):
            return "done: " + job["id"]

        assert agent.process_job(JOB) is True
        done = next(c for c in client.calls if c[0] == "complete")
        assert done == ("complete", "j1", "done: j1", None)

    def test_tuple_result_carries_structured_handoff(self):
        client = FakeClient()
        agent = _agent(client)

        @agent.handler
        def handle(job, prompt):
            return "done", {"summary": "fixed", "files": ["a.py"]}

        agent.process_job(JOB)
        done = next(c for c in client.calls if c[0] == "complete")
        assert done[3] == {"summary": "fixed", "files": ["a.py"]}

    def test_handler_exception_reports_failure(self):
        client = FakeClient()
        agent = _agent(client)

        @agent.handler
        def handle(job, prompt):
            raise ValueError("kaput")

        assert agent.process_job(JOB) is True  # we won the lease; failure is reported
        failed = next(c for c in client.calls if c[0] == "fail")
        assert failed == ("fail", "j1", "kaput")
        assert not any(c[0] == "complete" for c in client.calls)

    def test_lost_lease_race_is_a_clean_no(self):
        client = FakeClient(lease_success=False)
        agent = _agent(client)

        @agent.handler
        def handle(job, prompt):  # pragma: no cover - must not run
            raise AssertionError("handler must not run without a lease")

        assert agent.process_job(JOB) is False

    def test_no_handler_is_a_loud_error(self):
        with pytest.raises(RuntimeError, match="handler"):
            _agent(FakeClient()).process_job(JOB)


class TestBuildPrompt:
    def test_workflow_thread_and_recall_prefix_the_prompt(self):
        thread_entry = {"id": "t1", "kind": "handoff", "title": "step research",
                        "content": "Found the bug in parser.py",
                        "created_by": "claude-1", "created_at": "2026-06-12"}
        lesson = {"id": "g1", "kind": "lesson", "title": "CI quirk",
                  "content": "Retry flaky network tests once.",
                  "created_by": "joe", "created_at": "2026-06-01"}
        client = FakeClient(recall_map={("run:abc",): [thread_entry], "general": [lesson]})
        agent = _agent(client)
        job = {"id": "j2", "title": "Implement fix",
               "input_payload": {"prompt": "apply the fix",
                                 "workflow": {"name": "rel", "run": "ABC", "step": "build"}}}
        prompt = agent.build_prompt(job)
        assert "WORKFLOW THREAD" in prompt
        assert "parser.py" in prompt
        assert "SHARED CONTEXT" in prompt
        assert prompt.rstrip().endswith("apply the fix")

    def test_context_failure_still_returns_bare_prompt(self):
        class ExplodingClient(FakeClient):
            def recall(self, *a, **k):
                raise ConnectionError("gateway down")

        agent = _agent(ExplodingClient())
        assert agent.build_prompt(JOB) == "make it green"

    def test_prompt_falls_back_to_title_and_description(self):
        agent = _agent(FakeClient())
        prompt = agent.build_prompt({"id": "x", "title": "T", "description": "D"})
        assert "T" in prompt and "D" in prompt


class TestRunOnce:
    def test_processes_every_inbox_job(self):
        jobs = [{"id": f"j{i}", "title": "t", "input_payload": {"prompt": "p"}}
                for i in range(3)]
        client = FakeClient(inbox=jobs)
        agent = _agent(client)

        @agent.handler
        def handle(job, prompt):
            return "ok"

        assert agent.run_once() == 3
        assert sum(1 for c in client.calls if c[0] == "complete") == 3


# ── Agent Exchange (additive, non-authoritative) ─────────────────────────────

def _exchange_agent(handler):
    import httpx
    from mco.orchestrator.client import GatewayClient
    client = GatewayClient(base_url="http://gw", token="t", transport=httpx.MockTransport(handler))
    return BitCadenceAgent(role="codex", instance_id="w1", client=client)


def test_exchange_post_list_get_use_exchange_api_only():
    import json
    import httpx
    seen = []

    def handler(request: httpx.Request):
        seen.append((request.method, request.url.path, dict(request.url.params),
                     json.loads(request.content) if request.content else None))
        return httpx.Response(200, json={"success": True, "items": []})

    agent = _exchange_agent(handler)
    agent.exchange_post("question", "why?", "k1", job_id="j1")
    agent.exchange_list(job_id="j1", limit=5)
    agent.exchange_get("abc")
    assert [s[:2] for s in seen] == [("POST", "/api/exchanges"), ("GET", "/api/exchanges"),
                                     ("GET", "/api/exchanges/abc")]
    assert seen[0][3]["provenance"] == {"source": "sdk"} and seen[0][3]["job_id"] == "j1"
    assert not any(s[1].startswith("/api/context") for s in seen)


def test_exchange_error_surfaces_safe_detail_only():
    import httpx
    agent = _exchange_agent(lambda r: httpx.Response(409, json={"detail": "idempotency_key was already used"}))
    with pytest.raises(RuntimeError, match="HTTP 409: idempotency_key was already used"):
        agent.exchange_post("question", "SECRET BODY", "k")
