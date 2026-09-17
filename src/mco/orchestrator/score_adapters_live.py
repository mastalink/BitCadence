"""Governed Score live repository mutation adapters (repository:write).

Unlike S05's dry-run executor, this module implements governed, live git commit
operations inside an ALREADY-EXISTING git worktree with strict defense in depth:
1. Hardcoded target branch denylist (main, deploy/local, release/*, production/*)
   checked immediately before any git command is executed.
2. Exact worktree verification (.git resolution, not just path existence, plus
   current branch verification).
3. Exact-head verification against expected_before_sha.
4. Selective staging matching only allowed_paths globs; untracked/unmatched
   files remain untouched, and empty matches are refused.
5. Authorizing Score trailer appended to commit message.
6. Safe compensation/rollback verifying HEAD descendant status before reset.
7. Durable grant verification via GrantService.require(...).
"""
from __future__ import annotations

import copy
import fnmatch
import hashlib
import json
import os
from pathlib import Path
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Sequence

from mco.orchestrator.score_authority import GrantService, AuthorityError
from mco.orchestrator.score_adapters import (
    AdapterError,
    AdapterResult,
    AdapterSpec,
    AdapterWrapper,
    EffectStatus,
    Operation,
    RecoveryRequired,
    _canonical,
    _sha,
    _desired,
    _is_duplicate_claim_error,
)
from mco.orchestrator.scores import ScoreError


class LiveAdapterError(AdapterError):
    """Error raised when a live adapter operation is rejected or fails."""


class LiveRecoveryRequired(RecoveryRequired, LiveAdapterError):
    """An uncertain live effect requires inspection or compensation."""


# Hardcoded denylist: defense in depth against committing to protected branches.
# Case-insensitive. Checked BEFORE any git command runs.
DENIED_EXACT_BRANCHES = frozenset({
    "main",
    "deploy/local",
    "production",
    "release",
})

DENIED_BRANCH_PATTERNS = (
    "release/*",
    "production/*",
)


def is_denied_branch(branch: Any) -> bool:
    """Check if branch matches the hardcoded denylist (case-insensitive)."""
    if not isinstance(branch, str) or not branch.strip():
        return True
    cleaned = branch.strip().lower()
    ref_stripped = cleaned
    if ref_stripped.startswith("refs/heads/"):
        ref_stripped = ref_stripped[len("refs/heads/"):]
    elif ref_stripped.startswith("heads/"):
        ref_stripped = ref_stripped[len("heads/"):]

    for candidate in (cleaned, ref_stripped):
        if candidate in DENIED_EXACT_BRANCHES:
            return True
        for pattern in DENIED_BRANCH_PATTERNS:
            if fnmatch.fnmatch(candidate, pattern):
                return True
    return False


def verify_not_denied_branch(branch: Any) -> None:
    """Refuse immediately before any git command if target branch is denied."""
    if not isinstance(branch, str) or not branch.strip():
        raise LiveAdapterError("target_branch_must_be_non_empty_string")
    if is_denied_branch(branch):
        raise LiveAdapterError(f"denied_target_branch:{branch}")


def _run_git(args: Sequence[str], cwd: str | Path) -> subprocess.CompletedProcess[str]:
    """Execute git via subprocess with explicit arg list and explicit cwd.

    Never uses shell=True or ambient working directory.
    """
    cmd = ["git", *args]
    return subprocess.run(
        cmd,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=False,
    )


