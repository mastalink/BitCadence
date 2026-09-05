"""
BitCadence Agent SDK - write a worker in fifteen lines.

The public, stable surface for third-party agent authors. Everything else in
this codebase may move; this module follows semver.

    from mco.sdk import BitCadenceAgent

    agent = BitCadenceAgent(role="summarizer", instance_id="sum-1",
                       token="mco_tok_...", gateway="http://127.0.0.1:18789")

    @agent.handler
    def handle(job, prompt):
        text = do_the_work(prompt)
        return text                       # or: return text, {"summary": ...,
                                          #     "files": [...], "gotchas": [...]}

    agent.run()   # poll -> lease -> handle -> complete/fail, forever

What you get for free:
- **Atomic leasing**: two instances of your worker never run the same job.
- **The Context Exchange**: the prompt your handler receives is prefixed
  with the workflow thread (every predecessor handoff in this run) and the
  best general Drumline recall - your agent starts where the last one
  stopped, whatever vendor it was.
- **Structured handoffs**: return `(result, handoff_dict)` and the next
  agent receives your decisions/files/gotchas verbatim.
- **Governance**: failures are reported (never swallowed), so retry
  budgets, escalation, and the audit trail behave exactly as for the
  built-in workers.
"""

from __future__ import annotations

import logging
import time
import threading
import httpx
from contextlib import contextmanager
from typing import Callable, List, Optional, Tuple, Union

from mco.orchestrator.client import GatewayClient
from mco.orchestrator.drumline import merge_context

logger = logging.getLogger("mco.sdk")

# A handler returns the result text, optionally with a structured handoff:
#   {summary, decisions, files, gotchas, follow_ups}
HandlerResult = Union[str, Tuple[str, dict]]


class Halted(RuntimeError):
    """The worker lost permission to continue this attempt."""


