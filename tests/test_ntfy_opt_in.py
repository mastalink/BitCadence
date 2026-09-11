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
