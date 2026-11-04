"""Read-only portfolio projection for the BitCadence console.

Real portfolio data is deliberately local-only.  This module accepts a small
YAML document and projects board rows without persisting or mutating either.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import subprocess
from typing import Any, Optional

import httpx
import yaml


MILESTONE_STATES = {"done", "next", "blocked"}
PROJECT_STATES = {"active", "paused"}


class PortfolioConfigError(ValueError):
    """A local portfolio document does not satisfy the public contract."""


@dataclass(frozen=True)
class PortfolioConfigResult:
    configured: bool
    config: dict[str, Any]
    source: str
    warning: Optional[str] = None


def _source_label(path: Optional[Path]) -> str:
    return "default" if path is None else "explicit"


def default_portfolio_path() -> Path:
    return Path.home() / ".bitcadence" / "portfolio.yaml"


def _require_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PortfolioConfigError(f"{label} must be non-empty text")
    return value.strip()


def _validate_command(value: Any, label: str) -> list[str]:
    if not isinstance(value, list) or not value or any(
        not isinstance(part, str) or not part.strip() for part in value
    ):
        raise PortfolioConfigError(f"{label} must be a non-empty argv list")
    return [part.strip() for part in value]


def _validate_optional_command(value: Any, label: str) -> list[str]:
    return [] if value == [] else _validate_command(value, label)


def _validate_config(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise PortfolioConfigError("portfolio configuration must be a mapping")
    if raw.get("version") != 1:
        raise PortfolioConfigError("portfolio configuration version must be 1")
    projects = raw.get("projects")
    if not isinstance(projects, list):
        raise PortfolioConfigError("projects must be a list")

    seen: set[str] = set()
    validated: list[dict[str, Any]] = []
    for item in projects:
        if not isinstance(item, dict):
            raise PortfolioConfigError("each project must be a mapping")
        project = dict(item)
        project_id = _require_text(project.get("id"), "project id")
        if project_id in seen:
            raise PortfolioConfigError("project ids must be unique")
        seen.add(project_id)
        project["id"] = project_id
        project["name"] = _require_text(project.get("name"), "project name")
        if project.get("status") not in PROJECT_STATES:
            raise PortfolioConfigError("project status must be active or paused")
        priority = project.get("priority")
        if not isinstance(priority, int) or isinstance(priority, bool) or priority < 1:
            raise PortfolioConfigError("project priority must be a positive integer")
        project["done_definition"] = _require_text(
            project.get("done_definition"), "done definition"
        )
        aliases = project.get("aliases", [])
        if not isinstance(aliases, list) or any(
            not isinstance(alias, str) or not alias.strip() for alias in aliases
        ):
            raise PortfolioConfigError("project aliases must be a list of text values")
        project["aliases"] = [alias.strip() for alias in aliases]

        milestones = project.get("milestones")
        if not isinstance(milestones, list):
            raise PortfolioConfigError("project milestones must be a list")
        checked_milestones = []
        for milestone in milestones:
            if not isinstance(milestone, dict):
                raise PortfolioConfigError("each milestone must be a mapping")
            title = _require_text(milestone.get("title"), "milestone title")
            if milestone.get("status") not in MILESTONE_STATES:
                raise PortfolioConfigError(
                    "milestone status must be done, next, or blocked"
                )
            checked_milestones.append(
                {"title": title, "status": milestone["status"]}
            )
        project["milestones"] = checked_milestones
        repository = project.get("repository")
        if repository is not None:
            if not isinstance(repository, dict):
                raise PortfolioConfigError("project repository must be a mapping")
            repository = dict(repository)
            if "pr_command" in repository:
                repository["pr_command"] = _validate_optional_command(
                    repository["pr_command"], "repository pr_command"
                )
            project["repository"] = repository
        validated.append(project)

    result = dict(raw)
    result["projects"] = validated
    publishing = result.get("publishing", {})
    if not isinstance(publishing, dict):
        raise PortfolioConfigError("publishing must be a mapping")
    publishing = dict(publishing)
    if "status_command" in publishing:
        publishing["status_command"] = _validate_optional_command(
            publishing["status_command"], "publishing status_command"
        )
    if publishing.get("episodes_root") != "" and "episodes_root" in publishing and (
        not isinstance(publishing["episodes_root"], str)
        or not publishing["episodes_root"].strip()
    ):
        raise PortfolioConfigError("publishing episodes_root must be text")
    result["publishing"] = publishing

    gpu = result.get("gpu", {})
    if not isinstance(gpu, dict):
        raise PortfolioConfigError("gpu must be a mapping")
    gpu = dict(gpu)
    terms = gpu.get("stage_terms", [])
    if not isinstance(terms, list) or any(
        not isinstance(term, str) or not term.strip() for term in terms
    ):
        raise PortfolioConfigError("gpu stage_terms must be a list of text values")
    gpu["stage_terms"] = [term.strip() for term in terms]
    for field in ("lock_command", "nvidia_smi_command"):
        if field in gpu:
            gpu[field] = _validate_optional_command(gpu[field], f"gpu {field}")
    if gpu.get("comfy_url") != "" and "comfy_url" in gpu and (
        not isinstance(gpu["comfy_url"], str) or not gpu["comfy_url"].strip()
    ):
        raise PortfolioConfigError("gpu comfy_url must be text")
    result["gpu"] = gpu

    thresholds = result.get("thresholds", {})
    if not isinstance(thresholds, dict):
        raise PortfolioConfigError("thresholds must be a mapping")
    thresholds = dict(thresholds)
    for field in ("gpu_idle_minutes", "pending_hours", "publish_due_days"):
        value = thresholds.get(field)
        if value is not None and (
            not isinstance(value, int) or isinstance(value, bool) or value < 1
        ):
            raise PortfolioConfigError(f"threshold {field} must be a positive integer")
    result["thresholds"] = thresholds
    return result


def load_portfolio_config(path: Optional[Path] = None) -> PortfolioConfigResult:
    """Load and validate a portfolio without disclosing its filesystem path."""

    selected = Path(path) if path is not None else default_portfolio_path()
    source = _source_label(path)
    if not selected.is_file():
        return PortfolioConfigResult(
            configured=False,
            config={},
            source=source,
            warning="Portfolio configuration was not found.",
        )
    try:
        raw = yaml.safe_load(selected.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise PortfolioConfigError("portfolio configuration could not be read") from exc
    return PortfolioConfigResult(
        configured=True, config=_validate_config(raw), source=source
    )


def parse_timestamp(value: Any) -> Optional[datetime]:
    """Parse an external timestamp to UTC, returning None for unsafe input."""

    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(value or "").strip().lower()).strip("-")


def _job_project_values(job: dict[str, Any]) -> set[str]:
    payload = job.get("input_payload")
    payload = payload if isinstance(payload, dict) else {}
    values: list[Any] = [
        job.get("project"),
        job.get("project_id"),
        job.get("project_name"),
        payload.get("project"),
        payload.get("project_id"),
        payload.get("project_name"),
    ]
    expanded: list[Any] = []
    for value in values:
        if isinstance(value, dict):
            expanded.extend(value.get(field) for field in ("id", "slug", "name", "title"))
        else:
            expanded.append(value)
    return {_key(value) for value in expanded if _key(value)}


def _matches(project: dict[str, Any], job: dict[str, Any]) -> bool:
    declared = {_key(project["id"]), _key(project["name"])}
    declared.update(_key(alias) for alias in project.get("aliases", []))
    return bool(declared & _job_project_values(job))


def _job_time(job: dict[str, Any]) -> Optional[datetime]:
    return parse_timestamp(job.get("updated_at")) or parse_timestamp(job.get("created_at"))


def _safe_job(job: dict[str, Any]) -> dict[str, Any]:
    fields = (
        "id",
        "title",
        "status",
        "target_agent_role",
        "leased_by_instance_id",
        "priority",
        "created_at",
        "updated_at",
    )
    return {field: job.get(field) for field in fields if job.get(field) is not None}


def _health(
    project: dict[str, Any], matched: list[dict[str, Any]], last: Optional[datetime], now: datetime
) -> str:
    milestones = project.get("milestones", [])
    if any(milestone["status"] == "blocked" for milestone in milestones):
        return "red"
    if any(job.get("status") in {"failed", "rejected"} for job in matched):
        return "red"
    if milestones and all(milestone["status"] == "done" for milestone in milestones):
        return "green"
    if project.get("status") == "paused":
        return "amber"
    if any(job.get("status") in {"needs_approval", "waiting"} for job in matched):
        return "amber"
    if last is None or (now - last).total_seconds() > 7 * 24 * 3600:
        return "amber"
    return "green"


def project_snapshot(
    config: dict[str, Any], jobs: list[dict[str, Any]], now: datetime
) -> list[dict[str, Any]]:
    """Project configured roadmaps and explicitly matched board activity."""

    now_utc = now.astimezone(timezone.utc) if now.tzinfo else now.replace(tzinfo=timezone.utc)
    snapshots = []
    for project in config.get("projects", []):
        matched = [job for job in jobs if _matches(project, job)]
        matched.sort(
            key=lambda job: _job_time(job) or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
        timestamps = [stamp for stamp in (_job_time(job) for job in matched) if stamp]
        last = max(timestamps) if timestamps else None
        milestones = [dict(item) for item in project.get("milestones", [])]
        done = sum(item["status"] == "done" for item in milestones)
        next_item = next(
            (item for item in milestones if item["status"] == "next"),
            next((item for item in milestones if item["status"] != "done"), None),
        )
        total = len(milestones)
        snapshots.append(
            {
                "id": project["id"],
                "name": project["name"],
                "status": project["status"],
                "priority": project["priority"],
                "done_definition": project["done_definition"],
                "milestones": milestones,
                "progress": {
                    "done": done,
                    "total": total,
                    "percent": round(done / total * 100) if total else 0,
                },
                "next_milestone": next_item["title"] if next_item else None,
                "last_activity": last.isoformat() if last else None,
                "health": _health(project, matched, last, now_utc),
                "recent_jobs": [_safe_job(job) for job in matched[:20]],
                "open_prs": [],
                "controls": {"read_only": True},
            }
        )
    return sorted(snapshots, key=lambda item: (item["priority"], item["name"].lower()))


def _default_runner(argv: list[str], timeout: float):
    return subprocess.run(
        argv,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def _default_fetcher(url: str, timeout: float) -> Any:
    response = httpx.get(url, timeout=timeout)
    response.raise_for_status()
    return response.json()


def _command(value: Any) -> list[str]:
    if not isinstance(value, list) or not value:
        return []
    if any(not isinstance(part, str) or not part for part in value):
        return []
    return list(value)


def _run_json(command: Any, runner, timeout: float = 5.0) -> Any:
    argv = _command(command)
    if not argv:
        raise ValueError("command is not configured")
    result = runner(argv, timeout)
    if getattr(result, "returncode", 1) != 0:
        raise RuntimeError("command failed")
    return json.loads(getattr(result, "stdout", ""))


def _safe_episode(value: Any) -> Optional[dict[str, Any]]:
    if not isinstance(value, dict):
        return None
    episode_id = value.get("id") or value.get("slug") or value.get("episode_id")
    title = value.get("title") or episode_id
    stage = value.get("stage") or value.get("status") or "unknown"
    if not title or not isinstance(stage, str):
        return None
    result = {
        "id": str(episode_id or _key(title)),
        "title": str(title),
        "stage": stage.lower(),
        "due_at": value.get("publish_at") or value.get("due_at"),
        "gates_missing": [
            str(item) for item in value.get("gates_missing", []) if isinstance(item, (str, int))
        ] if isinstance(value.get("gates_missing", []), list) else [],
    }
    if value.get("project_id"):
        result["project_id"] = str(value["project_id"])
    return result


def _publishing_result(
    episodes: list[dict[str, Any]], source: str, warning: Optional[str] = None
) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for episode in episodes:
        stage = episode["stage"]
        counts[stage] = counts.get(stage, 0) + 1
    result = {
        "available": bool(episodes),
        "source": source,
        "episodes": episodes,
        "counts": counts,
        "scheduled": counts.get("scheduled", 0),
        "published": counts.get("published", 0),
    }
    if warning:
        result["warning"] = warning
    return result


def read_publishing_state(
    config: Any, *, runner=_default_runner
) -> dict[str, Any]:
    """Read an optional publishing pipeline command, then episode files."""

    config = config if isinstance(config, dict) else {}
    command_failed = False
    if _command(config.get("status_command")):
        try:
            payload = _run_json(config["status_command"], runner)
            rows = payload.get("episodes", []) if isinstance(payload, dict) else payload
            episodes = [episode for episode in (_safe_episode(row) for row in rows or []) if episode]
            return _publishing_result(episodes, "command")
        except (OSError, ValueError, TypeError, RuntimeError, subprocess.SubprocessError, TimeoutError):
            command_failed = True

    root_value = config.get("episodes_root")
    root = Path(root_value).expanduser() if isinstance(root_value, str) and root_value else None
    episodes = []
    invalid = False
    if root and root.is_dir():
        for path in sorted(root.rglob("episode.json")):
            try:
                episode = _safe_episode(json.loads(path.read_text(encoding="utf-8")))
                if episode:
                    episodes.append(episode)
                else:
                    invalid = True
            except (OSError, UnicodeError, json.JSONDecodeError):
                invalid = True
    warning = None
    if command_failed and episodes:
        warning = "Publishing status command was unavailable; using episode files."
    elif command_failed:
        warning = "Publishing status was unavailable."
    elif invalid and not episodes:
        warning = "Publishing episode data was unavailable."
    return _publishing_result(episodes, "episodes" if root else "none", warning)


def _gpu_job(job: dict[str, Any], terms: list[str]) -> bool:
    if job.get("status") not in {"pending", "waiting"}:
        return False
    payload = job.get("input_payload")
    payload = payload if isinstance(payload, dict) else {}
    searchable = " ".join(
        str(value)
        for value in (job.get("title"), payload.get("stage"), payload.get("resource"))
        if value is not None
    ).lower()
    return any(term.lower() in searchable for term in terms)


def read_gpu_state(
    config: Any,
    jobs: list[dict[str, Any]],
    *,
    runner=_default_runner,
    fetcher=_default_fetcher,
) -> dict[str, Any]:
    """Read GPU ownership and queue sources independently and fail soft."""

    config = config if isinstance(config, dict) else {}
    warnings: list[str] = []
    holder = None
    idle_since = None
    observed = False
    lock_command = _command(config.get("lock_command"))
    if lock_command:
        try:
            lock = _run_json(lock_command, runner)
            if isinstance(lock, dict):
                holder = lock.get("holder")
                idle_since = lock.get("idle_since")
                observed = True
        except (OSError, ValueError, TypeError, RuntimeError, subprocess.SubprocessError, TimeoutError):
            warnings.append("GPU lock status was unavailable.")

    running_count = 0
    comfy_pending = 0
    comfy_url = config.get("comfy_url")
    if isinstance(comfy_url, str) and comfy_url:
        try:
            payload = fetcher(comfy_url.rstrip("/") + "/queue", 5.0)
            if isinstance(payload, dict):
                running_count = len(payload.get("queue_running") or [])
                comfy_pending = len(payload.get("queue_pending") or [])
                observed = True
        except (OSError, ValueError, TypeError, RuntimeError, httpx.HTTPError, TimeoutError):
            warnings.append("ComfyUI queue was unavailable.")

    utilization = None
    memory = None
    smi_command = _command(config.get("nvidia_smi_command"))
    if smi_command:
        try:
            result = runner(smi_command, 5.0)
            if getattr(result, "returncode", 1) != 0:
                raise RuntimeError("nvidia-smi failed")
            first = getattr(result, "stdout", "").strip().splitlines()[0]
            parts = [int(float(part.strip())) for part in first.split(",")]
            if len(parts) >= 3:
                utilization = parts[0]
                memory = {"used_mb": parts[1], "total_mb": parts[2]}
                observed = True
        except (OSError, ValueError, IndexError, RuntimeError, subprocess.SubprocessError, TimeoutError):
            warnings.append("GPU utilization was unavailable.")

    terms = [str(term) for term in config.get("stage_terms", []) if str(term).strip()]
    board_queue = [job for job in jobs if _gpu_job(job, terms)]
    stamps = [stamp for stamp in (_job_time(job) for job in board_queue) if stamp]
    queued_since = min(stamps).isoformat() if stamps else None
    idle = holder in (None, "") and running_count == 0 and (utilization is None or utilization <= 5)
    return {
        "available": observed,
        "holder": holder,
        "idle_since": idle_since,
        "utilization_percent": utilization,
        "memory": memory,
        "running": running_count,
        "queued": comfy_pending + len(board_queue),
        "queued_since": queued_since,
        "idle": idle,
        "warnings": warnings,
    }


def cpu_queue_snapshot(jobs: list[dict[str, Any]]) -> dict[str, Any]:
    running = [
        _safe_job(job) for job in jobs if job.get("status") in {"leased", "in_progress"}
    ]
    pending = [_safe_job(job) for job in jobs if job.get("status") == "pending"]
    by_worker: dict[str, int] = {}
    for job in running:
        worker = job.get("leased_by_instance_id") or "unassigned"
        by_worker[worker] = by_worker.get(worker, 0) + 1
    return {"running": running, "pending": pending, "by_worker": by_worker}


def worker_snapshot(agents: list[dict[str, Any]]) -> dict[str, Any]:
    workers = []
    counts: dict[str, int] = {}
    for agent in agents:
        status = str(agent.get("status") or "away").lower()
        if status not in {"online", "broken", "away"}:
            status = "away"
        counts[status] = counts.get(status, 0) + 1
        workers.append(
            {
                "instance_id": agent.get("instance_id"),
                "role": agent.get("role"),
                "status": status,
                "last_seen_at": agent.get("last_seen_at"),
            }
        )
    return {"workers": workers, "counts": counts}


def read_open_prs(project: dict[str, Any], *, runner=_default_runner) -> dict[str, Any]:
    repository = project.get("repository")
    repository = repository if isinstance(repository, dict) else {}
    command = _command(repository.get("pr_command"))
    if not command:
        return {"available": False, "pull_requests": []}
    try:
        rows = _run_json(command, runner)
    except (OSError, ValueError, TypeError, RuntimeError, subprocess.SubprocessError, TimeoutError):
        return {
            "available": False,
            "pull_requests": [],
            "warning": "Repository pull requests were unavailable.",
        }
    if not isinstance(rows, list):
        return {
            "available": False,
            "pull_requests": [],
            "warning": "Repository pull requests were unavailable.",
        }
    safe = []
    for row in rows:
        if not isinstance(row, dict) or not row.get("title") or not row.get("url"):
            continue
        item = {field: row[field] for field in ("number", "title", "url", "updatedAt") if field in row}
        safe.append(item)
    return {"available": True, "pull_requests": safe}


def _project_for_job(projects: list[dict[str, Any]], job_id: Any) -> Optional[str]:
    for project in projects:
        if any(job.get("id") == job_id for job in project.get("recent_jobs", [])):
            return project.get("id")
    return None


def _flag(kind: str, key: Any, title: str, detail: str, **extra: Any) -> dict[str, Any]:
    result = {
        "id": f"{kind}:{key}",
        "kind": kind,
        "severity": "red",
        "title": title,
        "detail": detail,
    }
    result.update({name: value for name, value in extra.items() if value is not None})
    return result


def compute_flow_flags(
    *,
    projects: list[dict[str, Any]],
    jobs: list[dict[str, Any]],
    workers: dict[str, Any],
    gpu: dict[str, Any],
    publishing: dict[str, Any],
    events: list[dict[str, Any]],
    thresholds: dict[str, Any],
    now: datetime,
) -> list[dict[str, Any]]:
    """Compute deterministic v1 stall flags from a single observed snapshot."""

    now_utc = now.astimezone(timezone.utc) if now.tzinfo else now.replace(tzinfo=timezone.utc)
    gpu_minutes = int(thresholds.get("gpu_idle_minutes", 30) or 30)
    pending_hours = int(thresholds.get("pending_hours", 2) or 2)
    due_days = int(thresholds.get("publish_due_days", 7) or 7)
    flags = []

    queued_since = parse_timestamp(gpu.get("queued_since"))
    idle_since = parse_timestamp(gpu.get("idle_since"))
    if (
        gpu.get("idle")
        and int(gpu.get("queued") or 0) > 0
        and queued_since
        and idle_since
    ):
        if (
            now_utc - queued_since > timedelta(minutes=gpu_minutes)
            and now_utc - idle_since > timedelta(minutes=gpu_minutes)
        ):
            flags.append(
                _flag(
                    "gpu_idle",
                    "queue",
                    "GPU work is waiting",
                    f"GPU-stage work has waited more than {gpu_minutes} minutes while the GPU is idle.",
                    observed_at=queued_since.isoformat(),
                )
            )

    for worker in workers.get("workers", []):
        if worker.get("status") == "broken":
            flags.append(
                _flag(
                    "worker_broken",
                    worker.get("instance_id") or "unknown",
                    "A worker is broken",
                    f"{worker.get('instance_id') or 'Unknown worker'} cannot take work.",
                    observed_at=worker.get("last_seen_at"),
                )
            )

    for job in jobs:
        created = parse_timestamp(job.get("created_at"))
        if job.get("status") == "pending" and created and now_utc - created > timedelta(hours=pending_hours):
            flags.append(
                _flag(
                    "job_pending",
                    job.get("id"),
                    "A job has waited too long",
                    f"{job.get('title') or 'Untitled job'} has been pending more than {pending_hours} hours.",
                    project_id=_project_for_job(projects, job.get("id")),
                    observed_at=created.isoformat(),
                )
            )

    for episode in publishing.get("episodes", []):
        due = parse_timestamp(episode.get("due_at"))
        stage = str(episode.get("stage") or "").lower()
        if due and now_utc <= due <= now_utc + timedelta(days=due_days) and stage not in {"scheduled", "published"}:
            flags.append(
                _flag(
                    "publish_due",
                    episode.get("id"),
                    "Publishing deadline is close",
                    f"{episode.get('title') or 'An episode'} is due within {due_days} days and is not scheduled.",
                    project_id=episode.get("project_id"),
                    observed_at=due.isoformat(),
                )
            )

    cutoff = now_utc - timedelta(hours=24)
    recent_events = []
    for event in events:
        stamp = parse_timestamp(event.get("created_at"))
        if event.get("event") == "chain_stalled" and stamp and cutoff <= stamp <= now_utc:
            recent_events.append((stamp, event))
    recent_events.sort(key=lambda pair: pair[0], reverse=True)
    for stamp, event in recent_events[:100]:
        flags.append(
            _flag(
                "chain_stalled",
                event.get("id") or event.get("job_id"),
                "A workflow chain stalled",
                "A dependency chain stopped advancing.",
                project_id=_project_for_job(projects, event.get("job_id")),
                observed_at=stamp.isoformat(),
            )
        )
    return flags


def build_portfolio_snapshot(
    config_result: PortfolioConfigResult,
    *,
    jobs: list[dict[str, Any]],
    agents: list[dict[str, Any]],
    events: list[dict[str, Any]],
    now: datetime,
    runner=_default_runner,
    fetcher=_default_fetcher,
) -> dict[str, Any]:
    """Compose a transport-ready snapshot from validated config and board rows."""

    generated_at = (now if now.tzinfo else now.replace(tzinfo=timezone.utc)).astimezone(timezone.utc).isoformat()
    if not config_result.configured:
        return {
            "configured": False,
            "source": config_result.source,
            "warning": config_result.warning,
            "projects": [],
            "queues": {"gpu": {"available": False}, "cpu": cpu_queue_snapshot(jobs)},
            "workers": worker_snapshot(agents),
            "publishing": {"available": False, "episodes": []},
            "flags": [],
            "generated_at": generated_at,
            "controls": {"read_only": True},
        }
    config = config_result.config
    projects = project_snapshot(config, jobs, now)
    for snapshot, project_config in zip(projects, sorted(config["projects"], key=lambda item: (item["priority"], item["name"].lower()))):
        repository = read_open_prs(project_config, runner=runner)
        snapshot["open_prs"] = repository["pull_requests"]
        snapshot["repository"] = {
            key: value for key, value in repository.items() if key != "pull_requests"
        }
        if repository.get("warning") and snapshot["health"] != "red":
            snapshot["health"] = "amber"
    gpu = read_gpu_state(config.get("gpu", {}), jobs, runner=runner, fetcher=fetcher)
    cpu = cpu_queue_snapshot(jobs)
    workers = worker_snapshot(agents)
    publishing = read_publishing_state(config.get("publishing", {}), runner=runner)
    flags = compute_flow_flags(
        projects=projects,
        jobs=jobs,
        workers=workers,
        gpu=gpu,
        publishing=publishing,
        events=events,
        thresholds=config.get("thresholds", {}),
        now=now,
    )
    red_projects = {flag.get("project_id") for flag in flags if flag.get("project_id")}
    for project in projects:
        if project["id"] in red_projects:
            project["health"] = "red"
    return {
        "configured": True,
        "source": config_result.source,
        "projects": projects,
        "queues": {"gpu": gpu, "cpu": cpu},
        "workers": workers,
        "publishing": publishing,
        "flags": flags,
        "generated_at": generated_at,
        "controls": {"read_only": True},
    }
