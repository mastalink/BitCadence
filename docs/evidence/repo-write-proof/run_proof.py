"""Master runner executing all 7 S06b adversarial injections for repository:write."""
import datetime
import hashlib
import json
import os
import secrets
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

# Paths
WT = Path("C:/AI/baton/wt/score-repo-write-s06b").resolve()
S6 = Path("C:/AI/baton/wt/scratch-s06b").resolve()
PROOF_DIR = WT / "docs" / "evidence" / "repo-write-proof"
LOGS_DIR = PROOF_DIR / "logs"
LOGS_DIR.mkdir(parents=True, exist_ok=True)

THROWAWAY_REPO = S6 / "throwaway-repo"
THROWAWAY_WT = S6 / "throwaway-wt"
TARGET_BRANCH = "canary/s06b-target"
PORT = "18997"
GATEWAY_URL = f"http://127.0.0.1:{PORT}"
ADMIN_TOKEN = "a1b2c3d4e5f60718293a4b5c6d7e8f90"
GRANT_KEY = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"

# Set base environment
os.environ["S6"] = str(S6)
os.environ["WT"] = str(WT)
os.environ["HOME"] = str(S6 / "home")
os.environ["USERPROFILE"] = str(S6 / "home")
os.environ["HOMEDRIVE"] = str(S6 / "home")[:2]
os.environ["HOMEPATH"] = str(S6 / "home")[2:]
os.environ["MCO_ENV_FILE"] = str(S6 / "home" / ".mco" / ".env")
os.environ["PYTHONPATH"] = str(WT / "src")
os.environ["MCO_SCORE_DB"] = str(S6 / "home" / ".mco" / "score-runs.db")
os.environ["MCO_SCORE_ARTIFACT_ROOT"] = str(S6 / "home" / ".mco" / "score-artifacts")
os.environ["MCO_SCORE_SWEEP_SECONDS"] = "2"
os.environ["MCO_DELIVERY_STALL_SECONDS"] = "0"
os.environ["MCO_LEASE_TTL_SECONDS"] = "10"
os.environ["NTFY_TOPIC"] = ""
os.environ["MCO_GATEWAY_URL"] = GATEWAY_URL
os.environ["PORT"] = PORT
os.environ["MCO_LOCAL_TOKEN"] = ADMIN_TOKEN
os.environ["MCO_AGENT_TOKEN"] = ADMIN_TOKEN
os.environ["AGENT_INSTANCE_ID"] = "local-operator"
os.environ["AGENT_ROLE"] = "admin"
os.environ["MCO_SCORE_GRANT_KEY"] = GRANT_KEY
os.environ["THROWAWAY_REPO"] = str(THROWAWAY_REPO)
os.environ["THROWAWAY_WT"] = str(THROWAWAY_WT)

gateway_proc = None
AGENT_TOKENS = {}

def log(msg):
    ts = datetime.datetime.now(datetime.timezone.utc).isoformat()
    print(f"[{ts}] {msg}", flush=True)

def cleanup_test_state():
    sys.path.insert(0, str(WT / "src"))
    from mco.localstore import get_local_store
    store = get_local_store()
    try:
        store._conn.execute("DELETE FROM agent_jobs")
        store._conn.execute("DELETE FROM score_grants")
        store._conn.execute("DELETE FROM score_recovery")
        store._conn.commit()
    except Exception as e:
        log(f"Cleanup error (non-fatal): {e}")
    score_db_path = Path(os.environ.get("MCO_SCORE_DB", ""))
    if score_db_path.exists():
        try:
            with sqlite3.connect(score_db_path) as sdb:
                sdb.execute("DELETE FROM runs")
                sdb.execute("DELETE FROM dispatch")
                sdb.execute("DELETE FROM events")
                sdb.commit()
        except Exception as e:
            log(f"Score DB cleanup error (non-fatal): {e}")

