import copy
import hashlib
import json
import subprocess
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import pytest
from mco.localstore import LocalStore
from mco.orchestrator.score_adapters_live import LiveScoreAdapterExecutor
from mco.orchestrator.score_authority import GrantService
from mco.orchestrator.score_bridge import ScoreBridge
from mco.orchestrator.scores import ScoreError, digest


def score():
    task = dict(id="audit", goal="G01", title="Audit", instructions="Read-only", role="auditor", review_role="reviewer", depends_on=[], resources=["audit"], capabilities=["cloud:inspect"], evidence=["report"], max_attempts=1, timeout_seconds=300, max_cost_cents=0, checkpoint=None)
    return dict(score_version=1,id="audit",revision=1,objective="Audit",constraints=["Read only"],budget_cents=0,max_parallel=1,tasks=[task],launch_requires=["audit"])


class Board:
    identity = "credential-fingerprint"
    def __init__(self):
        self.jobs = {}
        self.creates = 0
        self.lose_ack = False
    def capabilities(self):
        return {"create_with_id": 1}
    def create(self, payload):
        self.creates += 1
        if payload["id"] not in self.jobs:
            self.jobs[payload["id"]] = dict(copy.deepcopy(payload), source_agent_id="conductor", org_id="default", status="pending")
        elif any(self.jobs[payload["id"]].get(k) != v for k,v in payload.items()):
            raise ScoreError("Conflicting intent")
        if self.lose_ack:
            self.lose_ack = False
            raise TimeoutError("ack lost")
        return self.jobs[payload["id"]]
    def get(self, job_id):
        return self.jobs[job_id]
    def events(self, job_id):
        j = self.jobs[job_id]
        return [dict(job_id=job_id,event="status:completed",actor_id=j["target_agent_id"],actor_role=j["target_agent_role"])]
    def complete(self, job_id, result):
        j = self.jobs[job_id]
        j.update(status="completed",leased_by_instance_id=j["target_agent_id"],output_payload={"result":json.dumps(result)})


@pytest.fixture
def setup(tmp_path):
    bridge = ScoreBridge(tmp_path/"state.db",tmp_path/"artifacts")
    board = Board()
    bridge.initialize("run",score(),principal="conductor",org="default",targets={"auditor":"worker","reviewer":"independent"},credential_hash=board.identity)
    path = bridge.root/"report.json"
    path.write_text('{"audit":"test"}',encoding="utf-8")
    evidence={"report":{"path":"report.json","sha256":hashlib.sha256(path.read_bytes()).hexdigest()}}
    return bridge,board,evidence


def test_end_to_end_persisted_restart_review_gate(setup):
    b,g,e=setup
    work=b.plan("run")[0]
    b.dispatch("run",g)
    g.complete(work,{"artifacts":e})
    b=ScoreBridge(b.database,b.root)
    b.poll("run",g)
    assert b.status("run")["status"]=="running"
    review=b.plan("run")[0]
    b.dispatch("run",g)
    g.complete(review,{"verdict":"pass","review_of":e,"findings":[]})
    b=ScoreBridge(b.database,b.root)
    b.poll("run",g)
    assert b.status("run")["status"]=="accepted"
    assert b.plan("run")==[]
    assert b.dispatch("run",g)==[]


def test_lost_ack_retries_exact_id_after_restart(setup):
    b,g,e=setup
    job=b.plan("run")[0]
    g.lose_ack=True
    with pytest.raises(TimeoutError): b.dispatch("run",g)
    b=ScoreBridge(b.database,b.root)
    b.dispatch("run",g)
    assert list(g.jobs)==[job]
    assert g.creates==2
    assert b.status("run")["dispatches"][0]["status"]=="submitted"


def test_concurrent_planners_create_one_intent(setup):
    b,g,e=setup
    with ThreadPoolExecutor(4) as pool:
        results=list(pool.map(lambda _:ScoreBridge(b.database,b.root).plan("run"),range(4)))
    assert sum(map(len,results))==1


