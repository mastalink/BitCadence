import hashlib
import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from mco.localstore import LocalStore
from mco.orchestrator.score_adapters_live import LiveScoreAdapterExecutor
from mco.orchestrator.score_authority import GrantService
from mco.orchestrator.score_bridge import ScoreBridge
from mco.orchestrator.score_conductor import Conductor
from mco.orchestrator.scores import ScoreError, digest


def _make_git_worktree(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True, capture_output=True)
    (repo / "README.md").write_text("# Repo\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "initial commit"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "checkout", "-b", "score/test-branch"], cwd=repo, check=True, capture_output=True)
    proc = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True)
    initial_sha = proc.stdout.strip()
    return repo, "score/test-branch", initial_sha


class FakeBoard:
    def __init__(self):
        self.jobs = {}
        self.evs = {}
        self.identity = "mock-board-hash"

    def capabilities(self):
        return {"create_with_id": 1}

    def create(self, payload):
        jid = payload["id"]
        job = dict(payload)
        job["status"] = "pending"
        job["leased_by_instance_id"] = None
        job["source_agent_id"] = "conductor"
        job["org_id"] = "default"
        self.jobs[jid] = job
        self.evs[jid] = []
        return job

    def get(self, jid):
        return self.jobs[jid]

    def events(self, jid):
        return self.evs.get(jid, [])

    def lease(self, jid, actor_id):
        self.jobs[jid]["status"] = "leased"
        self.jobs[jid]["leased_by_instance_id"] = actor_id

    def complete(self, jid, result, actor_id="worker", actor_role="auditor"):
        self.jobs[jid]["status"] = "completed"
        self.jobs[jid]["leased_by_instance_id"] = actor_id
        self.jobs[jid]["output_payload"] = {"result": json.dumps(result)}
        self.evs.setdefault(jid, []).append({
            "job_id": jid,
            "event": "status:completed",
            "actor_id": actor_id,
            "actor_role": actor_role,
        })


def _setup_bridge_and_executor(tmp_path, wt_dir, target_branch, initial_sha, evidence_keys):
    key = b"s05-test-key-material-is-long-enough-0001"
    now_dt = datetime(2026, 9, 16, tzinfo=timezone.utc)
    db_store = LocalStore(tmp_path / "live_score.db")
    grant_svc = GrantService(db_store, verification_key=key)
    executor = LiveScoreAdapterExecutor(
        db=db_store,
        grant_service=grant_svc,
        now=lambda: now_dt,
    )

    s = {
        "score_version": 1,
        "id": "evidence-test-run",
        "revision": 1,
        "objective": "Test evidence integrity",
        "constraints": ["Test only"],
        "budget_cents": 0,
        "max_parallel": 1,
        "launch_requires": ["write_task"],
        "tasks": [
            {
                "id": "write_task",
                "goal": "Write code and produce evidence",
                "checkpoint": None,
                "title": "Write Code and Produce Evidence",
                "role": "auditor",
                "review_role": "reviewer",
                "capabilities": ["repository:write", "evidence:write", "evidence:review"],
                "depends_on": [],
                "max_attempts": 1,
                "max_cost_cents": 0,
                "timeout_seconds": 3600,
                "resources": [str(wt_dir)],
                "evidence": evidence_keys,
                "instructions": "Implement changes",
                "commit": {
                    "worktree_path": str(wt_dir),
                    "target_branch": target_branch,
                    "allowed_paths": ["src/*", "docs/verification/*"],
                    "commit_message": "feat: write code",
                    "expected_before_sha": initial_sha,
                },
            }
        ],
    }

    run_digest = digest(s)
    grant_svc.issue({
        "org_id": "default",
        "run_id": "evidence-test-run",
        "digest": run_digest,
        "actions": ["repository:write"],
        "resources": [str(wt_dir)],
        "env": "test",
        "not_before": "2026-09-15T00:00:00Z",
        "expires_at": (datetime.now(timezone.utc) + timedelta(days=365)).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "budget_cents": 0,
        "human_principal": "conductor",
    })

    bridge = ScoreBridge(tmp_path / "bridge.db", tmp_path / "artifacts", live_executor=executor)
    board = FakeBoard()
    bridge.initialize(
        "evidence-test-run",
        s,
        principal="conductor",
        org="default",
        targets={"auditor": "worker", "reviewer": "independent"},
        credential_hash=board.identity,
    )
    return bridge, board, executor, s


