"""Tests for run-start role resolver (abstract role -> online eligible identities)."""

from datetime import datetime, timedelta, timezone
import hashlib
import pytest

from mco.localstore import LocalStore
from mco.orchestrator.audit import record_event
from mco.orchestrator.delivery import REROUTED
from mco.orchestrator.presence import BROKEN, DISABLED, OFFLINE, STANDBY, WORKING
from mco.orchestrator.score_bridge import ScoreBridge
from mco.orchestrator.score_dispatcher import resolve_score_targets
from mco.orchestrator.score_resolver import resolve_score_targets as resolver_direct
from mco.orchestrator.scores import ScoreError


def make_score(
    roles=None,
    num_tasks=1,
):
    roles = roles or [("auditor", "reviewer")]
    tasks = []
    for i, (work_role, rev_role) in enumerate(roles):
        task_id = f"task_{i+1}"
        task = dict(
            id=task_id,
            goal=f"G0{i+1}",
            title=f"Task {i+1}",
            instructions="Read-only instructions",
            role=work_role,
            review_role=rev_role,
            depends_on=[f"task_{i}"] if i > 0 else [],
            resources=[f"res_{i+1}"],
            capabilities=["cloud:inspect"],
            evidence=["report"],
            max_attempts=1,
            timeout_seconds=300,
            max_cost_cents=0,
            checkpoint=None,
        )
        tasks.append(task)
    return dict(
        score_version=1,
        id="test-score",
        revision=1,
        objective="Run-start resolution test",
        constraints=["Read only"],
        budget_cents=0,
        max_parallel=1,
        tasks=tasks,
        launch_requires=["task_1"],
    )


def _now():
    return datetime.now(timezone.utc)


def _agent(instance, role, *, seen_seconds_ago=None, status="online", org_id="default"):
    return {
        "instance_id": instance,
        "role": role,
        "status": status,
        "org_id": org_id,
        "last_seen_at": (
            None
            if seen_seconds_ago is None
            else (_now() - timedelta(seconds=seen_seconds_ago)).isoformat()
        ),
    }


def _job(db, job_id, *, status="pending", role="auditor", target=None, age=0, leased_by=None):
    db.table("agent_jobs").insert({
        "id": job_id,
        "title": job_id,
        "status": status,
        "target_agent_role": role,
        "target_agent_id": target,
        "leased_by_instance_id": leased_by,
        "created_at": (_now() - timedelta(seconds=age)).isoformat(),
    }).execute()


@pytest.fixture
def db(tmp_path):
    store = LocalStore(tmp_path / "fleet.db")
    yield store
    store.close()


def test_import_from_both_modules():
    assert resolve_score_targets is resolver_direct


def test_online_eligible_identities_collected_correctly(db):
    score = make_score([("auditor", "reviewer")])
    # Auditor agents: one standby (fresh heartbeat), one working (holding lease)
    db.table("agent_registry").insert(_agent("aud-1", "auditor", seen_seconds_ago=10)).execute()
    db.table("agent_registry").insert(_agent("aud-2", "auditor", seen_seconds_ago=3600)).execute()
    _job(db, "j-active", status="in_progress", leased_by="aud-2")
    # Reviewer agent: standby
    db.table("agent_registry").insert(_agent("rev-1", "reviewer", seen_seconds_ago=5)).execute()

    targets = resolve_score_targets(score, org_id="default", db_client=db)
    assert targets == {
        "auditor": ["aud-1", "aud-2"],
        "reviewer": ["rev-1"],
    }


def test_offline_identities_are_excluded(db):
    score = make_score([("auditor", "reviewer")])
    # aud-1 is online (seen 10s ago)
    db.table("agent_registry").insert(_agent("aud-1", "auditor", seen_seconds_ago=10)).execute()
    # aud-stale has stale heartbeat (3600s ago) with no live lease or socket -> offline
    db.table("agent_registry").insert(_agent("aud-stale", "auditor", seen_seconds_ago=3600)).execute()
    # rev-offline has status="offline" with no heartbeat -> offline
    db.table("agent_registry").insert(_agent("rev-offline", "reviewer", status="offline")).execute()
    # rev-1 is online
    db.table("agent_registry").insert(_agent("rev-1", "reviewer", seen_seconds_ago=15)).execute()

    targets = resolve_score_targets(score, org_id="default", db_client=db, threshold=300)
    assert targets["auditor"] == ["aud-1"]
    assert targets["reviewer"] == ["rev-1"]


