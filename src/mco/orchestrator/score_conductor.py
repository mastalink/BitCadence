"""The loop that actually advances a Score run.

Everything a conductor needs already existed - the durable bridge (plan,
dispatch, poll, deadlines), the idempotent outbox dispatcher, evidence
verification, gates - but nothing *drove* them, so a run only moved when a
person typed a command. This is the missing piece: one tick that advances a run
by whatever is possible right now, and a loop that repeats it until the run
reaches a terminal state or its deadline.

A tick is: plan (what may start, respecting dependencies, parallelism and
resource locks) -> dispatch (create the jobs, retry-safe) -> poll (validate
completed work against its evidence, accept reviews, unlock dependents).

Every step is already idempotent, so a tick that dies halfway is safe to repeat
and a restart resumes from the database rather than resubmitting blindly.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from mco.orchestrator.score_bridge import GatewayBoard, ScoreBridge
from mco.orchestrator.scores import ScoreError, load_score

logger = logging.getLogger("mco.orchestrator.score_conductor")

TERMINAL_RUN_STATES = {"accepted", "blocked", "failed", "completed"}


@dataclass
class TickResult:
    """What one tick changed. Empty everywhere means the run is waiting on workers."""
    run_id: str
    status: str
    planned: list[str] = field(default_factory=list)
    dispatched: list[str] = field(default_factory=list)
    accepted: list[str] = field(default_factory=list)
    error: Optional[str] = None

    @property
    def idle(self) -> bool:
        return not (self.planned or self.dispatched or self.accepted)

    def describe(self) -> str:
        parts = [f"run={self.run_id}", f"status={self.status}"]
        for name, value in (("planned", self.planned), ("dispatched", self.dispatched),
                            ("accepted", self.accepted)):
            if value:
                parts.append(f"{name}={len(value)}")
        if self.error:
            parts.append(f"error={self.error}")
        return " ".join(parts)


class Conductor:
    """Drives one Score run on a board. Owns no policy of its own."""

    def __init__(self, bridge: ScoreBridge, board: GatewayBoard, *,
                 sleep: Callable[[float], Any] = time.sleep):
        self.bridge = bridge
        self.board = board
        self._sleep = sleep

    # ── state ────────────────────────────────────────────────────────────

    def status(self, run_id: str) -> dict:
        with self.bridge.tx() as db:
            run = self.bridge.run(db, run_id)
            rows = [dict(r) for r in db.execute(
                "SELECT task,phase,job_id,status FROM dispatch WHERE run=? ORDER BY task,phase", (run_id,))]
            events = [dict(r) for r in db.execute(
                "SELECT event,detail,at FROM events WHERE run=? ORDER BY seq DESC LIMIT 10", (run_id,))]
        score = json.loads(run["definition"])
        accepted = {r["task"] for r in rows if r["phase"] == "review" and r["status"] == "accepted"}
        return {
            "run_id": run_id,
            "score_id": score["id"],
            "digest": run["digest"],
            "status": run["status"],
            "tasks_total": len(score["tasks"]),
            "tasks_accepted": sorted(accepted),
            "launch_requires": score["launch_requires"],
            "launched": set(score["launch_requires"]) <= accepted,
            "dispatch": rows,
            "recent_events": events,
        }

    def _accepted(self, run_id: str) -> set:
        with self.bridge.tx() as db:
            return {r[0] for r in db.execute(
                "SELECT task FROM dispatch WHERE run=? AND phase='review' AND status='accepted'", (run_id,))}

    # ── the loop ─────────────────────────────────────────────────────────

    def tick(self, run_id: str) -> TickResult:
        """Advance the run by whatever is possible right now."""
        before = self._accepted(run_id)
        planned: list[str] = []
        dispatched: list[str] = []
        error: Optional[str] = None
        try:
            planned = self.bridge.plan(run_id)
            dispatched = self.bridge.dispatch(run_id, self.board)
            self.bridge.poll(run_id, self.board)
        except ScoreError as exc:
            # The bridge already recorded why and blocked the run; a tick
            # reports it rather than crashing the loop that called it.
            error = str(exc)
        status = self.status(run_id)
        return TickResult(run_id=run_id, status=status["status"], planned=planned,
                          dispatched=dispatched,
                          accepted=sorted(set(status["tasks_accepted"]) - before), error=error)

    def run_until_settled(self, run_id: str, *, interval: float = 5.0, timeout: float = 900.0,
                          on_tick: Optional[Callable[[TickResult], Any]] = None) -> dict:
        """Tick until the run is terminal, launched, or the timeout expires.

        Returns the final status. A conductor that cannot finish says so; it
        never reports success because it stopped looking.
        """
        deadline = time.monotonic() + timeout
        while True:
            result = self.tick(run_id)
            if on_tick is not None:
                on_tick(result)
            status = self.status(run_id)
            if status["status"] in TERMINAL_RUN_STATES or status["launched"]:
                return status
            if time.monotonic() >= deadline:
                status["timed_out"] = True
                return status
            self._sleep(interval)


def open_bridge(database: str | Path, artifact_root: str | Path) -> ScoreBridge:
    return ScoreBridge(str(database), str(artifact_root))


def start_run(bridge: ScoreBridge, *, run_id: str, score_path: str | Path, principal: str,
              org: str, targets: dict, credential_hash: str) -> dict:
    """Initialize a run from a score file. Idempotent: re-initializing the same
    run with the same policy and identities is a no-op, a changed one is an error."""
    score = load_score(Path(score_path).read_text(encoding="utf-8"))
    bridge.initialize(run_id, score, principal=principal, org=org, targets=targets,
                      credential_hash=credential_hash)
    return {"run_id": run_id, "score_id": score["id"]}


def board_for(client) -> GatewayBoard:
    return GatewayBoard(client)


def sqlite_runs(database: str | Path) -> list[dict]:
    """List runs in a conductor database (for `mco score list`)."""
    try:
        db = sqlite3.connect(str(database))
        db.row_factory = sqlite3.Row
        rows = [dict(r) for r in db.execute("SELECT id,digest,org,status FROM runs ORDER BY id")]
        db.close()
        return rows
    except sqlite3.Error:
        return []