def test_stale_head_in_worker_output_rejected(tmp_path):
    wt_dir, target_branch, initial_sha = _make_git_worktree(tmp_path)
    bridge, board, _, _ = _setup_bridge_and_executor(tmp_path, wt_dir, target_branch, initial_sha, ["commit_sha"])

    work_id = bridge.plan("evidence-test-run")[0]
    bridge.dispatch("evidence-test-run", board)

    (wt_dir / "src").mkdir(parents=True, exist_ok=True)
    (wt_dir / "src" / "app.py").write_text("x = 1\n", encoding="utf-8")

    # Worker explicitly claims a stale expected_before_sha
    board.complete(work_id, {"ready": True, "expected_before_sha": "stale00000000000000000000000000000000000"})

    with pytest.raises(ScoreError, match="Stale-head rejected"):
        bridge.poll("evidence-test-run", board)


def test_missing_evidence_label_rejected_without_fabricating_commit_sha(tmp_path):
    wt_dir, target_branch, initial_sha = _make_git_worktree(tmp_path)
    bridge, board, _, _ = _setup_bridge_and_executor(
        tmp_path, wt_dir, target_branch, initial_sha, ["commit_sha", "verification_report"]
    )

    work_id = bridge.plan("evidence-test-run")[0]
    bridge.dispatch("evidence-test-run", board)

    (wt_dir / "src").mkdir(parents=True, exist_ok=True)
    (wt_dir / "src" / "app.py").write_text("x = 1\n", encoding="utf-8")

    # Worker signals ready but omits verification_report
    board.complete(work_id, {"ready": True})

    with pytest.raises(ScoreError, match="Missing required evidence label 'verification_report'"):
        bridge.poll("evidence-test-run", board)


def test_path_traversal_in_artifact_path_rejected(tmp_path):
    wt_dir, target_branch, initial_sha = _make_git_worktree(tmp_path)
    bridge, board, _, _ = _setup_bridge_and_executor(
        tmp_path, wt_dir, target_branch, initial_sha, ["commit_sha", "test_report"]
    )

    work_id = bridge.plan("evidence-test-run")[0]
    bridge.dispatch("evidence-test-run", board)

    (wt_dir / "src").mkdir(parents=True, exist_ok=True)
    (wt_dir / "src" / "app.py").write_text("x = 1\n", encoding="utf-8")

    # Worker claims an artifact with path traversal
    board.complete(work_id, {
        "ready": True,
        "artifacts": {
            "test_report": {
                "path": "../../../etc/passwd",
                "sha256": "0" * 64,
            }
        },
    })

    with pytest.raises(ScoreError, match="Path traversal or invalid path rejected"):
        bridge.poll("evidence-test-run", board)


def test_digest_mismatch_in_artifact_rejected(tmp_path):
    wt_dir, target_branch, initial_sha = _make_git_worktree(tmp_path)
    bridge, board, _, _ = _setup_bridge_and_executor(
        tmp_path, wt_dir, target_branch, initial_sha, ["commit_sha", "test_report"]
    )

    work_id = bridge.plan("evidence-test-run")[0]
    bridge.dispatch("evidence-test-run", board)

    (wt_dir / "src").mkdir(parents=True, exist_ok=True)
    (wt_dir / "src" / "app.py").write_text("x = 1\n", encoding="utf-8")

    report_dir = wt_dir / "docs" / "verification"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_file = report_dir / "test_report.md"
    report_file.write_text("actual test report content\n", encoding="utf-8")

    board.complete(work_id, {
        "ready": True,
        "artifacts": {
            "test_report": {
                "path": "docs/verification/test_report.md",
                "sha256": "f" * 64,  # wrong digest
            }
        },
    })

    with pytest.raises(ScoreError, match="Artifact digest mismatch"):
        bridge.poll("evidence-test-run", board)


def test_real_artifact_in_verification_dir_successfully_bound(tmp_path):
    wt_dir, target_branch, initial_sha = _make_git_worktree(tmp_path)
    bridge, board, _, _ = _setup_bridge_and_executor(
        tmp_path, wt_dir, target_branch, initial_sha, ["commit_sha", "test_report"]
    )

    work_id = bridge.plan("evidence-test-run")[0]
    bridge.dispatch("evidence-test-run", board)

    (wt_dir / "src").mkdir(parents=True, exist_ok=True)
    (wt_dir / "src" / "app.py").write_text("x = 1\n", encoding="utf-8")

    report_dir = wt_dir / "docs" / "verification"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_file = report_dir / "test_report.md"
    content = b"# Verified Test Results\nAll 10 tests passed.\n"
    report_file.write_bytes(content)
    real_digest = hashlib.sha256(content).hexdigest()

    board.complete(work_id, {
        "ready": True,
        "artifacts": {
            "test_report": {
                "path": "docs/verification/test_report.md",
                "sha256": real_digest,
            }
        },
    })

    bridge.poll("evidence-test-run", board)

    status = bridge.status("evidence-test-run")
    work_disp = [d for d in status["dispatches"] if d["phase"] == "work"][0]
    assert work_disp["status"] == "validated"
    ev = json.loads(work_disp["evidence"])
    assert "commit_sha" in ev
    assert ev["test_report"] == {
        "path": "docs/verification/test_report.md",
        "sha256": real_digest,
    }


