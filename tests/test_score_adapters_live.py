"""Adversarial and functional test suite for governed Score live repository adapters (repository:write).

Verifies:
1. Denylist genuinely cannot be talked past (refuses BEFORE any git subprocess).
2. Exact-head mismatch is refused.
3. Files outside allowed_paths are never staged.
4. Empty allowed-path match (nothing to commit) is refused.
5. Happy path: real git worktree, real commit, real trailers in git log.
6. compensate() resets when safe, refuses/no-ops when worktree has moved past produced commit.
7. Grant requirement is enforced via GrantService.require(...).
"""
from __future__ import annotations

import copy
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

from mco.localstore import LocalStore
from mco.orchestrator.score_authority import GrantService, AuthorityError
from mco.orchestrator.score_adapters import (
    AdapterError,
    AdapterResult,
    EffectStatus,
    Operation,
    RecoveryRequired,
)
from mco.orchestrator.score_adapters_live import (
    GitWorktreeCommitAdapter,
    LiveAdapterError,
    LiveRecoveryRequired,
    LiveScoreAdapterExecutor,
    commit_worktree_changes,
    compensate_worktree_commit,
    is_denied_branch,
    matches_allowed_paths,
    verify_not_denied_branch,
)


NOW = datetime(2026, 9, 16, tzinfo=timezone.utc)
KEY = b"s05-test-key-material-is-long-enough-0001"
DIGEST = "a" * 64


@pytest.fixture
def store(tmp_path):
    value = LocalStore(tmp_path / "live_score.db")
    yield value
    value.close()


@pytest.fixture
def grants(store):
    service = GrantService(store, verification_key=KEY)
    service.issue({
        "org_id": "default",
        "run_id": "run-live-1",
        "digest": DIGEST,
        "actions": ["repository:write"],
        "resources": ["repo"],
        "env": "test",
        "not_before": "2026-09-15T00:00:00Z",
        "expires_at": "2026-10-01T00:00:00Z",
        "budget_cents": 0,
        "human_principal": "joseph",
    })
    return service


