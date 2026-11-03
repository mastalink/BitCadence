from datetime import datetime
from zoneinfo import ZoneInfo

from mco.localstore import LocalStore
from mco.notifiers import ntfy
from mco.orchestrator import operator_notifications


ET = ZoneInfo("America/New_York")


def test_digest_is_silent_when_nothing_is_pending(tmp_path, monkeypatch):
    db = LocalStore(tmp_path / "digest.db")
    sent = []
    monkeypatch.setattr(operator_notifications, "flush_batched", lambda: False)
    monkeypatch.setattr(operator_notifications, "notify_event", lambda *a, **k: sent.append((a, k)))
    operator_notifications._last_digest_date[0] = None
    result = operator_notifications.maintenance_once(db, datetime(2026, 9, 27, 8, 0, tzinfo=ET))
    assert result == {"flushed": False, "digest_sent": False, "pending": 0}
    assert sent == []
    db.close()


def test_digest_counts_only_owner_decisions_once_at_8am(tmp_path, monkeypatch):
    db = LocalStore(tmp_path / "digest.db")
    for job_id, title in (
        ("11111111-aaaa", "Owner decision: choose lane"),
        ("22222222-bbbb", "CIO approval: spend cap"),
        ("33333333-cccc", "ordinary worker task"),
    ):
        db.table("agent_jobs").insert({
            "id": job_id, "title": title, "status": "pending", "depends_on": [],
            "source_agent_id": "test", "source_agent_role": "test",
            "target_agent_role": "test", "input_payload": {},
        }).execute()
    sent = []
    monkeypatch.setattr(operator_notifications, "flush_batched", lambda: False)
    monkeypatch.setattr(operator_notifications, "notify_event", lambda *a, **k: sent.append((a, k)) or True)
    operator_notifications._last_digest_date[0] = None
    now = datetime(2026, 9, 27, 8, 0, tzinfo=ET)
    assert operator_notifications.maintenance_once(db, now)["pending"] == 2
    assert operator_notifications.maintenance_once(db, now)["pending"] == 0
    assert sent == [(('digest', '11111111-aaaa'), {'count': 2})]
    db.close()


def test_payloads_are_minimized_and_priorities_are_fixed(monkeypatch):
    delivered = []
    monkeypatch.setattr(ntfy, "_deliver", lambda message, title, priority, **kw: delivered.append((message, title, priority)) or True)
    ntfy.notify_sidecar_escalation("1a2b3c4d-secret", "simlab")
    ntfy.notify_operate_alert("9f8e7d6c-hostname", "ops")
    assert delivered == [
        ("Decision waiting on simlab (job 1a2b3c4d)", "BitCadence: decision needed", 4),
        ("Attention needed on operations (job 9f8e7d6c)", "BitCadence: alert", 4),
    ]
    assert all("secret" not in message and "hostname" not in message for message, _, _ in delivered)
