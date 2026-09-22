"""The conductor sweep: score runs advance because the gateway ticks them.

C02 proved a tick works. These tests pin the thing that calls it on a timer -
and, more importantly, the six ways an automatic caller could do damage that a
human typing `mco score tick` never would: trampling another conductor's run,
resurrecting a blocked one, letting one poisoned run stall the rest, racing a
second gateway, dying mid-tick, or failing silently.
"""

import asyncio
import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from mco.localstore import LocalStore
from mco.orchestrator import health, routes, score_sweep
from mco.orchestrator.score_canary_worker import review, work
from mco.orchestrator.score_conductor import Conductor, open_bridge, start_run
from mco.orchestrator.scores import ScoreIdentityError
from mco.orchestrator.score_sweep import (
    SKIP_LAUNCHED,
    SKIP_OTHER_CREDENTIAL,
    SweepResult,
    get_sweep_seconds,
    sweep,
)

CANARY = Path(__file__).resolve().parents[1] / "examples" / "scores" / "via-score-conductor-canary.score.json"
TARGETS = {"score-canary-worker": "canary-worker-1", "score-canary-review": "canary-review-1"}
PRINCIPAL = "score-conductor-1"


class FakeBoard:
    """A board with real create_with_id v1 semantics.

    Re-creating an id returns the *existing* job untouched - that is the whole
    point of the retry-safe protocol, and the reason two gateways dispatching the
    same score job at the same moment is safe. A board that overwrote instead
    would silently reset a completed job to pending and hide the race.
    """

    def __init__(self, identity="cred-hash"):
        self.identity = identity
        self.jobs: dict = {}
        self.event_log: dict = {}
        self.create_calls: list[str] = []
        self.before_create = None
        self._lock = threading.RLock()

    def capabilities(self):
        return {"create_with_id": 1}

    def create(self, payload):
        if self.before_create is not None:
            self.before_create(payload)
        with self._lock:
            self.create_calls.append(payload["id"])
            existing = self.jobs.get(payload["id"])
            if existing is not None:
                return dict(existing)
            job = {**payload, "status": "pending", "source_agent_id": PRINCIPAL,
                   "org_id": "default", "output_payload": None, "leased_by_instance_id": None}
            self.jobs[payload["id"]] = job
            return dict(job)

    def get(self, job_id):
        with self._lock:
            return dict(self.jobs[job_id])

    def events(self, job_id):
        with self._lock:
            return list(self.event_log.get(job_id, []))

    # ── standing in for a worker ─────────────────────────────────────────
    def finish(self, job_id, result: str, *, actor=None):
        with self._lock:
            job = self.jobs[job_id]
            actor = actor or job["target_agent_id"]
            job.update(status="completed", leased_by_instance_id=actor,
                       output_payload={"result": result})
            self.event_log.setdefault(job_id, []).append(
                {"job_id": job_id, "event": "status:completed", "actor_id": actor,
                 "actor_role": job["target_agent_role"]})


def _conductor(tmp_path, board=None, *, database="runs.db"):
    board = board if board is not None else FakeBoard()
    bridge = open_bridge(tmp_path / database, tmp_path / "artifacts")
    return Conductor(bridge, board, sleep=lambda _s: None)


def _start(conductor, run_id="run-1", *, score_path=CANARY, credential=None):
    start_run(conductor.bridge, run_id=run_id, score_path=score_path, principal=PRINCIPAL,
              org="default", targets=TARGETS,
              credential_hash=credential if credential is not None else conductor.board.identity)
    return run_id


def _events(conductor, run_id):
    return [row["event"] for row in conductor.bridge.status(run_id)["events"]]


def _job_of(conductor, run_id, phase="work", task="C01"):
    """The board job the conductor created for one phase of one task."""
    row = next(r for r in conductor.status(run_id)["dispatch"]
               if r["task"] == task and r["phase"] == phase)
    return row["job_id"]


def _do_work(conductor, job_id, *, actor="canary-worker-1"):
    job = conductor.board.get(job_id)
    conductor.board.finish(job_id, work(job, conductor.bridge.root), actor=actor)


def _do_review(conductor, job_id, reviewer="canary-review-1"):
    job = conductor.board.get(job_id)
    conductor.board.finish(job_id, review(job, reviewer, conductor.bridge.root))