def verify_git_worktree(worktree_path: str | Path, target_branch: str) -> Path:
    """Verify that worktree_path is an existing real git worktree on target_branch.

    Returns the resolved worktree root Path.
    """
    if not isinstance(worktree_path, (str, Path)):
        raise LiveAdapterError("worktree_path_must_be_string_or_path")
    p = Path(worktree_path).resolve()
    if not p.is_dir():
        raise LiveAdapterError(f"worktree_path_not_found:{worktree_path}")

    git_marker = p / ".git"
    if not git_marker.exists():
        raise LiveAdapterError(f"worktree_missing_git_marker:{worktree_path}")

    # Check git rev-parse --git-dir
    proc_gitdir = _run_git(["rev-parse", "--git-dir"], cwd=p)
    if proc_gitdir.returncode != 0:
        raise LiveAdapterError(f"not_a_valid_git_worktree:{worktree_path}: {proc_gitdir.stderr.strip()}")

    # Check git rev-parse --show-toplevel
    proc_top = _run_git(["rev-parse", "--show-toplevel"], cwd=p)
    if proc_top.returncode != 0:
        raise LiveAdapterError(f"cannot_resolve_toplevel:{worktree_path}: {proc_top.stderr.strip()}")
    top_path = Path(proc_top.stdout.strip()).resolve()
    if os.path.normcase(str(top_path)) != os.path.normcase(str(p)):
        raise LiveAdapterError(f"worktree_path_is_not_toplevel:{worktree_path}")

    # Check current branch of worktree
    proc_branch = _run_git(["symbolic-ref", "--short", "HEAD"], cwd=p)
    if proc_branch.returncode != 0:
        raise LiveAdapterError(f"worktree_detached_or_unborn_head:{worktree_path}")
    current_branch = proc_branch.stdout.strip()

    # Denylist check on actual branch (defense in depth!)
    if is_denied_branch(current_branch):
        raise LiveAdapterError(f"worktree_checked_out_on_denied_branch:{current_branch}")

    # Branch match check
    norm_target = target_branch.strip()
    if norm_target.startswith("refs/heads/"):
        norm_target = norm_target[len("refs/heads/"):]
    if current_branch.lower() != norm_target.lower():
        raise LiveAdapterError(
            f"worktree_branch_mismatch: worktree on '{current_branch}', target is '{norm_target}'"
        )

    return p


def verify_exact_head(worktree_path: Path, expected_before_sha: str) -> str:
    """Verify that current HEAD matches expected_before_sha exactly."""
    if not isinstance(expected_before_sha, str) or not expected_before_sha.strip():
        raise LiveAdapterError("expected_before_sha_required")
    proc = _run_git(["rev-parse", "HEAD"], cwd=worktree_path)
    if proc.returncode != 0:
        raise LiveAdapterError(f"failed_to_resolve_head: {proc.stderr.strip()}")
    current_head = proc.stdout.strip()
    if current_head.lower() != expected_before_sha.strip().lower():
        raise LiveAdapterError(
            f"exact_head_mismatch: expected '{expected_before_sha.strip()}', got '{current_head}'"
        )
    return current_head


def matches_allowed_paths(relpath: str, patterns: Sequence[str]) -> bool:
    """Check if relpath matches any glob in allowed_paths.

    Matches against the full relative path only (no implicit directory prefixes).
    Task authors must explicitly provide globs like '**/README.md' to match
    across arbitrary directory depths.
    """
    norm_p = relpath.replace("\\", "/").strip("/")
    for pat in patterns:
        raw_pat = pat.replace("\\", "/").strip()
        # If pattern represents a directory (ends with /)
        if raw_pat.endswith("/"):
            dir_pat = raw_pat.rstrip("/")
            if norm_p == dir_pat or norm_p.startswith(f"{dir_pat}/"):
                return True
        norm_pat = raw_pat.strip("/")
        # Direct fnmatch against full relative path
        if fnmatch.fnmatch(norm_p, norm_pat):
            return True
        # Explicit globstar prefix (e.g. **/README.md) matching at root level as well
        if norm_pat.startswith("**/") and fnmatch.fnmatch(norm_p, norm_pat[3:]):
            return True
        # Explicit mid-pattern globstar (e.g. foo/**/bar.txt) matching zero directory levels
        if "/**/" in norm_pat and fnmatch.fnmatch(norm_p, norm_pat.replace("/**/", "/")):
            return True
    return False


def _parse_porcelain_z(raw_output: str) -> list[str]:
    tokens = raw_output.split("\0")
    paths = []
    i = 0
    while i < len(tokens):
        item = tokens[i]
        if not item:
            i += 1
            continue
        status = item[:2]
        path1 = item[3:] if len(item) > 3 else ""
        if status[0] in ("R", "C") or status[1] in ("R", "C"):
            i += 1
            path2 = tokens[i] if i < len(tokens) else ""
            if path1:
                paths.append(path1)
            if path2:
                paths.append(path2)
        else:
            if path1:
                paths.append(path1)
        i += 1
    return paths


