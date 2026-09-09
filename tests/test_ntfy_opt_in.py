"""Local-Only must not silently enable public ntfy.sh.

Growth G1: a default NTFY_TOPIC of mco-events made get_ntfy_config() look
enabled, printed 'NTFY notifier enabled -> https://ntfy.sh/mco-events', and
POSTed job events to a shared public topic. Blank topic = off.
"""
from __future__ import annotations

import mco.notifiers.ntfy as ntfy_mod


class _Cfg:
    def __init__(self, values=None):
        self.values = values or {}

    def get(self, key, default=None):
        return self.values[key] if key in self.values else default


def test_topic_unset_means_off(monkeypatch):
    monkeypatch.setattr(ntfy_mod, "get_config", lambda: _Cfg())
    cfg = ntfy_mod.get_ntfy_config()
    assert cfg["topic"] == ""
    assert cfg["server"] == "https://ntfy.sh"


def test_notify_does_not_post_when_topic_unset(monkeypatch):
    monkeypatch.setattr(ntfy_mod, "get_config", lambda: _Cfg())
    posted = []
    monkeypatch.setattr(
        ntfy_mod.requests,
        "post",
        lambda *a, **k: posted.append((a, k)),
    )
    assert ntfy_mod.notify("hello") is False
    ntfy_mod.notify_job_created("j1", "title", "claude")
    ntfy_mod.notify_gateway_startup({"host": "127.0.0.1", "port": 18789, "pid": 1})
    assert posted == []


def test_notify_posts_when_topic_set(monkeypatch):
    monkeypatch.setattr(
        ntfy_mod, "get_config", lambda: _Cfg({"NTFY_TOPIC": "my-topic"})
    )

    class _Resp:
        def raise_for_status(self):
            return None

    posted = []

    def _post(url, **kwargs):
        posted.append(url)
        return _Resp()

    monkeypatch.setattr(ntfy_mod.requests, "post", _post)
    assert ntfy_mod.notify("hello") is True
    assert posted == ["https://ntfy.sh/my-topic"]


def test_job_notifiers_never_leak_to_bare_mco_role_topics(monkeypatch):
    """F15 fix: Notifiers must strictly emit the configured NTFY_TOPIC, never bare mco-<role>."""
    monkeypatch.setattr(
        ntfy_mod, "get_config", lambda: _Cfg({"NTFY_TOPIC": "company-private-topic"})
    )

    class _Resp:
        def raise_for_status(self):
            return None

    posted = []

    def _post(url, **kwargs):
        posted.append(url)
        return _Resp()

    monkeypatch.setattr(ntfy_mod.requests, "post", _post)

    # Trigger all job notifiers
    ntfy_mod.notify_job_created("j1", "title", "claude")
    ntfy_mod.notify_job_leased("j1", "agent-1", "codex")
    ntfy_mod.notify_job_completed("j1", "completed", "reviewer")
    ntfy_mod.notify_job_failed("j1", "boom", "gemini")
    ntfy_mod.notify_job_needs_approval("j1", "title", "claude")
    ntfy_mod.notify_job_escalated("j1", "title", "reviewer", "exhausted")
    ntfy_mod.notify_force_pull("codex")
    ntfy_mod.notify_agent_online("claude", "claude-1")
    ntfy_mod.notify_agent_offline("claude", "claude-1")

    # Also test an explicit call attempting to pass an unconfigured topic
    ntfy_mod.notify("sneaky", topic="mco-claude")

    expected_url = "https://ntfy.sh/company-private-topic"
    assert len(posted) == 10
    for url in posted:
        assert url == expected_url, f"Leaked to unconfigured URL: {url}"
        assert not url.endswith("/mco-claude")
        assert not url.endswith("/mco-codex")
        assert not url.endswith("/mco-reviewer")


def test_job_notifiers_support_opt_in_per_role_suffix(monkeypatch):
    """When NTFY_PER_ROLE_TOPICS is enabled, topic is <configured>-<role>."""
    monkeypatch.setattr(
        ntfy_mod,
        "get_config",
        lambda: _Cfg({"NTFY_TOPIC": "my-events", "NTFY_PER_ROLE_TOPICS": "true"}),
    )

    class _Resp:
        def raise_for_status(self):
            return None

    posted = []

    def _post(url, **kwargs):
        posted.append(url)
        return _Resp()

    monkeypatch.setattr(ntfy_mod.requests, "post", _post)

    ntfy_mod.notify_job_created("j1", "title", "claude")
    ntfy_mod.notify_job_leased("j1", "agent-1", "codex")
    ntfy_mod.notify_gateway_startup({"host": "127.0.0.1", "port": 18789})

    assert posted == [
        "https://ntfy.sh/my-events-claude",
        "https://ntfy.sh/my-events-codex",
        "https://ntfy.sh/my-events",
    ]