def _drive_to_accepted(conductor, run_id, *, task="C01"):
    """Sweep the run all the way through, playing the workers as it goes."""
    for _ in range(12):
        sweep(conductor)
        status = conductor.status(run_id)
        pending = [row for row in status["dispatch"]
                   if row["task"] == task and row["status"] == "submitted"]
        for row in pending:
            if row["phase"] == "work":
                _do_work(conductor, row["job_id"])
            else:
                _do_review(conductor, row["job_id"])
        if task in conductor.status(run_id)["tasks_accepted"]:
            return
    raise AssertionError(f"{task} never reached accepted")


def _two_task_score(tmp_path):
    """The canary score plus a second task, so `launched` and `accepted` differ.

    With one task the two are the same instant; the sweep has to leave a run
    alone once it is launched even though tasks remain, and only a second task
    can show that.
    """
    score = json.loads(CANARY.read_text(encoding="utf-8"))
    second = dict(score["tasks"][0], id="C02", goal="C02", resources=["score-canary-lane-2"])
    score["tasks"].append(second)
    score["launch_requires"] = ["C01"]
    path = tmp_path / "two-task.score.json"
    path.write_text(json.dumps(score), encoding="utf-8")
    return path


# ─── Requirement 5: off unless configured ────────────────────────────────────

class TestConfiguration:
    def test_an_upgraded_gateway_does_not_start_sweeping(self):
        assert get_sweep_seconds({}) == 0
        assert get_sweep_seconds({"MCO_SCORE_SWEEP_SECONDS": ""}) == 0

    def test_zero_or_negative_disables_the_sweep(self):
        assert get_sweep_seconds({"MCO_SCORE_SWEEP_SECONDS": "0"}) == 0
        assert get_sweep_seconds({"MCO_SCORE_SWEEP_SECONDS": "-5"}) == -5

    def test_a_configured_interval_is_honoured(self):
        assert get_sweep_seconds({"MCO_SCORE_SWEEP_SECONDS": "15"}) == 15

    def test_an_unparseable_interval_falls_back_to_off(self):
        assert get_sweep_seconds({"MCO_SCORE_SWEEP_SECONDS": "every minute"}) == 0

    def test_the_gateway_sweeps_the_same_database_the_cli_writes(self):
        from mco import cli
        assert score_sweep.DEFAULT_SCORE_DB == cli.DEFAULT_SCORE_DB
        assert score_sweep.DEFAULT_SCORE_ROOT == cli.DEFAULT_SCORE_ROOT

    def test_configured_paths_override_the_defaults(self, tmp_path):
        config = {"MCO_SCORE_DB": str(tmp_path / "x.db"),
                  "MCO_SCORE_ARTIFACT_ROOT": str(tmp_path / "evidence")}
        assert score_sweep.get_database(config) == tmp_path / "x.db"
        assert score_sweep.get_artifact_root(config) == tmp_path / "evidence"

    def test_a_board_without_a_credential_refuses_to_open(self, monkeypatch, tmp_path):
        monkeypatch.setattr(score_sweep, "get_config", lambda: {"MCO_SCORE_DB": str(tmp_path / "n.db")})
        with pytest.raises(RuntimeError, match="MCO_AGENT_TOKEN"):
            score_sweep.open_conductor()
        assert not (tmp_path / "n.db").exists()

    def test_pause_survives_a_simulated_restart(self, monkeypatch, tmp_path):
        config = {"MCO_SCORE_ARTIFACT_ROOT": str(tmp_path / "evidence")}
        monkeypatch.setattr(score_sweep, "get_config", lambda: config)

        assert score_sweep.set_sweep_paused(True, paused_by="operator-1") is True
        monkeypatch.setattr(score_sweep, "_sweep_paused", False)
        assert score_sweep.is_sweep_paused() is True

        state = json.loads((tmp_path / "evidence" / score_sweep.PAUSE_STATE_FILENAME).read_text())
        assert state["paused"] is True
        assert state["paused_by"] == "operator-1"
        assert "paused_at" in state

    def test_resume_clears_persisted_pause(self, monkeypatch, tmp_path):
        config = {"MCO_SCORE_ARTIFACT_ROOT": str(tmp_path / "evidence")}
        monkeypatch.setattr(score_sweep, "get_config", lambda: config)
        score_sweep.set_sweep_paused(True)

        assert score_sweep.set_sweep_paused(False) is False
        monkeypatch.setattr(score_sweep, "_sweep_paused", False)
        assert score_sweep.is_sweep_paused() is False
        assert not (tmp_path / "evidence" / score_sweep.PAUSE_STATE_FILENAME).exists()

    def test_corrupt_pause_state_retains_in_memory_pause(self, monkeypatch, tmp_path, caplog):
        config = {"MCO_SCORE_ARTIFACT_ROOT": str(tmp_path / "evidence")}
        monkeypatch.setattr(score_sweep, "get_config", lambda: config)
        path = tmp_path / "evidence" / score_sweep.PAUSE_STATE_FILENAME
        path.parent.mkdir()
        path.write_text("not json", encoding="utf-8")
        monkeypatch.setattr(score_sweep, "_sweep_paused", True)

        assert score_sweep.is_sweep_paused() is True
        assert "Unable to read score sweep pause state" in caplog.text
        assert str(path) not in caplog.text
        assert "not json" not in caplog.text


