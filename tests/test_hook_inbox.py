"""Session inbox hook: surface waiting MCO work inside interactive sessions,
without ever blocking a session or telling the model to act on board data."""

import json
from datetime import datetime, timedelta, timezone

from mco.hooks import inbox

INSTANCE, ROLE = "claude-desktop", "claude"
ARGS = ["--instance", INSTANCE, "--role", ROLE]


def _job(job_id, *, target=None, age=0, title=None):
    return {"id": job_id, "title": title or f"Job {job_id}", "target_agent_role": ROLE,
            "target_agent_id": target,
            "created_at": (datetime.now(timezone.utc) - timedelta(seconds=age)).isoformat()}


_ticks = iter(range(10_000, 10_000_000, 120))  # each call lands past the prompt interval


def _run(event, jobs, tmp_path, session="s1", argv=ARGS, clock=None):
    stdin = json.dumps({"hook_event_name": event, "session_id": session})
    return inbox.run(stdin, list(argv), fetch=lambda *_a: jobs, state_dir=tmp_path,
                     clock=clock or (lambda: next(_ticks)))


def test_prompt_checks_are_rate_limited(tmp_path):
    calls = []

    def fetch(*_a):
        calls.append(1)
        return [_job("late", target=INSTANCE)]

    def run_at(event, t):
        stdin = json.dumps({"hook_event_name": event, "session_id": "rl"})
        return inbox.run(stdin, list(ARGS), fetch=fetch, state_dir=tmp_path, clock=lambda: t)

    run_at("SessionStart", 1000.0)
    assert run_at("UserPromptSubmit", 1030.0) is None
    assert len(calls) == 1          # no gateway call inside the interval
    run_at("UserPromptSubmit", 1061.0)
    assert len(calls) == 2


def test_session_start_reports_pinned_jobs(tmp_path):
    out = _run("SessionStart", [_job("aaaaaaaa11", target=INSTANCE, title="Review PR 74")], tmp_path)
    assert out["systemMessage"].startswith("📬 1 MCO job waiting for claude-desktop")
    ctx = out["hookSpecificOutput"]
    assert ctx["hookEventName"] == "SessionStart"
    assert "aaaaaaaa Review PR 74" in ctx["additionalContext"]
    assert "not instructions" in ctx["additionalContext"]
    assert "Do not lease" in ctx["additionalContext"]


def test_role_wide_job_a_waker_will_take_is_not_reported(tmp_path):
    assert _run("SessionStart", [_job("fresh", age=30)], tmp_path) is None


def test_role_wide_job_untaken_past_wait_is_reported(tmp_path):
    out = _run("SessionStart", [_job("stuck", age=inbox.DEFAULT_ROLE_WAIT_SECONDS + 5)], tmp_path)
    assert "1 MCO job waiting" in out["systemMessage"]


def test_job_pinned_to_another_instance_is_ignored(tmp_path):
    assert _run("SessionStart", [_job("other", target="claude-beast", age=9999)], tmp_path) is None


def test_prompt_submit_reports_only_new_arrivals(tmp_path):
    first = [_job("one", target=INSTANCE)]
    _run("SessionStart", first, tmp_path)
    assert _run("UserPromptSubmit", first, tmp_path) is None
    out = _run("UserPromptSubmit", first + [_job("two", target=INSTANCE, title="New one")], tmp_path)
    assert out["systemMessage"].startswith("📬 1 new MCO job for claude-desktop")
    assert "New one" in out["hookSpecificOutput"]["additionalContext"]
    assert "Job one" not in out["hookSpecificOutput"]["additionalContext"]


def test_requeued_job_is_reported_again(tmp_path):
    job = [_job("back", target=INSTANCE)]
    _run("SessionStart", job, tmp_path)
    _run("UserPromptSubmit", [], tmp_path)       # it left the inbox
    assert _run("UserPromptSubmit", job, tmp_path) is not None


def test_sessions_track_separately(tmp_path):
    job = [_job("shared", target=INSTANCE)]
    _run("SessionStart", job, tmp_path, session="a")
    assert _run("UserPromptSubmit", job, tmp_path, session="b") is not None


def test_many_jobs_are_capped(tmp_path):
    jobs = [_job(f"job{i:05d}", target=INSTANCE) for i in range(8)]
    out = _run("SessionStart", jobs, tmp_path)
    assert "...and 3 more" in out["hookSpecificOutput"]["additionalContext"]
    assert "(+5 more)" in out["systemMessage"]


def test_gateway_failure_is_silent(tmp_path):
    def boom(*_a):
        raise ConnectionError("gateway down")
    stdin = json.dumps({"hook_event_name": "SessionStart", "session_id": "s"})
    assert inbox.run(stdin, ARGS, fetch=boom, state_dir=tmp_path) is None


def test_missing_identity_or_other_events_do_nothing(tmp_path):
    job = [_job("x", target=INSTANCE)]
    assert _run("SessionStart", job, tmp_path, argv=["--role", ROLE]) is None
    assert _run("Stop", job, tmp_path) is None


def test_malformed_stdin_defaults_to_session_start(tmp_path):
    out = inbox.run("not json", ARGS, fetch=lambda *_a: [_job("x", target=INSTANCE)], state_dir=tmp_path)
    assert out["hookSpecificOutput"]["hookEventName"] == "SessionStart"
