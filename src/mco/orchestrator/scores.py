"""Score v1: strict, portable plan contract and deterministic policy reference.

No network, shell execution, live job submission, or credential loading. Actor,
grant and verifier inputs must come from a trusted adapter, NOT a worker payload.
The reference kernel is for sandbox/replay testing, not a production auth layer.
"""
import argparse
import copy
import hashlib
import json
import re
import uuid
from pathlib import Path


class ScoreError(ValueError):
    """Invalid contract or forbidden state transition."""


class ScoreIdentityError(ScoreError):
    """The board's credential is not the one this run was started under.

    A subclass, because every existing `except ScoreError` must keep catching
    it. It exists so that the one failure with two right answers can be told
    apart from the rest by string-free means: typed at a terminal a credential
    change means "someone reauthorized behind this run's back" and the run
    stops durably, while for the automatic sweep it means only "not ours this
    second" and the run must be left exactly as it was.
    """


def _keys(value, required, optional=()):
    if not isinstance(value, dict):
        raise ScoreError("Expected an object")
    missing = set(required) - set(value)
    extra = set(value) - set(required) - set(optional)
    if missing or extra:
        raise ScoreError(f"Invalid fields: missing={sorted(missing)}, unknown={sorted(extra)}")


def _string(value):
    if not isinstance(value, str) or not value.strip():
        raise ScoreError("Expected nonempty string")


def _integer(value, minimum=0):
    if type(value) is not int or value < minimum:
        raise ScoreError(f"Expected integer >= {minimum}")


def _strings(value, nonempty=False):
    if not isinstance(value, list) or (nonempty and not value):
        raise ScoreError("Expected string list")
    for item in value:
        _string(item)
    if len(set(value)) != len(value):
        raise ScoreError("Duplicate list entries")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ScoreError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def load_score(source):
    """JSON text or dict only: never interpret network input as a local path."""
    try:
        score = json.loads(source, object_pairs_hook=_unique_object) if isinstance(source, str) else copy.deepcopy(source)
    except (ValueError, TypeError) as exc:
        raise ScoreError(str(exc)) from exc
    _keys(score, ("score_version", "id", "revision", "objective", "constraints", "budget_cents", "max_parallel", "tasks", "launch_requires"))
    if type(score["score_version"]) is not int or score["score_version"] != 1:
        raise ScoreError("Unsupported score_version")
    _string(score["id"])
    _string(score["objective"])
    _integer(score["revision"], 1)
    _integer(score["budget_cents"])
    _integer(score["max_parallel"], 1)
    _strings(score["constraints"], True)
    _strings(score["launch_requires"], True)
    if not isinstance(score["tasks"], list) or not 1 <= len(score["tasks"]) <= 500:
        raise ScoreError("Expected 1..500 tasks")
    ids = set()
    fields = ("id", "goal", "title", "instructions", "role", "review_role", "depends_on", "resources", "capabilities", "evidence", "max_attempts", "timeout_seconds", "max_cost_cents", "checkpoint")
    for task in score["tasks"]:
        _keys(task, fields, optional=("on_reject", "commit"))
        for key in ("id", "goal", "title", "instructions", "role", "review_role"):
            _string(task[key])
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", task["id"]):
            raise ScoreError("Invalid task identifier")
        if task["id"] in ids:
            raise ScoreError("Duplicate task identifier")
        ids.add(task["id"])
        if task["role"] == task["review_role"]:
            raise ScoreError("Work and review roles must differ")
        if "on_reject" in task and task["on_reject"] is not None:
            _string(task["on_reject"])
            if task["on_reject"] == task["id"]:
                raise ScoreError("Task on_reject cannot point to itself")
        for key in ("depends_on", "resources", "capabilities", "evidence"):
            _strings(task[key], key in ("resources", "capabilities", "evidence"))
        for key in ("max_attempts", "timeout_seconds"):
            _integer(task[key], 1)
        _integer(task["max_cost_cents"])
        if task["checkpoint"] is not None:
            _keys(task["checkpoint"], ("id", "reason"))
            _string(task["checkpoint"]["id"])
            _string(task["checkpoint"]["reason"])
        commit_conf = task.get("commit")
        if commit_conf is not None:
            if "repository:write" not in task["capabilities"]:
                raise ScoreError("commit configuration forbidden unless repository:write capability is requested")
            _keys(commit_conf, ("worktree_path", "target_branch", "allowed_paths"), optional=("commit_message", "expected_before_sha"))
            _string(commit_conf["worktree_path"])
            _string(commit_conf["target_branch"])
            _strings(commit_conf["allowed_paths"], nonempty=True)
            if "commit_message" in commit_conf and commit_conf["commit_message"] is not None:
                _string(commit_conf["commit_message"])
            if "expected_before_sha" in commit_conf and commit_conf["expected_before_sha"] is not None:
                _string(commit_conf["expected_before_sha"])
    tasks_by_id = {t["id"]: t for t in score["tasks"]}
    for task in score["tasks"]:
        if not set(task["depends_on"]) <= ids:
            raise ScoreError("Unknown dependency")
        target_id = task.get("on_reject")
        if target_id is not None and target_id not in ids:
            raise ScoreError(f"Unknown on_reject task: {target_id}")
    # An on_reject chain is a fix-attempt sequence that must also terminate without cycles.
    reject_pending = {t["id"]: {t["on_reject"]} for t in score["tasks"] if t.get("on_reject")}
    reject_nodes = set(reject_pending)
    while reject_pending:
        ready = [k for k, targets in reject_pending.items() if not (targets & reject_nodes)]
        if not ready:
            raise ScoreError("on_reject cycle")
        for k in ready:
            reject_nodes.remove(k)
            del reject_pending[k]
    for task in score["tasks"]:
        target_id = task.get("on_reject")
        if target_id is not None and task["id"] not in tasks_by_id[target_id]["depends_on"]:
            raise ScoreError("on_reject target must depend on rejected task")
    if not set(score["launch_requires"]) <= ids:
        raise ScoreError("Unknown launch requirement")
    # Kahn's algorithm avoids recursion limits on adversarial input.
    pending = {t["id"]: set(t["depends_on"]) for t in score["tasks"]}
    done = set()
    while pending:
        ready = [key for key, deps in pending.items() if deps <= done]
        if not ready:
            raise ScoreError("Dependency cycle")
        for key in ready:
            done.add(key)
            del pending[key]
    return score