def stage_allowed_paths(worktree_path: Path, allowed_paths: Sequence[str]) -> list[str]:
    """Stage ONLY files matching allowed_paths; refuse if nothing matches."""
    if not isinstance(allowed_paths, (list, tuple, set, frozenset)) or not allowed_paths:
        raise LiveAdapterError("allowed_paths_required")
    for pat in allowed_paths:
        if not isinstance(pat, str) or not pat.strip():
            raise LiveAdapterError("allowed_paths_contains_empty_pattern")

    # Unstage any previously staged files so index is clean
    _run_git(["reset"], cwd=worktree_path)

    # Discover all changed or untracked files
    proc = _run_git(["status", "--porcelain=v1", "-z", "-uall"], cwd=worktree_path)
    if proc.returncode != 0:
        raise LiveAdapterError(f"git_status_failed: {proc.stderr.strip()}")

    changed_files = _parse_porcelain_z(proc.stdout)
    to_stage = [p for p in changed_files if matches_allowed_paths(p, allowed_paths)]
    if not to_stage:
        raise LiveAdapterError("no_matching_changes_to_commit: no changes match allowed_paths")

    # Stage only matching files
    proc_add = _run_git(["add", "--", *to_stage], cwd=worktree_path)
    if proc_add.returncode != 0:
        raise LiveAdapterError(f"git_add_failed: {proc_add.stderr.strip()}")

    # Verify index has staged changes and none outside allowed_paths
    proc_diff = _run_git(["diff", "--cached", "--name-only", "-z"], cwd=worktree_path)
    if proc_diff.returncode != 0:
        raise LiveAdapterError(f"git_diff_cached_failed: {proc_diff.stderr.strip()}")
    staged = [p for p in proc_diff.stdout.split("\0") if p]
    if not staged:
        raise LiveAdapterError("no_matching_changes_to_commit: index empty after staging")

    for p in staged:
        if not matches_allowed_paths(p, allowed_paths):
            _run_git(["reset"], cwd=worktree_path)
            raise LiveAdapterError(f"file_outside_allowed_paths_was_staged:{p}")

    return staged


def format_score_commit_message(
    commit_message: str,
    *,
    run_id: str,
    task_id: str,
    digest: str,
) -> str:
    """Format commit message appending authorizing Score trailer."""
    msg = commit_message.strip()
    if not msg:
        raise LiveAdapterError("commit_message_required")
    trailer = (
        f"Score-Run: {run_id}\n"
        f"Score-Task: {task_id}\n"
        f"Score-Digest: {digest}"
    )
    return f"{msg}\n\n{trailer}\n"


def commit_staged_changes(
    worktree_path: Path,
    message: str,
) -> str:
    """Commit staged changes and return the new commit sha."""
    proc_commit = _run_git(["commit", "-m", message], cwd=worktree_path)
    if proc_commit.returncode != 0:
        raise LiveAdapterError(f"git_commit_failed: {proc_commit.stderr.strip()}")
    proc_head = _run_git(["rev-parse", "HEAD"], cwd=worktree_path)
    if proc_head.returncode != 0:
        raise LiveAdapterError(f"failed_to_resolve_new_head: {proc_head.stderr.strip()}")
    new_sha = proc_head.stdout.strip()
    return new_sha


