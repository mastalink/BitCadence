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

import hashlib
import json
import os
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, List, Optional

import httpx

DEFAULT_GATEWAY = "http://127.0.0.1:18789"
_LEASE_PROOF_FIELDS = (
    "lease_id",
    "lease_epoch",
    "lease_incarnation",
    "agent_instance_id",
)
_THREAD_LOCKS: dict[str, threading.RLock] = {}
_THREAD_LOCKS_GUARD = threading.Lock()


class LeaseProofAmbiguityError(RuntimeError):
    """No single active lease proof can safely authorize a job write."""


def _sync_directory(path: Path) -> None:
    """Persist directory-entry changes where the platform permits it."""
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


@contextmanager
def _locked_file(path: Path):
    """Serialize proof selection and retirement across threads/processes."""
    key = str(path.resolve())
    with _THREAD_LOCKS_GUARD:
        thread_lock = _THREAD_LOCKS.setdefault(key, threading.RLock())
    with thread_lock:
        descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        stream = os.fdopen(descriptor, "r+b", buffering=0)
        try:
            if os.name == "nt":  # pragma: no cover - exercised on Windows CI
                import msvcrt

                if path.stat().st_size == 0:
                    stream.write(b"\0")
                    stream.seek(0)
                while True:
                    try:
                        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                        break
                    except OSError:
                        time.sleep(0.05)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                if os.name == "nt":  # pragma: no cover - exercised on Windows CI
                    stream.seek(0)
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        finally:
            stream.close()