@pytest.mark.parametrize("mutation",[
    lambda s:s.update(budget_cents=1),
    lambda s:s["tasks"][0].update(capabilities=["cloud:change"]),
    lambda s:s["tasks"][0].update(max_cost_cents=100),
])
def test_authority_rejected(tmp_path,mutation):
    s=score();mutation(s)
    b=ScoreBridge(tmp_path/"state.db",tmp_path/"artifacts")
    with pytest.raises(ScoreError):
        b.initialize("r",s,principal="c",org="default",targets={"auditor":"a","reviewer":"b"},credential_hash="hash")


def test_checkpointed_task_initializes(tmp_path):
    s = score()
    s["tasks"][0].update(checkpoint={"id": "pause_1", "reason": "human inspection"})
    b = ScoreBridge(tmp_path / "state.db", tmp_path / "artifacts")
    b.initialize("r", s, principal="c", org="default", targets={"auditor": "a", "reviewer": "b"}, credential_hash="hash")
    status = b.status("r")
    assert status["status"] == "running"


def test_changed_identity_rejected_before_network(setup):
    b,g,e=setup
    b.plan("run")
    g.identity="changed"
    with pytest.raises(ScoreError):b.dispatch("run",g)
    assert not g.jobs


def test_changed_definition_rejected(setup):
    b,g,e=setup
    s=score();s["objective"]="different"
    with pytest.raises(ScoreError):
        b.initialize("run",s,principal="conductor",org="default",targets={"auditor":"worker","reviewer":"independent"},credential_hash=g.identity)


def test_old_gateway_fail_closed(setup):
    b,g,e=setup
    b.plan("run")
    g.capabilities=lambda:{}
    with pytest.raises(ScoreError):b.dispatch("run",g)
    assert not g.jobs


@pytest.mark.parametrize("mode",["wrong_actor","wrong_event","missing_file","hash_changed","outside_root","missing_evidence","wrong_tenant"])
def test_untrusted_completion_does_not_advance(setup,mode):
    b,g,e=setup
    job=b.plan("run")[0];b.dispatch("run",g)
    g.complete(job,{"artifacts":e})
    if mode=="wrong_actor":g.jobs[job]["leased_by_instance_id"]="attacker"
    if mode=="wrong_event":g.events=lambda _: []
    if mode=="missing_file":(b.root/"report.json").unlink()
    if mode=="hash_changed":(b.root/"report.json").write_text("changed")
    if mode=="outside_root":
        e["report"]["path"]="../outside.json";g.complete(job,{"artifacts":e})
    if mode=="missing_evidence":g.complete(job,{"artifacts":{}})
    if mode=="wrong_tenant":g.jobs[job]["org_id"]="other"
    with pytest.raises(ScoreError):b.poll("run",g)
    assert b.plan("run")==[]


@pytest.mark.parametrize("verdict",["fail","wrong_hash"])
def test_failed_or_unbound_review_cannot_accept(setup,verdict):
    b,g,e=setup
    work=b.plan("run")[0];b.dispatch("run",g);g.complete(work,{"artifacts":e});b.poll("run",g)
    review=b.plan("run")[0];b.dispatch("run",g)
    result={"verdict":"fail" if verdict=="fail" else "pass","review_of":e if verdict=="fail" else {},"findings":["issue"]}
    g.complete(review,result)
    if verdict=="wrong_hash":
        with pytest.raises(ScoreError):b.poll("run",g)
    else:
        b.poll("run",g)
        assert b.status("run")["status"]=="blocked"
    assert b.status("run")["status"]!="accepted"


def test_completed_without_review_not_acceptance(setup):
    b,g,e=setup
    job=b.plan("run")[0];b.dispatch("run",g);g.complete(job,{"artifacts":e});b.poll("run",g)
    assert b.status("run")["status"]=="running"