def test_lineage_normalization_in_conductor_launch_requires(tmp_path):
    """If root task is rejected but fix task is accepted, launched becomes True."""
    s = {
        "score_version": 1,
        "id": "repair-run",
        "revision": 1,
        "objective": "Test repair lineage",
        "constraints": ["Test only"],
        "budget_cents": 0,
        "max_parallel": 1,
        "launch_requires": ["T1"],
        "tasks": [
            {
                "id": "T1",
                "goal": "Do T1",
                "checkpoint": None,
                "title": "Task 1",
                "role": "auditor",
                "review_role": "reviewer",
                "capabilities": ["evidence:review"],
                "depends_on": [],
                "max_attempts": 1,
                "max_cost_cents": 0,
                "timeout_seconds": 3600,
                "resources": ["T1"],
                "evidence": ["report"],
                "instructions": "Do T1",
                "on_reject": "T1-repair1",
            },
            {
                "id": "T1-repair1",
                "goal": "Fix T1",
                "checkpoint": None,
                "title": "Task 1 Repair",
                "role": "auditor",
                "review_role": "reviewer",
                "capabilities": ["evidence:review"],
                "depends_on": ["T1"],
                "max_attempts": 1,
                "max_cost_cents": 0,
                "timeout_seconds": 3600,
                "resources": ["T1"],
                "evidence": ["report"],
                "instructions": "Fix T1",
            },
        ],
    }

    art_root = tmp_path / "artifacts"
    art_root.mkdir()
    (art_root / "rep.json").write_text("{}", encoding="utf-8")
    rep_sha = hashlib.sha256(b"{}").hexdigest()

    bridge = ScoreBridge(tmp_path / "bridge.db", art_root)
    board = FakeBoard()
    bridge.initialize("repair-run", s, principal="conductor", org="default", targets={"auditor": "worker", "reviewer": ["rev-1", "rev-2"]}, credential_hash=board.identity)

    # 1. T1 planned, dispatched, completed, review rejected
    w1 = bridge.plan("repair-run")[0]
    bridge.dispatch("repair-run", board)
    board.complete(w1, {"artifacts": {"report": {"path": "rep.json", "sha256": rep_sha}}})
    bridge.poll("repair-run", board)

    r1 = bridge.plan("repair-run")[0]
    bridge.dispatch("repair-run", board)
    board.complete(r1, {"verdict": "fail", "review_of": {"report": {"path": "rep.json", "sha256": rep_sha}}, "findings": ["bad"]}, actor_id="rev-1", actor_role="reviewer")
    bridge.poll("repair-run", board)

    conductor = Conductor(bridge, board)
    cond_st = conductor.status("repair-run")
    assert cond_st["launched"] is False

    # 2. T1-repair1 planned, dispatched, completed, review accepted by second reviewer
    w2 = bridge.plan("repair-run")[0]
    bridge.dispatch("repair-run", board)
    board.complete(w2, {"artifacts": {"report": {"path": "rep.json", "sha256": rep_sha}}})
    bridge.poll("repair-run", board)

    r2 = bridge.plan("repair-run")[0]
    bridge.dispatch("repair-run", board)
    board.complete(r2, {"verdict": "pass", "review_of": {"report": {"path": "rep.json", "sha256": rep_sha}}, "findings": []}, actor_id="rev-2", actor_role="reviewer")
    bridge.poll("repair-run", board)

    # Conductor status must now report launched=True because T1-repair1 satisfied T1!
    cond_st2 = conductor.status("repair-run")
    assert cond_st2["launched"] is True


def test_get_jobs_reads_all_rows_without_100_row_loss(monkeypatch):
    class FakeDBForJobs:
        def __init__(self):
            self.jobs = [
                {"id": f"j-{i}", "title": f"Job {i}", "created_at": f"2026-01-01T00:{i:02d}:00Z", "org_id": "default", "archived": False}
                for i in range(150)
            ]
            self._limit = None

        def table(self, name):
            return self

        def select(self, *_args):
            return self

        def eq(self, col, val):
            return self

        def order(self, *_args, **_kw):
            return self

        def limit(self, n):
            self._limit = n
            return self

        def execute(self):
            class Res:
                pass
            r = Res()
            r.data = self.jobs[:self._limit] if self._limit else list(self.jobs)
            return r

    import mco.orchestrator.routes as routes_mod
    fake_db = FakeDBForJobs()
    monkeypatch.setattr(routes_mod, "get_db_client", lambda: fake_db)

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from mco.orchestrator.auth import require_agent

    app = FastAPI()
    app.include_router(routes_mod.router)
    app.dependency_overrides[require_agent] = lambda: {
        "instance_id": "test-agent",
        "role": "admin",
        "org_id": "default",
    }
    client = TestClient(app)

    # All 150 jobs returned without truncation when limit is omitted
    res = client.get("/api/jobs")
    assert res.status_code == 200
    assert len(res.json()) == 150

    # Limit parameter works when specified
    res_lim = client.get("/api/jobs?limit=50")
    assert res_lim.status_code == 200
    assert len(res_lim.json()) == 50