def setup_test_agents():
    sys.path.insert(0, str(WT / "src"))
    from mco.localstore import get_local_store
    store = get_local_store()
    agents = [
        ("worker-1", "canary-worker-repo-write"),
        ("reviewer-1", "canary-review-repo-write"),
    ]
    for instance_id, role in agents:
        token = f"mco_tok_{instance_id}_{secrets.token_hex(16)}"
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        store.table("agent_registry").upsert({
            "instance_id": instance_id,
            "role": role,
            "status": "offline",
            "auth_token_hash": token_hash,
        }).execute()
        AGENT_TOKENS[instance_id] = token
        log(f"Configured agent {instance_id} ({role})")

def start_gateway(tag="main"):
    global gateway_proc
    log(f"Starting isolated gateway (tag: {tag}) on port {PORT}...")
    gw_log = open(S6 / "logs" / f"gw-{tag}.log", "w", encoding="utf-8")
    cmd = [sys.executable, str(PROOF_DIR / "launch_gateway.py")]
    gateway_proc = subprocess.Popen(cmd, stdout=gw_log, stderr=subprocess.STDOUT, env=os.environ.copy())
    (S6 / "logs" / "gw.pid").write_text(str(gateway_proc.pid), encoding="utf-8")
    
    import urllib.request
    ready = False
    for i in range(30):
        time.sleep(1)
        try:
            req = urllib.request.Request(f"{GATEWAY_URL}/readyz")
            with urllib.request.urlopen(req, timeout=2) as resp:
                if resp.status == 200:
                    ready = True
                    break
        except Exception:
            pass
    if not ready:
        raise RuntimeError(f"Gateway failed to become ready within 30s. Check {S6}/logs/gw-{tag}.log")
    log(f"Gateway ready! PID: {gateway_proc.pid}")

def stop_gateway():
    global gateway_proc
    if gateway_proc is not None:
        log(f"Stopping gateway (PID: {gateway_proc.pid})...")
        gateway_proc.terminate()
        try:
            gateway_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            log("Force killing gateway process...")
            gateway_proc.kill()
        gateway_proc = None
        time.sleep(2)
        log("Gateway stopped.")

def kill_gateway_hard():
    global gateway_proc
    if gateway_proc is not None:
        pid = gateway_proc.pid
        log(f"HARD KILLING gateway process (PID: {pid})...")
        subprocess.run(["powershell.exe", "-NoProfile", "-Command", f"Stop-Process -Id {pid} -Force -ErrorAction SilentlyContinue"])
        gateway_proc = None
        time.sleep(1)
        log("Gateway hard-killed.")

def reset_worktree():
    log("Resetting throwaway worktree to clean HEAD...")
    subprocess.run(["git", "checkout", TARGET_BRANCH], cwd=str(THROWAWAY_WT), check=True, capture_output=True)
    subprocess.run(["git", "reset", "--hard", "71b5e04"], cwd=str(THROWAWAY_WT), check=True, capture_output=True)
    subprocess.run(["git", "clean", "-fd"], cwd=str(THROWAWAY_WT), check=True, capture_output=True)

def observe(run_id, output_path=None):
    cmd = [sys.executable, str(PROOF_DIR / "observe.py"), run_id]
    res = subprocess.run(cmd, capture_output=True, text=True, env=os.environ.copy())
    if res.returncode != 0:
        log(f"Observe error: {res.stderr}")
        return None
    data = json.loads(res.stdout)
    if output_path:
        Path(output_path).write_text(json.dumps(data, indent=2), encoding="utf-8")
    return data

def run_cli_score_start(score_file, run_id, worker_id="worker-1", reviewer_id="reviewer-1"):
    cmd = [
        sys.executable, str(PROOF_DIR / "harness_cli.py"), "score", "start",
        str(score_file),
        "--run-id", run_id,
        "--target", f"canary-worker-repo-write={worker_id}",
        "--target", f"canary-review-repo-write={reviewer_id}",
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, cwd=str(WT), env=os.environ.copy())
    return res