def test_broken_identities_are_excluded(db):
    score = make_score([("auditor", "reviewer")])
    stall_sec = 600

    # aud-healthy is fine
    db.table("agent_registry").insert(_agent("aud-healthy", "auditor", seen_seconds_ago=10)).execute()
    # aud-stalled has a pending job targeted at it that stalled past window -> broken
    db.table("agent_registry").insert(_agent("aud-stalled", "auditor", seen_seconds_ago=10)).execute()
    _job(db, "stuck-job", role="auditor", target="aud-stalled", age=stall_sec + 120)

    # rev-healthy is fine
    db.table("agent_registry").insert(_agent("rev-healthy", "reviewer", seen_seconds_ago=5)).execute()
    # rev-rerouted has had a job rerouted away within the last hour -> broken
    db.table("agent_registry").insert(_agent("rev-rerouted", "reviewer", seen_seconds_ago=5)).execute()
    _job(db, "moved-job", status="completed", role="reviewer")
    record_event(
        db,
        "moved-job",
        REROUTED,
        "system",
        "delivery",
        {"from_role": "reviewer", "from_instance": "rev-rerouted", "to_role": "reviewer"},
    )

    targets = resolve_score_targets(
        score,
        org_id="default",
        db_client=db,
        stall_seconds=stall_sec,
    )
    assert targets["auditor"] == ["aud-healthy"]
    assert targets["reviewer"] == ["rev-healthy"]


def test_disabled_identities_are_excluded(db):
    score = make_score([("auditor", "reviewer")])
    # Disabled agent should never be eligible even if technically connected
    db.table("agent_registry").insert(_agent("aud-disabled", "auditor", status="disabled")).execute()
    db.table("agent_registry").insert(_agent("aud-live", "auditor", seen_seconds_ago=5)).execute()
    db.table("agent_registry").insert(_agent("rev-live", "reviewer", seen_seconds_ago=5)).execute()

    targets = resolve_score_targets(score, org_id="default", db_client=db, connected={"aud-disabled"})
    assert targets["auditor"] == ["aud-live"]
    assert targets["reviewer"] == ["rev-live"]


def test_zero_eligible_identities_returns_empty_list(db):
    score = make_score([("auditor", "reviewer")])
    # Only auditor is registered and online; reviewer has zero eligible identities
    db.table("agent_registry").insert(_agent("aud-1", "auditor", seen_seconds_ago=10)).execute()

    targets = resolve_score_targets(score, org_id="default", db_client=db)
    # Honest presence: resolver returns empty list, does not throw
    assert targets == {
        "auditor": ["aud-1"],
        "reviewer": [],
    }


def test_integration_empty_pool_role_fails_in_score_bridge_initialize(tmp_path, db):
    score = make_score([("auditor", "reviewer")])
    # Setup fleet with auditor online, reviewer completely absent
    db.table("agent_registry").insert(_agent("aud-1", "auditor", seen_seconds_ago=10)).execute()

    targets = resolve_score_targets(score, org_id="default", db_client=db)
    assert targets["reviewer"] == []

    bridge = ScoreBridge(tmp_path / "state.db", tmp_path / "artifacts")
    with pytest.raises(ScoreError, match="Explicit worker identity required"):
        bridge.initialize(
            "run-empty-pool",
            score,
            principal="conductor",
            org="default",
            targets=targets,
            credential_hash="cred-hash-test",
        )


def test_tenant_isolation_excludes_other_orgs(db):
    score = make_score([("auditor", "reviewer")])
    # Agents in "tenant-alpha"
    db.table("agent_registry").insert(_agent("aud-alpha", "auditor", seen_seconds_ago=5, org_id="tenant-alpha")).execute()
    db.table("agent_registry").insert(_agent("rev-alpha", "reviewer", seen_seconds_ago=5, org_id="tenant-alpha")).execute()
    # Agents in "default"
    db.table("agent_registry").insert(_agent("aud-default", "auditor", seen_seconds_ago=5, org_id="default")).execute()
    db.table("agent_registry").insert(_agent("rev-default", "reviewer", seen_seconds_ago=5, org_id="default")).execute()

    targets_alpha = resolve_score_targets(score, org_id="tenant-alpha", db_client=db)
    assert targets_alpha == {
        "auditor": ["aud-alpha"],
        "reviewer": ["rev-alpha"],
    }

    targets_default = resolve_score_targets(score, org_id="default", db_client=db)
    assert targets_default == {
        "auditor": ["aud-default"],
        "reviewer": ["rev-default"],
    }


def test_live_waker_socket_is_online_standby(db):
    score = make_score([("auditor", "reviewer")])
    # Heartbeat is stale (1 hour ago), but agent has a live waker broadcast socket
    db.table("agent_registry").insert(_agent("aud-socket", "auditor", seen_seconds_ago=3600)).execute()
    db.table("agent_registry").insert(_agent("rev-1", "reviewer", seen_seconds_ago=5)).execute()

    targets = resolve_score_targets(
        score,
        org_id="default",
        db_client=db,
        connected={"aud-socket"},
        threshold=300,
    )
    assert "aud-socket" in targets["auditor"]


def test_case_insensitive_role_matching(db):
    score = make_score([("Auditor", "Reviewer")])
    # Registered in lowercase in agent_registry
    db.table("agent_registry").insert(_agent("aud-1", "auditor", seen_seconds_ago=5)).execute()
    db.table("agent_registry").insert(_agent("rev-1", "reviewer", seen_seconds_ago=5)).execute()

    targets = resolve_score_targets(score, org_id="default", db_client=db)
    assert targets == {
        "Auditor": ["aud-1"],
        "Reviewer": ["rev-1"],
    }