class BitCadenceAgent:
    """A polling worker bound to one role/instance identity."""

    def __init__(
        self,
        role: str,
        instance_id: str,
        token: str = "",
        gateway: str = "",
        poll_interval: float = 15.0,
        context_limit: int = 5,
        client: Optional[GatewayClient] = None,
    ):
        self.role = role
        self.instance_id = instance_id
        self.poll_interval = poll_interval
        self.context_limit = context_limit
        self.client = client or GatewayClient(
            base_url=gateway or None, token=token or None,
            role=role, instance_id=instance_id,
        )
        self._handler: Optional[Callable[[dict, str], HandlerResult]] = None
        self._active_job = None
        self._halted = threading.Event()

    # ── Wiring ───────────────────────────────────────────────────────────

    def handler(self, func: Callable[[dict, str], HandlerResult]):
        """Decorator registering the function that executes each job.

        The function receives (job, prompt) and returns the result text or
        (result_text, handoff_dict). Raising marks the job failed with the
        exception message - never swallow errors yourself; let governance
        (retries/escalation) do its job.
        """
        self._handler = func
        return func

    # ── Context (Drumline) ───────────────────────────────────────────────

    def build_prompt(self, job: dict) -> str:
        """The job's prompt prefixed with its Context Exchange blocks.

        Workflow thread first (deterministic - every predecessor handoff in
        this run), then general recall. Best-effort: if the gateway's
        context API is unreachable, the bare prompt still comes back.
        """
        payload = job.get("input_payload") or {}
        prompt = payload.get("prompt") or (
            f"Task Title: {job.get('title', '')}\nInstructions:\n{job.get('description', '')}"
        )
        try:
            wf = (payload.get("workflow")) or {}
            run_id = str(wf.get("run") or "").strip().lower()
            thread: List[dict] = []
            if run_id:
                thread = self.client.recall(tags=[f"run:{run_id}"], limit=10)
            query = f"{job.get('title', '')} {(job.get('description') or '')[:200]}"
            recalled = self.client.recall(query=query, limit=self.context_limit)
            block = merge_context(thread, recalled)
            if block:
                return f"{block}\n\n{prompt}"
        except Exception as e:
            logger.debug(f"Context fetch skipped: {e}")
        return prompt

    def remember(self, title: str, content: str, kind: str = "fact",
                 tags: Optional[List[str]] = None) -> dict:
        """Write a durable fact/decision/lesson into Drumline for every agent."""
        return self.client.remember(title, content, kind=kind, tags=tags, role=self.role)

    def recall(self, query: str = "", tags: Optional[List[str]] = None,
               limit: int = 5) -> List[dict]:
        """Read the most relevant shared context, best first."""
        return self.client.recall(query=query, tags=tags, limit=limit)

    def send(self, to_role: str, title: str, instructions: str, **kwargs) -> dict:
        """Drop a job into another agent's dropbox (any vendor, any machine)."""
        return self.client.send(to_role, title, instructions, **kwargs)

    # ── The work loop ────────────────────────────────────────────────────

    def checkpoint(self):
        """Call between work units and before any irreversible external action.

        Python cannot safely interrupt arbitrary user code. A handler must
        cooperate here; built-in subprocess workers cancel their child task.
        """
        if self._halted.is_set():
            raise Halted("Lease expired, was cancelled, or was halted")
        if self._active_job:
            try:
                self.client.renew(self._active_job)
            except (httpx.HTTPError, RuntimeError) as exc:
                self._halted.set()
                raise Halted("Cannot confirm ownership; stop work") from exc

    @contextmanager
    def _keep_lease(self, job_id, interval):
        self._active_job = job_id
        self._halted.clear()
        stop = threading.Event()
        def maintain():
            while not stop.wait(max(0.1, interval)):
                try:
                    self.checkpoint()
                except Halted:
                    return
        thread = threading.Thread(target=maintain, daemon=True)
        thread.start()
        try:
            yield
        finally:
            stop.set()
            thread.join(timeout=self.client.timeout + 1 if isinstance(self.client, GatewayClient) else 1)
            self._active_job = None

    def process_job(self, job: dict) -> bool:
        """Lease and execute one job. True if this instance won and ran it."""
        if self._handler is None:
            raise RuntimeError("No handler registered. Decorate one with @agent.handler.")
        job_id = job.get("id")
        if not job_id:
            return False
        lease = self.client.lease(job_id)
        if not (lease or {}).get("success"):
            return False  # another instance won the race

        job = {**job, **(lease.get("lease") or {})}
        from mco.orchestrator.leases import idempotency_key
        job["idempotency_key"] = idempotency_key(job_id, job.get("lease_epoch"))
        prompt = self.build_prompt(job)
        try:
            with self._keep_lease(job_id, min(5, lease.get("renew_after_seconds", 300))):
                result = self._handler(job, prompt)
                if self._halted.is_set():
                    raise Halted("Handler stopped after losing its lease")
        except Halted:
            logger.warning("Job %s halted; result withheld", job_id)
            return True
        except Exception as e:
            logger.exception(f"Handler failed for job {job_id}")
            self.client.fail(job_id, str(e) or e.__class__.__name__)
            return True

        handoff: Optional[dict] = None
        if isinstance(result, tuple):
            result, handoff = result[0], result[1]
        self.client.complete(job_id, str(result), handoff=handoff)
        return True

    def run_once(self) -> int:
        """One poll cycle: process every pending job in the inbox.
        Returns how many jobs this instance executed."""
        executed = 0
        if hasattr(self.client, 'flush_reports'):
            self.client.flush_reports()
        for job in self.client.inbox() or []:
            if self.process_job(job):
                executed += 1
        return executed

    def run(self) -> None:
        """Poll forever (Ctrl+C to stop). One process, one identity."""
        logger.info(f"BitCadenceAgent '{self.instance_id}' ({self.role}) polling "
                    f"{self.client.base_url} every {self.poll_interval}s")
        while True:
            try:
                self.run_once()
            except KeyboardInterrupt:
                logger.info("Stopped.")
                return
            except Exception as e:
                logger.warning(f"Poll cycle failed (will retry): {e}")
            time.sleep(self.poll_interval)


# Back-compat: the class was `BatonAgent` before the BitCadence rename. Worker
# scripts live outside this repo (pilot installs, agent VMs), so importing the
# old name keeps working rather than breaking a fleet on upgrade.
BatonAgent = BitCadenceAgent
