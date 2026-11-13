import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from mco.orchestrator.score_bridge import ScoreBridge
from mco.orchestrator.score_recovery import RecoveryStaging
from mco.orchestrator.scores import ScoreError
from tests.test_score_bridge import Board, score


@pytest.fixture
def recovery(tmp_path):
    bridge = ScoreBridge(tmp_path / 'runs.db', tmp_path / 'artifacts', clock=lambda: 1000)
    board = Board()
    bridge.initialize('root', score(), principal='conductor', org='default',
                      targets={'auditor': 'worker', 'reviewer': 'independent'},
                      credential_hash=board.identity)
    with bridge.tx() as db:
        db.execute("UPDATE runs SET status='blocked' WHERE id='root'")
        bridge.event(db, 'root', 'tick_blocked', {'reason': 'worker timeout'})
    return RecoveryStaging(bridge), board


def test_records_reason_and_stages_without_activating(recovery):
    staging, board = recovery
    result = staging.stage('root', board=board, standing_grant_id='grant-ref')
    assert result['action'] == 'fork'
    assert result['reason'] == {'reason': 'worker timeout'}
    assert result['standing_grant_id'] == 'grant-ref'
    assert result['child_run_id'] != 'root'
    assert result['resume_after'] is None
    with staging.bridge.tx() as db:
        assert db.execute('SELECT COUNT(*) FROM runs').fetchone()[0] == 1
    assert board.creates == 0


def test_restart_and_concurrent_stage_create_one_fork(recovery):
    staging, board = recovery
    def stage(_):
        return RecoveryStaging(staging.bridge).stage('root', board=board, standing_grant_id='grant-ref')
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(stage, range(8)))
    assert all(r == results[0] for r in results)
    with staging.bridge.tx() as db:
        assert db.execute('SELECT COUNT(*) FROM recovery_staging').fetchone()[0] == 1


def test_two_forks_then_escalation_and_grant_cannot_change(recovery):
    staging, board = recovery
    parent = 'root'
    for attempt in (1, 2):
        result = staging.stage(parent, board=board, standing_grant_id='grant-ref')
        assert result['attempt'] == attempt
        child = result['child_run_id']
        # Simulate a separately authorized go step, then another block.
        staging.bridge.initialize(child, score(), principal='conductor', org='default',
                                  targets={'auditor': 'worker', 'reviewer': 'independent'},
                                  credential_hash=board.identity)
        with staging.bridge.tx() as db:
            db.execute("UPDATE runs SET status='blocked' WHERE id=?", (child,))
            staging.bridge.event(db, child, 'run_blocked', {'reason': 'still blocked'})
        parent = child
    with pytest.raises(ScoreError, match='grant'):
        staging.stage(parent, board=board, standing_grant_id='different')
    result = staging.stage(parent, board=board, standing_grant_id='grant-ref')
    assert result['action'] == 'escalate'
    assert result['child_run_id'] is None
    assert result['attempt'] == 2
    assert staging.stage(parent, board=board, standing_grant_id='grant-ref') == result


def test_checkpoint_uses_accepted_review_not_completed_work(recovery):
    staging, board = recovery
    evidence = {'report': {'path': 'root/report.json', 'sha256': 'a' * 64}}
    with staging.bridge.tx() as db:
        db.execute("INSERT INTO dispatch VALUES(?,?,?,?,?,?,?,?)",
                   ('root', 'audit', 'review', 'accepted-review', '{}', 'accepted', json.dumps(evidence), 2000))
        staging.bridge.event(db, 'root', 'accepted', {'job_id': 'accepted-review'})
        db.execute("INSERT INTO dispatch VALUES(?,?,?,?,?,?,?,?)",
                   ('root', 'unfinished', 'work', 'work-job', '{}', 'validated', '{}', 2000))
    result = staging.stage('root', board=board, standing_grant_id='grant-ref')
    assert result['resume_after'] == {'run_id': 'root', 'task': 'audit'}
    assert result['accepted'] == [{'run_id': 'root', 'task': 'audit', 'evidence': evidence}]