# ─── Requirements 1 and 2: isolation between runs, and settled means settled ──

class TestSweepSelection:
    def test_one_exploding_run_does_not_stop_the_others(self, tmp_path, monkeypatch):
        conductor = _conductor(tmp_path)
        for run_id in ("run-a", "run-b", "run-c"):
            _start(conductor, run_id)
        real_tick = conductor.tick
        monkeypatch.setattr(conductor, "tick", lambda run_id, **kw: (
            (_ for _ in ()).throw(RuntimeError("board on fire")) if run_id == "run-b"
            else real_tick(run_id, **kw)))

        result = sweep(conductor)

        assert result.ticked == ["run-a", "run-c"]
        assert "RuntimeError: board on fire" == result.errors["run-b"]
        assert sorted(result.advanced) == ["run-a", "run-c"]
        # The two healthy runs really did move: each has its work job on the board.
        assert len(conductor.board.jobs) == 2

    def test_a_score_error_blocks_its_own_run_and_the_sweep_carries_on(self, tmp_path):
        conductor = _conductor(tmp_path)
        _start(conductor, "run-a")
        _start(conductor, "run-b")
        assert sorted(sweep(conductor).advanced) == ["run-a", "run-b"]
        # Forge the completion identity on run-a's job: poll raises ScoreError.
        _do_work(conductor, _job_of(conductor, "run-a"), actor="somebody-else")

        result = sweep(conductor)

        assert "identity" in result.blocked["run-a"].lower()
        assert "run-b" in result.ticked and "run-b" not in result.blocked
        assert conductor.status("run-a")["status"] == "blocked"
        assert conductor.status("run-b")["status"] == "running"

    def test_a_blocked_run_is_reported_once_and_never_re_blocked(self, tmp_path):
        conductor = _conductor(tmp_path)
        run = _start(conductor)
        sweep(conductor)
        _do_work(conductor, _job_of(conductor, run), actor="somebody-else")
        sweep(conductor)
        events = _events(conductor, run)
        assert events.count("validation_blocked") == 1
        # tick() already blocked it durably; the sweep must not add its own.
        assert "tick_blocked" not in events

        second = sweep(conductor)

        # A blocked run is not even a candidate: the scan excludes terminal
        # states, so nothing downstream can resurrect it by accident.
        assert score_sweep.candidates(conductor.bridge) == []
        assert second.ticked == [] and second.skipped == {} and second.blocked == {}
        assert _events(conductor, run) == events

    def test_an_accepted_run_is_never_advanced_again(self, tmp_path):
        conductor = _conductor(tmp_path)
        run = _start(conductor)
        _drive_to_accepted(conductor, run)
        assert conductor.status(run)["status"] == "accepted"
        events, jobs = _events(conductor, run), dict(conductor.board.jobs)

        result = sweep(conductor)

        assert score_sweep.candidates(conductor.bridge) == []
        assert result.ticked == [] and result.skipped == {}
        assert _events(conductor, run) == events and conductor.board.jobs.keys() == jobs.keys()

    def test_a_launched_run_is_left_alone_even_with_tasks_outstanding(self, tmp_path):
        conductor = _conductor(tmp_path)
        run = _start(conductor, score_path=_two_task_score(tmp_path))
        _drive_to_accepted(conductor, run, task="C01")
        status = conductor.status(run)
        assert status["launched"] is True and status["status"] == "running"
        assert status["tasks_accepted"] == ["C01"] and status["tasks_total"] == 2
        events = _events(conductor, run)

        result = sweep(conductor)

        assert result.skipped == {run: SKIP_LAUNCHED} and result.ticked == []
        assert _events(conductor, run) == events
        assert "C02" not in [row["task"] for row in conductor.status(run)["dispatch"]]

    def test_another_conductors_run_is_skipped_rather_than_killed(self, tmp_path):
        conductor = _conductor(tmp_path)
        run = _start(conductor, credential="a-different-conductor")
        events = _events(conductor, run)

        result = sweep(conductor)

        assert result.skipped == {run: SKIP_OTHER_CREDENTIAL} and result.ticked == []
        # The decisive part: ticking it would have raised "Conductor credential
        # changed" and durably blocked a perfectly healthy run.
        assert conductor.status(run)["status"] == "running"
        assert _events(conductor, run) == events
        assert conductor.board.create_calls == []

    def test_a_credential_change_between_scan_and_tick_skips_rather_than_kills(
            self, tmp_path, monkeypatch):
        """The credential check above is a snapshot; this is the one that counts.

        A reauthorization that lands between the candidate scan and the tick used
        to be fatal: `dispatch` raised "Conductor credential changed", `tick`
        blocked the run durably, and a perfectly healthy run was dead because the
        gateway happened to sweep it at the wrong microsecond. Skip, never kill.
        """
        conductor = _conductor(tmp_path)
        run = _start(conductor)
        events = _events(conductor, run)
        real_status = conductor.status

        def reauthorize_mid_sweep(run_id):
            status = real_status(run_id)
            conductor.board.identity = "reauthorized-conductor"
            return status

        monkeypatch.setattr(conductor, "status", reauthorize_mid_sweep)

        result = sweep(conductor)

        assert result.skipped == {run: SKIP_OTHER_CREDENTIAL}
        assert result.ticked == [] and result.blocked == {} and result.errors == {}
        assert real_status(run)["status"] == "running"
        assert "tick_blocked" not in _events(conductor, run)
        assert _events(conductor, run) == events
        # Nothing was submitted under the new credential either.
        assert conductor.board.create_calls == []

    def test_a_credential_change_inside_a_tick_skips_rather_than_kills(self, tmp_path):
        """The same race one stage later: dispatch sees the old credential, poll the new.

        `board.create` is the only moment inside a tick where wall-clock time
        passes between the two identity checks, so that is where the
        reauthorization is injected. `poll` used to write `validation_blocked`
        and stop the run from inside the bridge, before `tick` had any say.
        """
        conductor = _conductor(tmp_path)
        run = _start(conductor)
        original = conductor.board.identity
        conductor.board.before_create = lambda _payload: setattr(
            conductor.board, "identity", "reauthorized-conductor")

        result = sweep(conductor)

        conductor.board.identity = original
        assert result.skipped == {run: SKIP_OTHER_CREDENTIAL}
        assert result.blocked == {} and result.errors == {}
        assert conductor.status(run)["status"] == "running"
        events = _events(conductor, run)
        assert "tick_blocked" not in events and "validation_blocked" not in events
        # The dispatch that did get through is whole, not stranded mid-send.
        assert [row["status"] for row in conductor.status(run)["dispatch"]] == ["submitted"]

    def test_a_typed_tick_still_stops_a_run_whose_credential_changed(self, tmp_path):
        """The sweep is fail-safe; a person typing `mco score tick` is not.

        Only the automatic caller opts out of the durable block, so the
        reauthorization a human drives into a run still stops it dead.
        """
        conductor = _conductor(tmp_path)
        run = _start(conductor)
        conductor.board.identity = "reauthorized-conductor"

        outcome = conductor.tick(run)

        assert "credential" in (outcome.error or "").lower()
        assert conductor.status(run)["status"] == "blocked"
        assert "tick_blocked" in _events(conductor, run)

    def test_a_durably_blocked_run_is_still_named_by_a_later_sweep(self, tmp_path):
        """Blocked is terminal, so the next pass never sees the run at all.

        Readiness built out of what one pass happened to notice goes quiet at
        exactly the moment the failure becomes permanent, which is the moment it
        matters most. `failing` is read from the runs table instead.
        """
        conductor = _conductor(tmp_path)
        run = _start(conductor, "run-a")
        _start(conductor, "run-b")
        sweep(conductor)
        _do_work(conductor, _job_of(conductor, "run-a"), actor="somebody-else")
        first = sweep(conductor)
        # The pass that blocks it names the specific reason it was blocked for.
        assert "run-a" in first.blocked
        assert "identity" in first.failing["run-a"].lower()

        second = sweep(conductor)

        assert "run-a" not in second.blocked          # terminal: not even a candidate
        assert second.failing == {"run-a": "blocked"}  # and still named anyway
        assert conductor.status("run-a")["status"] == "blocked"
        assert conductor.status("run-b")["status"] == "running"

    def test_a_transient_sweep_error_is_named_alongside_the_durable_ones(self, tmp_path, monkeypatch):
        """A run that merely exploded is not in the database as failing."""
        conductor = _conductor(tmp_path)
        _start(conductor, "run-a")
        real_tick = conductor.tick
        monkeypatch.setattr(conductor, "tick", lambda run_id, **kw: (
            (_ for _ in ()).throw(RuntimeError("board on fire"))))

        result = sweep(conductor)

        assert "run-a" in result.errors
        assert "run-a" in result.failing

    def test_a_candidate_settled_between_scan_and_tick_is_not_ticked(self, tmp_path, monkeypatch):
        """A terminal session can settle a run while the sweep is mid-pass.

        The candidate scan is a snapshot, so the sweep re-reads each run before
        ticking it. Here the scan is frozen at `running` while the database says
        `accepted` - exactly what a concurrent `mco score tick` produces.
        """
        conductor = _conductor(tmp_path)
        run = _start(conductor)
        stale = [(run, "running", conductor.board.identity)]
        monkeypatch.setattr(score_sweep, "candidates", lambda _bridge: stale)
        with conductor.bridge.tx() as db:
            db.execute("UPDATE runs SET status='accepted' WHERE id=?", (run,))

        result = sweep(conductor)

        assert result.skipped == {run: "accepted"} and result.ticked == []
        assert conductor.board.create_calls == []

    def test_describe_reports_what_the_sweep_did(self):
        result = SweepResult(ticked=["a"], advanced=["a"], errors={"b": "boom"})
        assert "ticked=1" in result.describe() and "errors=1" in result.describe()
        assert not result.quiet and SweepResult(skipped={"a": "accepted"}).quiet


