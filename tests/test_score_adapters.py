import threading
from types import SimpleNamespace
from datetime import datetime, timezone

import pytest

from mco.localstore import LocalStore
from mco.orchestrator.score_adapters import (
    AdapterError,
    AdapterResult,
    AdapterSpec,
    AdapterWrapper,
    EffectStatus,
    Operation,
    RecoveryRequired,
    S05_DRY_RUN_ALLOWLIST,
    ScoreAdapterExecutor,
    require_quiet_notification,
)
from mco.orchestrator.score_authority import GrantService


NOW = datetime(2026, 9, 16, tzinfo=timezone.utc)
KEY = b"s05-test-key-material-is-long-enough-0001"
DIGEST = "a" * 64


@pytest.fixture
def store(tmp_path):
    value = LocalStore(tmp_path / "s05.db")
    yield value
    value.close()


@pytest.fixture
def grants(store):
    service = GrantService(store, verification_key=KEY)
    service.issue({
        "org_id": "default",
        "run_id": "run-5",
        "digest": DIGEST,
        "actions": ["repository:prepare", "build:verify", "deploy:preview", "notify:owner"],
        "resources": ["repo", "build", "via", "owner"],
        "env": "test",
        "not_before": "2026-09-15T00:00:00Z",
        "expires_at": "2026-10-01T00:00:00Z",
        "budget_cents": 0,
        "human_principal": "joseph",
    })
    return service


def operation(adapter="deploy-preview", action="deploy:preview", resource="via",
              desired=None, dry_run=True):
    return Operation(
        org_id="default", run_id="run-5", digest=DIGEST, task_id="G05",
        attempt=1, adapter=adapter, action=action, resource=resource,
        environment="test", owner_principal="joseph", cost_cents=0,
        desired_state=desired or {"version": "candidate", "mode": "dry-run"},
        dry_run=dry_run,
    )


def executor(store, grants, *, state, invoke, compensate=None,
             name="deploy-preview", kind="deploy", action="deploy:preview",
             compensation=True):
    spec = AdapterSpec(
        name=name, kind=kind, actions=frozenset({action}),
        replay_requires_recheck=kind in {"deploy", "notify"},
        compensation_supported=compensation,
    )
    wrapper = AdapterWrapper(
        inspect=lambda _op: dict(state), invoke=invoke, compensate=compensate,
    )
    return ScoreAdapterExecutor(
        store, grant_service=grants, allowlist={name: spec},
        wrappers={name: wrapper}, now=lambda: NOW,
    )


def receipts(store):
    return [row["payload"] for row in store.table("score_events").select("*").order("seq").execute().data]


class _QueryOrderProbe:
    """Simulate PostgREST returning a legal non-insertion order by default."""

    def __init__(self, query, *, scramble):
        self.query = query
        self.scramble = scramble
        self.ordered = False

    def __getattr__(self, name):
        attribute = getattr(self.query, name)
        if not callable(attribute):
            return attribute

        def chained(*args, **kwargs):
            result = attribute(*args, **kwargs)
            if result is self.query:
                return self
            return result

        return chained

    def order(self, *args, **kwargs):
        self.ordered = True
        self.query.order(*args, **kwargs)
        return self

    def execute(self):
        result = self.query.execute()
        if self.scramble and not self.ordered and len(result.data or []) == 3:
            rows = result.data
            return SimpleNamespace(data=[rows[0], rows[2], rows[1]])
        return result


class _UnorderedEventsDB:
    def __init__(self, store):
        self.store = store

    def table(self, name):
        return _QueryOrderProbe(
            self.store.table(name), scramble=name == "score_events",
        )


def test_s05_allowlist_covers_each_wrapper_class_and_is_dry_run_only():
    assert {spec.kind for spec in S05_DRY_RUN_ALLOWLIST.values()} == {
        "repository", "build", "deploy", "notify",
    }
    assert all(spec.dry_run_only for spec in S05_DRY_RUN_ALLOWLIST.values())
    assert all(
        spec.replay_requires_recheck
        for spec in S05_DRY_RUN_ALLOWLIST.values()
        if spec.kind in {"deploy", "notify"}
    )


def test_success_has_before_after_receipts_and_replay_is_idempotent(store, grants):
    state = {"version": "old", "mode": "dry-run"}
    calls = []

    def invoke(_op):
        calls.append("invoke")
        state["version"] = "candidate"
        return AdapterResult(EffectStatus.SUCCEEDED, dict(state), "preview complete")

    run = executor(store, grants, state=state, invoke=invoke,
                   compensate=lambda *_: None)
    first = run.execute(operation())
    replay = run.execute(operation())

    assert first["status"] == "succeeded"
    assert replay["replayed"] is True
    assert calls == ["invoke"]
    saved = receipts(store)
    assert [r["phase"] for r in saved] == ["before", "after"]
    assert saved[0]["operation_id"] == saved[1]["operation_id"]
    assert saved[0]["grant_identity"] == saved[1]["grant_identity"]
    assert saved[0]["dry_run"] is True