def digest(score):
    return hashlib.sha256(json.dumps(load_score(score), sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()


def compile_score(score):
    """Produce inspectable work packets, NOT legacy-submittable workflows."""
    score = load_score(score)
    fingerprint = digest(score)
    return {
        "format": "bitcadence.score.execution-plan/v1",
        "score_digest": fingerprint,
        "live_submission_supported": False,
        "required_adapter_features": ["authenticated-principals", "digest-bound-authority", "transactional-outbox", "fenced-leases", "evidence-verifier", "independent-review", "human-checkpoints", "budget-reservations", "resource-locks"],
        "launch_requires": score["launch_requires"],
        "packets": [dict(copy.deepcopy(t), score_digest=fingerprint, constraints=score["constraints"], downstream_unlock="accepted_not_worker_completed") for t in score["tasks"]],
    }


class SandboxRun:
    """Pure reference state machine. No authentication or live dispatch adapter.

    Grant set and budget are externally supplied policy decisions. All clocks
    are monotonic integer seconds supplied by the trusted conductor. Evidence
    references are checked structurally here; a live verifier must fetch/hash
    artifacts and enforce their semantics before calling review().
    """

    def __init__(self, score, run_id, *, grants=(), authorized_budget_cents=0,
                 gate_service=None, org_id="default", projected_monthly_cents=0):
        self.score = load_score(score)
        _string(run_id)
        _integer(authorized_budget_cents)
        _strings(list(grants))
        self.run_id = run_id
        _string(org_id)
        self.org_id = org_id
        self.fingerprint = digest(self.score)
        self.grants = frozenset(grants)
        self.budget = min(self.score["budget_cents"], authorized_budget_cents)
        self.reserved = 0  # Conservative: failed attempts retain their full reservation.
        self.tasks = {t["id"]: t for t in self.score["tasks"]}
        self.state = {key: {"status": "pending", "attempt": 0, "token": None, "author": None, "deadline": None, "evidence": None, "approved": False} for key in self.tasks}
        self.events = []
        # Human checkpoint decisions are deliberately distinct from external
        # capability authorizations. They are rendered by the gate view and
        # never expand ``self.grants``.
        self.checkpoint_decisions = []
        self.clock = 0
        self.gate_service = gate_service
        if isinstance(projected_monthly_cents, dict):
            for task_id, amount in projected_monthly_cents.items():
                if task_id not in self.tasks:
                    raise ScoreError("Unknown projected-spend task")
                _integer(amount)
            self.projected_monthly_cents = dict(projected_monthly_cents)
        else:
            _integer(projected_monthly_cents)
            self.projected_monthly_cents = {key: projected_monthly_cents for key in self.tasks}
        if gate_service is not None:
            from mco.orchestrator.score_policy import VIA_OWNER_POLICY, required_gates
            for task_id in self.tasks:
                kinds = required_gates(
                    task_id=task_id,
                    projected_monthly_cents=self.projected_monthly_cents.get(task_id, 0),
                )
                for kind in kinds:
                    gate_service.request(
                        org_id=self.org_id, run_id=self.run_id,
                        digest=self.fingerprint, task_id=task_id, kind=kind,
                        evidence={
                            "policy_id": VIA_OWNER_POLICY["policy_id"],
                            "projected_monthly_cents": self.projected_monthly_cents.get(task_id, 0),
                        },
                    )

    def _time(self, now):
        _integer(now)
        if now < self.clock:
            raise ScoreError("Clock moved backwards")
        self.clock = now

    def _state(self, task_id):
        if task_id not in self.state:
            raise ScoreError("Unknown task")
        return self.state[task_id]

    def _event(self, kind, task_id, **data):
        self.events.append(dict(sequence=len(self.events) + 1, kind=kind, task=task_id, run=self.run_id, score_digest=self.fingerprint, at=self.clock, **data))

    def blockers(self, task_id):
        state = self._state(task_id)
        task = self.tasks[task_id]
        reasons = []
        if state["status"] != "pending":
            return [state["status"]]
        if any(self.state[d]["status"] != "accepted" for d in task["depends_on"]):
            reasons.append("dependencies")
        if not set(task["capabilities"]) <= self.grants:
            reasons.append("authority")
        from mco.orchestrator.score_policy import required_gates
        policy_gates = required_gates(
            task_id=task_id,
            projected_monthly_cents=self.projected_monthly_cents.get(task_id, 0),
        )
        # Owner-policy gates never fall back to an in-memory boolean: only an
        # immutable durable checkpoint decision can release the path.
        gate_approved = state["approved"] if not policy_gates else False
        if policy_gates and self.gate_service is not None:
            gate_approved = all(
                self.gate_service.approved(
                    org_id=self.org_id, run_id=self.run_id, digest=self.fingerprint,
                    task_id=task_id, kind=kind,
                )
                for kind in policy_gates
            )
        if (task["checkpoint"] or policy_gates) and not gate_approved:
            reasons.append("human_checkpoint")
        if self.reserved + task["max_cost_cents"] > self.budget:
            reasons.append("budget")
        active = [key for key, value in self.state.items() if value["status"] in ("running", "review")]
        if len(active) >= self.score["max_parallel"]:
            reasons.append("parallel_limit")
        if any(set(task["resources"]) & set(self.tasks[key]["resources"]) for key in active):
            reasons.append("resource_lock")
        return reasons

    def ready(self):
        return [key for key in self.tasks if not self.blockers(key)]

    def decide_checkpoint(self, task_id, *, actor, actor_kind, decision,
                          evidence=None, caller=None, gate_kind=None):
        state = self._state(task_id)
        _string(actor)
        from mco.orchestrator.score_policy import required_gates
        policy_gates = required_gates(
            task_id=task_id,
            projected_monthly_cents=self.projected_monthly_cents.get(task_id, 0),
        )
        if actor_kind != "human" or not (self.tasks[task_id]["checkpoint"] or policy_gates):
            raise ScoreError("Explicit human principal required")
        if policy_gates and self.gate_service is None:
            raise ScoreError("Durable gate service required for owner-policy decisions")
        if state["status"] != "pending":
            raise ScoreError("Checkpoint is not pending")
        if decision not in ("approved", "rejected"):
            raise ScoreError("Checkpoint decision must be approved or rejected")
        if evidence is not None and not isinstance(evidence, dict):
            raise ScoreError("Checkpoint evidence must be an object")
        if policy_gates and self.gate_service is not None:
            if not isinstance(caller, dict) or caller.get("instance_id") != actor:
                raise ScoreError("Authenticated human caller must match checkpoint actor")
            if gate_kind is None:
                if len(policy_gates) != 1:
                    raise ScoreError("gate_kind is required when multiple owner-policy gates apply")
                gate_kind = policy_gates[0]
            if gate_kind not in policy_gates:
                raise ScoreError("Gate kind does not apply to this task")
            gate_id = str(uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"score-gate:{self.org_id}:{self.run_id}:{self.fingerprint}:{task_id}:{gate_kind}",
            ))
            record = self.gate_service.decide(
                gate_id,
                caller=caller,
                decision=decision,
                reason=str((evidence or {}).get("reason") or ""),
            )
        else:
            record = dict(task=task_id, run=self.run_id, score_digest=self.fingerprint,
                          decision=decision, actor=actor, evidence=copy.deepcopy(evidence or {}), at=self.clock)
        self.checkpoint_decisions.append(record)
        state["approved"] = decision == "approved"
        self._event("human_" + decision, task_id, actor=actor, evidence=copy.deepcopy(evidence or {}))
        return copy.deepcopy(record)

    def approve(self, task_id, *, actor, actor_kind, evidence=None, caller=None,
                gate_kind=None):
        return self.decide_checkpoint(task_id, actor=actor, actor_kind=actor_kind,
                                      decision="approved", evidence=evidence, caller=caller,
                                      gate_kind=gate_kind)

    def reject(self, task_id, *, actor, actor_kind, evidence=None, caller=None,
               gate_kind=None):
        return self.decide_checkpoint(task_id, actor=actor, actor_kind=actor_kind,
                                      decision="rejected", evidence=evidence, caller=caller,
                                      gate_kind=gate_kind)

    def start(self, task_id, *, actor, role, now):
        self._time(now)
        state = self._state(task_id)
        task = self.tasks[task_id]
        _string(actor)
        if role != task["role"] or self.blockers(task_id):
            raise ScoreError(f"Task not dispatchable: {self.blockers(task_id)}")
        state["attempt"] += 1
        token = f"{self.run_id}:{self.fingerprint}:{task_id}:{state['attempt']}"
        state.update(status="running", token=token, author=actor, deadline=now + task["timeout_seconds"], evidence=None)
        self.reserved += task["max_cost_cents"]
        self._event("started", task_id, actor=actor, token=token)
        return token

    def _claim(self, task_id, token, status, now):
        self._time(now)
        state = self._state(task_id)
        if state["token"] != token or state["status"] != status or now >= state["deadline"]:
            raise ScoreError("Stale, expired or wrong-state claim")
        return state

    def finish(self, task_id, token, *, actor, evidence, now):
        state = self._claim(task_id, token, "running", now)
        if actor != state["author"]:
            raise ScoreError("Wrong author")
        _keys(evidence, self.tasks[task_id]["evidence"])
        for value in evidence.values():
            _string(value)
        state.update(status="review", evidence=copy.deepcopy(evidence), deadline=now + self.tasks[task_id]["timeout_seconds"])
        self._event("work_completed", task_id, actor=actor, evidence=copy.deepcopy(evidence))

    def review(self, task_id, token, *, actor, role, passed, verification=None, verified_evidence=None, now):
        state = self._claim(task_id, token, "review", now)
        _string(actor)
        if actor == state["author"] or role != self.tasks[task_id]["review_role"]:
            raise ScoreError("Independent reviewer required")
        on_reject_targets = {t["on_reject"]: t["id"] for t in self.tasks.values() if t.get("on_reject")}
        pred = on_reject_targets.get(task_id)
        while pred:
            pred_review_actor = getattr(self, "_review_actors", {}).get(pred)
            if pred_review_actor and actor == pred_review_actor:
                raise ScoreError("Independent reviewer required")
            pred = on_reject_targets.get(pred)
        if not hasattr(self, "_review_actors"):
            self._review_actors = {}
        self._review_actors[task_id] = actor
        if type(passed) is not bool:
            raise ScoreError("Boolean review decision required")
        # Compatibility argument is deliberately never authoritative.  A
        # worker or reviewer saying verified_evidence=true proves nothing.
        if verified_evidence is not None:
            raise ScoreError("Worker-claimed evidence verification is not authoritative")
        if passed:
            from mco.orchestrator.score_evidence import VerifiedEvidence

            if not isinstance(verification, VerifiedEvidence):
                raise ScoreError("Server evidence verification required")
            binding = verification.binding
            if binding.run_id != self.run_id or binding.task_id != task_id or binding.attempt != state["attempt"] or binding.score_digest != self.fingerprint:
                raise ScoreError("Evidence verification is stale or for another task")
            if verification.reviewer_id != actor:
                raise ScoreError("Review actor does not match server-selected reviewer")
            for event in verification.events:
                self._event(event["event_type"], task_id, actor=actor, receipt=copy.deepcopy(event["receipt"]))
        if passed:
            state["status"] = "accepted"
            self._event("accepted", task_id, actor=actor, evidence=copy.deepcopy(state["evidence"]))
        else:
            self._retry(task_id, "review_rejected")

    def _retry(self, task_id, reason):
        state = self._state(task_id)
        state["status"] = "pending" if state["attempt"] < self.tasks[task_id]["max_attempts"] else "blocked"
        state.update(token=None, evidence=None, approved=False)
        self._event(reason, task_id, status=state["status"])

    def expire(self, *, now):
        self._time(now)
        for key, state in self.state.items():
            if state["status"] in ("running", "review") and now >= state["deadline"]:
                self._retry(key, "expired")

    def report(self):
        return {"mode": "sandbox_only", "run_id": self.run_id, "score_digest": self.fingerprint,
                "launch_accepted": all(self.state[key]["status"] == "accepted" for key in self.score["launch_requires"]),
                "reserved_cents": self.reserved, "budget_cents": self.budget,
                "tasks": copy.deepcopy(self.state), "blockers": {key: self.blockers(key) for key in self.tasks},
                "events": copy.deepcopy(self.events),
                "checkpoint_decisions": copy.deepcopy(self.checkpoint_decisions)}


def main():
    parser = argparse.ArgumentParser(description="Offline Score v1 validation/compiler; never submits jobs")
    parser.add_argument("command", choices=("validate", "compile", "preview"))
    parser.add_argument("file", type=Path)
    args = parser.parse_args()
    try:
        score = load_score(args.file.read_text(encoding="utf-8"))
        if args.command == "compile":
            result = compile_score(score)
        elif args.command == "preview":
            result = SandboxRun(score, "offline-preview").report()
        else:
            result = {"valid": True, "id": score["id"], "tasks": len(score["tasks"]), "sha256": digest(score), "live_submission_supported": False}
        print(json.dumps(result, indent=2))
    except (ScoreError, OSError) as exc:
        parser.exit(2, f"Score error: {exc}\n")


if __name__ == "__main__":
    main()
