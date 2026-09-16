"""The conductor sweep: score runs advance without a person typing a tick.

C02 gave the conductor a working `tick()` - plan, dispatch, poll - and
`run_until_settled()`. Nothing called either one. The canary run that went end
to end got there because a terminal drove it, which means a run only progresses
while someone is watching it. This module is the missing caller: one sweep
advances every run the gateway is entitled to advance by exactly one tick, and
`health.py` repeats it on a timer the same way it repeats the delivery watchdog.

Three rules shape it, and each is the reason for a specific line below:

  * **Only our runs.** A run records the credential hash of the conductor that
    started it, and `ScoreBridge.identity` refuses to advance a run under any
    other credential - by raising, which a *typed* `tick()` turns into a durable
    block. So a sweep that ticked indiscriminately would not merely fail on a
    CLI-owned run, it would kill it. Runs bound to another credential are
    skipped, never touched - and because the credential can change between the
    scan below and either identity check inside a tick, the sweep also ticks
    with `block_on_identity_change=False`, so losing that race is a skip too.
  * **Only unsettled runs.** Accepted, blocked, failed and completed runs are
    finished, and a launched run has done what it was asked to do. The sweep
    filters them out before ticking rather than relying on the bridge's internal
    `status != 'running'` early-returns.
  * **One bad run is one bad run.** Every run is ticked inside its own
    try/except so a poisoned run cannot stop the ones behind it in the sweep.
    `Exception` is deliberate: `asyncio.CancelledError` is a `BaseException` and
    must travel straight out, so gateway shutdown stops the sweep at once.

Off unless configured. `MCO_SCORE_SWEEP_SECONDS` defaults to 0, so upgrading a
gateway never silently starts driving score runs.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from mco.config import get_config
from mco.orchestrator.score_conductor import TERMINAL_RUN_STATES
from mco.orchestrator.scores import ScoreIdentityError

logger = logging.getLogger("mco.orchestrator.score_sweep")

DEFAULT_SWEEP_SECONDS = 0
# These mirror `mco score --db` / `--artifact-root` (cli.DEFAULT_SCORE_DB and
# cli.DEFAULT_SCORE_ROOT). A gateway that swept a different database than the
# terminal writes to would report an empty board while runs sat untouched, so
# tests/test_score_sweep.py asserts the two stay equal.
DEFAULT_SCORE_DB = Path.home() / ".mco" / "score-runs.db"
DEFAULT_SCORE_ROOT = Path.home() / ".mco" / "score-artifacts"

# Why a run was passed over. Anything here means "left exactly as it was".
SKIP_OTHER_CREDENTIAL = "other_credential"
SKIP_LAUNCHED = "launched"

# Terminal states that mean a run is stuck rather than finished. Readiness has
# to keep naming these; see `failing_runs`.
FAILING_RUN_STATES = ("blocked", "failed")


def get_sweep_seconds(config: Optional[dict] = None) -> int:
    """Seconds between conductor sweeps (``MCO_SCORE_SWEEP_SECONDS``).

    0 or negative disables the sweep, and that is the default: an existing
    gateway must not start advancing score runs merely because it was upgraded.
    """
    config = config if config is not None else get_config()
    try:
        return int(config.get("MCO_SCORE_SWEEP_SECONDS") or DEFAULT_SWEEP_SECONDS)
    except (TypeError, ValueError):
        return DEFAULT_SWEEP_SECONDS


def get_database(config: Optional[dict] = None) -> Path:
    """Conductor state (``MCO_SCORE_DB``); the same SQLite file `mco score` uses."""
    config = config if config is not None else get_config()
    return Path(config.get("MCO_SCORE_DB") or DEFAULT_SCORE_DB)


def get_artifact_root(config: Optional[dict] = None) -> Path:
    """Evidence root (``MCO_SCORE_ARTIFACT_ROOT``); a run is bound to the root it started under."""
    config = config if config is not None else get_config()
    return Path(config.get("MCO_SCORE_ARTIFACT_ROOT") or DEFAULT_SCORE_ROOT)


@dataclass
class SweepResult:
    """What one sweep did. Empty everywhere means nothing needed the gateway."""

    ticked: list[str] = field(default_factory=list)
    advanced: list[str] = field(default_factory=list)
    blocked: dict[str, str] = field(default_factory=dict)
    skipped: dict[str, str] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)
    # Every run that is stuck right now, not only the ones this pass touched.
    # `blocked` is what this pass did; `failing` is what is still true.
    failing: dict[str, str] = field(default_factory=dict)

    @property
    def quiet(self) -> bool:
        return not (self.advanced or self.blocked or self.errors)

    def describe(self) -> str:
        parts = [f"ticked={len(self.ticked)}"]
        for name, value in (("advanced", self.advanced), ("blocked", self.blocked),
                            ("skipped", self.skipped), ("errors", self.errors)):
            if value:
                parts.append(f"{name}={len(value)}")
        return " ".join(parts)


def candidates(bridge) -> list[tuple[str, str, str]]:
    """(run_id, status, credential_hash) for every run that is not already terminal.

    Ordered by id so two gateways sweep in the same sequence and a failing run
    cannot change which runs the rest of the sweep reaches.
    """
    terminal = sorted(TERMINAL_RUN_STATES)
    placeholders = ",".join("?" for _ in terminal)
    with bridge.tx() as db:
        return [(row["id"], row["status"], row["credential_hash"]) for row in db.execute(
            f"SELECT id,status,credential_hash FROM runs WHERE status NOT IN ({placeholders}) ORDER BY id",
            tuple(terminal))]


def failing_runs(bridge) -> dict[str, str]:
    """{run_id: status} for every run that is durably stuck, read fresh each pass.

    Blocked and failed are terminal, so the pass *after* the one that blocks a
    run never sees that run again - `candidates` filters it out. Readiness built
    from what a single pass happened to notice therefore went quiet at exactly
    the moment the failure became permanent, which is the moment somebody needed
    to be told. Reading the runs table instead means a stuck run keeps being
    named until something actually moves it, and that a gateway restart does not
    amnesty the failures it inherited.
    """
    placeholders = ",".join("?" for _ in FAILING_RUN_STATES)
    with bridge.tx() as db:
        return {row["id"]: row["status"] for row in db.execute(
            f"SELECT id,status FROM runs WHERE status IN ({placeholders}) ORDER BY id",
            FAILING_RUN_STATES)}


def sweep(conductor) -> SweepResult:
    """Advance every run this conductor owns and is allowed to move, by one tick."""
    result = SweepResult()
    for run_id, _status, credential in candidates(conductor.bridge):
        try:
            if credential != conductor.board.identity:
                # Another conductor's run. Ticking it would raise "Conductor
                # credential changed" and durably block work that is perfectly
                # healthy, so the sweep does not go near it.
                result.skipped[run_id] = SKIP_OTHER_CREDENTIAL
                continue
            # Re-read rather than trusting the row above: a terminal session or a
            # second gateway may have settled this run since the candidate scan.
            status = conductor.status(run_id)
            if status["status"] in TERMINAL_RUN_STATES:
                result.skipped[run_id] = status["status"]
                continue
            if status["launched"]:
                result.skipped[run_id] = SKIP_LAUNCHED
                continue
            # `block_on_identity_change=False` is the whole fail-safe: the
            # comparison above is a snapshot, and the credential can still move
            # between it and the identity checks inside dispatch and poll. A
            # tick that blocked on those would turn losing that race into a dead
            # run, so an identity change raises out here and becomes a skip.
            outcome = conductor.tick(run_id, block_on_identity_change=False)
            result.ticked.append(run_id)
            if outcome.error:
                # tick() already blocked the run durably and wrote the
                # tick_blocked/validation_blocked event. Report it once; do not
                # block or re-event it here.
                result.blocked[run_id] = outcome.error
                logger.warning("Conductor sweep: run %s blocked - %s", run_id, outcome.error)
            elif not outcome.idle:
                result.advanced.append(run_id)
                logger.info("Conductor sweep advanced %s", outcome.describe())
        except ScoreIdentityError:
            # The credential moved under us mid-tick. Whatever the tick had
            # already committed is a whole, idempotent step - the run is still
            # running, still ownable, still exactly as advanceable as it was -
            # and the thing that matters is what did *not* happen: it was not
            # blocked. Reported as the same skip as a run that belonged to
            # somebody else before the sweep even started.
            result.skipped[run_id] = SKIP_OTHER_CREDENTIAL
        except Exception as exc:  # noqa: BLE001 - one run must not end the sweep
            result.errors[run_id] = f"{type(exc).__name__}: {exc}"
            logger.exception("Conductor sweep failed on run %s", run_id)
    # Durable truth first, then what this pass saw for itself: a block the
    # database somehow did not take, and the transient explosions that no runs
    # table records because they never reached one.
    result.failing = {**failing_runs(conductor.bridge), **result.blocked, **result.errors}
    return result


def open_conductor(config: Optional[dict] = None):
    """The gateway's own conductor: configured state, board authenticated as us.

    Deliberately not called when the sweep is disabled - opening the bridge
    creates the SQLite file and the artifact root, and a disabled feature must
    leave no trace on disk.
    """
    from mco.orchestrator.client import GatewayClient
    from mco.orchestrator.score_conductor import Conductor, board_for, open_bridge

    config = config if config is not None else get_config()
    token = config.get("MCO_AGENT_TOKEN") or config.get("MCO_LOCAL_TOKEN") or None
    if not token:
        raise RuntimeError(
            "The conductor sweep needs MCO_AGENT_TOKEN or MCO_LOCAL_TOKEN: a score run is "
            "bound to the credential that started it, and an unauthenticated board cannot "
            "advance one.")
    client = GatewayClient(
        base_url=config.get("MCO_GATEWAY_URL") or None,
        token=token,
        role=config.get("AGENT_ROLE") or None,
        instance_id=config.get("AGENT_INSTANCE_ID") or None,
    )
    bridge = open_bridge(get_database(config), get_artifact_root(config))
    return Conductor(bridge, board_for(client))