def rollback_worktree_commit(
    worktree_path: str | Path,
    target_branch: str,
    expected_before_sha: str,
    produced_commit_sha: str,
) -> str:
    """Safely rollback a commit by resetting to expected_before_sha.

    Refuses immediately if target_branch is in denylist.
    Verifies that worktree HEAD is equal to produced_commit_sha before reset.
    If HEAD has moved past produced_commit_sha (descendant commit landed),
    or diverged, refuses to prevent discarding newer work.
    """
    # 1. Denylist check FIRST
    verify_not_denied_branch(target_branch)

    # 2. Worktree validation
    wt = verify_git_worktree(worktree_path, target_branch)

    # 3. Check current HEAD
    proc_head = _run_git(["rev-parse", "HEAD"], cwd=wt)
    if proc_head.returncode != 0:
        raise LiveAdapterError(f"failed_to_resolve_head: {proc_head.stderr.strip()}")
    current_head = proc_head.stdout.strip()

    # If already at before_sha: safe no-op
    if current_head.lower() == expected_before_sha.strip().lower():
        return expected_before_sha.strip()

    # If current HEAD equals produced_commit_sha: safe to reset
    if current_head.lower() == produced_commit_sha.strip().lower():
        proc_reset = _run_git(["reset", "--hard", expected_before_sha.strip()], cwd=wt)
        if proc_reset.returncode != 0:
            raise LiveAdapterError(f"git_reset_failed: {proc_reset.stderr.strip()}")
        proc_check = _run_git(["rev-parse", "HEAD"], cwd=wt)
        restored = proc_check.stdout.strip()
        if restored.lower() != expected_before_sha.strip().lower():
            raise LiveAdapterError("reset_did_not_restore_expected_before_sha")
        return restored

    # Otherwise, current_head != produced_commit_sha and != expected_before_sha.
    # Check if produced_commit_sha is ancestor of current_head (worktree moved past)
    proc_anc = _run_git(
        ["merge-base", "--is-ancestor", produced_commit_sha.strip(), current_head],
        cwd=wt,
    )
    if proc_anc.returncode == 0:
        raise LiveRecoveryRequired(
            f"worktree_has_moved_past_commit: current HEAD {current_head} is descendant of {produced_commit_sha}"
        )
    raise LiveRecoveryRequired(
        f"worktree_head_diverged: current HEAD {current_head} does not contain {produced_commit_sha}"
    )


def _extract_descriptor(op: Any) -> dict[str, Any]:
    if isinstance(op, Mapping):
        if "desired_state" in op and isinstance(op["desired_state"], Mapping):
            d = dict(op["desired_state"])
            for k in ("run_id", "task_id", "digest", "org_id", "action", "resource", "environment", "owner_principal"):
                if k in op and k not in d:
                    d[k] = op[k]
            return d
        return dict(op)
    if hasattr(op, "desired_state") and isinstance(op.desired_state, Mapping):
        d = dict(op.desired_state)
        for k in ("run_id", "task_id", "digest", "org_id", "action", "resource", "environment", "owner_principal"):
            if hasattr(op, k) and k not in d:
                d[k] = getattr(op, k)
        return d
    raise LiveAdapterError("invalid_task_descriptor")