def test_self_review_routing_rejected(tmp_path):
    b=ScoreBridge(tmp_path/"db",tmp_path/"artifacts")
    with pytest.raises(ScoreError):
        b.initialize("r",score(),principal="c",org="default",targets={"auditor":"same","reviewer":"same"},credential_hash="hash")


def make_fix_score(num_fixes=1):
    s = score()
    tasks = [s["tasks"][0]]
    curr_id = "audit"
    for i in range(1, num_fixes + 1):
        fix_id = f"fix_{i}"
        tasks[-1]["on_reject"] = fix_id
        fix_task = dict(
            id=fix_id,
            goal=f"G0{i+1}",
            title=f"Fix {i}",
            instructions=f"Fix attempt {i}",
            role="auditor",
            review_role="reviewer",
            depends_on=[curr_id],
            resources=["audit"],
            capabilities=["cloud:inspect"],
            evidence=["report"],
            max_attempts=1,
            timeout_seconds=300,
            max_cost_cents=0,
            checkpoint=None,
        )
        tasks.append(fix_task)
        curr_id = fix_id
    s["tasks"] = tasks
    s["launch_requires"] = [curr_id]
    return s


def test_rejected_task_without_on_reject_blocks_run(setup):
    b, g, e = setup
    work = b.plan("run")[0]
    b.dispatch("run", g)
    g.complete(work, {"artifacts": e})
    b.poll("run", g)
    review = b.plan("run")[0]
    b.dispatch("run", g)
    g.complete(review, {"verdict": "fail", "review_of": e, "findings": ["bad proof"]})
    b.poll("run", g)
    assert b.status("run")["status"] == "blocked"
    assert b.plan("run") == []