def wait_for_run_state(run_id, target_states, timeout_seconds=30):
    log(f"Waiting for run {run_id} to reach {target_states} (timeout: {timeout_seconds}s)...")
    start = time.time()
    while time.time() - start < timeout_seconds:
        obs = observe(run_id)
        if obs and obs.get("run"):
            status = obs["run"]["status"]
            if status in target_states:
                log(f"Run {run_id} reached state: {status}")
                return status, obs
        time.sleep(1)
    obs = observe(run_id)
    cur = obs["run"]["status"] if obs and obs.get("run") else "unknown"
    log(f"Timeout waiting for {run_id}! Current status: {cur}")
    return cur, obs

def test_01_baseline():
    log("\n=== INJECTION 1: BASELINE CANARY RUN ===")
    cleanup_test_state()
    reset_worktree()
    run_id = "s06b-01-baseline"
    score_file = S6 / "scores" / "canary-01.json"
    
    # 1. Create score and issue grant
    res = subprocess.run([
        sys.executable, str(PROOF_DIR / "grant_and_score.py"),
        "--run-id", run_id,
        "--worktree", str(THROWAWAY_WT),
        "--branch", TARGET_BRANCH,
        "--output", str(score_file),
    ], capture_output=True, text=True, env=os.environ.copy())
    assert res.returncode == 0, f"Failed to issue grant: {res.stderr}"

    # 2. Start score run
    res = run_cli_score_start(score_file, run_id, "worker-1", "reviewer-1")
    assert res.returncode == 0, f"mco score start failed: {res.stdout}\n{res.stderr}"
    log("Score run started.")

    # 3. Wait for work job dispatch
    time.sleep(2)

    # 4. Worker executes
    w_res = subprocess.run([
        sys.executable, str(PROOF_DIR / "repo_write_worker.py"),
        "--role", "canary-worker-repo-write",
        "--instance", "worker-1",
        "--token", AGENT_TOKENS["worker-1"],
        "--gateway", GATEWAY_URL,
        "--worktree", str(THROWAWAY_WT),
        "--run-id", run_id,
        "--mode", "normal",
        "--timeout", "20",
    ], capture_output=True, text=True, env=os.environ.copy())
    log(f"Worker stdout:\n{w_res.stdout.strip()}")
    assert w_res.returncode == 0, f"Worker failed: {w_res.stderr}"

    # 5. Wait for conductor adapter commit and review dispatch
    time.sleep(4)

    # 6. Reviewer executes
    r_res = subprocess.run([
        sys.executable, str(PROOF_DIR / "repo_write_reviewer.py"),
        "--role", "canary-review-repo-write",
        "--instance", "reviewer-1",
        "--token", AGENT_TOKENS["reviewer-1"],
        "--gateway", GATEWAY_URL,
        "--worktree", str(THROWAWAY_WT),
        "--run-id", run_id,
        "--mode", "pass",
        "--timeout", "20",
    ], capture_output=True, text=True, env=os.environ.copy())
    log(f"Reviewer stdout:\n{r_res.stdout.strip()}")
    assert r_res.returncode == 0, f"Reviewer failed: {r_res.stderr}"

    # 7. Wait for run to settle at accepted
    final_status, obs = wait_for_run_state(run_id, ["accepted"], timeout_seconds=15)
    assert final_status == "accepted", f"Run did not accept: {final_status}"

    evidence_file = LOGS_DIR / "01-baseline.json"
    observe(run_id, evidence_file)
    log(f"Baseline passed. Evidence saved to {evidence_file}")
    return True