# ─── Requirement 3: two gateways, one run ────────────────────────────────────

class TestConcurrentGateways:
    def _pair(self, tmp_path):
        """Two conductors over one board and one conductor database - a gateway
        sweep and a typed `mco score tick`, or simply two gateways."""
        board = FakeBoard()
        return (_conductor(tmp_path, board), _conductor(tmp_path, board))

    @staticmethod
    def _race(conductors):
        barrier = threading.Barrier(len(conductors))
        results, failures = [], []

        def go(conductor):
            try:
                barrier.wait(timeout=10)
                results.append(sweep(conductor))
            except BaseException as exc:  # noqa: BLE001 - reported, not swallowed
                failures.append(exc)

        threads = [threading.Thread(target=go, args=(c,)) for c in conductors]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
            assert not thread.is_alive()
        assert not failures, failures
        return results

    def test_two_gateways_dispatch_a_task_exactly_once(self, tmp_path):
        first, second = self._pair(tmp_path)
        run = _start(first)

        results = self._race([first, second])

        assert all(not r.errors for r in results)
        dispatch = first.status(run)["dispatch"]
        assert len(dispatch) == 1 and dispatch[0]["status"] == "submitted"
        # The board may well have been asked twice - create_with_id absorbs that -
        # but exactly one job exists and exactly one submission was recorded.
        assert len(first.board.jobs) == 1
        assert _events(first, run).count("submitted") == 1
        assert _events(first, run).count("planned") == 1

    def test_two_gateways_validate_and_accept_a_task_exactly_once(self, tmp_path):
        first, second = self._pair(tmp_path)
        run = _start(first)
        sweep(first)
        _do_work(first, _job_of(first, run))

        self._race([first, second])          # both validate the work evidence
        self._race([first, second])          # both plan + dispatch the review
        review_job = next(j for j, job in first.board.jobs.items() if "review" in job["title"])
        _do_review(first, review_job)
        self._race([first, second])          # both accept the review

        events = _events(first, run)
        assert events.count("validated") == 1
        assert events.count("accepted") == 1
        assert events.count("score_accepted") == 1
        assert first.status(run)["status"] == "accepted"
        assert len(first.board.jobs) == 2


