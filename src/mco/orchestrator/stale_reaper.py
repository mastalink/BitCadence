"""Staged stale-job policy. Nothing schedules this module or enables its grant.

Planning is pure; the report CLI opens SQLite read-only. The only Deputy
mutation is an explicitly granted, revalidated, transactional stale cancel.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

REROUTES = {"delivery_rerouted", "stale_reaper_rerouted"}
TAKEN = {"leased", "started", "in_progress", "completed", "failed"}
SCOPE = "jobs:cancel-stale"


@dataclass(frozen=True)
class Policy:
    ladder: tuple[str, ...]
    owner_ids: frozenset[str]
    retired_instances: frozenset[str] = frozenset()
    deputy_id: str = ""
    enabled: bool = False
    stop_file: Path = Path.home() / ".mco" / "STOP"

    def __post_init__(self):
        if not self.owner_ids or not self.ladder or len(set(self.ladder)) != len(self.ladder):
            raise ValueError("Explicit owner identities and a unique capacity ladder are required")


def load_policy(path: Path) -> Policy:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    return Policy(ladder=tuple(data["ladder"]), owner_ids=frozenset(data["owner_ids"]),
                  retired_instances=frozenset(data.get("retired_instances", [])),
                  deputy_id=data.get("deputy_id", ""), enabled=data.get("enabled") is True,
                  stop_file=Path(data.get("stop_file", Path.home() / ".mco" / "STOP")))


def load_server_policy() -> Policy:
    from mco.config import get_config
    path = get_config().get("MCO_STALE_REAPER_POLICY")
    if not path:
        raise PermissionError("Stale reaper has no gated server policy")
    return load_policy(Path(path))


def stamp(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else None
    except (ValueError, TypeError):
        return None


def protected(job: dict, policy: Policy) -> str | None:
    payload = job.get("input_payload") or {}
    if not isinstance(payload, dict):
        return "invalid payload"
    if job.get("archived") or job.get("status") != "pending":
        return "not an unarchived pending job"
    if not job.get("source_agent_id") or job["source_agent_id"] in policy.owner_ids:
        return "owner-created or unknown creator"
    if str(job.get("source_agent_role") or "").lower() in {"admin", "human", "user", "owner", "operator"}:
        return "human or administrative creator"
    if job.get("target_agent_id") or job.get("pinned") or payload.get("pinned"):
        return "pinned; explicit target identities are protected"
    if payload.get("no_reroute") or payload.get("unmerged_work"):
        return "identity-bound or unmerged work"
    if str(job.get("title") or "").startswith("[shepherd]"):
        return "review identity protected"
    if job.get("started_at") or job.get("leased_by_instance_id") or job.get("completed_at"):
        return "already taken"
    if job.get("depends_on"):
        return "dependency-bound; requires separate review"
    return None


def _decision(job, events, agents, policy, now):
    out = {"job_id": job.get("id"), "org_id": job.get("org_id") or "default",
           "action": "hold"}
    reason = protected(job, policy)
    history = [e for e in events if e.get("job_id") == job.get("id")]
    if any(e.get("event") in TAKEN for e in history):
        reason = "audit history shows job was taken"
    created = stamp(job.get("created_at"))
    if reason or created is None or created > now:
        return {**out, "reason": reason or "invalid or future creation clock"}
    if any(stamp(e.get("created_at")) is None for e in history):
        return {**out, "reason": "incomplete audit clock"}
    resets = [stamp(e["created_at"]) for e in history
              if e.get("event") in {"retried", "approved", "dependencies_resolved", "lease_expired", "reassigned"}]
    pending_since = max([created, *resets])
    reroutes = [stamp(e["created_at"]) for e in history if e.get("event") in REROUTES
                and stamp(e["created_at"]) >= pending_since]
    if reroutes:
        last = max(reroutes)
        if last < created or last > now:
            return {**out, "reason": "inconsistent reroute clock"}
        if now - last >= timedelta(hours=24):
            return {**out, "action": "cancel_stale", "reason": "untaken 24h after recorded reroute",
                    "rerouted_at": last.isoformat()}
        return {**out, "reason": "rerouted destination has a full 24h window"}
    # Retries, dependency release and approval start a new pending interval.
    if now - pending_since < timedelta(hours=6):
        return {**out, "reason": "pending less than 6h"}
    role = str(job.get("target_agent_role") or "").lower()
    if role not in policy.ladder:
        return {**out, "reason": "role missing from configured capacity ladder"}
    for target in policy.ladder[policy.ladder.index(role) + 1:]:
        eligible = [a for a in agents
                    if str(a.get("role") or "").lower() == target
                    and (a.get("org_id") or "default") == out["org_id"]
                    and a.get("instance_id") not in policy.retired_instances
                    and not a.get("retired") and a.get("status") == "online"
                    and (seen := stamp(a.get("last_seen_at"))) is not None
                    and timedelta(0) <= now-seen <= timedelta(minutes=5)]
        if eligible:
            return {**out, "action": "reroute", "to_role": target,
                    "reason": "untaken 6h; next online non-retired capacity tier"}
    return {**out, "reason": "no available lower capacity tier; cancellation clock not started"}


def plan_snapshot(jobs: list, events: list, agents: list, policy: Policy, *, now=None) -> dict:
    now = now or datetime.now(timezone.utc)
    stopped = policy.stop_file.exists()
    by_job = _event_index(events)
    decisions = [_decision(j, by_job.get(j.get("id"), []), agents, policy, now) for j in jobs]
    if stopped:
        decisions = [{**d, "action": "hold", "reason": "global STOP exists"} for d in decisions]
    return {"dry_run": True, "as_of": now.isoformat(), "stopped": stopped,
            "jobs": decisions,
            "retirements": [] if stopped else [
                {"instance_id": a["instance_id"], "org_id": a.get("org_id") or "default",
                 "action": "retire_from_routing", "reason": "explicit private retirement list"}
                for a in agents if a.get("instance_id") in policy.retired_instances
                and a.get("status") != "disabled"],
            "morning_report": [d for d in decisions if d["action"] == "cancel_stale"]}


def cancel_stale(db, job_id: str, policy: Policy, actor: dict, *, now=None) -> dict:
    """Narrow Deputy capability; never falls back to the general cancel API.

    A server-owned policy and an explicit scope are both mandatory. SQLite
    holds the lock through the fresh read, eligibility decision and receipt.
    No HTTP-spanning pseudo-transaction is permitted on a remote backend.
    """
    from mco.localstore import LocalStore
    from mco.orchestrator.auth import normalize_scopes
    from mco.orchestrator.audit import record_event
    if (not policy.enabled or policy.stop_file.exists() or not policy.deputy_id
            or actor.get("instance_id") != policy.deputy_id
            or SCOPE not in normalize_scopes(actor.get("scopes"))):
        raise PermissionError("Explicit Deputy stale-cancel grant and gated policy required; STOP blocks actions")
    if not isinstance(db, LocalStore):
        raise PermissionError("Stale cancellation requires an atomic backend; remote RPC not staged")
    now = now or datetime.now(timezone.utc)
    with db.transaction():
        rows = db.table("agent_jobs").select("*").eq("id", job_id).execute().data
        if not rows or (rows[0].get("org_id") or "default") != (actor.get("org_id") or "default"):
            raise PermissionError("Job outside Deputy organization")
        job = rows[0]
        events = db.table("agent_job_events").select("*").eq("job_id", job_id).execute().data
        decision = _decision(job, events, [], policy, now)
        if decision["action"] != "cancel_stale":
            raise ValueError(decision["reason"])
        if policy.stop_file.exists():
            raise PermissionError("Global STOP exists")
        result = db.table("agent_jobs").update({"status": "cancelled",
            "completed_at": now.isoformat(), "error_message": "Stale: untaken 24h after reroute"}
        ).eq("id", job_id).eq("status", "pending").execute().data
        if not result:
            raise ValueError("Job changed before cancellation")
        receipt = {**decision, "at": now.isoformat(), "actor_id": actor["instance_id"],
                   "previous_status": "pending", "morning_report": True}
        if not record_event(db, job_id, "stale_reaper_cancelled", actor["instance_id"], actor.get("role"), receipt):
            raise RuntimeError("Stale cancellation receipt did not persist")
        return receipt


def sweep_local(db, policy: Policy, *, now=None, dry_run=True) -> dict:
    """Staged trusted maintenance entry point; no timer or CLI apply switch.

    Re-read and re-plan each job under the store's write transaction. Receipts
    and mutations commit together. Registry retirement uses existing disabled
    presence semantics, removing the instance from delivery's available fleet.
    """
    from mco.localstore import LocalStore
    from mco.orchestrator.audit import record_event
    if not isinstance(db, LocalStore):
        raise PermissionError("Only atomic LocalStore sweeps are staged")
    if not dry_run and (not policy.enabled or policy.stop_file.exists()):
        raise PermissionError("Gated policy required; global STOP blocks sweep")
    now = now or datetime.now(timezone.utc)
    receipts = []
    with db.transaction():
        jobs = db.table("agent_jobs").select("*").execute().data
        events = db.table("agent_job_events").select("*").execute().data
        agents = db.table("agent_registry").select("*").execute().data
        report = plan_snapshot(jobs, events, agents, policy, now=now)
        if dry_run:
            return report
        for proposal in report["jobs"]:
            if policy.stop_file.exists():
                raise PermissionError("Global STOP exists")
            if proposal["action"] == "hold":
                continue
            job_id = proposal["job_id"]
            fresh = db.table("agent_jobs").select("*").eq("id", job_id).execute().data[0]
            history = db.table("agent_job_events").select("*").eq("job_id", job_id).execute().data
            decision = _decision(fresh, history, agents, policy, now)
            if decision["action"] == "hold":
                continue
            if decision["action"] == "cancel_stale":
                # Internal maintenance authority is deliberately separate from
                # the Deputy's externally scoped cancellation endpoint.
                changes = {"status": "cancelled", "completed_at": now.isoformat(),
                           "error_message": "Stale: untaken 24h after reroute"}
                event_name = "stale_reaper_cancelled"
            else:
                changes = {"target_agent_role": decision["to_role"], "target_agent_id": None}
                event_name = "stale_reaper_rerouted"
            updated = db.table("agent_jobs").update(changes).eq("id", job_id).eq("status", "pending").execute().data
            if not updated:
                continue
            receipt = {**decision, "at": now.isoformat(), "previous_role": fresh.get("target_agent_role"),
                       "morning_report": decision["action"] == "cancel_stale"}
            if not record_event(db, job_id, event_name, "system", "stale-reaper", receipt):
                raise RuntimeError("Sweep receipt did not persist")
            receipts.append(receipt)
        for proposal in report["retirements"]:
            if policy.stop_file.exists():
                raise PermissionError("Global STOP exists")
            instance = proposal["instance_id"]
            if any(j.get("leased_by_instance_id") == instance and j.get("status") in {"leased", "in_progress"}
                   for j in jobs):
                continue
            updated = db.table("agent_registry").update({"status": "disabled", "retired": True}
                      ).eq("instance_id", instance).execute().data
            if not updated:
                continue
            receipt = {**proposal, "at": now.isoformat()}
            if not record_event(db, "retirement:" + instance, "stale_reaper_retired", "system", "stale-reaper", receipt):
                raise RuntimeError("Retirement receipt did not persist")
            receipts.append(receipt)
    return {"dry_run": False, "receipts": receipts,
            "morning_report": [r for r in receipts if r["action"] == "cancel_stale"]}


def read_snapshot(path: Path) -> dict:
    """Read a consistent live snapshot without constructing/migrating LocalStore."""
    conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=10)
    try:
        conn.execute("PRAGMA query_only=ON")
        conn.execute("BEGIN")
        return {key: [json.loads(row[0]) for row in conn.execute(f'SELECT data FROM "{table}"')]
                for key, table in (("jobs", "agent_jobs"), ("events", "agent_job_events"),
                                   ("agents", "agent_registry"))}
    finally:
        conn.close()


def _event_index(events):
    grouped = {}
    for event in events:
        grouped.setdefault(event.get("job_id"), []).append(event)
    return grouped


def retrospective(jobs, events, agents, policy, *, now=None, days=14) -> dict:
    """Conservative threshold replay. Historical capacity cannot be inferred.

    Only jobs still pending and protected by the same current safeguards are
    simulated. A proposed reroute is never counted as a real recorded reroute.
    The downstream cancel is explicitly conditional on that first proposal.
    """
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days)
    proposals = []
    by_job = _event_index(events)
    for job in jobs:
        if protected(job, policy):
            continue
        history = by_job.get(job.get("id"), [])
        if any(e.get("event") in TAKEN for e in history):
            continue
        created = stamp(job.get("created_at"))
        if created is None or any(stamp(e.get("created_at")) is None for e in history):
            continue
        resets = [stamp(e["created_at"]) for e in history
                  if e.get("event") in {"retried", "approved", "dependencies_resolved", "lease_expired", "reassigned"}]
        pending_since = max([created, *resets])
        reroutes = [stamp(e["created_at"]) for e in history if e.get("event") in REROUTES
                    and pending_since <= stamp(e["created_at"]) <= now]
        at = pending_since + timedelta(hours=6)
        if reroutes:
            cancellation = max(reroutes) + timedelta(hours=24)
        else:
            role = str(job.get("target_agent_role") or "").lower()
            if role not in policy.ladder or role == policy.ladder[-1]:
                continue
            if cutoff <= at <= now:
                proposals.append({"job_id": job["id"], "action": "reroute", "at": at.isoformat(),
                                  "conditional": "lower tier online at this time"})
            cancellation = at + timedelta(hours=24)
        if cutoff <= cancellation <= now:
            proposals.append({"job_id": job["id"], "action": "cancel_stale", "at": cancellation.isoformat(),
                              "conditional": "recorded reroute and no intervening claim, owner protection or pin"})
    return {"window_start": cutoff.isoformat(), "window_end": now.isoformat(),
            "historical_presence_verified": False, "proposals": proposals,
            "coverage": "Conservative still-pending cohort; historical pin/creator/presence changes unavailable. Completed/taken jobs excluded, not reconstructed.",
            "actual_receipts": [e for e in events if e.get("event") == "stale_reaper_cancelled"
                                and (ts := stamp(e.get("created_at"))) is not None and cutoff <= ts <= now]}


def main():
    parser = argparse.ArgumentParser(description="Read-only stale reaper report; no apply mode")
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--policy", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    policy = load_policy(args.policy)
    if policy.stop_file.exists():
        raise SystemExit("Global STOP exists; report not started")
    data = read_snapshot(args.database)
    now = datetime.now(timezone.utc)
    report = plan_snapshot(**data, policy=policy, now=now)
    report["last_14_days"] = retrospective(**data, policy=policy, now=now)
    report["counts"] = {action: sum(d["action"] == action for d in report["jobs"])
                        for action in ("hold", "reroute", "cancel_stale")}
    # Refuse accidental overwrite of an existing evidence file.
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2, default=str)
    print(json.dumps({"counts": report["counts"], "retirements": len(report["retirements"]),
                      "historical_proposals": len(report["last_14_days"]["proposals"])}))


if __name__ == "__main__":
    main()
