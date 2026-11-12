"""Durable, inert recovery proposals and conductor inactivity observations.

This is the work-step staging surface, deliberately not called by the live
sweep. A go-step consumer must revalidate evidence, unresolved effects, human
gates and signed authority before creating a child run. A grant reference is
context, never authorization: existing signed grants are bound to a run ID.
No jobs, runs, grants or notifications are created by this module.
"""
from __future__ import annotations

import json
import math
import uuid

from mco.orchestrator.scores import ScoreError


class RecoveryStaging:
    def __init__(self, bridge):
        self.bridge = bridge
        with bridge.tx() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS recovery_staging(
                parent_run_id TEXT PRIMARY KEY, root_run_id TEXT NOT NULL,
                child_run_id TEXT UNIQUE, attempt INTEGER NOT NULL,
                proposal TEXT NOT NULL)''')
            db.execute('''CREATE TABLE IF NOT EXISTS conductor_activity(
                conductor_id TEXT PRIMARY KEY, last_job_at REAL NOT NULL,
                observed_at REAL NOT NULL, alarmed INTEGER NOT NULL)''')
            db.execute('''CREATE TABLE IF NOT EXISTS conductor_job_observations(
                conductor_id TEXT NOT NULL, job_id TEXT NOT NULL,
                observed_at REAL NOT NULL, PRIMARY KEY(conductor_id,job_id))''')

    def stage(self, run_id, *, board, standing_grant_id):
        """Snapshot a blocked lineage, reserving at most two inert child IDs.

        BEGIN IMMEDIATE serializes retries and competing callers. Repeated
        calls for a parent return its immutable proposal. Accepted reviews are
        references to the original evidence, never newly forged completions.
        """
        if not isinstance(standing_grant_id, str) or not standing_grant_id.strip():
            raise ScoreError('standing grant reference required')
        with self.bridge.tx() as db:
            run = self.bridge.run(db, run_id)
            self.bridge.identity(run, board)
            if run['status'] != 'blocked':
                raise ScoreError('recovery requires a blocked run')
            old = db.execute('SELECT proposal FROM recovery_staging WHERE parent_run_id=?',
                             (run_id,)).fetchone()
            if old:
                proposal = json.loads(old[0])
                if proposal['standing_grant_id'] != standing_grant_id:
                    raise ScoreError('standing grant reference changed')
                return proposal
            ancestors = []
            current = run_id
            while True:
                ancestors.append(current)
                parent = db.execute('SELECT parent_run_id,proposal FROM recovery_staging WHERE child_run_id=?',
                                    (current,)).fetchone()
                if parent is None:
                    break
                inherited = json.loads(parent['proposal'])
                if inherited['standing_grant_id'] != standing_grant_id:
                    raise ScoreError('standing grant reference changed')
                current = parent['parent_run_id']
            root = self.bridge.run(db, ancestors[-1])
            for key in ('digest', 'principal', 'org', 'credential_hash', 'targets', 'artifact_root'):
                if run[key] != root[key]:
                    raise ScoreError('recovery lineage policy/identity changed')
            attempt = len(ancestors) - 1
            accepted = {}
            resume_after = None
            for ancestor in reversed(ancestors):
                # Event sequence, not lexical task ID, defines the checkpoint.
                rows = db.execute('''SELECT d.task,d.evidence,MAX(e.seq) AS accepted_seq
                    FROM dispatch d LEFT JOIN events e ON e.run=d.run
                    AND e.event='accepted' AND json_extract(e.detail,'$.job_id')=d.job_id
                    WHERE d.run=? AND d.phase='review' AND d.status='accepted'
                    GROUP BY d.task,d.evidence ORDER BY accepted_seq,d.task''', (ancestor,))
                for row in rows:
                    accepted[row['task']] = {'run_id': ancestor, 'task': row['task'],
                                             'evidence': json.loads(row['evidence'] or '{}')}
                    if row['accepted_seq'] is not None:
                        resume_after = {'run_id': ancestor, 'task': row['task']}
            reason_row = db.execute('''SELECT event,detail FROM events WHERE run=?
                AND event IN ('tick_blocked','validation_blocked','run_blocked','gate_rejected')
                ORDER BY seq DESC LIMIT 1''', (run_id,)).fetchone()
            reason = json.loads(reason_row['detail']) if reason_row else {'reason': 'blocked; no reason event retained'}
            gate_rejected = db.execute("SELECT 1 FROM events WHERE run=? AND event='gate_rejected' LIMIT 1",
                                       (run_id,)).fetchone()
            action = 'escalate' if attempt >= 2 or gate_rejected else 'fork'
            child = None
            if action == 'fork':
                attempt += 1
                child = 'recovery-' + str(uuid.uuid5(uuid.NAMESPACE_URL,
                            f"score-recovery:{root['org']}:{root['id']}:{root['digest']}:{attempt}"))
                if db.execute('SELECT 1 FROM runs WHERE id=?', (child,)).fetchone():
                    raise ScoreError('recovery child ID already exists outside this staging lineage')
            proposal = dict(action=action, root_run_id=root['id'], parent_run_id=run_id,
                            child_run_id=child, attempt=attempt, reason=reason,
                            standing_grant_id=standing_grant_id, digest=root['digest'],
                            resume_after=resume_after, accepted=list(accepted.values()),
                            state='staged', activation_requires=[
                                'owner-go-step', 'signed-child-run-authority',
                                'reverify-accepted-evidence', 'inspect-uncertain-effects',
                                'reevaluate-human-gates'])
            db.execute('INSERT INTO recovery_staging VALUES(?,?,?,?,?)',
                       (run_id, root['id'], child, attempt, json.dumps(proposal, sort_keys=True)))
            self.bridge.event(db, run_id, 'recovery_staged', proposal)
            return proposal

    def observe_dispatches(self, conductor_id, job_ids, *, now=None):
        """Return one inert alarm after 12 hours without a *new* submitted job.

        Feed only successful dispatch IDs, never plans, accepts or heartbeats.
        Replay IDs cannot hide inactivity. Observation and alarm state survive
        restarts; no historical inactivity is invented on first observation.
        """
        now = self.bridge.clock() if now is None else now
        if not isinstance(conductor_id, str) or not conductor_id.strip():
            raise ScoreError('explicit conductor identity required')
        if isinstance(now, bool) or not isinstance(now, (float, int)) or not math.isfinite(now):
            raise ScoreError('invalid activity clock')
        if not isinstance(job_ids, (list, tuple)) or any(not isinstance(j, str) or not j for j in job_ids):
            raise ScoreError('submitted job IDs required')
        with self.bridge.tx() as db:
            db.execute('INSERT OR IGNORE INTO conductor_activity VALUES(?,?,?,0)',
                       (conductor_id, now, now))
            state = db.execute('SELECT * FROM conductor_activity WHERE conductor_id=?',
                               (conductor_id,)).fetchone()
            if now < state['observed_at']:
                raise ScoreError('activity clock moved backwards')
            created = 0
            for job_id in job_ids:
                created += db.execute('INSERT OR IGNORE INTO conductor_job_observations VALUES(?,?,?)',
                                      (conductor_id, job_id, now)).rowcount
            last = now if created else state['last_job_at']
            alarmed = 0 if created else state['alarmed']
            alarm = None
            if not alarmed and now - last >= 12 * 60 * 60:
                alarm = dict(kind='conductor_stalled', conductor_id=conductor_id,
                             zero_jobs_seconds=now - last, observed_at=now,
                             state='staged', escalation_role='chief')
                alarmed = 1
                self.bridge.event(db, '_conductor_activity', 'stall_alarm_staged', alarm)
            db.execute('UPDATE conductor_activity SET last_job_at=?,observed_at=?,alarmed=? WHERE conductor_id=?',
                       (last, now, alarmed, conductor_id))
            return alarm