# ─── Requirements 4 and 6: the gateway loop ──────────────────────────────────

def _app():
    return SimpleNamespace(state=SimpleNamespace(
        score_sweep_seconds=1, score_sweep_started=0.0, score_sweep_last_ok=None,
        score_sweep_error=None, score_sweep_failing_runs=[]))


class TestGatewayLoop:
    async def test_shutdown_does_not_return_while_a_tick_is_in_flight(
            self, tmp_path, monkeypatch):
        """The real shutdown boundary, not "it finished eventually".

        `asyncio.to_thread` cannot be cancelled. Cancelling the loop returns at
        once while the worker thread is still inside `board.create`, so the
        lifespan could return with the dispatch row durably 'sending' and no job
        on the board for it - a restart's problem, invented by shutdown. The
        sweep is therefore asked to stop and then joined, and every assertion
        below runs at the exact instant `__aexit__` returns.
        """
        db = LocalStore(tmp_path / "gateway.db")
        monkeypatch.setattr(routes, "get_db_client", lambda: db)
        entered, release = threading.Event(), threading.Event()
        board = FakeBoard()
        board.before_create = lambda _payload: (entered.set(), release.wait(30))
        conductor = _conductor(tmp_path, board)
        run = _start(conductor)
        monkeypatch.setattr(score_sweep, "get_config",
                            lambda: {"MCO_SCORE_SWEEP_SECONDS": "1"})
        monkeypatch.setattr(score_sweep, "open_conductor", lambda *a, **k: conductor)
        app = SimpleNamespace(state=SimpleNamespace())

        context = health.lifespan(app)
        await context.__aenter__()
        for _ in range(500):              # wait until a tick is genuinely in flight
            if entered.is_set():
                break
            await asyncio.sleep(0.01)
        assert entered.is_set(), "the sweep never reached the board"
        # The timer is the only thing that frees the board, and it is the only
        # thing that frees it - nothing after `__aexit__` may help the tick
        # along, or the test would pass on "it finished eventually" again.
        threading.Timer(0.3, release.set).start()

        await context.__aexit__(None, None, None)

        dispatch = conductor.status(run)["dispatch"]
        assert len(dispatch) == 1 and dispatch[0]["status"] == "submitted"
        assert len(board.jobs) == 1
        assert conductor.status(run)["status"] == "running"
        db.close()

    async def test_shutdown_gives_up_on_a_tick_that_will_not_drain(
            self, tmp_path, monkeypatch):
        """Waiting for the sweep must not mean waiting for ever.

        A board that never answers would otherwise hold the gateway open
        indefinitely, so the drain is bounded and then the task is cancelled.
        """
        db = LocalStore(tmp_path / "gateway.db")
        monkeypatch.setattr(routes, "get_db_client", lambda: db)
        entered, release = threading.Event(), threading.Event()
        board = FakeBoard()
        board.before_create = lambda _payload: (entered.set(), release.wait(30))
        conductor = _conductor(tmp_path, board)
        _start(conductor)
        monkeypatch.setattr(score_sweep, "get_config",
                            lambda: {"MCO_SCORE_SWEEP_SECONDS": "1"})
        monkeypatch.setattr(score_sweep, "open_conductor", lambda *a, **k: conductor)
        monkeypatch.setattr(health, "SCORE_SWEEP_DRAIN_SECONDS", 0.1)
        app = SimpleNamespace(state=SimpleNamespace())

        context = health.lifespan(app)
        await context.__aenter__()
        for _ in range(500):
            if entered.is_set():
                break
            await asyncio.sleep(0.01)
        assert entered.is_set(), "the sweep never reached the board"

        started = time.monotonic()
        await context.__aexit__(None, None, None)
        elapsed = time.monotonic() - started

        release.set()
        db.close()
        assert elapsed < 10, f"shutdown waited {elapsed:.1f}s on a board that never answered"

    async def test_readiness_keeps_naming_a_run_that_is_durably_blocked(
            self, tmp_path, monkeypatch):
        """Two passes. The second cannot see the blocked run, and names it anyway.

        A blocked run is terminal, so the pass after the one that blocked it
        excludes it from the candidate scan. Readiness rebuilt from that pass
        alone went quiet at the exact moment the failure became permanent.
        """
        conductor = _conductor(tmp_path)
        _start(conductor, "run-a")
        _start(conductor, "run-b")
        sweep(conductor)
        _do_work(conductor, _job_of(conductor, "run-a"), actor="somebody-else")
        monkeypatch.setattr(score_sweep, "open_conductor", lambda *a, **k: conductor)
        passes: list = []
        real_sweep = score_sweep.sweep
        monkeypatch.setattr(score_sweep, "sweep", lambda c: _record(real_sweep, c, passes))
        app = _app()

        task = asyncio.create_task(health.score_sweep_loop(app, 0.01))
        for _ in range(500):
            if len(passes) >= 3:
                break
            await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert len(passes) >= 3, "the loop never got past the pass that blocks the run"
        assert conductor.status("run-a")["status"] == "blocked"
        assert "run-a" not in passes[-1].blocked     # terminal: never a candidate again
        assert app.state.score_sweep_failing_runs == ["run-a"]
        assert app.state.score_sweep_error is None   # one bad run, not a bad sweep

    async def test_a_broken_sweep_is_recorded_not_swallowed(self, monkeypatch):
        monkeypatch.setattr(score_sweep, "open_conductor",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no credential")))
        app = _app()

        task = asyncio.create_task(health.score_sweep_loop(app, 0.01))
        for _ in range(500):
            if app.state.score_sweep_error:
                break
            await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert app.state.score_sweep_error == "RuntimeError"
        assert app.state.score_sweep_last_ok is None

    async def test_a_sweep_stopped_before_it_starts_does_absolutely_nothing(
            self, tmp_path, monkeypatch):
        """A stop set before the loop is first scheduled must cost nothing.

        The loop used to open the conductor and run a full tick before it ever
        looked at the flag, so a shutdown racing startup still created the
        database, the artifact root, and real dispatched jobs on the board -
        side effects invented by a gateway that was already stopping.
        """
        opens, sweeps = [], []
        conductor = _conductor(tmp_path)
        _start(conductor, "run-a")

        def _open(*_a, **_k):
            opens.append(1)
            return conductor

        async def _sweep(*_a, **_k):
            sweeps.append(1)
            raise AssertionError("a pre-stopped sweep must not tick")

        monkeypatch.setattr(score_sweep, "open_conductor", _open)
        monkeypatch.setattr(health, "score_sweep_once", _sweep)
        app = _app()
        stop = asyncio.Event()
        stop.set()

        await asyncio.wait_for(health.score_sweep_loop(app, 0.01, stop), timeout=5)

        assert opens == [], "opened a conductor while already stopped"
        assert sweeps == [], "ticked while already stopped"
        assert conductor.status("run-a")["dispatch"] == [], "dispatched while already stopped"
        assert app.state.score_sweep_last_ok is None
        assert app.state.score_sweep_error is None

    async def test_a_failing_run_is_named_without_breaking_the_sweep(
            self, tmp_path, monkeypatch):
        conductor = _conductor(tmp_path)
        _start(conductor, "run-a")
        _start(conductor, "run-b")
        real_tick = conductor.tick
        monkeypatch.setattr(conductor, "tick", lambda run_id, **kw: (
            (_ for _ in ()).throw(RuntimeError("boom")) if run_id == "run-b" else real_tick(run_id, **kw)))
        monkeypatch.setattr(score_sweep, "open_conductor", lambda *a, **k: conductor)
        app = _app()

        task = asyncio.create_task(health.score_sweep_loop(app, 0.01))
        for _ in range(500):
            if app.state.score_sweep_last_ok is not None:
                break
            await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert app.state.score_sweep_failing_runs == ["run-b"]
        assert app.state.score_sweep_error is None       # the sweep itself is fine
        assert conductor.status("run-a")["dispatch"]      # run-a advanced regardless


def _record(func, arg, passes):
    """Run a sweep and keep its result, so a test can count completed passes."""
    result = func(arg)
    passes.append(result)
    return result


# ─── Requirement 6 seen from outside: /readyz ────────────────────────────────

@pytest.fixture
def gateway(tmp_path, monkeypatch):
    from mco.cli import create_app
    db = LocalStore(tmp_path / "gateway.db")
    monkeypatch.setattr(routes, "get_db_client", lambda: db)
    app = create_app()
    # No lifespan: these tests drive app.state themselves, so /readyz reports on
    # exactly the sweep state under test and nothing else.
    yield TestClient(app), app
    db.close()


class TestReadiness:
    def test_an_unconfigured_gateway_reports_the_sweep_as_not_configured(self, gateway):
        client, _app = gateway
        body = client.get("/readyz")
        assert body.status_code == 200
        assert body.json()["checks"]["score_sweep"] == {"ok": True, "configured": False}

    def test_a_broken_sweep_makes_the_gateway_unready(self, gateway):
        client, app = gateway
        app.state.score_sweep_seconds = 15
        app.state.score_sweep_error = "RuntimeError"
        body = client.get("/readyz")
        assert body.status_code == 503
        assert body.json()["checks"]["score_sweep"]["ok"] is False
        assert body.json()["checks"]["score_sweep"]["error"] == "RuntimeError"

    def test_a_sweep_that_never_completes_a_pass_goes_stale(self, gateway):
        client, app = gateway
        app.state.score_sweep_seconds = 1
        app.state.score_sweep_started = -1000.0      # long ago, and never succeeded
        assert client.get("/readyz").status_code == 503

    def test_failing_runs_are_visible_without_failing_the_gateway(self, gateway):
        import time as _time
        client, app = gateway
        app.state.score_sweep_seconds = 15
        app.state.score_sweep_last_ok = _time.monotonic()
        app.state.score_sweep_failing_runs = ["run-b"]
        body = client.get("/readyz")
        assert body.status_code == 200
        check = body.json()["checks"]["score_sweep"]
        assert check["ok"] is True and check["failing_runs"] == ["run-b"]


class TestLifespanWiring:
    def test_a_disabled_gateway_opens_no_score_database(self, tmp_path, monkeypatch):
        from mco.cli import create_app
        db = LocalStore(tmp_path / "gateway.db")
        monkeypatch.setattr(routes, "get_db_client", lambda: db)
        monkeypatch.setattr(score_sweep, "get_config", lambda: {
            "MCO_SCORE_DB": str(tmp_path / "score.db"),
            "MCO_SCORE_ARTIFACT_ROOT": str(tmp_path / "evidence")})
        monkeypatch.setattr(score_sweep, "open_conductor",
                            lambda *a, **k: pytest.fail("a disabled sweep must not open a conductor"))
        app = create_app()
        with TestClient(app):
            assert app.state.score_sweep_seconds == 0
        assert not (tmp_path / "score.db").exists()
        assert not (tmp_path / "evidence").exists()
        db.close()

    def test_a_configured_gateway_starts_sweeping(self, tmp_path, monkeypatch):
        from mco.cli import create_app
        db = LocalStore(tmp_path / "gateway.db")
        monkeypatch.setattr(routes, "get_db_client", lambda: db)
        conductor = _conductor(tmp_path)
        run = _start(conductor)
        monkeypatch.setattr(score_sweep, "get_config", lambda: {"MCO_SCORE_SWEEP_SECONDS": "1"})
        monkeypatch.setattr(score_sweep, "open_conductor", lambda *a, **k: conductor)
        app = create_app()
        with TestClient(app) as client:
            assert app.state.score_sweep_seconds == 1
            for _ in range(500):
                if conductor.status(run)["dispatch"]:
                    break
                client.get("/healthz")
        # Nobody typed a tick: the gateway dispatched the run's first job itself.
        assert conductor.status(run)["dispatch"][0]["status"] == "submitted"
        db.close()
