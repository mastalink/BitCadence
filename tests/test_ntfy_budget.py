"""Push budget: routine job traffic must not bury the escalations.

Real case: 73 notifications came back 429 from ntfy.sh in a day because every
job transition pushed, so the stuck-job escalations - the only messages meant
to reach a person - were dropped."""

import pytest

from mco.notifiers import ntfy


@pytest.fixture(autouse=True)
def clean_state():
    ntfy._last_sent.clear()
    ntfy._routine_sends.clear()
    ntfy._last_rate_limit_log[0] = 0.0
    yield
    ntfy._last_sent.clear()
    ntfy._routine_sends.clear()


CFG = {}


def _allowed(message="m", title="t", priority=3, now=1000.0, cfg=None):
    return ntfy._allowed(message, title, priority, cfg if cfg is not None else CFG, now)


def test_identical_message_is_not_repeated_within_the_window():
    assert _allowed(now=1000.0)
    assert not _allowed(now=1000.0 + ntfy.REPEAT_AFTER_SECONDS - 1)
    assert _allowed(now=1000.0 + ntfy.REPEAT_AFTER_SECONDS + 1)


def test_a_different_message_is_not_suppressed():
    assert _allowed(message="one")
    assert _allowed(message="two")


def test_routine_traffic_has_an_hourly_budget():
    for i in range(ntfy.MAX_ROUTINE_PER_HOUR):
        assert _allowed(message=f"job {i}", now=1000.0 + i)
    assert not _allowed(message="one too many", now=1000.0 + 100)


def test_urgent_messages_ignore_the_budget():
    for i in range(ntfy.MAX_ROUTINE_PER_HOUR):
        _allowed(message=f"job {i}", now=1000.0 + i)
    assert _allowed(message="a job is stuck", priority=5, now=1000.0 + 100)
    assert _allowed(message="another is stuck", priority=4, now=1000.0 + 101)


def test_urgent_messages_are_still_de_duplicated():
    assert _allowed(message="stuck", priority=5, now=1000.0)
    assert not _allowed(message="stuck", priority=5, now=1000.0 + 5)


def test_budget_refills_after_an_hour():
    for i in range(ntfy.MAX_ROUTINE_PER_HOUR):
        _allowed(message=f"job {i}", now=1000.0 + i)
    assert not _allowed(message="blocked", now=1000.0 + 200)
    assert _allowed(message="later", now=1000.0 + 3700)


def test_limits_are_configurable():
    cfg = {"NTFY_MAX_PER_HOUR": "1", "NTFY_REPEAT_AFTER": "0"}
    assert _allowed(message="a", now=1.0, cfg=cfg)
    assert not _allowed(message="b", now=2.0, cfg=cfg)
    assert _allowed(message="a", now=3.0, cfg=cfg, priority=5)   # urgent still passes


def test_bad_config_values_fall_back_to_defaults():
    assert ntfy._throttle_config({"NTFY_MAX_PER_HOUR": "lots"}) == (
        ntfy.REPEAT_AFTER_SECONDS, ntfy.MAX_ROUTINE_PER_HOUR, ntfy.URGENT_PRIORITY)


def test_notify_returns_false_without_sending_when_suppressed(monkeypatch):
    calls = []
    monkeypatch.setattr(ntfy, "get_ntfy_config", lambda: {
        "server": "https://example.invalid", "topic": "t", "token": None, "levels": []})
    monkeypatch.setattr(ntfy.requests, "post", lambda *a, **k: calls.append(1))
    ntfy._last_sent[("BitCadence", "hello")] = 10_000_000_000.0   # far future: still inside window
    assert ntfy.notify("hello", title="BitCadence") is False
    assert calls == []