class GitWorktreeCommitAdapter:
    """Live repository commit adapter for isolated git worktrees."""

    def __init__(self, grant_service: GrantService | None = None):
        self.grants = grant_service

    def inspect(self, op: Operation | Mapping[str, Any]) -> Mapping[str, Any]:
        req = _extract_descriptor(op)
        verify_not_denied_branch(req.get("target_branch"))
        wt_raw = req.get("worktree_path")
        if not wt_raw:
            return {"exists": False}
        wt = Path(wt_raw).resolve()
        if not wt.is_dir() or not (wt / ".git").exists():
            return {"exists": False}
        proc = _run_git(["rev-parse", "HEAD"], cwd=wt)
        head = proc.stdout.strip() if proc.returncode == 0 else ""
        return {"head_sha": head, "target_branch": req.get("target_branch")}

    def invoke(self, op: Operation | Mapping[str, Any]) -> AdapterResult:
        req = _extract_descriptor(op)

        # 1a. Refuse immediately if target_branch matches denylist - FIRST THING
        target_branch = req.get("target_branch")
        verify_not_denied_branch(target_branch)

        # Optional grant check if invoked directly with grant_service configured
        if self.grants is not None and "org_id" in req and "digest" in req:
            self.grants.require(
                org_id=req["org_id"],
                run_id=req.get("run_id", ""),
                digest=req["digest"],
                action=req.get("action", "repository:write"),
                resource=req.get("resource", "repo"),
                environment=req.get("environment", "test"),
                cost_cents=req.get("cost_cents", 0),
                owner_principal=req.get("owner_principal", ""),
            )

        # 1b. Refuse if worktree_path is not an existing, real git worktree
        worktree_path = req.get("worktree_path")
        wt = verify_git_worktree(worktree_path, target_branch)

        # 1c. Refuse if worktree HEAD does not equal expected_before_sha
        expected_before_sha = req.get("expected_before_sha")
        current_head = verify_exact_head(wt, expected_before_sha)

        # 1d. Stage ONLY files matching allowed_paths globs
        allowed_paths = req.get("allowed_paths")
        staged = stage_allowed_paths(wt, allowed_paths)

        # Extract Score metadata for trailer
        run_id = str(req.get("run_id") or "run-unspecified")
        task_id = str(req.get("task_id") or "task-unspecified")
        digest = str(req.get("digest") or "digest-unspecified")

        # 1e. Commit with given message + appended trailer
        commit_message = req.get("commit_message", "")
        full_msg = format_score_commit_message(
            commit_message,
            run_id=run_id,
            task_id=task_id,
            digest=digest,
        )
        new_sha = commit_staged_changes(wt, full_msg)

        # 1f. Return resulting commit sha
        observed = {
            "commit_sha": new_sha,
            "head_sha": new_sha,
            "target_branch": target_branch,
            "staged_files": staged,
        }
        partial = {
            "commit_sha": new_sha,
            "expected_before_sha": current_head,
            "worktree_path": str(wt),
            "target_branch": target_branch,
        }
        return AdapterResult(
            status=EffectStatus.SUCCEEDED,
            observed_state=observed,
            detail=f"committed {new_sha}",
            partial_effect=partial,
        )

    def compensate(
        self,
        op: Operation | Mapping[str, Any],
        partial_effect: Mapping[str, Any] | None = None,
    ) -> AdapterResult:
        req = _extract_descriptor(op)
        target_branch = (partial_effect or {}).get("target_branch") or req.get("target_branch")
        # 1. Denylist check FIRST
        verify_not_denied_branch(target_branch)

        worktree_path = (partial_effect or {}).get("worktree_path") or req.get("worktree_path")
        expected_before_sha = (partial_effect or {}).get("expected_before_sha") or req.get("expected_before_sha")
        produced_commit_sha = (partial_effect or {}).get("commit_sha") or req.get("produced_commit_sha")

        if not produced_commit_sha:
            wt = verify_git_worktree(worktree_path, target_branch)
            head = verify_exact_head(wt, expected_before_sha)
            return AdapterResult(
                status=EffectStatus.SUCCEEDED,
                observed_state={"head_sha": head},
                detail="no commit produced; worktree unchanged",
            )

        restored_sha = rollback_worktree_commit(
            worktree_path=worktree_path,
            target_branch=target_branch,
            expected_before_sha=expected_before_sha,
            produced_commit_sha=produced_commit_sha,
        )
        return AdapterResult(
            status=EffectStatus.SUCCEEDED,
            observed_state={"head_sha": restored_sha},
            detail=f"reset to {restored_sha}",
        )


def commit_worktree_changes(
    *,
    worktree_path: str | Path,
    target_branch: str,
    allowed_paths: Sequence[str],
    commit_message: str,
    expected_before_sha: str,
    run_id: str = "run-unspecified",
    task_id: str = "task-unspecified",
    digest: str = "digest-unspecified",
    grant_service: GrantService | None = None,
    org_id: str | None = None,
    action: str = "repository:write",
    resource: str = "repo",
    environment: str = "test",
    owner_principal: str = "",
    cost_cents: int = 0,
    now: datetime | None = None,
) -> str:
    """Standalone wrapper to commit changes in a worktree with full guardrails."""
    # 1. Denylist check FIRST before anything else
    verify_not_denied_branch(target_branch)

    # Grant check if grant_service provided
    if grant_service is not None:
        if not org_id or not digest:
            raise LiveAdapterError("org_id_and_digest_required_for_grant_verification")
        grant_service.require(
            org_id=org_id,
            run_id=run_id,
            digest=digest,
            action=action,
            resource=resource,
            environment=environment,
            cost_cents=cost_cents,
            owner_principal=owner_principal,
            now=now,
        )

    adapter = GitWorktreeCommitAdapter()
    result = adapter.invoke({
        "worktree_path": str(worktree_path),
        "target_branch": target_branch,
        "allowed_paths": list(allowed_paths),
        "commit_message": commit_message,
        "expected_before_sha": expected_before_sha,
        "run_id": run_id,
        "task_id": task_id,
        "digest": digest,
    })
    return str(result.observed_state["commit_sha"])