@pytest.fixture
def git_worktree(tmp_path):
    """Create a real disposable git repository and an isolated git worktree."""
    repo_dir = tmp_path / "main_repo"
    repo_dir.mkdir()

    # Initialize main repo on branch 'main'
    subprocess.run(["git", "init", "-b", "main"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test Committer"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "committer@test.local"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=repo_dir, check=True, capture_output=True)

    # Initial commit on main
    (repo_dir / "README.md").write_text("# Main repo\n")
    subprocess.run(["git", "add", "README.md"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "chore: initial commit on main"], cwd=repo_dir, check=True, capture_output=True)

    # Create worktree on safe target branch
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

    return {
        "repo_dir": repo_dir,
        "worktree_dir": wt_dir,
        "branch": target_branch,
        "initial_sha": initial_sha,
    }


# ==============================================================================
# DELIVERABLE 1: Denylist cannot be talked past (Adversarial)
# ==============================================================================

@pytest.mark.parametrize("denied_branch", [
    "main",
    "deploy/local",
    "release/2.0",
    "production",
    "MAIN",
    "Main",
    "DEPLOY/LOCAL",
    "Deploy/Local",
    "release/v1.0.0",
    "RELEASE/2.0",
    "production/stable",
    "PRODUCTION",
    "refs/heads/main",
    "refs/heads/deploy/local",
    "refs/heads/release/2.0",
    "refs/heads/production",
    "heads/main",
    "release",
    "RELEASE",
])
def test_denylist_refuses_before_any_subprocess_call(denied_branch, monkeypatch):
    """Prove with a spy/mock that subprocess.run is NEVER called for denied branches."""
    call_count = 0

    def spy_run(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        raise AssertionError(f"subprocess.run was called with {args} {kwargs} for denied branch {denied_branch}!")

    monkeypatch.setattr(subprocess, "run", spy_run)

    # 1. Direct verify_not_denied_branch
    with pytest.raises(LiveAdapterError) as exc_info:
        verify_not_denied_branch(denied_branch)
    assert "denied_target_branch" in str(exc_info.value)

    # 2. Direct GitWorktreeCommitAdapter.invoke
    adapter = GitWorktreeCommitAdapter()
    with pytest.raises(LiveAdapterError) as exc_info:
        adapter.invoke({
            "worktree_path": "/some/fake/path",
            "target_branch": denied_branch,
            "allowed_paths": ["src/*"],
            "commit_message": "sneak attempt",
            "expected_before_sha": "a" * 40,
        })
    assert "denied_target_branch" in str(exc_info.value)

    # 3. commit_worktree_changes standalone function
    with pytest.raises(LiveAdapterError) as exc_info:
        commit_worktree_changes(
            worktree_path="/some/fake/path",
            target_branch=denied_branch,
            allowed_paths=["src/*"],
            commit_message="sneak attempt",
            expected_before_sha="a" * 40,
        )
    assert "denied_target_branch" in str(exc_info.value)

    # 4. compensate_worktree_commit standalone function
    with pytest.raises(LiveAdapterError) as exc_info:
        compensate_worktree_commit(
            worktree_path="/some/fake/path",
            target_branch=denied_branch,
            expected_before_sha="a" * 40,
            produced_commit_sha="b" * 40,
        )
    assert "denied_target_branch" in str(exc_info.value)

    # Crucial assertion: assert subprocess.run was never invoked
    assert call_count == 0, f"Subprocess was called {call_count} times for denied branch {denied_branch}"


def test_denylist_refuses_in_live_executor_before_claim_or_command(store, grants, monkeypatch):
    """Prove LiveScoreAdapterExecutor refuses denied branch in _validate before claiming or running git."""
    call_count = 0

    def spy_run(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        raise AssertionError("subprocess.run must not be called!")

    monkeypatch.setattr(subprocess, "run", spy_run)

    executor = LiveScoreAdapterExecutor(store, grant_service=grants, now=lambda: NOW)
    op = Operation(
        org_id="default", run_id="run-live-1", digest=DIGEST, task_id="task-1",
        attempt=1, adapter="repository-worktree-commit", action="repository:write",
        resource="repo", environment="test", owner_principal="joseph",
        cost_cents=0, dry_run=False,
        desired_state={
            "worktree_path": "/some/fake/path",
            "target_branch": "main",  # literally main
            "allowed_paths": ["src/*"],
            "commit_message": "sneaking to main",
            "expected_before_sha": "a" * 40,
        },
    )

    with pytest.raises(LiveAdapterError) as exc_info:
        executor.execute(op)
    assert "denied_target_branch:main" in str(exc_info.value)
    assert call_count == 0


def test_worktree_checked_out_on_main_is_refused_even_if_descriptor_lies(git_worktree):
    """If task descriptor specifies a safe target_branch, but worktree is actually checked out on main, refuse!"""
    repo_dir = git_worktree["repo_dir"]  # main repo is checked out on 'main'
    adapter = GitWorktreeCommitAdapter()

    # Caller claims target_branch is 'feature/legit', but points worktree_path to repo_dir (on 'main')
    with pytest.raises(LiveAdapterError) as exc_info:
        adapter.invoke({
            "worktree_path": str(repo_dir),
            "target_branch": "feature/legit",
            "allowed_paths": ["*"],
            "commit_message": "trying to commit to main by lying",
            "expected_before_sha": git_worktree["initial_sha"],
        })
    # Either worktree_checked_out_on_denied_branch or branch_mismatch
    assert "denied_branch" in str(exc_info.value) or "branch_mismatch" in str(exc_info.value)


# ==============================================================================
# DELIVERABLE 2: Exact-head mismatch is refused
# ==============================================================================

def test_exact_head_mismatch_is_refused(git_worktree):
    wt_dir = git_worktree["worktree_dir"]
    branch = git_worktree["branch"]
    wrong_sha = "0123456789abcdef0123456789abcdef01234567"

    # Create a changed file matching allowed_paths
    (wt_dir / "src").mkdir(parents=True, exist_ok=True)
    (wt_dir / "src" / "code.py").write_text("print('hello')\n")

    adapter = GitWorktreeCommitAdapter()
    with pytest.raises(LiveAdapterError) as exc_info:
        adapter.invoke({
            "worktree_path": str(wt_dir),
            "target_branch": branch,
            "allowed_paths": ["src/*"],
            "commit_message": "feat: should fail due to head mismatch",
            "expected_before_sha": wrong_sha,
        })
    assert "exact_head_mismatch" in str(exc_info.value)

    # Verify no commit occurred
    proc = subprocess.run(["git", "rev-parse", "HEAD"], cwd=wt_dir, check=True, capture_output=True, text=True)
    assert proc.stdout.strip() == git_worktree["initial_sha"]


# ==============================================================================
# DELIVERABLE 3: Files outside allowed_paths are never staged
# ==============================================================================

def test_files_outside_allowed_paths_never_staged(git_worktree):
    wt_dir = git_worktree["worktree_dir"]
    branch = git_worktree["branch"]
    initial_sha = git_worktree["initial_sha"]

    # 1. Modify allowed file
    (wt_dir / "src").mkdir(parents=True, exist_ok=True)
    allowed_file = wt_dir / "src" / "service.py"
    allowed_file.write_text("SERVICE = True\n")

    # 2. Modify disallowed files
    (wt_dir / "secrets").mkdir(parents=True, exist_ok=True)
    forbidden_secret = wt_dir / "secrets" / "token.key"
    forbidden_secret.write_text("SUPER_SECRET_KEY\n")

    untracked_notes = wt_dir / "todo.txt"
    untracked_notes.write_text("untracked notes outside allowed_paths\n")

    # Run commit adapter allowing ONLY src/*
    adapter = GitWorktreeCommitAdapter()
    result = adapter.invoke({
        "worktree_path": str(wt_dir),
        "target_branch": branch,
        "allowed_paths": ["src/*"],
        "commit_message": "feat: allowed change only",
        "expected_before_sha": initial_sha,
        "run_id": "run-live-1",
        "task_id": "task-1",
        "digest": DIGEST,
    })

    assert result.status is EffectStatus.SUCCEEDED
    new_sha = result.observed_state["commit_sha"]
    assert new_sha != initial_sha

    # Check git show --name-only on the new commit: MUST ONLY contain src/service.py
    proc_diff = subprocess.run(
        ["git", "diff", "--name-only", f"{new_sha}~1", new_sha],
        cwd=wt_dir, check=True, capture_output=True, text=True,
    )
    committed_files = [f.strip() for f in proc_diff.stdout.splitlines() if f.strip()]
    assert committed_files == ["src/service.py"]

    # Check working tree: secrets/token.key and todo.txt MUST still exist and remain untracked
    proc_status = subprocess.run(
        ["git", "status", "--porcelain", "-uall"],
        cwd=wt_dir, check=True, capture_output=True, text=True,
    )
    status_output = proc_status.stdout
    assert "secrets/token.key" in status_output
    assert "todo.txt" in status_output
    assert forbidden_secret.read_text() == "SUPER_SECRET_KEY\n"
    assert untracked_notes.read_text() == "untracked notes outside allowed_paths\n"


# ==============================================================================
# DELIVERABLE 4: Empty allowed-path match (nothing to commit) is refused
# ==============================================================================

def test_empty_allowed_path_match_refused_when_changes_exist_outside(git_worktree):
    wt_dir = git_worktree["worktree_dir"]
    branch = git_worktree["branch"]
    initial_sha = git_worktree["initial_sha"]

    # Modify file outside allowed_paths
    (wt_dir / "docs").mkdir(parents=True, exist_ok=True)
    (wt_dir / "docs" / "readme.txt").write_text("some docs\n")

    adapter = GitWorktreeCommitAdapter()
    with pytest.raises(LiveAdapterError) as exc_info:
        adapter.invoke({
            "worktree_path": str(wt_dir),
            "target_branch": branch,
            "allowed_paths": ["src/*"],  # nothing in src/ changed!
            "commit_message": "feat: should refuse because nothing matches allowed_paths",
            "expected_before_sha": initial_sha,
        })
    assert "no_matching_changes_to_commit" in str(exc_info.value)

    # Confirm HEAD has not changed
    proc = subprocess.run(["git", "rev-parse", "HEAD"], cwd=wt_dir, check=True, capture_output=True, text=True)
    assert proc.stdout.strip() == initial_sha


def test_clean_worktree_nothing_to_commit_refused(git_worktree):
    wt_dir = git_worktree["worktree_dir"]
    branch = git_worktree["branch"]
    initial_sha = git_worktree["initial_sha"]

    adapter = GitWorktreeCommitAdapter()
    with pytest.raises(LiveAdapterError) as exc_info:
        adapter.invoke({
            "worktree_path": str(wt_dir),
            "target_branch": branch,
            "allowed_paths": ["src/*"],
            "commit_message": "feat: empty commit attempt",
            "expected_before_sha": initial_sha,
        })
    assert "no_matching_changes_to_commit" in str(exc_info.value)

    proc = subprocess.run(["git", "rev-parse", "HEAD"], cwd=wt_dir, check=True, capture_output=True, text=True)
    assert proc.stdout.strip() == initial_sha


# ==============================================================================
# DELIVERABLE 5: Happy path in REAL git worktree, verifying actual git history
# ==============================================================================

def test_happy_path_real_commit_with_authorizing_trailer(git_worktree):
    wt_dir = git_worktree["worktree_dir"]
    branch = git_worktree["branch"]
    initial_sha = git_worktree["initial_sha"]

    # Make real file changes
    (wt_dir / "src" / "app").mkdir(parents=True, exist_ok=True)
    file_path = wt_dir / "src" / "app" / "main.py"
    file_path.write_text("def run():\n    return 42\n")

    commit_msg = "feat(repo-write): implement isolated worktree commit"
    run_id = "run-score-live-99"
    task_id = "task-commit-adapter-1"
    digest = DIGEST

    adapter = GitWorktreeCommitAdapter()
    result = adapter.invoke({
        "worktree_path": str(wt_dir),
        "target_branch": branch,
        "allowed_paths": ["src/**"],
        "commit_message": commit_msg,
        "expected_before_sha": initial_sha,
        "run_id": run_id,
        "task_id": task_id,
        "digest": digest,
    })

    assert result.status is EffectStatus.SUCCEEDED
    new_sha = result.observed_state["commit_sha"]
    assert len(new_sha) == 40
    assert new_sha != initial_sha

    # Verify directly in real git history
    proc_head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=wt_dir, check=True, capture_output=True, text=True)
    assert proc_head.stdout.strip() == new_sha

    # Verify commit message and trailers in git log
    proc_log = subprocess.run(
        ["git", "log", "-1", "--format=%B"],
        cwd=wt_dir, check=True, capture_output=True, text=True,
    )
    commit_body = proc_log.stdout
    assert commit_msg in commit_body
    assert f"Score-Run: {run_id}" in commit_body
    assert f"Score-Task: {task_id}" in commit_body
    assert f"Score-Digest: {digest}" in commit_body

    # Verify file content at HEAD
    proc_show = subprocess.run(
        ["git", "show", f"{new_sha}:src/app/main.py"],
        cwd=wt_dir, check=True, capture_output=True, text=True,
    )
    assert "def run():\n    return 42\n" in proc_show.stdout


# ==============================================================================
# DELIVERABLE 6: Safe compensate() and refusal when worktree moved past
# ==============================================================================

def test_compensate_safe_reset_to_before_sha(git_worktree):
    wt_dir = git_worktree["worktree_dir"]
    branch = git_worktree["branch"]
    initial_sha = git_worktree["initial_sha"]

    # Make a commit
    (wt_dir / "src").mkdir(parents=True, exist_ok=True)
    (wt_dir / "src" / "item.py").write_text("ITEM = 1\n")

    adapter = GitWorktreeCommitAdapter()
    result = adapter.invoke({
        "worktree_path": str(wt_dir),
        "target_branch": branch,
        "allowed_paths": ["src/*"],
        "commit_message": "feat: item 1",
        "expected_before_sha": initial_sha,
        "run_id": "run-1",
        "task_id": "task-1",
        "digest": DIGEST,
    })
    new_sha = result.observed_state["commit_sha"]

    # Current HEAD is new_sha
    proc = subprocess.run(["git", "rev-parse", "HEAD"], cwd=wt_dir, check=True, capture_output=True, text=True)
    assert proc.stdout.strip() == new_sha

    # Compensate safely
    comp_result = adapter.compensate(
        {"worktree_path": str(wt_dir), "target_branch": branch},
        partial_effect=result.partial_effect,
    )
    assert comp_result.status is EffectStatus.SUCCEEDED
    assert comp_result.observed_state["head_sha"] == initial_sha

    # Verify worktree is back to initial_sha
    proc = subprocess.run(["git", "rev-parse", "HEAD"], cwd=wt_dir, check=True, capture_output=True, text=True)
    assert proc.stdout.strip() == initial_sha


def test_compensate_refuses_when_worktree_has_moved_past_produced_commit(git_worktree):
    wt_dir = git_worktree["worktree_dir"]
    branch = git_worktree["branch"]
    initial_sha = git_worktree["initial_sha"]

    # 1. Operation 1 makes commit S1
    (wt_dir / "src").mkdir(parents=True, exist_ok=True)
    (wt_dir / "src" / "first.py").write_text("FIRST = True\n")

    adapter = GitWorktreeCommitAdapter()
    result1 = adapter.invoke({
        "worktree_path": str(wt_dir),
        "target_branch": branch,
        "allowed_paths": ["src/*"],
        "commit_message": "feat: first commit S1",
        "expected_before_sha": initial_sha,
        "run_id": "run-1",
        "task_id": "task-1",
        "digest": DIGEST,
    })
    s1_sha = result1.observed_state["commit_sha"]

    # 2. Simulate a second commit landing on top of S1 before compensate() is called
    (wt_dir / "src" / "second.py").write_text("SECOND = True\n")
    subprocess.run(["git", "add", "src/second.py"], cwd=wt_dir, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "feat: second commit S2 by someone else"], cwd=wt_dir, check=True, capture_output=True)
    proc_s2 = subprocess.run(["git", "rev-parse", "HEAD"], cwd=wt_dir, check=True, capture_output=True, text=True)
    s2_sha = proc_s2.stdout.strip()
    assert s2_sha != s1_sha

    # 3. Now try to compensate Operation 1!
    # It must REFUSE because worktree has moved past S1 (S2 is a descendant of S1)
    with pytest.raises(LiveRecoveryRequired) as exc_info:
        adapter.compensate(
            {"worktree_path": str(wt_dir), "target_branch": branch},
            partial_effect=result1.partial_effect,
        )
    assert "worktree_has_moved_past_commit" in str(exc_info.value)

    # 4. Verify HEAD was NOT clobbered and is STILL S2!
    proc_head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=wt_dir, check=True, capture_output=True, text=True)
    assert proc_head.stdout.strip() == s2_sha


def test_compensate_idempotent_no_op_when_already_at_before_sha(git_worktree):
    wt_dir = git_worktree["worktree_dir"]
    branch = git_worktree["branch"]
    initial_sha = git_worktree["initial_sha"]

    adapter = GitWorktreeCommitAdapter()
    # If partial effect indicates expected_before_sha and HEAD is already at expected_before_sha
    result = adapter.compensate(
        {"worktree_path": str(wt_dir), "target_branch": branch},
        partial_effect={
            "worktree_path": str(wt_dir),
            "target_branch": branch,
            "expected_before_sha": initial_sha,
            "commit_sha": initial_sha,  # or already at before_sha
        },
    )
    assert result.status is EffectStatus.SUCCEEDED
    assert result.observed_state["head_sha"] == initial_sha


# ==============================================================================
# DELIVERABLE 7: Grant requirement is enforced
# ==============================================================================

def test_grant_requirement_missing_grant_refused(store, grants, git_worktree):
    wt_dir = git_worktree["worktree_dir"]
    branch = git_worktree["branch"]
    initial_sha = git_worktree["initial_sha"]

    executor = LiveScoreAdapterExecutor(store, grant_service=grants, now=lambda: NOW)

    # Operation with run_id that has NO issued grant
    op = Operation(
        org_id="default", run_id="unauthorized-run", digest=DIGEST, task_id="task-1",
        attempt=1, adapter="repository-worktree-commit", action="repository:write",
        resource="repo", environment="test", owner_principal="joseph",
        cost_cents=0, dry_run=False,
        desired_state={
            "worktree_path": str(wt_dir),
            "target_branch": branch,
            "allowed_paths": ["src/*"],
            "commit_message": "unauthorized commit",
            "expected_before_sha": initial_sha,
        },
    )

    with pytest.raises(AuthorityError) as exc_info:
        executor.execute(op)
    assert "issued_grant_not_found" in str(exc_info.value)

    # Confirm no commit occurred
    proc = subprocess.run(["git", "rev-parse", "HEAD"], cwd=wt_dir, check=True, capture_output=True, text=True)
    assert proc.stdout.strip() == initial_sha


def test_grant_requirement_lacking_write_action_refused(store, grants, git_worktree):
    wt_dir = git_worktree["worktree_dir"]
    branch = git_worktree["branch"]
    initial_sha = git_worktree["initial_sha"]

    # Issue grant with only read/prepare action, NOT repository:write
    grants.issue({
        "org_id": "default",
        "run_id": "run-read-only",
        "digest": DIGEST,
        "actions": ["repository:prepare"],  # missing repository:write!
        "resources": ["repo"],
        "env": "test",
        "not_before": "2026-09-15T00:00:00Z",
        "expires_at": "2026-10-01T00:00:00Z",
        "budget_cents": 0,
        "human_principal": "joseph",
    })

    executor = LiveScoreAdapterExecutor(store, grant_service=grants, now=lambda: NOW)
    op = Operation(
        org_id="default", run_id="run-read-only", digest=DIGEST, task_id="task-1",
        attempt=1, adapter="repository-worktree-commit", action="repository:write",
        resource="repo", environment="test", owner_principal="joseph",
        cost_cents=0, dry_run=False,
        desired_state={
            "worktree_path": str(wt_dir),
            "target_branch": branch,
            "allowed_paths": ["src/*"],
            "commit_message": "unauthorized write",
            "expected_before_sha": initial_sha,
        },
    )

    with pytest.raises(AuthorityError) as exc_info:
        executor.execute(op)
    assert "grant_does_not_authorize_action" in str(exc_info.value)

    proc = subprocess.run(["git", "rev-parse", "HEAD"], cwd=wt_dir, check=True, capture_output=True, text=True)
    assert proc.stdout.strip() == initial_sha


def test_grant_requirement_expired_grant_refused(store, grants, git_worktree):
    wt_dir = git_worktree["worktree_dir"]
    branch = git_worktree["branch"]
    initial_sha = git_worktree["initial_sha"]

    # Grant expired in the past
    grants.issue({
        "org_id": "default",
        "run_id": "run-expired",
        "digest": DIGEST,
        "actions": ["repository:write"],
        "resources": ["repo"],
        "env": "test",
        "not_before": "2026-09-01T00:00:00Z",
        "expires_at": "2026-09-10T00:00:00Z",  # before NOW (2026-09-16)
        "budget_cents": 0,
        "human_principal": "joseph",
    })

    executor = LiveScoreAdapterExecutor(store, grant_service=grants, now=lambda: NOW)
    op = Operation(
        org_id="default", run_id="run-expired", digest=DIGEST, task_id="task-1",
        attempt=1, adapter="repository-worktree-commit", action="repository:write",
        resource="repo", environment="test", owner_principal="joseph",
        cost_cents=0, dry_run=False,
        desired_state={
            "worktree_path": str(wt_dir),
            "target_branch": branch,
            "allowed_paths": ["src/*"],
            "commit_message": "expired write",
            "expected_before_sha": initial_sha,
        },
    )

    with pytest.raises(AuthorityError) as exc_info:
        executor.execute(op)
    assert "grant_expired" in str(exc_info.value)


def test_live_executor_full_lifecycle_with_valid_grant_and_receipts(store, grants, git_worktree):
    """Full lifecycle through LiveScoreAdapterExecutor: grant check, claim, commit, durable receipts."""
    wt_dir = git_worktree["worktree_dir"]
    branch = git_worktree["branch"]
    initial_sha = git_worktree["initial_sha"]

    (wt_dir / "src").mkdir(parents=True, exist_ok=True)
    (wt_dir / "src" / "feature.py").write_text("FEATURE = True\n")

    executor = LiveScoreAdapterExecutor(store, grant_service=grants, now=lambda: NOW)
    op = Operation(
        org_id="default", run_id="run-live-1", digest=DIGEST, task_id="task-lifecycle",
        attempt=1, adapter="repository-worktree-commit", action="repository:write",
        resource="repo", environment="test", owner_principal="joseph",
        cost_cents=0, dry_run=False,
        desired_state={
            "worktree_path": str(wt_dir),
            "target_branch": branch,
            "allowed_paths": ["src/*"],
            "commit_message": "feat: full lifecycle commit",
            "expected_before_sha": initial_sha,
        },
    )

    receipt = executor.execute(op)
    assert receipt["status"] == "succeeded"
    assert receipt["phase"] == "after"
    assert receipt["dry_run"] is False
    new_sha = receipt["observed_state"]["commit_sha"]
    assert new_sha != initial_sha

    # Verify durable events recorded in store
    events = store.table("score_events").select("*").eq("run_id", "run-live-1").execute().data
    phases = [e["payload"]["phase"] for e in events if e.get("payload")]
    assert "before" in phases
    assert "after" in phases
    for e in events:
        if e.get("payload"):
            assert e["payload"]["dry_run"] is False

    # Verify recovery claim was released on success
    recovery = store.table("score_recovery").select("*").eq("approval_or_attempt_id", op.id).execute().data
    assert len(recovery) == 0


def test_standalone_commit_worktree_changes_with_grant_service(grants, git_worktree):
    wt_dir = git_worktree["worktree_dir"]
    branch = git_worktree["branch"]
    initial_sha = git_worktree["initial_sha"]

    (wt_dir / "src").mkdir(parents=True, exist_ok=True)
    (wt_dir / "src" / "standalone.py").write_text("STANDALONE = True\n")

    new_sha = commit_worktree_changes(
        worktree_path=wt_dir,
        target_branch=branch,
        allowed_paths=["src/*"],
        commit_message="feat: standalone with grant",
        expected_before_sha=initial_sha,
        run_id="run-live-1",
        task_id="task-standalone",
        digest=DIGEST,
        grant_service=grants,
        org_id="default",
        action="repository:write",
        resource="repo",
        environment="test",
        owner_principal="joseph",
        now=NOW,
    )
    assert len(new_sha) == 40
    assert new_sha != initial_sha


# ==============================================================================
# ADDITIONAL ADVERSARIAL & BOUNDARY TESTS
# ==============================================================================

def test_invalid_target_branch_refused_without_subprocess(monkeypatch):
    call_count = 0

    def spy_run(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        raise AssertionError("subprocess.run must not be called!")

    monkeypatch.setattr(subprocess, "run", spy_run)

    for invalid in ["", "   ", None, 123]:
        with pytest.raises(LiveAdapterError):
            verify_not_denied_branch(invalid)
    assert call_count == 0


def test_worktree_checked_out_on_release_denied_branch_refused(tmp_path):
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test Committer"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "committer@test.local"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=repo_dir, check=True, capture_output=True)
    (repo_dir / "README.md").write_text("# Initial\n")
    subprocess.run(["git", "add", "README.md"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo_dir, check=True, capture_output=True)

    # Create worktree on denied branch release/2.0
    wt_dir = tmp_path / "wt_release"
    subprocess.run(
        ["git", "worktree", "add", "-b", "release/2.0", str(wt_dir), "main"],
        cwd=repo_dir, check=True, capture_output=True,
    )

    adapter = GitWorktreeCommitAdapter()
    with pytest.raises(LiveAdapterError) as exc_info:
        adapter.invoke({
            "worktree_path": str(wt_dir),
            "target_branch": "release/2.0",
            "allowed_paths": ["*"],
            "commit_message": "test",
            "expected_before_sha": "a" * 40,
        })
    assert "denied" in str(exc_info.value)


def test_worktree_not_a_git_directory_refused(tmp_path):
    fake_dir = tmp_path / "not_git"
    fake_dir.mkdir()

    adapter = GitWorktreeCommitAdapter()
    with pytest.raises(LiveAdapterError) as exc_info:
        adapter.invoke({
            "worktree_path": str(fake_dir),
            "target_branch": "feature/branch",
            "allowed_paths": ["src/*"],
            "commit_message": "test",
            "expected_before_sha": "a" * 40,
        })
    assert "worktree_missing_git_marker" in str(exc_info.value) or "not_found" in str(exc_info.value)


def test_worktree_subdirectory_refused(git_worktree):
    wt_dir = git_worktree["worktree_dir"]
    sub_dir = wt_dir / "src"
    sub_dir.mkdir(parents=True, exist_ok=True)

    adapter = GitWorktreeCommitAdapter()
    with pytest.raises(LiveAdapterError) as exc_info:
        adapter.invoke({
            "worktree_path": str(sub_dir),
            "target_branch": git_worktree["branch"],
            "allowed_paths": ["src/*"],
            "commit_message": "test",
            "expected_before_sha": git_worktree["initial_sha"],
        })
    assert "worktree_missing_git_marker" in str(exc_info.value) or "worktree_path_is_not_toplevel" in str(exc_info.value)


def test_compensate_diverged_head_refuses(git_worktree):
    wt_dir = git_worktree["worktree_dir"]
    branch = git_worktree["branch"]
    initial_sha = git_worktree["initial_sha"]

    (wt_dir / "src").mkdir(parents=True, exist_ok=True)
    (wt_dir / "src" / "test.py").write_text("TEST = True\n")

    adapter = GitWorktreeCommitAdapter()
    res = adapter.invoke({
        "worktree_path": str(wt_dir),
        "target_branch": branch,
        "allowed_paths": ["src/*"],
        "commit_message": "feat: commit to diverge from",
        "expected_before_sha": initial_sha,
        "run_id": "run-1",
        "task_id": "task-1",
        "digest": DIGEST,
    })
    s1_sha = res.observed_state["commit_sha"]

    # Now create a diverged branch from initial_sha
    subprocess.run(["git", "checkout", "-b", "diverged_branch", initial_sha], cwd=wt_dir, check=True, capture_output=True)
    (wt_dir / "diverged.txt").write_text("diverged\n")
    subprocess.run(["git", "add", "diverged.txt"], cwd=wt_dir, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "chore: diverged commit"], cwd=wt_dir, check=True, capture_output=True)

    # Now try to compensate with target_branch="diverged_branch" and produced_commit_sha=s1_sha
    with pytest.raises(LiveRecoveryRequired) as exc_info:
        adapter.compensate(
            {"worktree_path": str(wt_dir), "target_branch": "diverged_branch"},
            partial_effect={
                "worktree_path": str(wt_dir),
                "target_branch": "diverged_branch",
                "expected_before_sha": initial_sha,
                "commit_sha": s1_sha,
            },
        )
    assert "diverged" in str(exc_info.value)


def test_pre_staged_unauthorized_files_cleared_by_adapter(git_worktree):
    """If working tree index has pre-staged files outside allowed_paths, verify reset clears them."""
    wt_dir = git_worktree["worktree_dir"]
    branch = git_worktree["branch"]
    initial_sha = git_worktree["initial_sha"]

    # Attacker pre-stages forbidden file
    forbidden = wt_dir / "forbidden.txt"
    forbidden.write_text("danger")
    subprocess.run(["git", "add", "forbidden.txt"], cwd=wt_dir, check=True, capture_output=True)

    # Legitimate allowed file
    (wt_dir / "src").mkdir(parents=True, exist_ok=True)
    (wt_dir / "src" / "legit.py").write_text("LEGIT = True\n")

    adapter = GitWorktreeCommitAdapter()
    res = adapter.invoke({
        "worktree_path": str(wt_dir),
        "target_branch": branch,
        "allowed_paths": ["src/*"],
        "commit_message": "feat: legit only",
        "expected_before_sha": initial_sha,
        "run_id": "run-1",
        "task_id": "task-1",
        "digest": DIGEST,
    })

    new_sha = res.observed_state["commit_sha"]
    # Check that forbidden.txt was NOT in new commit
    proc = subprocess.run(["git", "diff", "--name-only", f"{new_sha}~1", new_sha], cwd=wt_dir, check=True, capture_output=True, text=True)
    committed = proc.stdout.splitlines()
    assert "forbidden.txt" not in committed
    assert "src/legit.py" in committed


def test_compensate_atomic_concurrency_race(store, grants, git_worktree):
    """B03 discipline: concurrent callers to compensate() must result in exactly one winner."""
    wt_dir = git_worktree["worktree_dir"]
    branch = git_worktree["branch"]
    initial_sha = git_worktree["initial_sha"]

    executor = LiveScoreAdapterExecutor(store, grant_service=grants, now=lambda: NOW)
    op = Operation(
        org_id="default", run_id="run-live-1", digest=DIGEST, task_id="task-race",
        attempt=1, adapter="repository-worktree-commit", action="repository:write",
        resource="repo", environment="test", owner_principal="joseph",
        cost_cents=0, dry_run=False,
        desired_state={
            "worktree_path": str(wt_dir),
            "target_branch": branch,
            "allowed_paths": ["src/*"],
            "commit_message": "feat: race test",
            "expected_before_sha": initial_sha,
        },
    )

    # Plant an uncertain effect in score_recovery
    store.table("score_recovery").insert({
        "approval_or_attempt_id": op.id,
        "org_id": op.org_id,
        "uncertain_effect": {
            "operation": copy.deepcopy(op.__dict__),
            "partial_effect": {
                "worktree_path": str(wt_dir),
                "target_branch": branch,
                "expected_before_sha": initial_sha,
                "commit_sha": initial_sha,
            },
            "compensation_supported": True,
        },
        "decision": "pending",
    }).execute()

    # First compensate succeeds
    comp1 = executor.compensate(op)
    assert comp1["status"] == "compensated"

    # Second compensate fails because row is no longer pending
    with pytest.raises(LiveRecoveryRequired) as exc_info:
        executor.compensate(op)
    assert "pending_uncertain_effect_not_found" in str(exc_info.value)


# ==============================================================================
# REGRESSION: allowed_paths precision and glob semantics
# ==============================================================================

def test_allowed_paths_bare_filename_does_not_match_in_subdirectory():
    """(a) matches_allowed_paths('subdir/README.md', ['README.md']) is False."""
    assert matches_allowed_paths("subdir/README.md", ["README.md"]) is False
    assert matches_allowed_paths("a/b/c/README.md", ["README.md"]) is False
    # But top-level README.md matches
    assert matches_allowed_paths("README.md", ["README.md"]) is True


def test_allowed_paths_relative_dir_glob_does_not_match_nested_prefix():
    """(b) matches_allowed_paths('src/tests/evil.py', ['tests/*.py']) is False."""
    assert matches_allowed_paths("src/tests/evil.py", ["tests/*.py"]) is False
    assert matches_allowed_paths("deep/nested/tests/evil.py", ["tests/*.py"]) is False
    # But direct tests/*.py matches
    assert matches_allowed_paths("tests/evil.py", ["tests/*.py"]) is True


def test_allowed_paths_explicit_globstar_matches_at_any_depth():
    """(c) Explicit **/README.md pattern still correctly matches at any depth."""
    assert matches_allowed_paths("subdir/README.md", ["**/README.md"]) is True
    assert matches_allowed_paths("a/b/c/README.md", ["**/README.md"]) is True
    assert matches_allowed_paths("README.md", ["**/README.md"]) is True
    # And other file names do not match
    assert matches_allowed_paths("subdir/OTHER.md", ["**/README.md"]) is False


def test_allowed_paths_top_level_pattern_matches_direct_children_only():
    """(d) Plain top-level pattern like 'src/*.py' still matches direct children of src/."""
    assert matches_allowed_paths("src/service.py", ["src/*.py"]) is True
    assert matches_allowed_paths("src/app.py", ["src/*.py"]) is True
    assert matches_allowed_paths("service.py", ["src/*.py"]) is False
    assert matches_allowed_paths("other/service.py", ["src/*.py"]) is False


def test_adapter_staging_refuses_bare_filename_in_subdirectory(git_worktree):
    """End-to-end adapter test: allowed_paths=['README.md'] refuses changes in subdir/README.md."""
    wt_dir = git_worktree["worktree_dir"]
    branch = git_worktree["branch"]
    initial_sha = git_worktree["initial_sha"]

    (wt_dir / "subdir").mkdir(parents=True, exist_ok=True)
    subdir_readme = wt_dir / "subdir" / "README.md"
    subdir_readme.write_text("evil nested readme\n")

    adapter = GitWorktreeCommitAdapter()
    with pytest.raises(LiveAdapterError) as exc_info:
        adapter.invoke({
            "worktree_path": str(wt_dir),
            "target_branch": branch,
            "allowed_paths": ["README.md"],
            "commit_message": "chore: should fail because subdir/README.md is not allowed",
            "expected_before_sha": initial_sha,
        })
    assert "no_matching_changes_to_commit" in str(exc_info.value)


def test_adapter_staging_allows_explicit_globstar_in_subdirectory(git_worktree):
    """End-to-end adapter test: allowed_paths=['**/README.md'] authorizes subdir/README.md."""
    wt_dir = git_worktree["worktree_dir"]
    branch = git_worktree["branch"]
    initial_sha = git_worktree["initial_sha"]

    (wt_dir / "nested" / "deep").mkdir(parents=True, exist_ok=True)
    nested_readme = wt_dir / "nested" / "deep" / "README.md"
    nested_readme.write_text("opt-in nested readme\n")

    adapter = GitWorktreeCommitAdapter()
    result = adapter.invoke({
        "worktree_path": str(wt_dir),
        "target_branch": branch,
        "allowed_paths": ["**/README.md"],
        "commit_message": "docs: allowed via explicit globstar",
        "expected_before_sha": initial_sha,
        "run_id": "run-globstar-1",
        "task_id": "task-globstar",
        "digest": DIGEST,
    })
    assert result.status is EffectStatus.SUCCEEDED
    new_sha = result.observed_state["commit_sha"]
    assert new_sha != initial_sha