def test_deploy_rechecks_desired_state_before_first_or_replayed_invocation(store, grants):
    state = {"version": "candidate", "mode": "dry-run"}
    calls = []
    run = executor(
        store, grants, state=state,
        invoke=lambda _op: calls.append("invoke"), compensate=lambda *_: None,
    )

    result = run.execute(operation())

    assert result["phase"] == "reconciled"
    assert result["status"] == "already_desired"
    assert calls == []


def test_uncertain_effect_blocks_replay_until_compensated(store, grants):
    state = {"version": "old", "mode": "dry-run", "temp": False}
    calls = []

    def invoke(_op):
        calls.append("invoke")
        if len(calls) == 1:
            state["temp"] = True
            return AdapterResult(
                EffectStatus.UNCERTAIN, dict(state), "ack lost",
                {"temporary_release": "preview-17"},
            )
        state.update(version="candidate", temp=False)
        return AdapterResult(EffectStatus.SUCCEEDED, dict(state))

    def compensate(_op, effect):
        assert effect == {"temporary_release": "preview-17"}
        state["temp"] = False
        return AdapterResult(EffectStatus.SUCCEEDED, dict(state), "preview removed")

    run = executor(store, grants, state=state, invoke=invoke, compensate=compensate)
    assert run.execute(operation())["status"] == "uncertain"
    with pytest.raises(RecoveryRequired, match="inspection_or_compensation"):
        run.execute(operation())
    assert calls == ["invoke"]

    compensated = run.compensate(operation())
    assert compensated["status"] == "compensated"
    assert run.execute(operation())["status"] == "succeeded"
    assert calls == ["invoke", "invoke"]
    recovery = store.table("score_recovery").select("*").execute().data[0]
    assert recovery["decision"] == "compensated"


def test_uncertain_deploy_is_reconciled_if_recheck_finds_desired_state(store, grants):
    state = {"version": "old", "mode": "dry-run"}
    calls = []

    def invoke(_op):
        calls.append("invoke")
        state["version"] = "candidate"
        return AdapterResult(EffectStatus.UNCERTAIN, dict(state), "timeout after submit")

    run = executor(store, grants, state=state, invoke=invoke,
                   compensate=lambda *_: None)
    assert run.execute(operation())["status"] == "uncertain"
    replay = run.execute(operation())

    assert replay["status"] == "already_desired"
    assert calls == ["invoke"]
    assert store.table("score_recovery").select("*").execute().data[0]["decision"] == "inspected"


def test_retry_safe_failure_can_reinvoke_without_recovery(store, grants):
    state = {"build": "absent"}
    calls = []

    def invoke(_op):
        calls.append("invoke")
        if len(calls) == 1:
            return AdapterResult(EffectStatus.RETRY_SAFE_FAILURE, dict(state), "rejected before start")
        state["build"] = "verified"
        return AdapterResult(EffectStatus.SUCCEEDED, dict(state))

    run = executor(
        store, grants, state=state, invoke=invoke, compensate=None,
        name="build-verify", kind="build", action="build:verify",
        compensation=False,
    )
    op = operation(
        adapter="build-verify", action="build:verify", resource="build",
        desired={"build": "verified"},
    )
    assert run.execute(op)["status"] == "retry_safe_failure"
    assert run.execute(op)["status"] == "succeeded"
    assert calls == ["invoke", "invoke"]
    assert store.table("score_recovery").select("*").execute().data == []


def test_wrapper_exception_is_uncertain_not_retry_safe(store, grants):
    state = {"version": "old", "mode": "dry-run"}
    run = executor(
        store, grants, state=state,
        invoke=lambda _op: (_ for _ in ()).throw(TimeoutError("lost ack")),
        compensate=lambda *_: AdapterResult(EffectStatus.SUCCEEDED, state),
    )
    result = run.execute(operation())
    assert result["status"] == "uncertain"
    effect = store.table("score_recovery").select("*").execute().data[0]["uncertain_effect"]
    assert effect["partial_effect"] == {"exception_type": "TimeoutError"}