class GatewayClient:
    def __init__(
        self,
        base_url: Optional[str] = None,
        token: Optional[str] = None,
        role: Optional[str] = None,
        instance_id: Optional[str] = None,
        timeout: float = 30.0,
        transport: Optional[httpx.BaseTransport] = None,
        lease_store_dir: Optional[os.PathLike] = None,
    ):
        self.base_url = (base_url or os.environ.get("MCO_GATEWAY_URL") or DEFAULT_GATEWAY).rstrip("/")
        self.token = token if token is not None else os.environ.get("MCO_AGENT_TOKEN", "")
        self.role = role if role is not None else os.environ.get("AGENT_ROLE", "")
        self.instance_id = instance_id if instance_id is not None else os.environ.get("AGENT_INSTANCE_ID", "")
        self.timeout = timeout
        self._leases = {}
        self._transport = transport  # test hook (httpx.MockTransport); None in production
        configured_store = (
            lease_store_dir
            if lease_store_dir is not None
            else os.environ.get("MCO_LEASE_STORE_DIR")
        )
        if configured_store is None and transport is not None:
            self._lease_store_root = None
        else:
            self._lease_store_root = (
                Path(configured_store).expanduser()
                if configured_store
                else Path.home() / ".mco" / "leases"
            )

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

    def _identity_digest(self) -> str:
        identity = "\0".join((self.base_url, self.token, self.role, self.instance_id))
        return hashlib.sha256(identity.encode("utf-8")).hexdigest()

    @staticmethod
    def _task_digest(task_id: str) -> str:
        return hashlib.sha256(str(task_id).encode("utf-8")).hexdigest()

    def _lease_directory(self) -> Optional[Path]:
        if self._lease_store_root is None:
            return None
        directory = self._lease_store_root / self._identity_digest()
        existed = directory.exists()
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            directory.chmod(0o700)
        except OSError:
            pass
        if not existed:
            _sync_directory(directory.parent)
        return directory

    @contextmanager
    def _task_lock(self, task_id: str):
        directory = self._lease_directory()
        if directory is None:
            yield
            return
        with _locked_file(directory / f"{self._task_digest(task_id)}.lock"):
            yield

    def _proof_path(self, task_id: str, claim: dict) -> Optional[Path]:
        directory = self._lease_directory()
        if directory is None or not claim.get("lease_id"):
            return None
        lease_digest = hashlib.sha256(
            str(claim["lease_id"]).encode("utf-8")
        ).hexdigest()
        return directory / f"{self._task_digest(task_id)}.{lease_digest}.json"

    def _active_proof_paths(self, task_id: str) -> list[Path]:
        directory = self._lease_directory()
        if directory is None:
            return []
        return list(directory.glob(f"{self._task_digest(task_id)}.*.json"))

    @staticmethod
    def _write_record(path: Path, record: dict) -> None:
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(record, stream, sort_keys=True)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            _sync_directory(path.parent)
        finally:
            temporary.unlink(missing_ok=True)

    def _normalized_claim(self, value: Any) -> dict:
        if not isinstance(value, dict):
            return {}
        claim = {field: value.get(field) for field in _LEASE_PROOF_FIELDS}
        if any(item is None or item == "" for item in claim.values()):
            return {}
        epoch = claim["lease_epoch"]
        if isinstance(epoch, bool) or not isinstance(epoch, int) or epoch < 1:
            return {}
        if self.instance_id and claim["agent_instance_id"] != self.instance_id:
            return {}
        return claim

    def _record_claim(self, task_id: str, raw_claim: Any) -> None:
        """Cache a claim and durably supersede earlier attempts for this job."""
        if not isinstance(raw_claim, dict) or not raw_claim:
            return
        self._leases[task_id] = dict(raw_claim)
        claim = self._normalized_claim(raw_claim)
        if not claim:
            return  # Keep compatibility with older, proofless/partial gateways.
        with self._task_lock(task_id):
            path = self._proof_path(task_id, claim)
            if path is None:
                return
            self._write_record(path, {"task_id": str(task_id), "claim": claim})
            for previous in self._active_proof_paths(task_id):
                if previous != path:
                    os.replace(previous, previous.with_suffix(".superseded"))
            _sync_directory(path.parent)

    def _read_claim_record(self, path: Path, task_id: str) -> dict:
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise LeaseProofAmbiguityError(
                f"Unreadable lease proof for job {task_id}"
            ) from exc
        claim = self._normalized_claim(record.get("claim"))
        if record.get("task_id") != str(task_id) or not claim:
            raise LeaseProofAmbiguityError(f"Invalid lease proof for job {task_id}")
        return claim

    def _load_claim_unlocked(self, task_id: str) -> dict:
        cached = self._leases.get(task_id)
        if cached:
            return dict(cached)
        paths = self._active_proof_paths(task_id)
        if len(paths) > 1:
            raise LeaseProofAmbiguityError(
                f"Multiple active lease proofs exist for job {task_id}"
            )
        if not paths:
            return {}
        claim = self._read_claim_record(paths[0], task_id)
        self._leases[task_id] = claim
        return dict(claim)

    def _retire_claim_unlocked(self, task_id: str, claim: dict, suffix: str) -> None:
        """Compare-and-delete/rename only the exact proof used by this report."""
        self._leases.pop(task_id, None)
        path = self._proof_path(task_id, claim)
        if path is None or not path.exists():
            return
        if self._read_claim_record(path, task_id) != self._normalized_claim(claim):
            raise LeaseProofAmbiguityError(
                f"Lease proof changed while reporting job {task_id}"
            )
        if suffix:
            os.replace(path, path.with_suffix(suffix))
        else:
            path.unlink()
        _sync_directory(path.parent)

    def lease_next(self) -> dict:
        """Lease the highest-priority job addressed to this agent, server-picked.

        Preferred over inbox()+lease(): the server chooses, so priority is
        enforced rather than left to whoever is reading the list."""
        with self._client() as c:
            r = c.post("/api/jobs/lease_next", json={"agent_instance_id": self.instance_id})
            r.raise_for_status()
            result = r.json()
        job = result.get("job") or {}
        if result.get("success") and result.get("lease") and job.get("id"):
            self._record_claim(job["id"], result["lease"])
        return result

    def lease(self, task_id: str) -> dict:
        """Claim a job and retain its proof for subsequent renew/complete/fail."""
        with self._client() as c:
            r = c.post("/api/jobs/lease", json={"task_id": task_id, "agent_instance_id": self.instance_id})
            r.raise_for_status()
            result = r.json()
        if result.get("success") and result.get("lease"):
            self._record_claim(task_id, result["lease"])
        return result

    def renew(self, task_id: str) -> dict:
        with self._task_lock(task_id):
            claim = self._load_claim_unlocked(task_id)
            with self._client() as c:
                r = c.post(f"/api/jobs/{task_id}/renew", json=claim)
                r.raise_for_status()
                return r.json()

    def _report(self, task_id: str, payload: dict) -> dict:
        """Retry transient transport/server failures; a fence is final."""
        with self._task_lock(task_id):
            claim = self._load_claim_unlocked(task_id)
            pending = self._save_report(task_id, payload, claim)
            for attempt in range(4):
                try:
                    with self._client() as c:
                        r = c.put(f"/api/jobs/{task_id}", json={**payload, **claim})
                        r.raise_for_status()
                        if pending is not None:
                            pending.unlink(missing_ok=True)
                        if claim:
                            self._retire_claim_unlocked(task_id, claim, "")
                        return r.json()
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code < 500:
                        # Preserve rejected output and proof for inspection, but
                        # never replay this fenced attempt as another attempt.
                        if pending is not None:
                            os.replace(pending, pending.with_suffix(".rejected"))
                        if claim:
                            self._retire_claim_unlocked(task_id, claim, ".rejected")
                    if exc.response.status_code < 500 or attempt == 3:
                        raise
                except httpx.TransportError:
                    if attempt == 3:
                        raise
                time.sleep(min(2 ** attempt, 4))

    def _spool_dir(self):
        configured = os.environ.get('MCO_RESULT_SPOOL_DIR')
        if self._transport is not None and not configured:
            return None  # In-memory test transports do not write user files.
        root = Path(configured).expanduser() if configured else Path.home()/'.mco'/'results'
        identity = hashlib.sha256(
            (self.base_url+'\n'+self.instance_id+'\n'+self.role+'\n'+self.token).encode()
        ).hexdigest()
        path = root/identity
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _save_report(self, task_id, payload, claim):
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
             extra_payload: Optional[dict] = None, priority: int = 0) -> dict:
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
        if priority:
            payload["priority"] = priority
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
