"""Read-only portfolio projection for the BitCadence console.

Real portfolio data is deliberately local-only.  This module accepts a small
YAML document and projects board rows without persisting or mutating either.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import re
from typing import Any, Optional

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
        validated.append(project)

    result = dict(raw)
    result["projects"] = validated
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