def test_process_death_after_partial_effect_blocks_blind_replay(store, grants):
    state = {"version": "old", "mode": "dry-run", "submitted": False}
    calls = []

    def invoke(_op):
        calls.append("invoke")
        state["submitted"] = True
        raise SystemExit("simulated process death")

    run = executor(store, grants, state=state, invoke=invoke,
                   compensate=lambda *_: None)
    with pytest.raises(SystemExit, match="process death"):
        run.execute(operation())

    restarted = executor(store, grants, state=state, invoke=invoke,
                         compensate=lambda *_: None)
    with pytest.raises(RecoveryRequired, match="inspection_or_compensation"):
        restarted.execute(operation())

    assert calls == ["invoke"]
    assert [r["phase"] for r in receipts(store)] == ["before"]
    recovery = store.table("score_recovery").select("*").execute().data[0]
    assert recovery["decision"] == "pending"
    assert recovery["uncertain_effect"]["partial_effect"] == {"in_flight": True}


def test_legacy_unmatched_before_receipt_is_promoted_to_pending_recovery(store, grants):
    state = {"version": "old", "mode": "dry-run", "submitted": True}
    calls = []
    op = operation()
    store.table("score_events").insert({
        "org_id": op.org_id,
        "run_id": op.run_id,
        "kind": "adapter_before",
        "task_id": op.task_id,
        "digest": op.digest,
        "actor": "score-conductor",
        "payload": {
            "protocol": "score-adapter-receipt/v1",
            "operation_id": op.id,
            "phase": "before",
            "status": "observed",
            "observed_state": {"version": "old", "mode": "dry-run"},
        },
    }).execute()
    run = executor(
        store, grants, state=state,
        invoke=lambda _op: calls.append("invoke"), compensate=lambda *_: None,
    )

    with pytest.raises(RecoveryRequired, match="inspection_or_compensation"):
        run.execute(op)

    assert calls == []
    recovery = store.table("score_recovery").select("*").execute().data[0]
    assert recovery["decision"] == "pending"
    assert recovery["uncertain_effect"]["detail"] == "unmatched adapter_before receipt"


def test_unmatched_before_detection_orders_postgrest_events_by_sequence(store, grants):
    state = {"version": "old", "mode": "dry-run", "submitted": True}
    calls = []
    op = operation()
    base = {
        "org_id": op.org_id, "run_id": op.run_id, "task_id": op.task_id,
        "digest": op.digest, "actor": "score-conductor",
    }
    payloads = [
        {"operation_id": op.id, "phase": "before", "status": "observed"},
        {"operation_id": op.id, "phase": "after", "status": "retry_safe_failure"},
        {"operation_id": op.id, "phase": "before", "status": "observed"},
    ]
    for payload in payloads:
        store.table("score_events").insert({
            **base, "kind": f"adapter_{payload['phase']}", "payload": payload,
        }).execute()
    run = executor(
        _UnorderedEventsDB(store), grants, state=state,
        invoke=lambda _op: calls.append("invoke"), compensate=lambda *_: None,
    )

    with pytest.raises(RecoveryRequired, match="inspection_or_compensation"):
        run.execute(op)

    assert calls == []


def test_pending_effect_blocks_a_new_attempt_from_invoking(store, grants):
    state = {"version": "old", "mode": "dry-run", "submitted": False}
    calls = []

    def invoke(op):
        calls.append(op.attempt)
        state["submitted"] = True
        raise SystemExit("simulated process death")

    run = executor(store, grants, state=state, invoke=invoke,
                   compensate=lambda *_: None)
    first = operation()
    second = Operation(**{**first.__dict__, "attempt": 2})
    assert first.id != second.id
    with pytest.raises(SystemExit, match="process death"):
        run.execute(first)

    with pytest.raises(RecoveryRequired, match="inspection_or_compensation"):
        run.execute(second)

    assert calls == [1]


def test_completed_replay_releases_orphaned_pending_claim(store, grants):
    state = {"version": "old", "mode": "dry-run"}

    def invoke(_op):
        state["version"] = "candidate"
        return AdapterResult(EffectStatus.SUCCEEDED, dict(state))

    run = executor(store, grants, state=state, invoke=invoke,
                   compensate=lambda *_: None)
    op = operation()
    assert run.execute(op)["status"] == "succeeded"
    store.table("score_recovery").insert({
        "approval_or_attempt_id": op.id,
        "org_id": op.org_id,
        "uncertain_effect": {"operation": dict(op.__dict__)},
        "decision": "pending",
    }).execute()

    assert run.execute(op)["replayed"] is True
    assert store.table("score_recovery").select("*").execute().data == []


