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
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from mco.localstore import LocalStore
from mco.orchestrator import health, routes, score_sweep
from mco.orchestrator.score_canary_worker import review, work
from mco.orchestrator.score_conductor import Conductor, open_bridge, start_run
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


# ─── Requirements 1 and 2: isolation between runs, and settled means settled ──

class TestSweepSelection:
    def test_one_exploding_run_does_not_stop_the_others(self, tmp_path, monkeypatch):
        conductor = _conductor(tmp_path)
        for run_id in ("run-a", "run-b", "run-c"):
            _start(conductor, run_id)
        real_tick = conductor.tick
        monkeypatch.setattr(conductor, "tick", lambda run_id: (
            (_ for _ in ()).throw(RuntimeError("board on fire")) if run_id == "run-b"
            else real_tick(run_id)))

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
    async def test_shutdown_cancels_the_sweep_without_half_ticking_a_run(
            self, tmp_path, monkeypatch):
        entered, release = threading.Event(), threading.Event()
        finished = threading.Event()
        board = FakeBoard()
        board.before_create = lambda _payload: (entered.set(), release.wait(30))
        conductor = _conductor(tmp_path, board)
        run = _start(conductor)
        monkeypatch.setattr(score_sweep, "open_conductor", lambda *a, **k: conductor)
        real_sweep = score_sweep.sweep
        monkeypatch.setattr(score_sweep, "sweep", lambda c: _finally(real_sweep, c, finished))
        app = _app()

        task = asyncio.create_task(health.score_sweep_loop(app, 0.01))
        for _ in range(500):                      # wait until a tick is genuinely in flight
            if entered.is_set():
                break
            await asyncio.sleep(0.01)
        assert entered.is_set(), "the sweep never reached the board"

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert app.state.score_sweep_last_ok is None   # cancelled, not quietly completed

        release.set()
        assert finished.wait(30), "the in-flight tick never finished"
        # Durable state is whole: the dispatch that was in flight committed, and
        # nothing is stranded in the sending state a restart would have to guess at.
        dispatch = conductor.status(run)["dispatch"]
        assert len(dispatch) == 1 and dispatch[0]["status"] == "submitted"
        assert conductor.status(run)["status"] == "running"

        # And the run still converges afterwards, with no duplicate work.
        board.before_create = None
        _drive_to_accepted(conductor, run)
        assert conductor.status(run)["status"] == "accepted"
        assert len(board.jobs) == 2

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

    async def test_a_failing_run_is_named_without_breaking_the_sweep(
            self, tmp_path, monkeypatch):
        conductor = _conductor(tmp_path)
        _start(conductor, "run-a")
        _start(conductor, "run-b")
        real_tick = conductor.tick
        monkeypatch.setattr(conductor, "tick", lambda run_id: (
            (_ for _ in ()).throw(RuntimeError("boom")) if run_id == "run-b" else real_tick(run_id)))
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


def _finally(func, arg, event):
    try:
        return func(arg)
    finally:
        event.set()


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