def test_02_worker_crash():
    log("\n=== INJECTION 2: WORKER CRASH MID-TASK ===")
    cleanup_test_state()
    reset_worktree()
    run_id = "s06b-02-crash"
    score_file = S6 / "scores" / "canary-02.json"

    res = subprocess.run([
        sys.executable, str(PROOF_DIR / "grant_and_score.py"),
        "--run-id", run_id,
        "--worktree", str(THROWAWAY_WT),
        "--branch", TARGET_BRANCH,
        "--output", str(score_file),
    ], capture_output=True, text=True, env=os.environ.copy())
    assert res.returncode == 0, f"Failed to issue grant: {res.stderr}"

    res = run_cli_score_start(score_file, run_id, "worker-1", "reviewer-1")
    assert res.returncode == 0, f"mco score start failed: {res.stdout}\n{res.stderr}"

    time.sleep(2)

    # 1. Crashing worker process: leases then hard exits 137
    log("Launching crashing worker process...")
    w_crash = subprocess.run([
        sys.executable, str(PROOF_DIR / "repo_write_worker.py"),
        "--role", "canary-worker-repo-write",
        "--instance", "worker-1",
        "--token", AGENT_TOKENS["worker-1"],
        "--gateway", GATEWAY_URL,
        "--worktree", str(THROWAWAY_WT),
        "--run-id", run_id,
        "--mode", "crash",
        "--timeout", "20",
    ], capture_output=True, text=True, env=os.environ.copy())
    log(f"Crashing worker exited with returncode: {w_crash.returncode} (expected 137)")
    assert w_crash.returncode == 137, f"Expected 137 exit code, got {w_crash.returncode}"

    # Verify no commit was made while worker was crashed
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=str(THROWAWAY_WT), text=True).strip()
    assert head.startswith("71b5e04"), f"Unexpected commit during crash: {head}"
    log("Verified no commit was made during dead worker state.")

    # 2. Wait for lease TTL (10s) to expire so gateway reaps it
    log("Waiting 12s for lease TTL expiration and reaper...")
    time.sleep(12)

    # 3. Replacement worker process restarts as worker-1, leases the reclaimed job and completes
    log("Launching replacement worker process...")
    w_rep = subprocess.run([
        sys.executable, str(PROOF_DIR / "repo_write_worker.py"),
        "--role", "canary-worker-repo-write",
        "--instance", "worker-1",
        "--token", AGENT_TOKENS["worker-1"],
        "--gateway", GATEWAY_URL,
        "--worktree", str(THROWAWAY_WT),
        "--run-id", run_id,
        "--mode", "normal",
        "--timeout", "20",
    ], capture_output=True, text=True, env=os.environ.copy())
    log(f"Replacement worker output:\n{w_rep.stdout.strip()}")
    assert w_rep.returncode == 0, f"Replacement worker failed: {w_rep.stderr}"

    time.sleep(4)

    # 4. Reviewer executes
    r_res = subprocess.run([
        sys.executable, str(PROOF_DIR / "repo_write_reviewer.py"),
        "--role", "canary-review-repo-write",
        "--instance", "reviewer-1",
        "--token", AGENT_TOKENS["reviewer-1"],
        "--gateway", GATEWAY_URL,
        "--worktree", str(THROWAWAY_WT),
        "--run-id", run_id,
        "--mode", "pass",
        "--timeout", "20",
    ], capture_output=True, text=True, env=os.environ.copy())
    assert r_res.returncode == 0, f"Reviewer failed: {r_res.stderr}"

    final_status, obs = wait_for_run_state(run_id, ["accepted"], timeout_seconds=15)
    assert final_status == "accepted", f"Run did not accept after crash recovery: {final_status}"

    evidence_file = LOGS_DIR / "02-crash.json"
    observe(run_id, evidence_file)
    log(f"Crash injection passed. Evidence saved to {evidence_file}")
    return True

