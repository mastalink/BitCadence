"""
Agent Listener Worker Client
============================
Listens for tasks delegated via the Job Board and executes them locally.
Supports multiple named instances across different machines.
"""

import os
import json
import asyncio
import websockets
import logging
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, Any, Callable

from mco.config import get_config

logger = logging.getLogger("mco.orchestrator.listener")

# Registry of custom executors mapping role strings to async execution functions:
# async def custom_executor(job: dict, prompt: str) -> tuple[Optional[str], Optional[str]]
_executor_registry: Dict[str, Callable] = {}

def register_executor(role: str, executor_func: Callable) -> None:
    """Register a custom execution function for a specific agent role."""
    _executor_registry[role] = executor_func
    logger.info(f"Registered custom executor for role: {role}")


def _shell_executor_enabled() -> bool:
    """Whether the opt-in shell-command executor is allowed (default: off).

    The standalone listener can run a raw shell command carried in a job's
    payload. That is arbitrary code execution driven by whoever can address a
    job to this worker, so it is disabled unless an operator explicitly opts in
    with MCO_ENABLE_SHELL_EXECUTOR (config or environment)."""
    val = get_config().get("MCO_ENABLE_SHELL_EXECUTOR")
    if val is None:
        val = os.environ.get("MCO_ENABLE_SHELL_EXECUTOR")
    return str(val or "").strip().lower() in ("1", "true", "on", "yes")