def compensate_worktree_commit(
    *,
    worktree_path: str | Path,
    target_branch: str,
    expected_before_sha: str,
    produced_commit_sha: str,
) -> str:
    """Standalone compensation to rollback commit safely."""
    return rollback_worktree_commit(
        worktree_path=worktree_path,
        target_branch=target_branch,
        expected_before_sha=expected_before_sha,
        produced_commit_sha=produced_commit_sha,
    )


LIVE_REPOSITORY_ALLOWLIST = {
    "repository-worktree-commit": AdapterSpec(
        name="repository-worktree-commit",
        kind="repository",
        actions=frozenset({"repository:write"}),
        replay_requires_recheck=True,
        compensation_supported=True,
        dry_run_only=False,
    ),
}


class LiveScoreAdapterExecutor:
    """Conductor executor for live Score adapters (repository:write).

    Enforces:
    - Target branch denylist check FIRST
    - Durable grant verification via GrantService.require(...)
    - Atomic claim via score_recovery (B03 discipline)
    - Durable audit receipts in score_events
    - Descendant-safe compensation
    """

    def __init__(
        self,
        db: Any,
        *,
        grant_service: GrantService,
        allowlist: Mapping[str, AdapterSpec] | None = None,
        wrappers: Mapping[str, AdapterWrapper] | None = None,
        now: Callable[[], datetime] | None = None,
    ):
        if not isinstance(grant_service, GrantService):
            raise LiveAdapterError("durable_grant_service_required")
        self.db = db
        self.grants = grant_service
        self.allowlist = dict(allowlist if allowlist is not None else LIVE_REPOSITORY_ALLOWLIST)

        default_adapter = GitWorktreeCommitAdapter(grant_service=grant_service)
        default_wrappers = {
            "repository-worktree-commit": AdapterWrapper(
                inspect=default_adapter.inspect,
                invoke=default_adapter.invoke,
                compensate=default_adapter.compensate,
            ),
        }
        self.wrappers = dict(wrappers if wrappers is not None else default_wrappers)

        if set(self.wrappers) != set(self.allowlist):
            raise LiveAdapterError("adapter_registry_must_exactly_match_allowlist")
        for name, spec in self.allowlist.items():
            if name != spec.name or not isinstance(self.wrappers[name], AdapterWrapper):
                raise LiveAdapterError("invalid_adapter_registry")
            if spec.compensation_supported and self.wrappers[name].compensate is None:
                raise LiveAdapterError("compensation_wrapper_required")
        self.now = now or (lambda: datetime.now(timezone.utc))

    def _validate(self, op: Operation) -> tuple[AdapterSpec, AdapterWrapper, str]:
        # 1. Denylist check FIRST on target_branch before any other check or command!
        target_branch = (op.desired_state or {}).get("target_branch")
        verify_not_denied_branch(target_branch)

        for value in (
            op.org_id, op.run_id, op.digest, op.task_id, op.adapter,
            op.action, op.resource, op.environment, op.owner_principal,
        ):
            if not isinstance(value, str) or not value.strip():
                raise LiveAdapterError("adapter_operation_binding_incomplete")
        if type(op.attempt) is not int or op.attempt < 1:
            raise LiveAdapterError("adapter_attempt_invalid")
        if type(op.cost_cents) is not int or op.cost_cents < 0:
            raise LiveAdapterError("adapter_cost_invalid")
        if not isinstance(op.desired_state, Mapping) or not op.desired_state:
            raise LiveAdapterError("desired_state_required")

        spec = self.allowlist.get(op.adapter)
        if spec is None or op.action not in spec.actions:
            raise LiveAdapterError("adapter_or_action_not_allowlisted")

        # 4. Grant requirement: must go through GrantService.require(...)
        _grant, identity = self.grants.require(
            org_id=op.org_id, run_id=op.run_id, digest=op.digest,
            action=op.action, resource=op.resource,
            environment=op.environment, cost_cents=op.cost_cents,
            owner_principal=op.owner_principal, now=self.now(),
        )
        return spec, self.wrappers[op.adapter], identity

    def _events(self, op: Operation) -> list[dict]:
        return (
            self.db.table("score_events").select("*")
            .eq("org_id", op.org_id).eq("run_id", op.run_id)
            .eq("task_id", op.task_id).order("seq").execute().data or []
        )

    def _receipt(
        self,
        op: Operation,
        *,
        phase: str,
        status: str,
        grant_identity: str,
        observed: Mapping[str, Any],
        detail: str = "",
    ) -> dict:
        receipt = {
            "protocol": "score-adapter-receipt/v1",
            "operation_id": op.id,
            "phase": phase,
            "status": status,
            "adapter": op.adapter,
            "action": op.action,
            "resource": op.resource,
            "environment": op.environment,
            "attempt": op.attempt,
            "dry_run": False,
            "desired_state_sha256": _sha(op.desired_state),
            "observed_state": copy.deepcopy(dict(observed)),
            "observed_state_sha256": _sha(observed),
            "grant_identity": grant_identity,
            "detail": str(detail or ""),
        }
        self.db.table("score_events").insert({
            "org_id": op.org_id,
            "run_id": op.run_id,
            "kind": f"adapter_{phase}",
            "task_id": op.task_id,
            "digest": op.digest,
            "actor": "score-conductor",
            "payload": receipt,
        }).execute()
        return receipt

    def _completed(self, op: Operation) -> dict | None:
        for event in reversed(self._events(op)):
            payload = event.get("payload") or {}
            if (payload.get("operation_id") == op.id
                    and payload.get("phase") in {"after", "reconciled"}
                    and payload.get("status") in {"succeeded", "already_desired"}):
                return payload
        return None

    def _recovery(self, op: Operation) -> dict | None:
        rows = (
            self.db.table("score_recovery").select("*")
            .eq("approval_or_attempt_id", op.id).eq("org_id", op.org_id)
            .execute().data or []
        )
        return rows[0] if rows else None

    def _pending_effect_recovery(self, op: Operation) -> dict | None:
        rows = (
            self.db.table("score_recovery").select("*")
            .eq("org_id", op.org_id).eq("decision", "pending")
            .execute().data or []
        )
        scope = (op.run_id, op.task_id, op.adapter, op.action, op.resource)
        for row in rows:
            operation = (row.get("uncertain_effect") or {}).get("operation") or {}
            candidate = tuple(operation.get(key) for key in (
                "run_id", "task_id", "adapter", "action", "resource",
            ))
            if candidate == scope:
                return row
        return None

    def _in_flight_effect(
        self,
        op: Operation,
        spec: AdapterSpec,
        observed: Mapping[str, Any],
        *,
        detail: str,
    ) -> dict:
        return {
            "operation": copy.deepcopy(op.__dict__),
            "adapter_kind": spec.kind,
            "partial_effect": {"in_flight": True},
            "observed_state": copy.deepcopy(dict(observed)),
            "compensation_supported": spec.compensation_supported,
            "detail": detail,
        }

    def _claim(self, op: Operation, effect: Mapping[str, Any]) -> bool:
        row = {
            "approval_or_attempt_id": op.id,
            "org_id": op.org_id,
            "uncertain_effect": copy.deepcopy(dict(effect)),
            "decision": "pending",
        }
        try:
            self.db.table("score_recovery").insert(row).execute()
            return True
        except Exception as exc:
            if not _is_duplicate_claim_error(exc):
                raise
            existing = self._recovery(op)
            if not existing:
                raise
        if existing.get("decision") not in {"inspected", "compensated"}:
            return False

        claimed_effect = copy.deepcopy(dict(effect))
        claimed_effect["_prior_recovery"] = {
            "decision": existing["decision"],
            "uncertain_effect": copy.deepcopy(existing.get("uncertain_effect") or {}),
        }
        updated = (
            self.db.table("score_recovery")
            .update({"uncertain_effect": claimed_effect, "decision": "pending"})
            .eq("approval_or_attempt_id", op.id).eq("org_id", op.org_id)
            .eq("decision", existing["decision"]).execute().data or []
        )
        return bool(updated)

    def _resolve_claim(self, op: Operation, decision: str = "inspected") -> bool:
        updated = (
            self.db.table("score_recovery").update({"decision": decision})
            .eq("approval_or_attempt_id", op.id).eq("org_id", op.org_id)
            .eq("decision", "pending").execute().data or []
        )
        return bool(updated)

    def _release_claim(self, op: Operation) -> None:
        recovery = self._recovery(op)
        if not recovery or recovery.get("decision") != "pending":
            return
        prior = (recovery.get("uncertain_effect") or {}).get("_prior_recovery")
        if prior:
            self.db.table("score_recovery").update({
                "decision": prior["decision"],
                "uncertain_effect": copy.deepcopy(prior["uncertain_effect"]),
            }).eq("approval_or_attempt_id", op.id).eq("decision", "pending").execute()
        else:
            self.db.table("score_recovery").delete().eq(
                "approval_or_attempt_id", op.id,
            ).eq("decision", "pending").execute()

    def execute(self, op: Operation) -> dict:
        spec, wrapper, grant_identity = self._validate(op)
        completed = self._completed(op)
        if completed:
            self._release_claim(op)
            return dict(completed, replayed=True)

        recovery = self._pending_effect_recovery(op)
        if recovery and recovery.get("approval_or_attempt_id") != op.id:
            raise LiveRecoveryRequired("uncertain_effect_requires_inspection_or_compensation")
        if recovery:
            raise LiveRecoveryRequired("uncertain_effect_requires_inspection_or_compensation")

        observed_before = dict(wrapper.inspect(op))
        in_flight = self._in_flight_effect(
            op, spec, observed_before, detail="live wrapper invocation in flight",
        )
        if not self._claim(op, in_flight):
            raise LiveRecoveryRequired("operation_claim_failed_or_racing")

        self._receipt(
            op, phase="before", status="observed",
            grant_identity=grant_identity, observed=observed_before,
        )

        try:
            result = wrapper.invoke(op)
        except Exception as exc:
            result = AdapterResult(
                EffectStatus.UNCERTAIN, observed_state=observed_before,
                detail=f"wrapper_exception:{type(exc).__name__}:{exc}",
                partial_effect={"exception_type": type(exc).__name__, "error": str(exc)},
            )

        if not isinstance(result, AdapterResult):
            raise LiveAdapterError("adapter_returned_invalid_result")

        after = dict(result.observed_state)
        if result.status is EffectStatus.SUCCEEDED:
            receipt = self._receipt(
                op, phase="after", status="succeeded",
                grant_identity=grant_identity, observed=after,
                detail=result.detail,
            )
            self._release_claim(op)
            return receipt

        uncertain = {
            "operation": copy.deepcopy(op.__dict__),
            "adapter_kind": spec.kind,
            "partial_effect": copy.deepcopy(dict(result.partial_effect)),
            "observed_state": copy.deepcopy(after),
            "compensation_supported": spec.compensation_supported,
            "detail": result.detail,
        }
        self.db.table("score_recovery").update({"uncertain_effect": uncertain}).eq(
            "approval_or_attempt_id", op.id,
        ).eq("org_id", op.org_id).eq("decision", "pending").execute()
        return self._receipt(
            op, phase="after", status="uncertain",
            grant_identity=grant_identity, observed=after,
            detail=result.detail,
        )

    def compensate(self, op: Operation) -> dict:
        spec, wrapper, grant_identity = self._validate(op)
        recovery = self._recovery(op)
        if not recovery or recovery.get("decision") != "pending":
            raise LiveRecoveryRequired("pending_uncertain_effect_not_found")
        if not spec.compensation_supported or wrapper.compensate is None:
            raise LiveRecoveryRequired("uncertain_effect_requires_owner_action")

        effect = recovery.get("uncertain_effect") or {}
        partial = copy.deepcopy(effect.get("partial_effect") or {})
        result = wrapper.compensate(op, partial)
        if not isinstance(result, AdapterResult) or result.status is not EffectStatus.SUCCEEDED:
            raise LiveRecoveryRequired("compensation_not_confirmed")

        decision = "compensated"
        updated = (
            self.db.table("score_recovery").update({"decision": decision})
            .eq("approval_or_attempt_id", op.id).eq("decision", "pending")
            .execute().data or []
        )
        if not updated:
            raise LiveRecoveryRequired("compensation_lost_concurrent_race")

        return self._receipt(
            op, phase="compensated", status=decision,
            grant_identity=grant_identity,
            observed=dict(result.observed_state), detail=result.detail,
        )

    def rollback(self, op: Operation) -> dict:
        return self.compensate(op)