def test_03_denied_branch():
    log("\n=== INJECTION 3: ATTEMPT TO TARGET DENIED BRANCH (main) ===")
    cleanup_test_state()
    run_id = "s06b-03-denied-branch"
    score_file = S6 / "scores" / "canary-03.json"

    res = subprocess.run([
        sys.executable, str(PROOF_DIR / "grant_and_score.py"),
        "--run-id", run_id,
        "--worktree", str(THROWAWAY_WT),
        "--branch", "main",
        "--output", str(score_file),
    ], capture_output=True, text=True, env=os.environ.copy())
    assert res.returncode == 0, f"Failed to issue grant: {res.stderr}"

    log("Attempting to start score run targeting denied branch 'main'...")
    cli_res = run_cli_score_start(score_file, run_id)
    combined_out = cli_res.stdout + "\n" + cli_res.stderr
    log(f"CLI result (code {cli_res.returncode}):\n{combined_out.strip()}")

    assert cli_res.returncode != 0, "CLI score start unexpectedly succeeded on denied branch!"
    assert "denied_target_branch:main" in combined_out or "Denied target branch" in combined_out, (
        f"Missing denied branch error in CLI output: {combined_out}"
    )

    db = sqlite3.connect(os.environ["MCO_SCORE_DB"])
    count = db.execute("SELECT count(*) FROM runs WHERE id=?", (run_id,)).fetchone()[0]
    db.close()
    assert count == 0, f"Found {count} runs for {run_id} in database! Expected 0."

    main_sha = subprocess.check_output(["git", "rev-parse", "main"], cwd=str(THROWAWAY_REPO), text=True).strip()
    assert main_sha.startswith("71b5e04"), f"Main branch head modified: {main_sha}"

    (LOGS_DIR / "03-denied-branch.txt").write_text(combined_out, encoding="utf-8")
    log("Denied branch injection passed: refused at initialize(), 0 git commands run.")
    return True

def test_04_exact_head_drift():
    log("\n=== INJECTION 4: EXACT-HEAD DRIFT ===")
    cleanup_test_state()
    reset_worktree()
    run_id = "s06b-04-exact-head-drift"
    score_file = S6 / "scores" / "canary-04.json"

    res = subprocess.run([
        sys.executable, str(PROOF_DIR / "grant_and_score.py"),
        "--run-id", run_id,
        "--worktree", str(THROWAWAY_WT),
        "--branch", TARGET_BRANCH,
        "--output", str(score_file),
    ], capture_output=True, text=True, env=os.environ.copy())
    assert res.returncode == 0, f"Failed to issue grant: {res.stderr}"

    res = run_cli_score_start(score_file, run_id, "worker-1", "reviewer-1")
    assert res.returncode == 0, f"mco score start failed: {res.stdout}\n{res.stderr}"

    time.sleep(2)

    log("Launching worker with drift injection...")
    w_drift = subprocess.run([
        sys.executable, str(PROOF_DIR / "repo_write_worker.py"),
        "--role", "canary-worker-repo-write",
        "--instance", "worker-1",
        "--token", AGENT_TOKENS["worker-1"],
        "--gateway", GATEWAY_URL,
        "--worktree", str(THROWAWAY_WT),
        "--run-id", run_id,
        "--mode", "drift",
        "--timeout", "20",
    ], capture_output=True, text=True, env=os.environ.copy())
    log(f"Drift worker output:\n{w_drift.stdout.strip()}")
    assert w_drift.returncode == 0, f"Drift worker failed: {w_drift.stderr}"

    final_status, obs = wait_for_run_state(run_id, ["blocked"], timeout_seconds=15)
    assert final_status == "blocked", f"Run did not block on exact head drift: {final_status}"

    evidence_file = LOGS_DIR / "04-exact-head-drift.json"
    data = observe(run_id, evidence_file)
    work_dispatch = next((d for d in data["dispatch"] if d["phase"] == "work"), None)
    assert work_dispatch and work_dispatch["status"] == "failed", f"Unexpected dispatch status: {work_dispatch}"
    
    events = [e.get("detail") for e in data.get("conductor_events", [])]
    assert any("exact_head_mismatch" in str(e) for e in events), f"Missing exact_head_mismatch in events: {events}"

    log("Exact-head drift injection passed: conductor blocked run on head mismatch.")
    return True

