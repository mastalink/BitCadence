"""Scaffold a governed Score document from a plain-language brief.

Encodes what live Score runs taught: one work attempt per step, two automatic
fix attempts that swap builder and reviewer (the second reviewed by a third
identity), the committable path list repeated in the instructions because the
commit adapter silently drops everything else, a strict JSON result, and a
wiring rule so new code is never accepted as dead code.
"""

from __future__ import annotations

import copy
from typing import Any, Dict, List, Optional, Sequence

WIRING_RULE = (
    "New code is not done until a production entry point (CLI, worker, route, or script the "
    "user runs) calls it and a test proves that call. Dead code is a rejection finding."
)
RESULT_RULE = (
    'When the work is ready and tests pass, return exactly {"ready": true} as the result and put '
    "the narrative in the handoff."
)

DEFAULT_ALLOWED_PATHS = ["src/*", "tests/*", "test/*", "docs/*", "README.md", "package.json",
                         "package-lock.json", "pyproject.toml", "requirements*.txt", ".env.example"]


def _instructions(brief: str, allowed: Sequence[str], test_command: str) -> str:
    return (
        f"{brief.strip()} "
        f"Files you may commit: {', '.join(allowed)}. Anything else is silently left out of the commit. "
        f"{WIRING_RULE} Run `{test_command}` and report the counts. {RESULT_RULE}"
    )


def scaffold_score(
    *,
    score_id: str,
    objective: str,
    steps: Sequence[Dict[str, str]],
    worktree_path: str,
    target_branch: str,
    expected_before_sha: str,
    builders: Sequence[str] = ("codex", "claude"),
    third_reviewer: str = "antigravity",
    allowed_paths: Optional[Sequence[str]] = None,
    test_command: str = "the project's full test suite",
    constraints: Sequence[str] = (),
    timeout_seconds: int = 7200,
) -> Dict[str, Any]:
    """Return a Score v1 document with a build → review → fix → fix2 chain per step.

    Each step is ``{"id": ..., "title": ..., "brief": ...}``. Builders alternate
    across steps; each step's reviewer is the other builder role; every second
    fix attempt is reviewed by ``third_reviewer`` so a fix is never reviewed by
    an identity that already reviewed that chain.
    """
    if not steps:
        raise ValueError("at least one step is required")
    if len(builders) != 2 or builders[0] == builders[1]:
        raise ValueError("builders must be two distinct roles")
    if third_reviewer in builders:
        raise ValueError("third_reviewer must differ from both builders")
    if len(expected_before_sha) != 40:
        raise ValueError("expected_before_sha must be a full 40-character commit")
    allowed = list(allowed_paths or DEFAULT_ALLOWED_PATHS)
    ids = [step["id"] for step in steps]
    if len(set(ids)) != len(ids):
        raise ValueError("step ids must be unique")

    tasks: List[Dict[str, Any]] = []
    previous: Optional[str] = None
    for index, step in enumerate(steps):
        builder = builders[index % 2]
        reviewer = builders[(index + 1) % 2]
        commit: Dict[str, Any] = {
            "worktree_path": worktree_path,
            "target_branch": target_branch,
            "allowed_paths": allowed,
            "commit_message": f"{step['id']}: {step['title']}",
        }
        if previous is None:
            commit["expected_before_sha"] = expected_before_sha
        base = {
            "id": step["id"],
            "goal": step["id"],
            "title": step["title"],
            "instructions": _instructions(step["brief"], allowed, test_command),
            "role": builder,
            "review_role": reviewer,
            "depends_on": [previous] if previous else [],
            "resources": [worktree_path],
            "capabilities": ["repository:write", "evidence:write", "evidence:review"],
            "commit": commit,
            "evidence": ["commit_sha"],
            "max_attempts": 1,
            "timeout_seconds": timeout_seconds,
            "max_cost_cents": 0,
            "checkpoint": None,
            "on_reject": f"{step['id']}-fix",
        }
        fix = copy.deepcopy(base)
        fix.update({
            "id": f"{step['id']}-fix",
            "title": f"Fix review findings for {step['id']}",
            "role": reviewer,
            "review_role": builder,
            "depends_on": sorted(set(base["depends_on"]) | {step["id"]}),
            "on_reject": f"{step['id']}-fix2",
            "instructions": (
                f"The independent review of {step['id']} rejected its commit; the findings are appended below. "
                "Fix every finding on top of the current branch head, keeping the valid work. "
                + base["instructions"]
            ),
        })
        fix["commit"] = {k: v for k, v in base["commit"].items() if k != "expected_before_sha"}
        fix["commit"]["commit_message"] = f"{step['id']}: address review findings"
        fix2 = copy.deepcopy(fix)
        fix2.update({
            "id": f"{step['id']}-fix2",
            "title": f"Second fix attempt for {step['id']}",
            "role": builder,
            "review_role": third_reviewer,
            "depends_on": sorted(set(base["depends_on"]) | {fix["id"]}),
        })
        fix2.pop("on_reject", None)
        fix2["commit"]["commit_message"] = f"{step['id']}: second fix"
        tasks += [base, fix, fix2]
        previous = step["id"]

    return {
        "score_version": 1,
        "id": score_id,
        "revision": 1,
        "objective": objective,
        "constraints": list(constraints) + [
            f"All repository writes happen only in {worktree_path} on branch {target_branch}. "
            "Do not push, merge, deploy, handle or print secrets, or call a paid provider.",
            "Every step is reviewed by a different identity than its builder; second fixes by a third identity.",
        ],
        "budget_cents": 0,
        "max_parallel": 1,
        "launch_requires": [steps[-1]["id"]],
        "tasks": tasks,
    }
