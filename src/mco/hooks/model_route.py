"""Claude Code hook: suggest a cheaper/dearer model tier for the task ahead.

Wire this as a UserPromptSubmit hook:

    python -m mco.hooks.model_route

It asks Jev's bounded, frozen ``claude-code-model-route`` question set
(see mco.orchestrator.jev_questions) which model tier - haiku, sonnet, or
opus - fits the submitted prompt, and surfaces the suggestion as context for
the session to read. It is advisory only: no answer switches the running
session or spawns anything; the session (or a person via /model) still
decides. Jev disabled, unconfigured, unavailable, rate-limited, or timing out
all fall through to no suggestion, same as every other J03 shadow use case.

Costs a real Jev round trip when a mode beyond disabled is configured, so
calls are throttled per session (--min-interval, default 300s) rather than
firing on every prompt. It never blocks or slows a session on failure: no
token, gateway down, disabled Jev, or a slow response all exit 0 silently.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Callable, Optional

STATE_DIR = Path.home() / ".mco" / "hooks"
DEFAULT_MIN_INTERVAL_SECONDS = 300
MAX_TASK_CHARS = 2000


def _state_path(state_dir: Path, session_id: str) -> Path:
    safe = "".join(ch for ch in (session_id or "unknown") if ch.isalnum() or ch in "-_")[:80] or "unknown"
    return state_dir / f"model-route-{safe}.json"


def _load_checked_at(path: Path) -> float:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return float(data.get("checked_at") or 0)
    except (OSError, ValueError, AttributeError, TypeError):
        return 0.0


def _save_checked_at(path: Path, checked_at: float) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"checked_at": checked_at}), encoding="utf-8")
    except OSError:
        pass


def build_output(tier: str, task: str) -> dict:
    preview = " ".join(task.split())
    if len(preview) > 80:
        preview = preview[:77] + "..."
    context = (
        f"Jev model-route suggestion: {tier} (advisory only, for \"{preview}\"). "
        "This does not switch the running session. If the task's scope clearly "
        "differs from the suggestion, use your own judgment - for a subagent "
        "spawn, choose the model param yourself; for the whole session, mention "
        "the suggestion to the user and let them decide via /model."
    )
    return {
        "hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": context},
    }


def run(stdin_text: str, argv: Optional[list] = None,
        invoke: Optional[Callable[[str], Optional[str]]] = None,
        state_dir: Optional[Path] = None,
        clock: Optional[Callable[[], float]] = None) -> Optional[dict]:
    parser = argparse.ArgumentParser(prog="python -m mco.hooks.model_route")
    parser.add_argument("--min-interval", type=float, default=DEFAULT_MIN_INTERVAL_SECONDS,
                        help="minimum seconds between Jev round trips per session")
    parser.add_argument("--deterministic-tier", default="sonnet")
    args = parser.parse_args(argv)

    try:
        payload = json.loads(stdin_text or "{}")
    except ValueError:
        payload = {}
    if payload.get("hook_event_name") != "UserPromptSubmit":
        return None

    task = str(payload.get("prompt") or "").strip()
    if not task:
        return None
    task = task[:MAX_TASK_CHARS]

    path = _state_path(state_dir or STATE_DIR, payload.get("session_id") or "")
    checked_at = _load_checked_at(path)
    now = (clock or time.time)()
    if now - checked_at < args.min_interval:
        return None
    _save_checked_at(path, now)

    try:
        tier = (invoke or _invoke_default)(task, args.deterministic_tier)
    except Exception:
        return None
    if not tier:
        return None
    return build_output(tier, task)


def _invoke_default(task: str, deterministic_tier: str) -> Optional[str]:
    from mco.orchestrator.jev_ops import annotate_model_route

    outcome = annotate_model_route(None, task=task, deterministic_tier=deterministic_tier)
    return outcome.get("tier")


def main() -> int:
    try:
        stdin_text = sys.stdin.read() if not sys.stdin.isatty() else ""
    except Exception:
        stdin_text = ""
    try:
        output = run(stdin_text)
    except Exception:
        return 0
    if output:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stdout.write(json.dumps(output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