def test_05_rejected_review():
    log("\n=== INJECTION 5: REJECTED REVIEW ===")
    cleanup_test_state()
    reset_worktree()
    run_id = "s06b-05-rejected-review"
    score_file = S6 / "scores" / "canary-05.json"

    res = subprocess.run([
        sys.executable, str(PROOF_DIR / "grant_and_score.py"),
        "--run-id", run_id,
        "--worktree", str(THROWAWAY_WT),
        "--branch", TARGET_BRANCH,
        "--output", str(score_file),
    ], capture_output=True, text=True, env=os.environ.copy())
    assert res.returncode == 0, f"Failed to issue grant: {res.stderr}"

    res = run_cli_score_start(score_file, run_id, "worker-1", "reviewer-1")
    assert res.returncode == 0, f"mco score start failed: {res.stdout}\n{res.stderr}"

    time.sleep(2)

    w_res = subprocess.run([
        sys.executable, str(PROOF_DIR / "repo_write_worker.py"),
        "--role", "canary-worker-repo-write",
        "--instance", "worker-1",
        "--token", AGENT_TOKENS["worker-1"],
        "--gateway", GATEWAY_URL,
        "--worktree", str(THROWAWAY_WT),
        "--run-id", run_id,
        "--mode", "normal",
        "--timeout", "20",
    ], capture_output=True, text=True, env=os.environ.copy())
    assert w_res.returncode == 0, f"Worker failed: {w_res.stderr}"

    time.sleep(4)

    log("Launching reviewer in FAIL mode...")
    r_res = subprocess.run([
        sys.executable, str(PROOF_DIR / "repo_write_reviewer.py"),
        "--role", "canary-review-repo-write",
        "--instance", "reviewer-1",
        "--token", AGENT_TOKENS["reviewer-1"],
        "--gateway", GATEWAY_URL,
        "--worktree", str(THROWAWAY_WT),
        "--run-id", run_id,
        "--mode", "fail",
        "--timeout", "20",
    ], capture_output=True, text=True, env=os.environ.copy())
    log(f"Reviewer output:\n{r_res.stdout.strip()}")
    assert r_res.returncode == 0, f"Reviewer failed: {r_res.stderr}"

    final_status, obs = wait_for_run_state(run_id, ["blocked"], timeout_seconds=15)
    assert final_status == "blocked", f"Run did not block on failed review: {final_status}"

    log("Verifying run remains blocked and no retry occurs...")
    time.sleep(5)
    obs = observe(run_id)
    assert obs["run"]["status"] == "blocked", f"Run status changed from blocked: {obs['run']['status']}"
    review_dispatch = next((d for d in obs["dispatch"] if d["phase"] == "review"), None)
    assert review_dispatch and review_dispatch["status"] == "rejected", f"Review dispatch not rejected: {review_dispatch}"

    evidence_file = LOGS_DIR / "05-rejected-review.json"
    observe(run_id, evidence_file)
    log("Rejected review injection passed: dispatch rejected, run stays blocked, no silent retry.")
    return True