def test_get_recent_events_reads_all_jobs_without_500_limit(monkeypatch):
    class FakeDBForRecentEvents:
        def __init__(self):
            # 600 jobs, exceeding 500
            self.jobs = [
                {"id": f"j-{i}", "title": f"Job {i}", "status": "completed", "created_at": f"2026-01-01T00:{i:02d}:00Z", "org_id": "org-special"}
                for i in range(600)
            ]
            # An event referring to job 550 (which would be dropped if capped at 500)
            self.events = [
                {"id": "ev-1", "job_id": "j-550", "event": "status:completed", "created_at": "2026-01-02T00:00:00Z"}
            ]
            self._table = None
            self._limit = None

        def table(self, name):
            self._table = name
            self._limit = None
            return self

        def select(self, *_args):
            return self

        def order(self, *_args, **_kw):
            return self

        def limit(self, n):
            self._limit = n
            return self

        def execute(self):
            class Res:
                pass
            r = Res()
            if self._table == "agent_job_events":
                r.data = self.events[:self._limit] if self._limit else list(self.events)
            elif self._table == "agent_jobs":
                assert self._limit is None, "agent_jobs query in get_recent_events must not be limited to 500"
                r.data = self.jobs[:self._limit] if self._limit else list(self.jobs)
            else:
                r.data = []
            return r

    import mco.orchestrator.routes as routes_mod
    fake_db = FakeDBForRecentEvents()
    monkeypatch.setattr(routes_mod, "get_db_client", lambda: fake_db)

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from mco.orchestrator.auth import require_agent

    app = FastAPI()
    app.include_router(routes_mod.events_router)
    app.dependency_overrides[require_agent] = lambda: {
        "instance_id": "test-agent",
        "role": "worker",
        "org_id": "org-special",
    }
    client = TestClient(app)

    res = client.get("/api/events")
    assert res.status_code == 200
    evs = res.json()
    assert len(evs) == 1
    assert evs[0]["job_title"] == "Job 550"


def test_export_evidence_pack_reads_all_jobs_without_500_limit(monkeypatch):
    class FakeDBForEvidencePack:
        def __init__(self):
            self.jobs = [
                {"id": f"j-{i}", "title": f"Job {i}", "status": "completed", "created_at": f"2026-01-01T00:{i:02d}:00Z", "org_id": "org-special"}
                for i in range(600)
            ]
            self.events = [
                {"id": "ev-1", "job_id": "j-550", "event": "status:completed", "created_at": "2026-01-02T00:00:00Z"}
            ]
            self._table = None
            self._limit = None

        def table(self, name):
            self._table = name
            self._limit = None
            return self

        def select(self, *_args):
            return self

        def order(self, *_args, **_kw):
            return self

        def limit(self, n):
            self._limit = n
            return self

        def execute(self):
            class Res:
                pass
            r = Res()
            if self._table == "agent_job_events":
                r.data = self.events[:self._limit] if self._limit else list(self.events)
            elif self._table == "agent_jobs":
                assert self._limit is None, "agent_jobs query in export_evidence_pack must not be limited to 500"
                r.data = self.jobs[:self._limit] if self._limit else list(self.jobs)
            else:
                r.data = []
            return r

    import mco.orchestrator.routes as routes_mod
    import mco.orchestrator.admin_routes as admin_routes_mod
    fake_db = FakeDBForEvidencePack()
    monkeypatch.setattr(routes_mod, "get_db_client", lambda: fake_db)

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from mco.orchestrator.auth import require_agent

    app = FastAPI()
    app.include_router(admin_routes_mod.governance_router)
    app.dependency_overrides[require_agent] = lambda: {
        "instance_id": "test-agent",
        "role": "admin",
        "org_id": "org-special",
    }
    client = TestClient(app)

    res = client.post("/api/governance/evidence-pack", json={})
    assert res.status_code == 200
    pack = res.json()
    assert pack["summary"]["audit_events"] == 1
    trail = json.loads(pack["files"][1]["text"])
    assert len(trail["audit_events"]) == 1
    assert trail["audit_events"][0]["job_title"] == "Job 550"

