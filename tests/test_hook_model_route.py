"""Model-route hook: advisory-only Claude Code model-tier suggestion, never
blocks a session, never fires more than once per --min-interval."""

import json

from mco.hooks import model_route

SESSION = "s1"


def _run(prompt, tmp_path, invoke, clock, min_interval=None, session=SESSION):
    stdin = json.dumps({"hook_event_name": "UserPromptSubmit", "session_id": session, "prompt": prompt})
    argv = ["--min-interval", str(min_interval)] if min_interval is not None else []
    return model_route.run(stdin, argv, invoke=invoke, state_dir=tmp_path, clock=clock)


def test_suggestion_is_surfaced_as_additional_context(tmp_path):
    out = _run("fix a typo", tmp_path, invoke=lambda *_a: "haiku", clock=lambda: 1000.0)
    ctx = out["hookSpecificOutput"]
    assert ctx["hookEventName"] == "UserPromptSubmit"
    assert "haiku" in ctx["additionalContext"]
    assert "does not switch the running session" in ctx["additionalContext"]


def test_no_suggestion_means_no_output(tmp_path):
    assert _run("anything", tmp_path, invoke=lambda *_a: None, clock=lambda: 1000.0) is None


def test_invoke_failure_never_raises_or_blocks(tmp_path):
    def boom(*_a):
        raise RuntimeError("gateway down")
    assert _run("anything", tmp_path, invoke=boom, clock=lambda: 1000.0) is None


def test_calls_are_rate_limited_per_session(tmp_path):
    calls = []

    def invoke(task, tier):
        calls.append(task)
        return "opus"

    _run("first", tmp_path, invoke=invoke, clock=lambda: 1000.0, min_interval=300)
    assert _run("second", tmp_path, invoke=invoke, clock=lambda: 1200.0, min_interval=300) is None
    assert len(calls) == 1
    _run("third", tmp_path, invoke=invoke, clock=lambda: 1301.0, min_interval=300)
    assert len(calls) == 2


def test_empty_prompt_is_ignored(tmp_path):
    assert _run("   ", tmp_path, invoke=lambda *_a: "haiku", clock=lambda: 1000.0) is None


def test_non_prompt_event_is_ignored(tmp_path):
    stdin = json.dumps({"hook_event_name": "SessionStart", "session_id": SESSION, "prompt": "x"})
    assert model_route.run(stdin, [], invoke=lambda *_a: "haiku", state_dir=tmp_path, clock=lambda: 1.0) is None


def test_sessions_are_rate_limited_independently(tmp_path):
    calls = []

    def invoke(task, tier):
        calls.append(task)
        return "sonnet"

    _run("a", tmp_path, invoke=invoke, clock=lambda: 1000.0, min_interval=300, session="s1")
    _run("b", tmp_path, invoke=invoke, clock=lambda: 1000.0, min_interval=300, session="s2")
    assert len(calls) == 2