def test_06_gateway_restart():
    log("\n=== INJECTION 6: GATEWAY RESTART MID-COMMIT & B03 ATOMIC CLAIM RECOVERY ===")
    gw_started_here = False
    if gateway_proc is None:
        setup_test_agents()
        start_gateway("boot-06")
        gw_started_here = True
    try:
        cleanup_test_state()
        reset_worktree()
        run_id = "s06b-06-gateway-restart"
        score_file = S6 / "scores" / "canary-06.json"

        res = subprocess.run([
            sys.executable, str(PROOF_DIR / "grant_and_score.py"),
            "--run-id", run_id,
            "--worktree", str(THROWAWAY_WT),
            "--branch", TARGET_BRANCH,
            "--output", str(score_file),
        ], capture_output=True, text=True, env=os.environ.copy())
        assert res.returncode == 0, f"Failed to issue grant: {res.stderr}"

        res = run_cli_score_start(score_file, run_id, "worker-1", "reviewer-1")
        assert res.returncode == 0, f"mco score start failed: {res.stdout}\n{res.stderr}"

        time.sleep(2)

        w_res = subprocess.run([
            sys.executable, str(PROOF_DIR / "repo_write_worker.py"),
            "--role", "canary-worker-repo-write",
            "--instance", "worker-1",
            "--token", AGENT_TOKENS["worker-1"],
            "--gateway", GATEWAY_URL,
            "--worktree", str(THROWAWAY_WT),
            "--run-id", run_id,
            "--mode", "normal",
            "--timeout", "20",
        ], capture_output=True, text=True, env=os.environ.copy())
        assert w_res.returncode == 0, f"Worker failed: {w_res.stderr}"

        log("Simulating process kill: Hard-killing gateway process immediately after worker completion...")
        kill_gateway_hard()

        log("Restarting gateway to prove clean recovery without double-commit...")
        start_gateway(tag="restart-06")

        time.sleep(5)

        r_res = subprocess.run([
            sys.executable, str(PROOF_DIR / "repo_write_reviewer.py"),
            "--role", "canary-review-repo-write",
            "--instance", "reviewer-1",
            "--token", AGENT_TOKENS["reviewer-1"],
            "--gateway", GATEWAY_URL,
            "--worktree", str(THROWAWAY_WT),
            "--run-id", run_id,
            "--mode", "pass",
            "--timeout", "20",
        ], capture_output=True, text=True, env=os.environ.copy())
        assert r_res.returncode == 0, f"Reviewer failed: {r_res.stderr}"

        final_status, obs = wait_for_run_state(run_id, ["accepted"], timeout_seconds=15)
        assert final_status == "accepted", f"Run did not accept after gateway restart: {final_status}"

        log("Testing B03 atomic claim isolation: pending claim must refuse double-execution...")
        from mco.orchestrator.score_adapters import Operation
        from mco.orchestrator.score_adapters_live import LiveScoreAdapterExecutor, LiveRecoveryRequired
        from mco.localstore import get_local_store
        from mco.orchestrator.score_authority import GrantService, configured_grant_key

        store = get_local_store()
        svc = GrantService(store, verification_key=configured_grant_key())
        executor = LiveScoreAdapterExecutor(db=store, grant_service=svc)

        grant_rows = store.table("score_grants").select("digest").eq("run_id", run_id).execute().data
        grant_digest = grant_rows[0]["digest"] if grant_rows else obs["run"]["digest"]

        claim_id = f"{run_id}:crash-claim-pending"
        test_op = Operation(
            org_id="default",
            run_id=run_id,
            digest=grant_digest,
            task_id="write-task-1",
            attempt=2,
            adapter="repository-worktree-commit",
            action="repository:write",
            resource=str(THROWAWAY_WT),
            environment="test",
            owner_principal="local-operator",
            desired_state={
                "worktree_path": str(THROWAWAY_WT),
                "target_branch": TARGET_BRANCH,
                "allowed_paths": ["src/*"],
                "commit_message": "test",
                "expected_before_sha": "71b5e04d8292a43eb071dd3451d0f911ca5611bb",
            },
            cost_cents=0,
            dry_run=False,
        )
        store.table("score_recovery").insert({
            "approval_or_attempt_id": claim_id,
            "org_id": "default",
            "uncertain_effect": {
                "operation": {
                    "run_id": run_id,
                    "task_id": "write-task-1",
                    "adapter": "repository-worktree-commit",
                    "action": "repository:write",
                    "resource": str(THROWAWAY_WT),
                },
                "detail": "simulated in-flight crash claim",
            },
            "decision": "pending",
        }).execute()

        recovery_refused = False
        try:
            executor.execute(test_op)
        except LiveRecoveryRequired:
            recovery_refused = True
        assert recovery_refused, "Pending claim did not prevent re-execution!"
        log("Verified: B03 pending claim blocks re-execution (no double commit, no lost claim).")

        store.table("score_recovery").delete().eq("approval_or_attempt_id", claim_id).execute()

        evidence_file = LOGS_DIR / "06-gateway-restart.json"
        observe(run_id, evidence_file)
        log("Gateway restart injection passed.")
        return True
    finally:
        if gw_started_here:
            stop_gateway()

