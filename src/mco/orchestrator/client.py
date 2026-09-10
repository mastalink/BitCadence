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
_PROCESS_LEASE_OWNER_ID = uuid.uuid4().hex
_THREAD_LOCKS: dict[str, threading.RLock] = {}
_THREAD_LOCKS_GUARD = threading.Lock()


class LeaseProofAmbiguityError(RuntimeError):
    """No single lease attempt can be selected safely for a job write."""


def _sync_directory(path: Path) -> None:
    """Persist directory-entry changes where the platform permits it."""
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    try:
        fd = os.open(path, flags)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


@contextmanager
def _locked_file(path: Path):
    """Cross-process exclusive lock, paired with an in-process thread lock."""
    key = str(path)
    with _THREAD_LOCKS_GUARD:
        thread_lock = _THREAD_LOCKS.setdefault(key, threading.RLock())
    with thread_lock:
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        stream = os.fdopen(fd, "r+b", buffering=0)
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
        lease_owner_id: Optional[str] = None,
    ):
        self.base_url = (base_url or os.environ.get("MCO_GATEWAY_URL") or DEFAULT_GATEWAY).rstrip("/")
        self.token = token if token is not None else os.environ.get("MCO_AGENT_TOKEN", "")
        self.role = role if role is not None else os.environ.get("AGENT_ROLE", "")
        self.instance_id = instance_id if instance_id is not None else os.environ.get("AGENT_INSTANCE_ID", "")
        self.timeout = timeout
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
        self._lease_proofs: dict[str, dict] = {}
        self._legacy_leases: set[str] = set()
        self._retired_tasks: set[str] = set()
        self._lease_owner_id = (
            lease_owner_id if lease_owner_id is not None else _PROCESS_LEASE_OWNER_ID
        )
        if not self._lease_owner_id:
            raise ValueError("lease_owner_id must not be empty")

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
        """Atomically claim a job and retain its proof for reporting."""
        with self._client() as c:
            r = c.post("/api/jobs/lease", json={"task_id": task_id, "agent_instance_id": self.instance_id})
            r.raise_for_status()
            result = r.json()

        if result.get("success"):
            with self._task_lock(task_id):
                raw_proof = result.get("lease")
                if raw_proof is None:
                    self._legacy_leases.add(task_id)
                    self._write_legacy_marker(task_id)
                else:
                    proof = self._normalize_lease_proof(raw_proof)
                    if not proof:
                        raise LeaseProofAmbiguityError(
                            f"Gateway returned an invalid lease proof for job {task_id}"
                        )
                    self._lease_proofs[task_id] = proof
                    self._write_lease_proof(task_id, proof)
        return result

    def _normalize_lease_proof(self, proof: Any) -> dict:
        if not isinstance(proof, dict):
            return {}
        normalized = {field: proof.get(field) for field in _LEASE_PROOF_FIELDS}
        if any(value is None or value == "" for value in normalized.values()):
            return {}
        epoch = normalized["lease_epoch"]
        if isinstance(epoch, bool) or not isinstance(epoch, int) or epoch < 1:
            return {}
        if self.instance_id and normalized["agent_instance_id"] != self.instance_id:
            return {}
        return normalized

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

    def _lease_path(self, task_id: str, proof: dict) -> Optional[Path]:
        directory = self._lease_directory()
        if directory is None:
            return None
        lease_digest = hashlib.sha256(str(proof["lease_id"]).encode("utf-8")).hexdigest()
        return directory / f"{self._task_digest(task_id)}.{lease_digest}.json"

    @contextmanager
    def _task_lock(self, task_id: str):
        directory = self._lease_directory()
        if directory is None:
            yield
            return
        path = directory / f"{self._task_digest(task_id)}.lock"
        with _locked_file(path):
            yield

    def _legacy_path(self, task_id: str) -> Optional[Path]:
        directory = self._lease_directory()
        if directory is None:
            return None
        return directory / f"{self._task_digest(task_id)}.legacy"

    def _active_paths(self, task_id: str) -> list[Path]:
        directory = self._lease_directory()
        if directory is None:
            return []
        return list(directory.glob(f"{self._task_digest(task_id)}.*.json"))

    def _retired_paths(self, task_id: str) -> list[Path]:
        directory = self._lease_directory()
        if directory is None:
            return []
        return list(directory.glob(f"{self._task_digest(task_id)}.*.retired"))

    @staticmethod
    def _write_record(path: Path, record: dict) -> None:
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(record, stream, sort_keys=True)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            _sync_directory(path.parent)
        finally:
            temporary.unlink(missing_ok=True)

    def _write_lease_proof(self, task_id: str, proof: dict) -> None:
        path = self._lease_path(task_id, proof)
        if path is not None:
            self._write_record(path, {
                "task_id": str(task_id),
                "lease": proof,
                "owner_id": self._lease_owner_id,
            })

    def _write_legacy_marker(self, task_id: str) -> None:
        path = self._legacy_path(task_id)
        if path is not None:
            self._write_record(path, {
                "task_id": str(task_id),
                "legacy": True,
                "owner_id": self._lease_owner_id,
            })

    def _legacy_marker_owner(self, task_id: str) -> Optional[str]:
        path = self._legacy_path(task_id)
        if path is None:
            return None
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, json.JSONDecodeError) as exc:
            raise LeaseProofAmbiguityError(
                f"Unreadable legacy lease proof for job {task_id}"
            ) from exc
        owner_id = record.get("owner_id")
        if (
            record.get("task_id") != str(task_id)
            or record.get("legacy") is not True
            or not isinstance(owner_id, str)
            or not owner_id
        ):
            raise LeaseProofAmbiguityError(
                f"Invalid legacy lease proof for job {task_id}"
            )
        return owner_id

    def _load_lease_proof(self, task_id: str) -> dict:
        if task_id in self._lease_proofs:
            return dict(self._lease_proofs[task_id])
        if task_id in self._legacy_leases:
            return {}
        retired = task_id in self._retired_tasks or bool(self._retired_paths(task_id))
        records = []
        for path in self._active_paths(task_id):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise LeaseProofAmbiguityError(
                    f"Unreadable lease proof for job {task_id}"
                ) from exc
            proof = self._normalize_lease_proof(record.get("lease"))
            owner_id = record.get("owner_id")
            if (
                record.get("task_id") != str(task_id)
                or not proof
                or not isinstance(owner_id, str)
                or not owner_id
            ):
                raise LeaseProofAmbiguityError(
                    f"Invalid lease proof for job {task_id}"
                )
            records.append((proof, owner_id))

        legacy_owner = self._legacy_marker_owner(task_id)
        owned = [proof for proof, owner_id in records if owner_id == self._lease_owner_id]
        owns_legacy = legacy_owner == self._lease_owner_id
        if len(owned) + int(owns_legacy) > 1:
            raise LeaseProofAmbiguityError(
                f"Multiple lease proofs exist for job {task_id}; refusing automatic selection"
            )
        if owned:
            self._lease_proofs[task_id] = owned[0]
            return dict(owned[0])
        if owns_legacy:
            self._legacy_leases.add(task_id)
            return {}

        if retired:
            raise LeaseProofAmbiguityError(
                f"A retired lease proof exists for job {task_id}; refusing replay"
            )
        if len(records) > 1 or (legacy_owner is not None and records):
            raise LeaseProofAmbiguityError(
                f"Multiple lease proofs exist for job {task_id}; refusing automatic selection"
            )
        if records:
            self._lease_proofs[task_id] = records[0][0]
            return dict(records[0][0])
        if legacy_owner is not None:
            self._legacy_leases.add(task_id)
        return {}

    def _clear_lease_proof(self, task_id: str, proof: dict) -> None:
        self._lease_proofs.pop(task_id, None)
        self._legacy_leases.discard(task_id)
        path = self._lease_path(task_id, proof) if proof else self._legacy_path(task_id)
        if path is not None:
            path.unlink(missing_ok=True)
            _sync_directory(path.parent)

    def _retire_lease_proof(self, task_id: str, proof: dict) -> None:
        self._lease_proofs.pop(task_id, None)
        self._legacy_leases.discard(task_id)
        self._retired_tasks.add(task_id)
        active = self._lease_path(task_id, proof) if proof else self._legacy_path(task_id)
        if active is None:
            return
        retired = active.with_name(f"{active.name}.retired")
        if active.exists():
            os.replace(active, retired)
            _sync_directory(active.parent)
        elif not retired.exists():
            self._write_record(
                retired,
                {
                    "task_id": str(task_id),
                    "lease": proof,
                    "owner_id": self._lease_owner_id,
                    "retired": True,
                },
            )

    def _report(self, task_id: str, payload: dict) -> dict:
        with self._task_lock(task_id):
            proof = self._load_lease_proof(task_id)
            known_attempt = bool(proof) or task_id in self._legacy_leases
            with self._client() as c:
                r = c.put(f"/api/jobs/{task_id}", json={**payload, **proof})
                try:
                    r.raise_for_status()
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code == 409 and known_attempt:
                        self._retire_lease_proof(task_id, proof)
                    raise
                result = r.json()
            if known_attempt:
                self._clear_lease_proof(task_id, proof)
            return result

    def complete(self, task_id: str, output: str, handoff: Optional[dict] = None) -> dict:
        """Mark a job completed. `handoff` is the structured Context Exchange
        channel ({summary, decisions, files, gotchas, follow_ups}) - Drumline
        stores it verbatim for the next agent instead of mining the text."""
        output_payload: dict = {"result": output}
        if handoff:
            output_payload["handoff"] = handoff
        return self._report(
            task_id,
            {"status": "completed", "output_payload": output_payload},
        )

    def fail(self, task_id: str, error: str) -> dict:
        return self._report(
            task_id,
            {"status": "failed", "error_message": error},
        )

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