def test_refuses_wrong_identity_running_run_or_missing_grant(recovery):
    staging, board = recovery
    board.identity = 'other'
    with pytest.raises(ScoreError):
        staging.stage('root', board=board, standing_grant_id='grant-ref')
    board.identity = 'credential-fingerprint'
    with pytest.raises(ScoreError, match='grant'):
        staging.stage('root', board=board, standing_grant_id='')
    with staging.bridge.tx() as db:
        db.execute("UPDATE runs SET status='running' WHERE id='root'")
    with pytest.raises(ScoreError, match='blocked'):
        staging.stage('root', board=board, standing_grant_id='grant-ref')


def test_stall_alarm_boundary_restart_dedup_and_reset(recovery):
    staging, _ = recovery
    assert staging.observe_dispatches('conductor', [], now=0) is None
    assert staging.observe_dispatches('conductor', [], now=43199) is None
    fresh = RecoveryStaging(staging.bridge)
    alarm = fresh.observe_dispatches('conductor', [], now=43200)
    assert alarm['kind'] == 'conductor_stalled'
    assert alarm['zero_jobs_seconds'] == 43200
    assert fresh.observe_dispatches('conductor', [], now=43201) is None
    assert fresh.observe_dispatches('conductor', ['job-1'], now=43202) is None
    # Replaying the same sweep does not reset the inactivity timer.
    assert fresh.observe_dispatches('conductor', ['job-1'], now=80000) is None
    assert fresh.observe_dispatches('conductor', [], now=86402)['kind'] == 'conductor_stalled'
    assert fresh.observe_dispatches('different-conductor', [], now=86402) is None


def test_clock_regression_is_refused(recovery):
    staging, _ = recovery
    staging.observe_dispatches('conductor', [], now=100)
    with pytest.raises(ScoreError, match='clock'):
        staging.observe_dispatches('conductor', [], now=99)

def test_gate_rejection_stages_escalation_without_fork(recovery):
    staging, board = recovery
    with staging.bridge.tx() as db:
        staging.bridge.event(db, 'root', 'gate_rejected', {'reason': 'owner declined'})
    result = staging.stage('root', board=board, standing_grant_id='grant-ref')
    assert result['action'] == 'escalate'
    assert result['attempt'] == 0
    assert result['child_run_id'] is None


def test_checkpoint_follows_event_order_not_task_name(recovery):
    staging, board = recovery
    with staging.bridge.tx() as db:
        for task in ('z-first', 'a-last'):
            db.execute("INSERT INTO dispatch VALUES(?,?,?,?,?,?,?,?)",
                       ('root', task, 'review', task + '-review', '{}', 'accepted', '{}', 2000))
            staging.bridge.event(db, 'root', 'accepted', {'job_id': task + '-review'})
    assert staging.stage('root', board=board, standing_grant_id='grant-ref')['resume_after']['task'] == 'a-last'


def test_child_policy_drift_is_refused(recovery):
    staging, board = recovery
    result = staging.stage('root', board=board, standing_grant_id='grant-ref')
    child = result['child_run_id']
    changed = score()
    changed['constraints'] = ['different policy']
    staging.bridge.initialize(child, changed, principal='conductor', org='default',
                              targets={'auditor': 'worker', 'reviewer': 'independent'},
                              credential_hash=board.identity)
    with staging.bridge.tx() as db:
        db.execute("UPDATE runs SET status='blocked' WHERE id=?", (child,))
    with pytest.raises(ScoreError, match='policy/identity'):
        staging.stage(child, board=board, standing_grant_id='grant-ref')


@pytest.mark.parametrize('bad', [float('nan'), float('inf'), True, '100'])
def test_invalid_activity_clock_is_refused(recovery, bad):
    staging, _ = recovery
    with pytest.raises(ScoreError, match='clock'):
        staging.observe_dispatches('conductor', [], now=bad)


def test_stall_alarm_concurrent_observers_emit_once(recovery):
    staging, _ = recovery
    staging.observe_dispatches('conductor', [], now=0)
    def observe(_):
        return RecoveryStaging(staging.bridge).observe_dispatches('conductor', [], now=43200)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(observe, range(8)))
    assert sum(result is not None for result in results) == 1