def test_07_resource_lock():
    log("\n=== INJECTION 7: CONCURRENT ACCESS WITHOUT DECLARING RESOURCE LOCK ===")
    cleanup_test_state()
    run_id = "s06b-07-resource-lock"
    score_file = S6 / "scores" / "canary-07.json"

    res = subprocess.run([
        sys.executable, str(PROOF_DIR / "grant_and_score.py"),
        "--run-id", run_id,
        "--worktree", str(THROWAWAY_WT),
        "--branch", TARGET_BRANCH,
        "--output", str(score_file),
        "--omit-resource",
    ], capture_output=True, text=True, env=os.environ.copy())
    assert res.returncode == 0, f"Failed to issue grant: {res.stderr}"

    log("Attempting to start score run without resource lock...")
    cli_res = run_cli_score_start(score_file, run_id)
    combined_out = cli_res.stdout + "\n" + cli_res.stderr
    log(f"CLI result (code {cli_res.returncode}):\n{combined_out.strip()}")

    assert cli_res.returncode != 0, "CLI score start unexpectedly succeeded when omitting resource lock!"
    clean_out = " ".join(combined_out.split())
    assert "must include worktree_path in resources" in clean_out, (
        f"Missing resource lock error message in CLI output: {combined_out}"
    )

    db = sqlite3.connect(os.environ["MCO_SCORE_DB"])
    count = db.execute("SELECT count(*) FROM runs WHERE id=?", (run_id,)).fetchone()[0]
    db.close()
    assert count == 0, f"Found {count} runs in database! Expected 0."

    (LOGS_DIR / "07-resource-lock.txt").write_text(combined_out, encoding="utf-8")
    log("Resource lock injection passed: refused at initialize(), 0 runs created.")
    return True

def generate_inventory():
    log("\nGenerating 00-run-inventory.txt...")
    lines = [
        "S06b Repository:Write Adversarial Proof Run Inventory",
        f"Generated: {datetime.datetime.now(datetime.timezone.utc).isoformat()}",
        "=" * 70,
        "",
    ]
    files = sorted(LOGS_DIR.glob("*"))
    for f in files:
        if f.is_file():
            sha = hashlib.sha256(f.read_bytes()).hexdigest()
            size = f.stat().st_size
            lines.append(f"{f.name:30} {size:8} bytes  sha256:{sha}")
    inv_file = PROOF_DIR / "00-run-inventory.txt"
    inv_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    log(f"Saved run inventory to {inv_file}")

def main():
    import argparse
    parser = argparse.ArgumentParser(description="S06b Adversarial Proof Runner")
    parser.add_argument("--test", "--injection", type=int, choices=[1, 2, 3, 4, 5, 6, 7], help="Run a single injection")
    args = parser.parse_args()

    log("Starting S06b Adversarial Proof Run...")
    score_db = Path(os.environ["MCO_SCORE_DB"])
    if score_db.exists() and not args.test:
        score_db.unlink()

    cleanup_test_state()
    setup_test_agents()
    results = {}
    tests = {
        1: ("1. Baseline", test_01_baseline),
        2: ("2. Worker crash mid-task", test_02_worker_crash),
        3: ("3. Denied branch target", test_03_denied_branch),
        4: ("4. Exact-head drift", test_04_exact_head_drift),
        5: ("5. Rejected review", test_05_rejected_review),
        6: ("6. Gateway restart mid-commit", test_06_gateway_restart),
        7: ("7. Omitted resource lock", test_07_resource_lock),
    }
    selected = [args.test] if args.test else list(tests.keys())
    try:
        start_gateway("boot")
        for num in selected:
            name, fn = tests[num]
            results[name] = fn()
    finally:
        stop_gateway()

    generate_inventory()

    log("\n=================== SUMMARY OF INJECTION RESULTS ===================")
    for name, passed in results.items():
        status_str = "PASSED" if passed else "FAILED"
        log(f"  {name:35}: {status_str}")
    log("====================================================================")

if __name__ == "__main__":
    main()
