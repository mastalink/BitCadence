"""
BitCadence NTFY Notifier Addon
================================

Simple addon to push important MCO events and logs to ntfy.sh (or self-hosted ntfy server)
via webhooks.

Usage in BitCadence:
- Set in .env or config:
  NTFY_SERVER=https://ntfy.sh
  NTFY_TOPIC=mco-events   # required to enable; blank = off (Local-Only default)
  NTFY_LEVELS=INFO,WARNING,ERROR   # comma separated

- Then from anywhere in the code:
  from mco.notifiers.ntfy import notify
  notify("New job for codex", priority=4, tags=["job", "codex"])

This is intentionally lightweight so it can be used for both operational logging
and "force pull" signals to agents.
"""

from __future__ import annotations

import os
from typing import Optional, List

import requests
from loguru import logger


from mco.config import get_config


def get_ntfy_config() -> dict:
    """Read ntfy settings from BitCadence config."""
    config = get_config()
    # Blank NTFY_TOPIC means off. Do not default to "mco-events"; that
    # silently enabled public ntfy.sh on Local-Only installs.
    return {
        "server": (config.get("NTFY_SERVER") or "https://ntfy.sh").rstrip("/"),
        "topic": (config.get("NTFY_TOPIC") or "").strip(),
        "token": config.get("NTFY_TOKEN"),
        "levels": [x.strip().upper() for x in config.get("NTFY_LEVELS", "INFO,WARNING,ERROR,CRITICAL").split(",")],
    }


def notify(
    message: str,
    title: Optional[str] = None,
    priority: int = 3,          # 1-5, 5 = emergency
    tags: Optional[List[str]] = None,
    topic: Optional[str] = None,
    server: Optional[str] = None,
) -> bool:
    """
    Send a notification to ntfy.

    Returns True on success, False on failure (errors are logged but do not crash the orchestrator).
    """
    cfg = get_ntfy_config()
    if not cfg["topic"]:
        return False
    server = server or cfg["server"]
    topic = topic or cfg["topic"]

    url = f"{server}/{topic}"

    headers = {
        "Title": title or "BitCadence",
        "Priority": str(priority),
    }
    if tags:
        headers["Tags"] = ",".join(tags)

    token = cfg.get("token")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    try:
        resp = requests.post(url, data=message.encode("utf-8"), headers=headers, timeout=10)
        resp.raise_for_status()
        logger.debug(f"ntfy notification sent to {topic}")
        return True
    except Exception as e:
        logger.warning(f"Failed to send ntfy notification: {e}")
        return False


# Convenience wrappers for common MCO events
def notify_job_created(job_id: str, title: str, to_role: str):
    notify(
        f"New MCO job for {to_role}: {title}",
        title="BitCadence Job Created",
        priority=3,
        tags=["mco", "job", to_role.lower()],
        topic=f"mco-{to_role.lower()}" if to_role else None,
    )


def notify_job_leased(job_id: str, agent_id: str, to_role: str):
    notify(
        f"🏃 Job {job_id} leased by {agent_id} ({to_role})",
        title="BitCadence Job Leased",
        priority=2,
        tags=["mco", "job", "leased", to_role.lower()],
        topic=f"mco-{to_role.lower()}" if to_role else None,
    )


def notify_job_completed(job_id: str, status: str, to_role: str):
    emoji = "✅" if status.lower() in ("success", "done", "completed") else "❌"
    notify(
        f"{emoji} Job {job_id} for {to_role} -> {status}",
        title="BitCadence Job Completed",
        priority=2 if status.lower() in ("success", "done", "completed") else 4,
        tags=["mco", "job", status.lower(), to_role.lower()],
        topic=f"mco-{to_role.lower()}" if to_role else None,
    )


def notify_job_failed(job_id: str, error: str, to_role: str):
    notify(
        f"❌ Job {job_id} for {to_role} FAILED: {error}",
        title="BitCadence Job FAILED",
        priority=5,
        tags=["mco", "job", "failed", to_role.lower()],
        topic=f"mco-{to_role.lower()}" if to_role else None,
    )


def notify_job_needs_approval(job_id: str, title: str, to_role: str):
    """Human-in-the-loop gate: a job is paused waiting for an approval decision."""
    notify(
        f"Job {job_id} for {to_role} awaits approval: {title}",
        title="BitCadence Approval Required",
        priority=4,
        tags=["mco", "job", "approval", to_role.lower()],
        topic=f"mco-{to_role.lower()}" if to_role else None,
    )


def notify_job_escalated(job_id: str, title: str, escalate_to_role: str, error: str):
    """A job exhausted retries and was escalated to another role."""
    notify(
        f"Job {job_id} escalated to {escalate_to_role}: {title}\nLast error: {error}",
        title="BitCadence Job ESCALATED",
        priority=5,
        tags=["mco", "job", "escalated", escalate_to_role.lower()],
        topic=f"mco-{escalate_to_role.lower()}" if escalate_to_role else None,
    )


def notify_force_pull(role: str, reason: str = "Manual trigger"):
    """Special signal used by force-pull scripts."""
    notify(
        f"FORCE_PULL instruction for {role}. Reason: {reason}. Please run your MCO loop immediately.",
        title=f"FORCE MCO PULL - {role}",
        priority=5,   # highest
        tags=["mco", "force-pull", role.lower()],
        topic=f"mco-{role.lower()}",
    )


def notify_agent_online(role: str, instance_id: str):
    notify(
        f"Agent online: {role} ({instance_id})",
        title="BitCadence Agent Online",
        priority=2,
        tags=["mco", "agent", "online", role.lower()],
    )


def notify_agent_offline(role: str, instance_id: str):
    notify(
        f"Agent offline: {role} ({instance_id})",
        title="BitCadence Agent Offline",
        priority=3,
        tags=["mco", "agent", "offline", role.lower()],
    )


def notify_gateway_startup(stats: dict):
    """Send a rich startup message with current system state."""
    msg_lines = [
        f"Gateway started on {stats.get('host')}:{stats.get('port')}",
        f"PID: {stats.get('pid')}",
        f"Agents: {stats.get('agent_count', 0)} total ({stats.get('online_count', 0)} online)",
        f"Pending jobs: {stats.get('pending_jobs', 0)}",
    ]
    if stats.get('process_count'):
        msg_lines.append(f"Processes: {stats.get('process_count')}")
    
    notify(
        "\n".join(msg_lines),
        title="BitCadence Gateway Started",
        priority=2,
        tags=["gateway", "startup", "mco"],
    )