class AgentListener:
    """
    Background worker that registers as an agent instance,
    polls/listens for pending jobs, leases them atomically,
    runs them locally, and updates their status on Supabase.
    """

    def __init__(self, config_path: Optional[str] = None):
        self.config_path = config_path or "agent_config.json"
        self.instance_id = "default_agent"
        self.role = "codex"
        self.gateway_ws_url = "ws://127.0.0.1:18789/ws/broadcast"
        self.poll_interval = float(os.environ.get("MCO_POLL_INTERVAL", 30.0))  # seconds

        self._load_config()

        # Derive Gateway HTTP URL from WebSocket URL
        ws_url = self.gateway_ws_url
        if ws_url.startswith("wss://"):
            http_url = ws_url.replace("wss://", "https://")
        elif ws_url.startswith("ws://"):
            http_url = ws_url.replace("ws://", "http://")
        else:
            http_url = ws_url

        if "/ws/broadcast" in http_url:
            http_url = http_url.replace("/ws/broadcast", "")
        self.gateway_http_url = http_url

        # Bearer token for authenticating REST calls to the gateway (same token as WS auth).
        self.token = get_config().get("MCO_AGENT_TOKEN") or os.environ.get("MCO_AGENT_TOKEN") or ""
        from mco.orchestrator.client import GatewayClient
        self.result_client = GatewayClient(base_url=self.gateway_http_url, token=self.token,
            role=self.role, instance_id=self.instance_id)

        logger.info(f"Agent Listener initialized: {self.instance_id} ({self.role}) using gateway HTTP: {self.gateway_http_url}")

    def _auth_headers(self) -> Dict[str, str]:
        """Authorization header for gateway REST calls."""
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}


    def _load_config(self) -> None:
        """Load configuration from JSON file or environment variables."""
        # 1. Load from file if exists
        p = Path(self.config_path)
        if p.exists():
            try:
                with open(p, "r") as f:
                    data = json.load(f)
                    self.instance_id = data.get("AGENT_INSTANCE_ID", self.instance_id)
                    self.role = data.get("AGENT_ROLE", self.role)
                    self.gateway_ws_url = data.get("GATEWAY_WS_URL", self.gateway_ws_url)
                    self.poll_interval = float(data.get("POLL_INTERVAL", self.poll_interval))
                    logger.info(f"Loaded configuration from {self.config_path}")
            except Exception as e:
                logger.warning(f"Failed to load config file {self.config_path}: {e}")

        # 2. Override with env vars / MCO Config if present
        config = get_config()
        self.instance_id = config.get("AGENT_INSTANCE_ID") or os.environ.get("AGENT_INSTANCE_ID", self.instance_id)
        self.role = config.get("AGENT_ROLE") or os.environ.get("AGENT_ROLE", self.role)
        self.gateway_ws_url = config.get("GATEWAY_WS_URL") or os.environ.get("GATEWAY_WS_URL", self.gateway_ws_url)
        
        poll_val = config.get("POLL_INTERVAL") or os.environ.get("POLL_INTERVAL")
        if poll_val:
            try:
                self.poll_interval = float(poll_val)
            except ValueError:
                pass

    async def start(self) -> None:
        """Start the background loops (WebSocket listener + Polling timer)."""
        logger.info(f"Starting agent worker loops for {self.instance_id}...")
        
        # Start periodic polling task
        polling_task = asyncio.create_task(self._periodic_poll_loop())
        
        # Start WebSocket live trigger task
        ws_task = asyncio.create_task(self._websocket_loop())
        
        await asyncio.gather(polling_task, ws_task)

    async def _websocket_loop(self) -> None:
        """Connects to the Gateway WebSocket and listens for live trigger events."""
        reconnect_delay = 1.0
        while True:
            try:
                logger.info(f"Connecting to Gateway WebSocket: {self.gateway_ws_url}...")
                async with websockets.connect(self.gateway_ws_url) as ws:
                    reconnect_delay = 1.0
                    logger.info("Connected to Gateway WebSocket.")

                    # Load MCO_AGENT_TOKEN from config or environment
                    token = get_config().get("MCO_AGENT_TOKEN") or os.environ.get("MCO_AGENT_TOKEN") or ""

                    # Send authentication payload first
                    auth_msg = {
                        "type": "authenticate",
                        "payload": {
                            "instance_id": self.instance_id,
                            "role": self.role,
                            "token": token
                        }
                    }
                    await ws.send(json.dumps(auth_msg))

                    # Verify authentication response
                    auth_res_str = await ws.recv()
                    auth_res = json.loads(auth_res_str)
                    if auth_res.get("type") == "authenticated":
                        success = (auth_res.get("payload") or {}).get("success")
                        if not success:
                            err = (auth_res.get("payload") or {}).get("error", "Unknown error")
                            logger.error(f"WebSocket authentication failed: {err}")
                            await ws.close()
                            raise websockets.ConnectionClosed(None, None)
                        logger.info("WebSocket authentication successful.")

                    # Configure session on connection
                    setup_msg = {
                        "id": f"setup-{self.instance_id}",
                        "type": "session_create",
                        "payload": {
                            "tool_profile": "coding",
                            "metadata": {
                                "instance_id": self.instance_id,
                                "role": self.role
                            }
                        }
                    }
                    await ws.send(json.dumps(setup_msg))

                    # Listen for messages
                    async for message_str in ws:
                        try:
                            msg = json.loads(message_str)
                            msg_type = msg.get("type")
                            payload = msg.get("payload") or {}

                            # Check if this is a job pending notification matching our role/id
                            if msg_type == "event":
                                event_name = payload.get("event")
                                job = payload.get("job") or {}
                                
                                if event_name == "job_pending":
                                    target_role = job.get("target_agent_role")
                                    target_id = job.get("target_agent_id")
                                    
                                    if target_role == self.role or target_id == self.instance_id:
                                        logger.info(f"WebSocket trigger received for job: {job.get('title')}. Checking immediately.")
                                        asyncio.create_task(self._process_single_job(job))

                        except json.JSONDecodeError:
                            pass
                        except Exception as e:
                            logger.error(f"Error handling WebSocket message: {e}")

            except (websockets.ConnectionClosed, OSError) as e:
                logger.warning(f"WebSocket connection lost/failed: {e}. Reconnecting in {reconnect_delay}s...")
                await asyncio.sleep(reconnect_delay)
                reconnect_delay = min(reconnect_delay * 2, 60.0)

    async def _periodic_poll_loop(self) -> None:
        """Periodic safety poll fallback loop."""
        while True:
            try:
                await self.poll_and_execute()
            except Exception as e:
                logger.error(f"Error in poll loop: {e}")
            await asyncio.sleep(self.poll_interval)

    async def poll_and_execute(self) -> None:
        """Poll the Gateway for pending jobs and process them."""
        logger.debug("Polling Gateway for pending tasks...")
        await asyncio.to_thread(self.result_client.flush_reports)
        
        import httpx
        url = f"{self.gateway_http_url}/api/jobs/pending"
        params = {"role": self.role, "instance_id": self.instance_id}
        
        try:
            async with httpx.AsyncClient() as client:
                res = await client.get(url, params=params, headers=self._auth_headers(), timeout=10.0)
                if res.status_code == 200:
                    jobs = res.json()
                    for job in jobs:
                        await self._process_single_job(job)
                else:
                    logger.error(f"Failed to poll pending jobs: HTTP {res.status_code} - {res.text}")
        except Exception as e:
            logger.error(f"Error polling pending jobs: {e}")

    async def _process_single_job(self, job: Dict[str, Any]) -> None:
        """Run one attempt, renewing it and cancelling on loss of ownership."""
        import httpx
        from mco.orchestrator.leases import idempotency_key
        job_id = job.get("id")
        if not job_id:
            return
        async with httpx.AsyncClient(headers=self._auth_headers(), timeout=10) as client:
            try:
                response = await client.post(f"{self.gateway_http_url}/api/jobs/lease",
                    json={"task_id": job_id, "agent_instance_id": self.instance_id})
                response.raise_for_status()
                leased = response.json()
                if not leased.get("success"):
                    return
                claim = leased.get("lease") or {}
                job = {**job, **claim, "idempotency_key": idempotency_key(job_id, claim.get("lease_epoch"))}
                url = f"{self.gateway_http_url}/api/jobs/{job_id}"
                response = await client.put(url, json={"status": "in_progress", **claim})
                response.raise_for_status()
                work = asyncio.create_task(self._execute_task(job))
                async def renew():
                    while True:
                        await asyncio.sleep(min(5, max(0.1, leased.get("renew_after_seconds", 300))))
                        r = await client.post(url + "/renew", json=claim)
                        r.raise_for_status()
                heartbeat = asyncio.create_task(renew())
                try:
                    done, _ = await asyncio.wait({work, heartbeat}, return_when=asyncio.FIRST_COMPLETED)
                    if heartbeat in done:
                        heartbeat.result()  # Network/fence failure stops work.
                    output, error = await work
                finally:
                    work.cancel()
                    heartbeat.cancel()
                    await asyncio.gather(work, heartbeat, return_exceptions=True)
                payload = ({"status": "failed", "error_message": error} if error else
                           {"status": "completed", "output_payload": {"result": output}})
                self.result_client._leases[job_id] = claim
                await asyncio.to_thread(self.result_client._report, job_id, payload)
            except Exception as exc:
                logger.error("Job %s could not continue/report: %s", job_id, exc)

    async def _fetch_shared_context(self, job: Dict[str, Any]) -> str:
        """Drumline tap: build the context to prepend to this job's prompt.

        Two sources, composed by merge_context():
        1. The workflow thread - when the job carries a run stamp
           (input_payload["workflow"]["run"]), every predecessor handoff in
           that run is fetched by hard tag filter. Deterministic: the next
           step ALWAYS sees what the previous steps did, regardless of
           vendor, not merely when term-overlap scoring happens to match.
        2. General recall - the soft-scored best entries for this job.

        Never raises."""
        inject = get_config().get("MCO_DRUMLINE_INJECT") or os.environ.get("MCO_DRUMLINE_INJECT") or "true"
        if str(inject).lower() == "false":
            return ""
        try:
            import httpx
            from mco.orchestrator.drumline import merge_context
            query = f"{job.get('title', '')} {job.get('description', '')[:200]}"
            wf = ((job.get("input_payload") or {}).get("workflow")) or {}
            run_id = str(wf.get("run") or "").strip().lower()
            async with httpx.AsyncClient() as client:
                thread = []
                if run_id:
                    thread_res = await client.get(
                        f"{self.gateway_http_url}/api/context",
                        params={"tags": f"run:{run_id}", "limit": 10},
                        headers=self._auth_headers(), timeout=10.0,
                    )
                    if thread_res.status_code == 200:
                        thread = thread_res.json()
                res = await client.get(
                    f"{self.gateway_http_url}/api/context",
                    params={"query": query, "role": self.role, "limit": 5},
                    headers=self._auth_headers(), timeout=10.0,
                )
                recalled = res.json() if res.status_code == 200 else []
                return merge_context(thread, recalled)
        except Exception as e:
            logger.debug(f"Drumline context fetch skipped: {e}")
        return ""

    async def _execute_task(self, job: Dict[str, Any]) -> tuple[Optional[str], Optional[str]]:
        """Run the actual tool wrapper based on target role."""
        description = job.get("description", "")
        title = job.get("title", "")
        input_payload = job.get("input_payload") or {}

        prompt = f"Task Title: {title}\nInstructions:\n{description}"
        if "prompt" in input_payload:
            prompt = input_payload["prompt"]

        # Drumline: prepend the shared-context block so every agent starts from
        # the mesh's collective memory, not a blank slate.
        context_block = await self._fetch_shared_context(job)
        if context_block:
            prompt = f"{context_block}\n\n{prompt}"

        logger.info(f"Running task with prompt length {len(prompt)}...")

        try:
            # 1. Try registered custom executor first
            if self.role in _executor_registry:
                logger.info(f"Delegating task execution to registered custom executor for role: {self.role}")
                return await _executor_registry[self.role](job, prompt)

            # 2. Standalone fallback shell command executor.
            # SECURITY: this runs an arbitrary shell command carried in a job
            # payload. Any agent that can address a job to this worker's role
            # could otherwise achieve remote code execution here, so the path
            # is OPT-IN: it stays dormant unless MCO_ENABLE_SHELL_EXECUTOR is
            # explicitly truthy. Prefer registering a typed executor
            # (register_executor) over enabling this.
            cmd = input_payload.get("command") or input_payload.get("cmd")
            if cmd:
                if not _shell_executor_enabled():
                    logger.warning(
                        "Job carries a shell 'command' but the shell executor is "
                        "disabled. Set MCO_ENABLE_SHELL_EXECUTOR=1 to allow it "
                        "(understand the RCE risk first), or register a typed "
                        "executor for role '%s'.", self.role
                    )
                    return None, (
                        "Shell command execution is disabled on this worker. "
                        "Enable it with MCO_ENABLE_SHELL_EXECUTOR=1 or register a "
                        "typed executor."
                    )
                logger.info(f"Executing local subprocess command: {cmd}")
                # Run command in shell (gated above).
                proc = await asyncio.create_subprocess_shell(
                    cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                try:
                    stdout, stderr = await proc.communicate()
                except asyncio.CancelledError:
                    from mco.orchestrator.executors import stop_process_tree
                    await stop_process_tree(proc)
                    raise
                
                stdout_str = stdout.decode().strip()
                stderr_str = stderr.decode().strip()
                
                if proc.returncode == 0:
                    return stdout_str or "Success (No Output)", None
                else:
                    return None, f"Command failed with code {proc.returncode}. Stderr: {stderr_str}"

            if str(get_config().get("MCO_DEMO_MODE") or "").lower() not in {"1", "true", "yes"}:
                return None, f"No executor configured for role '{self.role}'. Register a worker or enable explicit demo mode."
            # Explicit demo executor only; never report work that did not run.
            logger.info("No explicit command/executor found. Mocking successful execution of instruction.")
            mock_result = f"Executed instruction: '{title}' locally. Prompt: {prompt[:100]}..."
            return mock_result, None

        except Exception as e:
            logger.exception(f"Exception during task execution: {e}")
            return None, str(e)