def test_multi_task_distinct_roles(db):
    # Score with 2 tasks with 3 distinct roles
    score = make_score([("engineer", "reviewer"), ("qa", "reviewer")])
    db.table("agent_registry").insert(_agent("eng-1", "engineer", seen_seconds_ago=5)).execute()
    db.table("agent_registry").insert(_agent("rev-1", "reviewer", seen_seconds_ago=5)).execute()
    db.table("agent_registry").insert(_agent("qa-1", "qa", seen_seconds_ago=5)).execute()

    targets = resolve_score_targets(score, org_id="default", db_client=db)
    assert targets == {
        "engineer": ["eng-1"],
        "reviewer": ["rev-1"],
        "qa": ["qa-1"],
    }


def test_presence_source_injection_direct():
    score = make_score([("auditor", "reviewer")])
    fake_fleet = [
        {"instance_id": "aud-fake", "role": "auditor", "state": STANDBY, "org_id": "default"},
        {"instance_id": "aud-broken", "role": "auditor", "state": BROKEN, "org_id": "default"},
        {"instance_id": "aud-off", "role": "auditor", "state": OFFLINE, "org_id": "default"},
        {"instance_id": "aud-dis", "role": "auditor", "state": DISABLED, "org_id": "default"},
        {"instance_id": "rev-fake", "role": "reviewer", "state": WORKING, "org_id": "default"},
    ]
    targets = resolve_score_targets(score, org_id="default", presence_source=fake_fleet)
    assert targets == {
        "auditor": ["aud-fake"],
        "reviewer": ["rev-fake"],
    }


def test_presence_source_callable_injection():
    score = make_score([("auditor", "reviewer")])
    called = False

    def supplier():
        nonlocal called
        called = True
        return [
            {"instance_id": "aud-c", "role": "auditor", "state": STANDBY, "org_id": "default"},
            {"instance_id": "rev-c", "role": "reviewer", "state": STANDBY, "org_id": "default"},
        ]

    targets = resolve_score_targets(score, org_id="default", presence_source=supplier)
    assert called is True
    assert targets == {
        "auditor": ["aud-c"],
        "reviewer": ["rev-c"],
    }


def test_monkeypatched_get_db_client(monkeypatch, db):
    score = make_score([("auditor", "reviewer")])
    db.table("agent_registry").insert(_agent("aud-1", "auditor", seen_seconds_ago=5)).execute()
    db.table("agent_registry").insert(_agent("rev-1", "reviewer", seen_seconds_ago=5)).execute()

    from mco.orchestrator import routes
    monkeypatch.setattr(routes, "get_db_client", lambda: db)

    targets = resolve_score_targets(score, org_id="default")
    assert targets == {
        "auditor": ["aud-1"],
        "reviewer": ["rev-1"],
    }


def test_invalid_score_raises_score_error():
    with pytest.raises(ScoreError):
        resolve_score_targets({"not": "a valid score"})
    with pytest.raises(ScoreError):
        resolve_score_targets(12345)


def test_author_reviewer_pool_overlap_interaction_with_initialize(tmp_path):
    """Demonstrates that ScoreBridge.initialize fails closed with 'Independent identity required'
    if ANY overlap exists between author targets and reviewer targets."""
    score = make_score([("auditor", "reviewer")])
    bridge = ScoreBridge(tmp_path / "state.db", tmp_path / "artifacts")

    # In a 2-agent fleet where both agents are eligible for authoring and reviewing:
    # Full overlap
    full_overlap_targets = {
        "auditor": ["agent-1", "agent-2"],
        "reviewer": ["agent-1", "agent-2"],
    }
    with pytest.raises(ScoreError, match="Independent identity required"):
        bridge.initialize(
            "run-overlap-1",
            score,
            principal="conductor",
            org="default",
            targets=full_overlap_targets,
            credential_hash="cred-hash",
        )

    # Partial overlap (one shared agent): even though reviewer has an independent agent-2,
    # initialize() rejects because set(author) & set(reviewer) is non-empty!
    partial_overlap_targets = {
        "auditor": ["agent-1"],
        "reviewer": ["agent-1", "agent-2"],
    }
    with pytest.raises(ScoreError, match="Independent identity required"):
        bridge.initialize(
            "run-overlap-2",
            score,
            principal="conductor",
            org="default",
            targets=partial_overlap_targets,
            credential_hash="cred-hash",
        )

    # Disjoint pools succeed
    disjoint_targets = {
        "auditor": ["agent-1"],
        "reviewer": ["agent-2"],
    }
    bridge.initialize(
        "run-disjoint",
        score,
        principal="conductor",
        org="default",
        targets=disjoint_targets,
        credential_hash="cred-hash",
    )
    assert bridge.status("run-disjoint")["status"] == "running"
