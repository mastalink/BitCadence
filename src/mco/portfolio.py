"""Portfolio view: one page answering "what is the whole operation doing?".

Everything personal lives in a LOCAL, gitignored config (``configs/portfolio.local.json``
or the path in ``BITCADENCE_PORTFOLIO_CONFIG``). This repo is public, so nothing in
here may name the owner; ``configs/portfolio.example.json`` is a neutral template.

All readers are defensive: a missing file, dead ComfyUI, or no ``gh`` yields an
"unavailable" note, never an exception, so the page always renders.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = ROOT / "configs" / "portfolio.local.json"
DONE_STAGES = {"scheduled", "released", "published", "done", "uploaded"}


def config_path() -> Path:
    return Path(os.environ.get("BITCADENCE_PORTFOLIO_CONFIG") or DEFAULT_CONFIG)


def load_config(path: Path | None = None) -> dict:
    p = path or config_path()
    try:
        data = json.loads(p.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {"projects": [], "_missing": True}
    if not isinstance(data, dict) or not isinstance(data.get("projects"), list):
        return {"projects": [], "_missing": True}
    return data


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse(s: Any) -> datetime | None:
    try:
        dt = datetime.fromisoformat(str(s).replace("Z", "+00:00")) if s else None
    except ValueError:
        return None
    if dt and dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


# -- roadmap ----------------------------------------------------------------
def roadmap_progress(roadmap: list) -> dict:
    items = [m for m in roadmap or [] if isinstance(m, dict)]
    done = sum(1 for m in items if m.get("done"))
    nxt = next((m.get("title") for m in items if not m.get("done")), None)
    return {"total": len(items), "done": done,
            "percent": round(100 * done / len(items)) if items else None,
            "next_milestone": nxt}


# -- live adapters (all injectable for tests) -------------------------------
def _http_json(url: str, timeout: float = 1.5) -> Any:
    with urllib.request.urlopen(url, timeout=timeout) as r:  # noqa: S310 - local URLs from owner config
        return json.loads(r.read().decode("utf-8"))


def read_gpu(cfg: dict, http: Callable = _http_json, run: Callable = subprocess.run,
             now: datetime | None = None) -> dict:
    now = now or _utcnow()
    out: dict = {"available": False, "lock_holders": [], "comfy": [], "nvidia": None, "flags": []}
    lock = cfg.get("gpu_lock_path")
    if lock:
        try:
            data = json.loads(Path(lock).read_text(encoding="utf-8-sig"))
            out["lock_holders"] = [h for h in data.get("holders", []) if isinstance(h, dict)]
            out["available"] = True
        except (OSError, ValueError):
            pass
    for url in cfg.get("comfy_urls", []):
        try:
            q = http(url.rstrip("/") + "/queue")
            out["comfy"].append({"url": url, "running": len(q.get("queue_running", [])),
                                 "pending": len(q.get("queue_pending", []))})
            out["available"] = True
        except Exception:  # noqa: BLE001 - ComfyUI is usually off between jobs
            out["comfy"].append({"url": url, "running": None, "pending": None})
    if shutil.which("nvidia-smi"):
        try:
            r = run(["nvidia-smi", "--query-gpu=utilization.gpu,memory.used,memory.total",
                     "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=5)
            u, used, total = [x.strip() for x in r.stdout.strip().splitlines()[0].split(",")]
            out["nvidia"] = {"util_pct": int(u), "mem_used_mb": int(used), "mem_total_mb": int(total)}
            out["available"] = True
        except Exception:  # noqa: BLE001
            pass
    for h in out["lock_holders"]:
        exp = _parse(h.get("expires_at") or h.get("expires"))
        if exp and exp < now - timedelta(minutes=5):
            who = h.get("holder") or h.get("owner") or "?"
            out["flags"].append({"severity": "red", "project": None, "code": "gpu_lock_stale",
                                 "message": f"GPU lock held by {who} expired {exp.isoformat()}"})
    return out


def read_saints(cfg: dict, now: datetime | None = None) -> dict:
    """Summarize the shepherd registry's episodes (read-only)."""
    now = now or _utcnow()
    out: dict = {"available": False, "episodes": 0, "by_stage": {}, "held": [], "due_soon": [], "flags": []}
    reg = cfg.get("registry_path")
    if not reg:
        return out
    try:
        paths = json.loads(Path(reg).read_text(encoding="utf-8-sig")).get("episodes", [])
    except (OSError, ValueError):
        return out
    out["available"] = True
    for ep_path in paths:
        try:
            ep = json.loads(Path(ep_path).read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            continue
        out["episodes"] += 1
        stage = str(ep.get("stage") or "?")
        out["by_stage"][stage] = out["by_stage"].get(stage, 0) + 1
        eid = ep.get("id")
        if ep.get("hold_reason"):
            out["held"].append({"id": eid, "reason": ep["hold_reason"]})
            out["flags"].append({"severity": "red", "code": "episode_held",
                                 "message": f"{eid} on HOLD: {ep['hold_reason']}"})
        pub = _parse(ep.get("publish_at"))
        if not pub or stage in DONE_STAGES:
            continue
        if pub < now:
            out["flags"].append({"severity": "red", "code": "episode_overdue",
                                 "message": f"{eid} missed its publish time in stage {stage}"})
        elif pub <= now + timedelta(days=3):
            out["due_soon"].append({"id": eid, "stage": stage, "publish_at": ep.get("publish_at")})
            out["flags"].append({"severity": "amber", "code": "episode_due_soon",
                                 "message": f"{eid} due {ep.get('publish_at')} but still {stage}"})
    return out


def read_prs(repo: str, run: Callable = subprocess.run) -> list:
    if not repo or not shutil.which("gh"):
        return []
    try:
        r = run(["gh", "pr", "list", "--repo", repo, "--state", "open", "--limit", "10",
                 "--json", "number,title,url,isDraft"], capture_output=True, text=True, timeout=15)
        return json.loads(r.stdout or "[]")
    except Exception:  # noqa: BLE001
        return []


def board_summary(jobs: list, agents: list) -> dict:
    by: dict = {}
    for j in jobs:
        by[j.get("status", "?")] = by.get(j.get("status", "?"), 0) + 1
    offline = [a.get("instance_id") for a in agents if a.get("effective_status") == "offline"]
    return {"jobs_by_status": by, "workers_total": len(agents),
            "workers_online": sum(1 for a in agents if a.get("effective_status") == "online"),
            "workers_offline": offline}


def project_jobs(project: dict, jobs: list, limit: int = 8) -> list:
    keys = [k.lower() for k in project.get("job_keywords", [])]
    hits = [j for j in jobs if keys and any(
        k in (str(j.get("title", "")) + " " + str(j.get("description", ""))).lower() for k in keys)]
    hits.sort(key=lambda j: str(j.get("created_at", "")), reverse=True)
    return [{"id": j.get("id"), "title": j.get("title"), "status": j.get("status"),
             "created_at": j.get("created_at")} for j in hits[:limit]]


# -- assembly ---------------------------------------------------------------
def build_portfolio(cfg: dict, jobs: list, agents: list, gpu: dict | None = None, saints: dict | None = None,
                    prs: Callable[[str], list] = read_prs, now: datetime | None = None) -> dict:
    now = now or _utcnow()
    gpu = gpu if gpu is not None else read_gpu(cfg)
    saints = saints if saints is not None else read_saints(cfg, now)
    flags: list = [dict(f, project=f.get("project")) for f in gpu.get("flags", []) + saints.get("flags", [])]
    board = board_summary(jobs, agents)
    for w in board["workers_offline"]:
        flags.append({"severity": "amber", "project": None, "code": "worker_offline",
                      "message": f"worker {w} offline"})
    projects = []
    for p in cfg.get("projects", []):
        pid = p.get("id")
        name = p.get("name", pid)
        prog = roadmap_progress(p.get("roadmap", []))
        pjobs = project_jobs(p, jobs)
        pflags: list = []
        active = p.get("status", "active") == "active"
        if active and not prog["total"]:
            pflags.append({"severity": "amber", "project": pid, "code": "no_roadmap",
                           "message": f"{name} is active but has no roadmap"})
        if active and not p.get("done_definition"):
            pflags.append({"severity": "amber", "project": pid, "code": "no_done_definition",
                           "message": f"{name} has no definition of done"})
        bad = [j for j in pjobs if j["status"] in ("failed", "blocked")]
        if active and bad:
            pflags.append({"severity": "red", "project": pid, "code": "jobs_failing",
                           "message": f"{name}: {len(bad)} failed/blocked job(s), e.g. {bad[0]['title']}"})
        adapter = p.get("adapter")
        shown = [dict(f, project=pid) for f in saints.get("flags", [])] if adapter == "saints" else []
        if adapter == "gpu":
            shown += [dict(f, project=pid) for f in gpu.get("flags", [])]
        allf = pflags + shown
        health = "paused" if not active else (
            "red" if any(f["severity"] == "red" for f in allf) else "amber" if allf else "green")
        flags += pflags
        projects.append({
            "id": pid, "name": name, "status": p.get("status", "active"),
            "priority": p.get("priority"), "done_definition": p.get("done_definition", ""),
            "roadmap": p.get("roadmap", []), "progress": prog, "health": health,
            "recent_jobs": pjobs, "repo": p.get("repo"),
            "open_prs": prs(p["repo"]) if p.get("repo") and active else [],
            "live": saints if adapter == "saints" else gpu if adapter == "gpu" else None,
            "flags": allf,
        })
    projects.sort(key=lambda x: (x["status"] != "active",
                                 x["priority"] if isinstance(x["priority"], int) else 99))
    seen: set = set()
    uniq: list = []
    for f in flags:
        k = (f.get("code"), f.get("message"))
        if k not in seen:
            seen.add(k)
            uniq.append(f)
    uniq.sort(key=lambda f: f["severity"] != "red")
    return {"generated_at": now.isoformat(), "config_missing": bool(cfg.get("_missing")),
            "projects": projects, "gpu": gpu, "saints": saints, "board": board, "flags": uniq,
            # TODO(controls): read-only for now. content-studio has
            # `shepherd override|priority|pause|resume|reschedule`; wire buttons through a
            # local owner-only endpoint once an owner scope exists.
            "controls": {"enabled": False,
                         "planned_commands": ["override", "priority", "pause", "resume", "reschedule"]}}