def test_uncertain_retry_preserves_prior_recovery_history(store, grants):
    state = {"version": "old", "mode": "dry-run", "temp": False}
    calls = []

    def invoke(_op):
        calls.append("invoke")
        state["temp"] = True
        return AdapterResult(
            EffectStatus.UNCERTAIN, dict(state), "ack lost",
            {"temporary_release": f"preview-{len(calls)}"},
        )

    def compensate(_op, _effect):
        state["temp"] = False
        return AdapterResult(EffectStatus.SUCCEEDED, dict(state))

    run = executor(store, grants, state=state, invoke=invoke, compensate=compensate)
    op = operation()
    assert run.execute(op)["status"] == "uncertain"
    assert run.compensate(op)["status"] == "compensated"
    assert run.execute(op)["status"] == "uncertain"

    effect = store.table("score_recovery").select("*").execute().data[0]["uncertain_effect"]
    assert effect["_prior_recovery"]["decision"] == "compensated"


def test_process_death_reconciles_deploy_when_inspection_finds_desired(store, grants):
    state = {"version": "old", "mode": "dry-run"}
    calls = []

    def invoke(_op):
        calls.append("invoke")
        state["version"] = "candidate"
        raise SystemExit("simulated process death")

    run = executor(store, grants, state=state, invoke=invoke,
                   compensate=lambda *_: None)
    with pytest.raises(SystemExit):
        run.execute(operation())

    replay = run.execute(operation())

    assert replay["status"] == "already_desired"
    assert calls == ["invoke"]
    assert [r["phase"] for r in receipts(store)] == ["before", "reconciled"]


def test_concurrent_duplicate_cannot_invoke_while_claim_is_pending(store, grants):
    state = {"version": "old", "mode": "dry-run"}
    entered = threading.Event()
    release = threading.Event()
    calls = []

    def invoke(_op):
        calls.append("invoke")
        entered.set()
        assert release.wait(timeout=5)
        state["version"] = "candidate"
        return AdapterResult(EffectStatus.SUCCEEDED, dict(state))

    run = executor(store, grants, state=state, invoke=invoke,
                   compensate=lambda *_: None)
    outcomes = []

    def first_attempt():
        outcomes.append(run.execute(operation()))

    worker = threading.Thread(target=first_attempt)
    worker.start()
    assert entered.wait(timeout=5)
    try:
        with pytest.raises(RecoveryRequired, match="inspection_or_compensation"):
            run.execute(operation())
    finally:
        release.set()
        worker.join(timeout=5)

    assert not worker.is_alive()
    assert calls == ["invoke"]
    assert outcomes[0]["status"] == "succeeded"


def test_allowlist_and_s05_dry_run_boundary_fail_closed(store, grants):
    state = {"version": "old", "mode": "dry-run"}
    run = executor(
        store, grants, state=state,
        invoke=lambda _op: AdapterResult(EffectStatus.SUCCEEDED, state),
        compensate=lambda *_: None,
    )
    with pytest.raises(AdapterError, match="not_allowlisted"):
        run.execute(operation(adapter="shell", action="shell:run"))
    with pytest.raises(AdapterError, match="live_effect_forbidden"):
        run.execute(operation(dry_run=False))
    assert receipts(store) == []


@pytest.mark.parametrize("event", [
    {"kind": "human_gate", "gate_kind": "g08_launch_signoff"},
    {"kind": "human_gate", "gate_kind": "spend_above_cap"},
    {"kind": "owner_action_blocker", "irrecoverable": True, "owner_action_required": True},
    {"kind": "milestone", "completed": True},
])
def test_quiet_operation_allows_only_owner_interrupts_and_milestones(event):
    assert require_quiet_notification(event) == event["kind"]


@pytest.mark.parametrize("event", [
    {"kind": "routine_status"},
    {"kind": "human_gate", "gate_kind": "task_approval"},
    {"kind": "owner_action_blocker", "irrecoverable": False, "owner_action_required": True},
    {"kind": "milestone", "completed": False},
])
def test_quiet_operation_suppresses_routine_or_recoverable_updates(event):
    with pytest.raises(AdapterError, match="suppresses"):
        require_quiet_notification(event)


def test_notify_adapter_enforces_quiet_policy_before_wrapper_invocation(store, grants):
    state = {"delivered": False}
    calls = []

    def invoke(_op):
        calls.append("notify")
        state["delivered"] = True
        return AdapterResult(EffectStatus.SUCCEEDED, dict(state))

    run = executor(
        store, grants, state=state, invoke=invoke, compensate=None,
        name="notify-preview", kind="notify", action="notify:owner",
        compensation=False,
    )
    base = operation(
        adapter="notify-preview", action="notify:owner", resource="owner",
        desired={"delivered": True},
    )
    with pytest.raises(AdapterError, match="suppresses"):
        run.execute(base)
    allowed = Operation(**{
        **base.__dict__,
        "notification_event": {"kind": "milestone", "completed": True},
    })
    assert run.execute(allowed)["status"] == "succeeded"
    assert calls == ["notify"]