def test_rejected_task_with_on_reject_dispatches_fix_with_findings(tmp_path):
    s = make_fix_score(1)
    b = ScoreBridge(tmp_path / "state.db", tmp_path / "artifacts")
    g = Board()
    b.initialize("run", s, principal="conductor", org="default", targets={"auditor": "worker", "reviewer": ["rev1", "rev2"]}, credential_hash=g.identity)
    path = b.root / "report.json"
    path.write_text('{"audit":"test"}', encoding="utf-8")
    e = {"report": {"path": "report.json", "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}}

    # Initial task work
    work = b.plan("run")[0]
    b.dispatch("run", g)
    g.complete(work, {"artifacts": e})
    b.poll("run", g)

    # Initial task review fails
    review = b.plan("run")[0]
    b.dispatch("run", g)
    assert g.get(review)["target_agent_id"] == "rev1"
    g.complete(review, {"verdict": "fail", "review_of": e, "findings": ["missing section B"]})
    b.poll("run", g)

    # Run is NOT blocked; fix_attempt_ready was emitted
    status = b.status("run")
    assert status["status"] == "running"
    events = [ev["event"] for ev in status["events"]]
    assert "fix_attempt_ready" in events

    # Fix attempt work task is planned with rejection findings
    fix_jobs = b.plan("run")
    assert len(fix_jobs) == 1
    fix_work_id = fix_jobs[0]
    b.dispatch("run", g)
    job_payload = g.get(fix_work_id)
    assert job_payload["title"] == "Score run fix_1 work"
    assert job_payload["input_payload"]["score"]["rejection_findings"] == ["missing section B"]
    assert "Rejection findings to fix:" in job_payload["input_payload"]["prompt"]
    assert "missing section B" in job_payload["input_payload"]["prompt"]

    # Fix work completes
    g.complete(fix_work_id, {"artifacts": e})
    b.poll("run", g)
    assert b.status("run")["status"] == "running"


def test_fix_attempt_terminal_rejection_blocks_run(tmp_path):
    s = make_fix_score(1)
    b = ScoreBridge(tmp_path / "state.db", tmp_path / "artifacts")
    g = Board()
    b.initialize("run", s, principal="conductor", org="default", targets={"auditor": "worker", "reviewer": ["rev1", "rev2"]}, credential_hash=g.identity)
    path = b.root / "report.json"
    path.write_text('{"audit":"test"}', encoding="utf-8")
    e = {"report": {"path": "report.json", "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}}

    # Original work + failed review
    work = b.plan("run")[0]
    b.dispatch("run", g)
    g.complete(work, {"artifacts": e})
    b.poll("run", g)
    review = b.plan("run")[0]
    b.dispatch("run", g)
    g.complete(review, {"verdict": "fail", "review_of": e, "findings": ["fail 1"]})
    b.poll("run", g)

    # Fix 1 work completes
    fix_work = b.plan("run")[0]
    b.dispatch("run", g)
    g.complete(fix_work, {"artifacts": e})
    b.poll("run", g)

    # Fix 1 review also fails (and fix_1 has no on_reject)
    fix_review = b.plan("run")[0]
    b.dispatch("run", g)
    g.complete(fix_review, {"verdict": "fail", "review_of": e, "findings": ["fail 2"]})
    b.poll("run", g)

    # Run is now permanently blocked
    assert b.status("run")["status"] == "blocked"
    assert b.plan("run") == []


def test_chain_of_3_rejections_then_4th_exhausts_bound(tmp_path):
    # Create a score with 4 fix attempts (5 tasks total: audit, fix_1, fix_2, fix_3, fix_4)
    # The policy bound is 3 automatic fix-attempts total before terminal block
    s = make_fix_score(4)
    b = ScoreBridge(tmp_path / "state.db", tmp_path / "artifacts")
    g = Board()
    reviewers = ["rev1", "rev2", "rev3", "rev4", "rev5"]
    b.initialize("run", s, principal="conductor", org="default", targets={"auditor": "worker", "reviewer": reviewers}, credential_hash=g.identity)
    path = b.root / "report.json"
    path.write_text('{"audit":"test"}', encoding="utf-8")
    e = {"report": {"path": "report.json", "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}}

    # Attempt 0: audit work + rejected review (1st rejection)
    w0 = b.plan("run")[0]; b.dispatch("run", g); g.complete(w0, {"artifacts": e}); b.poll("run", g)
    r0 = b.plan("run")[0]; b.dispatch("run", g); g.complete(r0, {"verdict": "fail", "review_of": e, "findings": ["f0"]}); b.poll("run", g)
    assert b.status("run")["status"] == "running"

    # Attempt 1: fix_1 work + rejected review (2nd rejection)
    w1 = b.plan("run")[0]; b.dispatch("run", g); g.complete(w1, {"artifacts": e}); b.poll("run", g)
    r1 = b.plan("run")[0]; b.dispatch("run", g); g.complete(r1, {"verdict": "fail", "review_of": e, "findings": ["f1"]}); b.poll("run", g)
    assert b.status("run")["status"] == "running"

    # Attempt 2: fix_2 work + rejected review (3rd rejection)
    w2 = b.plan("run")[0]; b.dispatch("run", g); g.complete(w2, {"artifacts": e}); b.poll("run", g)
    r2 = b.plan("run")[0]; b.dispatch("run", g); g.complete(r2, {"verdict": "fail", "review_of": e, "findings": ["f2"]}); b.poll("run", g)
    assert b.status("run")["status"] == "running"

    # Attempt 3: fix_3 work + rejected review (4th rejection -> bound exhausted!)
    w3 = b.plan("run")[0]; b.dispatch("run", g); g.complete(w3, {"artifacts": e}); b.poll("run", g)
    r3 = b.plan("run")[0]; b.dispatch("run", g); g.complete(r3, {"verdict": "fail", "review_of": e, "findings": ["f3"]}); b.poll("run", g)

    # Terminal block! fix_4 is never dispatched even though fix_3 has on_reject = "fix_4"
    assert b.status("run")["status"] == "blocked"
    assert b.plan("run") == []


def test_reviewer_diversity_in_fix_lineage(tmp_path):
    s = make_fix_score(2)
    b = ScoreBridge(tmp_path / "state.db", tmp_path / "artifacts")
    g = Board()
    reviewers = ["rev1", "rev2", "rev3"]
    b.initialize("run", s, principal="conductor", org="default", targets={"auditor": "worker", "reviewer": reviewers}, credential_hash=g.identity)
    path = b.root / "report.json"
    path.write_text('{"audit":"test"}', encoding="utf-8")
    e = {"report": {"path": "report.json", "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}}

    # Attempt 0 review assigned to rev1
    w0 = b.plan("run")[0]; b.dispatch("run", g); g.complete(w0, {"artifacts": e}); b.poll("run", g)
    r0 = b.plan("run")[0]
    b.dispatch("run", g)
    assert g.get(r0)["target_agent_id"] == "rev1"
    g.complete(r0, {"verdict": "fail", "review_of": e, "findings": ["f0"]}); b.poll("run", g)

    # Attempt 1 review CANNOT be rev1 (must be rev2)
    w1 = b.plan("run")[0]; b.dispatch("run", g); g.complete(w1, {"artifacts": e}); b.poll("run", g)
    r1 = b.plan("run")[0]
    b.dispatch("run", g)
    assert g.get(r1)["target_agent_id"] == "rev2"
    g.complete(r1, {"verdict": "fail", "review_of": e, "findings": ["f1"]}); b.poll("run", g)

    # Attempt 2 review CANNOT be rev1 or rev2 (must be rev3)
    w2 = b.plan("run")[0]; b.dispatch("run", g); g.complete(w2, {"artifacts": e}); b.poll("run", g)
    r2 = b.plan("run")[0]
    b.dispatch("run", g)
    assert g.get(r2)["target_agent_id"] == "rev3"
    g.complete(r2, {"verdict": "pass", "review_of": e, "findings": []}); b.poll("run", g)

    # Run is accepted!
    assert b.status("run")["status"] == "accepted"


def test_fix_attempt_no_eligible_reviewer_waits_on_gate(tmp_path):
    s = make_fix_score(1)
    b = ScoreBridge(tmp_path / "state.db", tmp_path / "artifacts")
    g = Board()
    # Only one reviewer identity configured
    b.initialize("run", s, principal="conductor", org="default", targets={"auditor": "worker", "reviewer": "rev1"}, credential_hash=g.identity)
    path = b.root / "report.json"
    path.write_text('{"audit":"test"}', encoding="utf-8")
    e = {"report": {"path": "report.json", "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}}

    # Attempt 0: rev1 rejects
    w0 = b.plan("run")[0]; b.dispatch("run", g); g.complete(w0, {"artifacts": e}); b.poll("run", g)
    r0 = b.plan("run")[0]; b.dispatch("run", g); g.complete(r0, {"verdict": "fail", "review_of": e, "findings": ["f0"]}); b.poll("run", g)

    # Fix 1 work completes
    w1 = b.plan("run")[0]; b.dispatch("run", g); g.complete(w1, {"artifacts": e}); b.poll("run", g)

    # Fix 1 review: rev1 was the rejector, worker is the author -> no eligible reviewer!
    created = b.plan("run")
    assert created == []
    status = b.status("run")
    # Does NOT block the run and does NOT fall back
    assert status["status"] == "waiting_on_gate"
    assert status["gated"] is True
    waiting_dispatches = [d for d in status["dispatches"] if d["status"] == "waiting_on_gate" and d["phase"] == "review"]
    assert len(waiting_dispatches) == 1
    assert waiting_dispatches[0]["task"] == "fix_1"


def test_on_reject_cycle_fails_at_score_load_before_conductor(tmp_path):
    s = make_fix_score(1)
    # Create cycle: fix_1.on_reject = audit
    s["tasks"][1]["on_reject"] = "audit"
    b = ScoreBridge(tmp_path / "state.db", tmp_path / "artifacts")
    with pytest.raises(ScoreError, match="on_reject cycle"):
        b.initialize("run", s, principal="conductor", org="default", targets={"auditor": "worker", "reviewer": "rev1"}, credential_hash="hash")


def _make_test_git_worktree(tmp_path):
    repo_dir = tmp_path / "main_repo"
    repo_dir.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test Committer"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "committer@test.local"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=repo_dir, check=True, capture_output=True)

    (repo_dir / "README.md").write_text("# Main repo\n")
    subprocess.run(["git", "add", "README.md"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "chore: initial commit"], cwd=repo_dir, check=True, capture_output=True)

    target_branch = "codex/score-v1-repo-write"
    wt_dir = tmp_path / "worktree"
    subprocess.run(
        ["git", "worktree", "add", "-b", target_branch, str(wt_dir), "main"],
        cwd=repo_dir,
        check=True,
        capture_output=True,
    )
    subprocess.run(["git", "config", "user.name", "Test Committer"], cwd=wt_dir, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "committer@test.local"], cwd=wt_dir, check=True, capture_output=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=wt_dir, check=True, capture_output=True)

    proc_head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=wt_dir, check=True, capture_output=True, text=True)
    initial_sha = proc_head.stdout.strip()
    return wt_dir, target_branch, initial_sha


def test_default_bridge_without_live_executor_rejects_repo_write(tmp_path):
    s = score()
    s["tasks"][0]["capabilities"] = ["repository:write"]
    s["tasks"][0]["resources"] = ["/tmp/repo"]
    s["tasks"][0]["commit"] = {
        "worktree_path": "/tmp/repo",
        "target_branch": "codex/test-branch",
        "allowed_paths": ["file.txt"],
    }
    b = ScoreBridge(tmp_path / "state.db", tmp_path / "artifacts")
    with pytest.raises(ScoreError, match="Only read-only audit authority supported"):
        b.initialize("run", s, principal="conductor", org="default", targets={"auditor": "worker", "reviewer": "independent"}, credential_hash="cred")


def test_repo_write_without_worktree_in_resources_refused_at_initialize(tmp_path):
    s = score()
    s["tasks"][0]["capabilities"] = ["repository:write"]
    s["tasks"][0]["resources"] = ["other_resource"]
    s["tasks"][0]["commit"] = {
        "worktree_path": "/tmp/repo",
        "target_branch": "codex/test-branch",
        "allowed_paths": ["file.txt"],
    }
    mock_executor = object()
    b = ScoreBridge(tmp_path / "state.db", tmp_path / "artifacts", live_executor=mock_executor)
    with pytest.raises(ScoreError, match="must include worktree_path in resources"):
        b.initialize("run", s, principal="conductor", org="default", targets={"auditor": "worker", "reviewer": "independent"}, credential_hash="cred")


def test_two_tasks_sharing_worktree_must_declare_resource_and_serialize(tmp_path):
    s = score()
    t1 = s["tasks"][0]
    t1["id"] = "task1"
    t1["capabilities"] = ["repository:write"]
    t1["resources"] = ["/tmp/repo"]
    t1["commit"] = {
        "worktree_path": "/tmp/repo",
        "target_branch": "codex/branch1",
        "allowed_paths": ["file1.txt"],
    }
    t2 = copy.deepcopy(t1)
    t2["id"] = "task2"
    t2["resources"] = ["other_lock"]
    t2["commit"] = {
        "worktree_path": "/tmp/repo",
        "target_branch": "codex/branch2",
        "allowed_paths": ["file2.txt"],
    }
    s["tasks"] = [t1, t2]
    s["launch_requires"] = ["task1", "task2"]

    mock_executor = object()
    b = ScoreBridge(tmp_path / "state.db", tmp_path / "artifacts", live_executor=mock_executor)
    with pytest.raises(ScoreError, match="must include worktree_path in resources"):
        b.initialize("run", s, principal="conductor", org="default", targets={"auditor": "worker", "reviewer": "independent"}, credential_hash="cred")

    t2["resources"] = ["/tmp/repo"]
    board = Board()
    b.initialize("run", s, principal="conductor", org="default", targets={"auditor": "worker", "reviewer": "independent"}, credential_hash=board.identity)
    planned = b.plan("run")
    assert len(planned) == 1


def test_repo_write_full_e2e_real_worktree_commit(tmp_path):
    wt_dir, target_branch, initial_sha = _make_test_git_worktree(tmp_path)

    key = b"s05-test-key-material-is-long-enough-0001"
    now_dt = datetime(2026, 9, 16, tzinfo=timezone.utc)
    db_store = LocalStore(tmp_path / "live_score.db")
    grant_svc = GrantService(db_store, verification_key=key)
    executor = LiveScoreAdapterExecutor(
        db=db_store,
        grant_service=grant_svc,
        now=lambda: now_dt,
    )

    s = score()
    s["id"] = "repo-write-run"
    s["tasks"][0]["id"] = "write_code"
    s["tasks"][0]["capabilities"] = ["repository:write"]
    s["tasks"][0]["resources"] = [str(wt_dir)]
    s["tasks"][0]["commit"] = {
        "worktree_path": str(wt_dir),
        "target_branch": target_branch,
        "allowed_paths": ["src/*"],
        "commit_message": "feat: add application core",
        "expected_before_sha": initial_sha,
    }
    s["tasks"][0]["evidence"] = ["commit_sha"]
    s["launch_requires"] = ["write_code"]

    run_digest = digest(s)
    grant_svc.issue({
        "org_id": "default",
        "run_id": "repo-write-run",
        "digest": run_digest,
        "actions": ["repository:write"],
        "resources": [str(wt_dir)],
        "env": "test",
        "not_before": "2026-09-15T00:00:00Z",
        "expires_at": "2026-10-01T00:00:00Z",
        "budget_cents": 0,
        "human_principal": "conductor",
    })

    b = ScoreBridge(tmp_path / "bridge.db", tmp_path / "artifacts", live_executor=executor)
    board = Board()
    b.initialize("repo-write-run", s, principal="conductor", org="default", targets={"auditor": "worker", "reviewer": "independent"}, credential_hash=board.identity)

    # 1. Plan work dispatch
    planned = b.plan("repo-write-run")
    assert len(planned) == 1
    work_id = planned[0]
    b.dispatch("repo-write-run", board)

    # Worker modifies files in the worktree
    src_dir = wt_dir / "src"
    src_dir.mkdir(parents=True, exist_ok=True)
    (src_dir / "app.py").write_text("def run(): return 'via-ok'\n", encoding="utf-8")

    # Worker completes and signals ready; worker's fake sha claim MUST be ignored!
    board.complete(work_id, {"ready": True, "commit_sha": "fake_sha_worker_claims"})

    # 2. Poll processes work completion, runs adapter commit, updates to validated
    b.poll("repo-write-run", board)
    st = b.status("repo-write-run")
    work_disp = [d for d in st["dispatches"] if d["phase"] == "work"][0]
    assert work_disp["status"] == "validated"

    # Verify a REAL commit was created in the REAL git worktree
    proc = subprocess.run(["git", "rev-parse", "HEAD"], cwd=wt_dir, check=True, capture_output=True, text=True)
    real_commit_sha = proc.stdout.strip()
    assert real_commit_sha != initial_sha
    # Verify commit log contains Score metadata
    proc_log = subprocess.run(["git", "log", "-1", "--pretty=%B"], cwd=wt_dir, check=True, capture_output=True, text=True)
    assert "Score-Run: repo-write-run" in proc_log.stdout
    assert "Score-Task: write_code" in proc_log.stdout

    # 3. Plan review dispatch
    rev_planned = b.plan("repo-write-run")
    assert len(rev_planned) == 1
    rev_id = rev_planned[0]
    b.dispatch("repo-write-run", board)

    rev_job = board.get(rev_id)
    rev_contract = rev_job["input_payload"]["score"]
    # Verify review_of is bound to adapter's actual commit sha
    assert rev_contract["review_of"] == {"commit_sha": real_commit_sha}

    # Reviewer submits verdict pass
    board.complete(rev_id, {
        "verdict": "pass",
        "review_of": {"commit_sha": real_commit_sha},
        "findings": ["Commit SHA and tests verified independently"],
    })

    # 4. Poll completes review, accepts score run
    b.poll("repo-write-run", board)
    final_status = b.status("repo-write-run")
    assert final_status["status"] == "accepted"


def test_repo_write_adapter_raising_head_mismatch_fails_and_blocks_run(tmp_path):
    wt_dir, target_branch, initial_sha = _make_test_git_worktree(tmp_path)

    key = b"s05-test-key-material-is-long-enough-0001"
    now_dt = datetime(2026, 9, 16, tzinfo=timezone.utc)
    db_store = LocalStore(tmp_path / "live_score.db")
    grant_svc = GrantService(db_store, verification_key=key)
    executor = LiveScoreAdapterExecutor(
        db=db_store,
        grant_service=grant_svc,
        now=lambda: now_dt,
    )

    s = score()
    s["id"] = "drift-run"
    s["tasks"][0]["id"] = "drift_task"
    s["tasks"][0]["capabilities"] = ["repository:write"]
    s["tasks"][0]["resources"] = [str(wt_dir)]
    s["tasks"][0]["commit"] = {
        "worktree_path": str(wt_dir),
        "target_branch": target_branch,
        "allowed_paths": ["src/*"],
        "commit_message": "feat: uncommitted drift test",
        "expected_before_sha": initial_sha,
    }
    s["tasks"][0]["evidence"] = ["commit_sha"]
    s["launch_requires"] = ["drift_task"]

    run_digest = digest(s)
    grant_svc.issue({
        "org_id": "default",
        "run_id": "drift-run",
        "digest": run_digest,
        "actions": ["repository:write"],
        "resources": [str(wt_dir)],
        "env": "test",
        "not_before": "2026-09-15T00:00:00Z",
        "expires_at": "2026-10-01T00:00:00Z",
        "budget_cents": 0,
        "human_principal": "conductor",
    })

    b = ScoreBridge(tmp_path / "bridge.db", tmp_path / "artifacts", live_executor=executor)
    board = Board()
    b.initialize("drift-run", s, principal="conductor", org="default", targets={"auditor": "worker", "reviewer": "independent"}, credential_hash=board.identity)

    planned = b.plan("drift-run")
    assert len(planned) == 1
    work_id = planned[0]
    b.dispatch("drift-run", board)

    # Worker writes changes
    src_dir = wt_dir / "src"
    src_dir.mkdir(parents=True, exist_ok=True)
    (src_dir / "app.py").write_text("def run(): pass\n", encoding="utf-8")

    # SIMULATE DRIFT: An uncoordinated commit lands in the worktree, changing HEAD!
    (wt_dir / "unrelated.txt").write_text("drift\n", encoding="utf-8")
    subprocess.run(["git", "add", "unrelated.txt"], cwd=wt_dir, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "chore: unexpected drift"], cwd=wt_dir, check=True, capture_output=True)

    # Worker signals ready
    board.complete(work_id, {"ready": True})

    # Poll fails adapter commit due to exact HEAD mismatch
    with pytest.raises(ScoreError, match="exact_head_mismatch|Live adapter execution failed"):
        b.poll("drift-run", board)

    # Verify run is BLOCKED and dispatch is FAILED
    st = b.status("drift-run")
    assert st["status"] == "blocked"
    work_disp = [d for d in st["dispatches"] if d["phase"] == "work"][0]
    assert work_disp["status"] == "failed"


