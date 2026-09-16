"""VIA owner policy and durable Score human-checkpoint decisions.

This module is a policy boundary, not a dispatch adapter.  It deliberately
knows only the two owner-approved reasons to interrupt a human.
"""
from __future__ import annotations

import copy
import uuid
from datetime import datetime, timezone

from mco.orchestrator.scores import ScoreError


SPEND_CAP_CENTS = 15_000
G08_LAUNCH_SIGNOFF = "g08_launch_signoff"
SPEND_ABOVE_CAP = "spend_above_cap"
GATE_KINDS = frozenset({G08_LAUNCH_SIGNOFF, SPEND_ABOVE_CAP})

VIA_OWNER_POLICY = {
    "policy_id": "via-owner-2026-09-15",
    "spend_cap_cents_per_month": SPEND_CAP_CENTS,
    "human_checkpoints": [G08_LAUNCH_SIGNOFF, SPEND_ABOVE_CAP],
    "ungated": ["production_deploy_before_g08", "all_other_goals"],
}


def required_gates(*, task_id: str, projected_monthly_cents: int) -> tuple[str, ...]:
    """Return every owner-policy gate that pauses this path.

    G08 sign-off is consumed by the first expansion task (G09); G01-G08 run
    unattended. Spend interrupts only the task that would cross the cap.
    """
    if type(projected_monthly_cents) is not int or projected_monthly_cents < 0:
        raise ScoreError("Projected monthly spend must be a non-negative integer")
    gates = []
    if task_id.upper() == "G09":
        gates.append(G08_LAUNCH_SIGNOFF)
    if projected_monthly_cents > SPEND_CAP_CENTS:
        gates.append(SPEND_ABOVE_CAP)
    return tuple(gates)


def required_gate(*, task_id: str, projected_monthly_cents: int) -> str | None:
    """Compatibility helper for callers that only render one gate."""
    gates = required_gates(
        task_id=task_id, projected_monthly_cents=projected_monthly_cents,
    )
    return gates[0] if gates else None


def authenticated_human(caller: dict) -> str:
    """Accept only an identity authenticated as a person, never an agent token."""
    if not isinstance(caller, dict) or caller.get("auth_method") not in {"session", "trusted_header"}:
        raise ScoreError("Authenticated human principal required; agent bearer tokens cannot decide Score gates")
    principal = str(caller.get("instance_id") or "").strip()
    if not principal:
        raise ScoreError("Authenticated human principal required")
    return principal


class GateService:
    """Persist and render the one explicit Score gate view."""

    def __init__(self, db):
        self.db = db

    def request(self, *, org_id: str, run_id: str, digest: str, task_id: str,
                kind: str, evidence: dict) -> dict:
        if kind not in GATE_KINDS:
            raise ScoreError("Gate kind is not allowed by the VIA owner policy")
        if not all(isinstance(value, str) and value.strip() for value in (org_id, run_id, digest, task_id)):
            raise ScoreError("Gate binding is incomplete")
        if not isinstance(evidence, dict) or not evidence:
            raise ScoreError("Gate evidence is required")
        if kind == G08_LAUNCH_SIGNOFF and task_id.upper() != "G09":
            raise ScoreError("G08 launch sign-off may pause only the G09 expansion path")
        if kind == SPEND_ABOVE_CAP:
            projected = evidence.get("projected_monthly_cents")
            if type(projected) is not int or projected <= SPEND_CAP_CENTS:
                raise ScoreError("Spend gate requires evidence above the owner cap")
        gate_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"score-gate:{org_id}:{run_id}:{digest}:{task_id}:{kind}"))
        existing = self.db.table("score_gate_requests").select("*").eq("id", gate_id).execute().data or []
        if existing:
            if existing[0].get("evidence") != evidence:
                raise ScoreError("Existing gate evidence does not match")
            return existing[0]
        row = {"id": gate_id, "org_id": org_id, "run_id": run_id, "digest": digest,
               "task_id": task_id, "kind": kind, "evidence": copy.deepcopy(evidence), "status": "pending"}
        return self.db.table("score_gate_requests").insert(row).execute().data[0]

    def list(self, *, org_id: str) -> list[dict]:
        gates = self.db.table("score_gate_requests").select("*").eq("org_id", org_id).execute().data or []
        decisions = self.db.table("score_checkpoint_decisions").select("*").eq("org_id", org_id).execute().data or []
        by_gate = {row["gate_id"]: row for row in decisions}
        return [dict(row, decision=by_gate.get(row["id"])) for row in gates]

    def decision_for(self, *, org_id: str, run_id: str, digest: str,
                     task_id: str, kind: str) -> dict | None:
        gates = (
            self.db.table("score_gate_requests").select("*")
            .eq("org_id", org_id).eq("run_id", run_id).eq("digest", digest)
            .eq("task_id", task_id).eq("kind", kind).execute().data or []
        )
        if not gates:
            return None
        decisions = (
            self.db.table("score_checkpoint_decisions").select("*")
            .eq("org_id", org_id).eq("gate_id", gates[0]["id"]).execute().data or []
        )
        return decisions[0] if decisions else None

    def approved(self, *, org_id: str, run_id: str, digest: str,
                 task_id: str, kind: str) -> bool:
        decision = self.decision_for(
            org_id=org_id, run_id=run_id, digest=digest,
            task_id=task_id, kind=kind,
        )
        return bool(decision and decision.get("decision") == "approved")

    def decide(self, gate_id: str, *, caller: dict, decision: str, reason: str = "") -> dict:
        principal = authenticated_human(caller)
        if decision not in {"approved", "rejected"}:
            raise ScoreError("Decision must be approved or rejected")
        org_id = caller.get("org_id") or "default"
        rows = self.db.table("score_gate_requests").select("*").eq("id", gate_id).eq("org_id", org_id).execute().data or []
        if not rows:
            raise ScoreError("Gate not found")
        gate = rows[0]
        if gate.get("status") != "pending":
            raise ScoreError("Gate already decided")
        record = {"id": str(uuid.uuid4()), "org_id": org_id, "gate_id": gate_id,
                  "run_id": gate["run_id"], "digest": gate["digest"], "task_id": gate["task_id"],
                  "decision": decision, "human_principal": principal, "reason": str(reason or ""),
                  "decided_at": datetime.now(timezone.utc).isoformat()}
        # UNIQUE(gate_id) on score_checkpoint_decisions is the race serializer:
        # only one immutable authorization record can be inserted. The request
        # status CAS below is a projection for efficient rendering, not the
        # guard that prevents two decisions.
        saved = self.db.table("score_checkpoint_decisions").insert(record).execute().data[0]
        updated = self.db.table("score_gate_requests").update({"status": decision}).eq("id", gate_id).eq("status", "pending").execute().data or []
        if not updated:
            raise ScoreError("Gate decision lost a concurrent race")
        return saved
