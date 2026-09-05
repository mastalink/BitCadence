"""
Thin HTTP client for the MCO gateway dropbox.

Used by the MCP server so an IDE/agent (Claude, Codex, Antigravity) can work the
dropbox over its own scheduler. Identity (token/role/instance) and gateway URL
come from env by default:
    MCO_GATEWAY_URL   (default http://127.0.0.1:18789)
    MCO_AGENT_TOKEN   (bearer token from `mco register`)
    AGENT_ROLE        (this agent's role, e.g. "codex")
    AGENT_INSTANCE_ID (this agent's instance name)
"""

import os
from typing import Any, List, Optional

import httpx

DEFAULT_GATEWAY = "http://127.0.0.1:18789"


class GatewayClient:
    def __init__(
        self,
        base_url: Optional[str] = None,
        token: Optional[str] = None,
        role: Optional[str] = None,
        instance_id: Optional[str] = None,
        timeout: float = 30.0,
        transport: Optional[httpx.BaseTransport] = None,
    ):
        self.base_url = (base_url or os.environ.get("MCO_GATEWAY_URL") or DEFAULT_GATEWAY).rstrip("/")
        self.token = token if token is not None else os.environ.get("MCO_AGENT_TOKEN", "")
        self.role = role if role is not None else os.environ.get("AGENT_ROLE", "")
        self.instance_id = instance_id if instance_id is not None else os.environ.get("AGENT_INSTANCE_ID", "")
        self.timeout = timeout
        self._leases = {}
        self._transport = transport  # test hook (httpx.MockTransport); None in production

    def _client(self) -> httpx.Client:
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        kwargs: dict = {"base_url": self.base_url, "headers": headers, "timeout": self.timeout}
        if self._transport is not None:
            kwargs["transport"] = self._transport
        return httpx.Client(**kwargs)

    def inbox(self) -> List[dict]:
        """Jobs addressed to this agent (role/instance) that are pending."""
        with self._client() as c:
            r = c.get("/api/jobs/pending", params={"role": self.role, "instance_id": self.instance_id})
            r.raise_for_status()
            return r.json()

    def lease(self, task_id: str) -> dict:
        """Claim a job and retain its proof for subsequent renew/complete/fail."""
        with self._client() as c:
            r = c.post("/api/jobs/lease", json={"task_id": task_id, "agent_instance_id": self.instance_id})
            r.raise_for_status()
            result = r.json()
        if result.get("success") and result.get("lease"):
            self._leases[task_id] = result["lease"]
        return result

    def renew(self, task_id: str) -> dict:
        with self._client() as c:
            r = c.post(f"/api/jobs/{task_id}/renew", json=self._leases.get(task_id, {}))
            r.raise_for_status()
            return r.json()

    def _report(self, task_id: str, payload: dict) -> dict:
        """Retry transient transport/server failures; a fence is final."""
        import time
        claim = dict(self._leases.get(task_id, {}))
        pending = self._save_report(task_id, payload, claim)
        for attempt in range(4):
            try:
                with self._client() as c:
                    r = c.put(f"/api/jobs/{task_id}", json={**payload, **claim})
                    r.raise_for_status()
                    if pending is not None:
                        pending.unlink(missing_ok=True)
                    return r.json()
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code < 500 and pending is not None:
                    # Preserve the rejected output for inspection; never replay
                    # it under a new attempt or turn a fence into success.
                    pending.replace(pending.with_suffix('.rejected'))
                if exc.response.status_code < 500 or attempt == 3:
                    raise
            except httpx.TransportError:
                if attempt == 3:
                    raise
            time.sleep(min(2 ** attempt, 4))

    def _spool_dir(self):
        from pathlib import Path
        import hashlib
        configured = os.environ.get('MCO_RESULT_SPOOL_DIR')
        if self._transport is not None and not configured:
            return None  # In-memory test transports do not write user files.
        root = Path(configured).expanduser() if configured else Path.home()/'.mco'/'results'
        identity = hashlib.sha256((self.base_url+'\n'+self.instance_id+'\n'+self.token).encode()).hexdigest()
        path = root/identity
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _save_report(self, task_id, payload, claim):
        import hashlib
        import json
        import uuid
        root = self._spool_dir()
        if root is None: return None
        name = hashlib.sha256((str(task_id)+'\n'+str(claim.get('lease_id',''))).encode()).hexdigest()
        path = root/(name+'.json')
        record = {'task_id':task_id,'payload':payload,'claim':claim}
        if path.exists() and json.loads(path.read_text(encoding='utf-8')) != record:
            raise RuntimeError('A different result for this attempt is already pending delivery')
        temp = root/(name+'.'+uuid.uuid4().hex+'.tmp')
        try:
            with temp.open('x', encoding='utf-8') as stream:
                os.chmod(temp, 0o600)
                json.dump(record,stream)
                stream.flush()
                os.fsync(stream.fileno())
            temp.replace(path)
        finally:
            temp.unlink(missing_ok=True)
        return path

    def flush_reports(self):
        """Replay saved results after reconnect/restart, using their old proof."""
        import json
        root = self._spool_dir()
        if root is None: return 0
        sent = 0
        for path in sorted(root.glob('*.json'))[:10]:
            record = json.loads(path.read_text(encoding='utf-8'))
            self._leases[record['task_id']] = record['claim']
            try:
                self._report(record['task_id'], record['payload'])
                sent += 1
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code >= 500: raise
        return sent

    def complete(self, task_id: str, output: str, handoff: Optional[dict] = None) -> dict:
        output_payload = {"result": output}
        if handoff:
            output_payload["handoff"] = handoff
        return self._report(task_id, {"status": "completed", "output_payload": output_payload})

    def fail(self, task_id: str, error: str) -> dict:
        return self._report(task_id, {"status": "failed", "error_message": error})

    def send(self, to_role: str, title: str, instructions: str, to_instance: Optional[str] = None,
             depends_on: Optional[List[str]] = None, requires_approval: bool = False,
             max_retries: int = 0, escalate_to_role: Optional[str] = None,
             extra_payload: Optional[dict] = None) -> dict:
        """Drop a task/message into another agent's dropbox.

        `extra_payload` is merged into input_payload (e.g. the workflow
        thread stamp {"workflow": {"name", "run", "step"}})."""
        input_payload: dict[str, Any] = {"prompt": instructions}
        if extra_payload:
            input_payload.update(extra_payload)
        payload: dict[str, Any] = {
            "title": title,
            "description": instructions,
            "target_agent_role": to_role,
            "target_agent_id": to_instance,
            "input_payload": input_payload,
            "depends_on": depends_on or [],
        }
        if requires_approval:
            payload["requires_approval"] = True
        if max_retries:
            payload["max_retries"] = max_retries
        if escalate_to_role:
            payload["escalate_to_role"] = escalate_to_role
        with self._client() as c:
            r = c.post("/api/jobs", json=payload)
            r.raise_for_status()
            return r.json()

    def approve(self, task_id: str) -> dict:
        """Approve a job paused at the human-in-the-loop gate (releases it to pending)."""
        with self._client() as c:
            r = c.post(f"/api/jobs/{task_id}/approve")
            r.raise_for_status()
            return r.json()

    def reject(self, task_id: str, reason: str = "") -> dict:
        """Reject a job paused at the human-in-the-loop gate (terminal)."""
        with self._client() as c:
            r = c.post(f"/api/jobs/{task_id}/reject", json={"reason": reason})
            r.raise_for_status()
            return r.json()

    def retry(self, task_id: str) -> dict:
        """Re-queue a failed/rejected job to pending (approver roles only)."""
        with self._client() as c:
            r = c.post(f"/api/jobs/{task_id}/retry")
            r.raise_for_status()
            return r.json()

    def cancel(self, task_id: str, reason: str = "") -> dict:
        """Call off a not-yet-finished job (approver roles only)."""
        with self._client() as c:
            r = c.post(f"/api/jobs/{task_id}/cancel", json={"reason": reason})
            r.raise_for_status()
            return r.json()

    def archive(self, task_id: str) -> dict:
        """Hide a terminal job from the default board view (reversible)."""
        with self._client() as c:
            r = c.post(f"/api/jobs/{task_id}/archive")
            r.raise_for_status()
            return r.json()

    def unarchive(self, task_id: str) -> dict:
        """Undo archive()."""
        with self._client() as c:
            r = c.post(f"/api/jobs/{task_id}/unarchive")
            r.raise_for_status()
            return r.json()

    def duplicates(self, task_id: str) -> List[dict]:
        """Other jobs that look like the same work (same title/role, or
        already linked via reassignment)."""
        with self._client() as c:
            r = c.get(f"/api/jobs/{task_id}/duplicates")
            r.raise_for_status()
            return r.json()

    def reassign(self, task_id: str, target_agent_role: str, target_agent_id: Optional[str] = None,
                 instructions: Optional[str] = None, title: Optional[str] = None) -> dict:
        """Clone a failed/rejected/cancelled job onto a new target, link both
        rows both ways, and archive the old one (approver roles only)."""
        payload: dict[str, Any] = {"target_agent_role": target_agent_role}
        if target_agent_id:
            payload["target_agent_id"] = target_agent_id
        if instructions is not None:
            payload["instructions"] = instructions
        if title:
            payload["title"] = title
        with self._client() as c:
            r = c.post(f"/api/jobs/{task_id}/reassign", json=payload)
            r.raise_for_status()
            return r.json()

    def events(self, task_id: str) -> List[dict]:
        """Immutable audit trail for a job, oldest first."""
        with self._client() as c:
            r = c.get(f"/api/jobs/{task_id}/events")
            r.raise_for_status()
            return r.json()

    def jobs(self, include_archived: bool = False) -> List[dict]:
        """Most recent jobs on the board (any status). Archived jobs are
        hidden by default - pass include_archived=True to see everything."""
        with self._client() as c:
            r = c.get("/api/jobs", params={"include_archived": include_archived} if include_archived else None)
            r.raise_for_status()
            return r.json()

    def recall(self, query: str = "", tags: Optional[List[str]] = None, limit: int = 5) -> List[dict]:
        """Recall the most relevant Drumline shared-context entries."""
        params: dict = {"query": query, "role": self.role, "limit": limit}
        if tags:
            params["tags"] = ",".join(tags)
        with self._client() as c:
            r = c.get("/api/context", params=params)
            r.raise_for_status()
            return r.json()

    def remember(self, title: str, content: str, kind: str = "fact",
                 tags: Optional[List[str]] = None, role: Optional[str] = None,
                 source_job_id: Optional[str] = None) -> dict:
        """Append an entry to the Drumline shared context."""
        with self._client() as c:
            r = c.post("/api/context", json={
                "title": title, "content": content, "kind": kind,
                "tags": tags or [], "role": role, "source_job_id": source_job_id,
            })
            r.raise_for_status()
            return r.json()

    def integrations(self) -> List[dict]:
        """Configured enterprise connectors with health and supported actions."""
        with self._client() as c:
            r = c.get("/api/integrations")
            r.raise_for_status()
            return r.json()

    def sync_connector(self, name: str) -> dict:
        """Ingest open platform objects (incidents/problems) as jobs."""
        with self._client() as c:
            r = c.post(f"/api/integrations/{name}/sync")
            r.raise_for_status()
            return r.json()

    def platform_action(self, name: str, action: str, params: Optional[dict] = None) -> dict:
        """Run a connector control action directly (approver roles only)."""
        with self._client() as c:
            r = c.post(f"/api/integrations/{name}/action",
                       json={"action": action, "params": params or {}})
            r.raise_for_status()
            return r.json()

    def agents(self) -> List[dict]:
        with self._client() as c:
            r = c.get("/api/agents")
            r.raise_for_status()
            return r.json()

    def settings(self) -> dict:
        """Current settings grouped for the Control Panel, plus edition and known scopes."""
        with self._client() as c:
            r = c.get("/api/settings")
            r.raise_for_status()
            return r.json()

    def settings_put(self, values: dict) -> dict:
        """Apply settings changes; a null/empty value deletes that key."""
        with self._client() as c:
            r = c.put("/api/settings", json=values)
            r.raise_for_status()
            return r.json()

    def orgs(self) -> dict:
        """Orgs available for registration, which are already in use, and host-operator status."""
        with self._client() as c:
            r = c.get("/api/agents/orgs")
            r.raise_for_status()
            return r.json()

    def reset_token(self, instance_id: str) -> dict:
        """Rotate an agent's access token; the new token is returned exactly once."""
        with self._client() as c:
            r = c.post(f"/api/agents/{instance_id}/reset-token")
            r.raise_for_status()
            return r.json()

    def delete_agent(self, instance_id: str) -> dict:
        """Remove an agent registration; its token stops working immediately."""
        with self._client() as c:
            r = c.delete(f"/api/agents/{instance_id}")
            r.raise_for_status()
            return r.json()
