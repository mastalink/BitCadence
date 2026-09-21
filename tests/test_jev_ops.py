"""J03 shadow ops: classify after deterministic code; never mutate truth."""
import httpx

from mco.orchestrator.jev import JevConfig, JevProvider
from mco.orchestrator.jev_ops import (
    DRUMLINE_QUESTIONS,
    FLEET_QUESTIONS,
    NOTIFY_QUESTIONS,
    QUESTION_SET_VERSION,
    annotate_drumline,
    annotate_fleet,
    annotate_notification,
    metrics_snapshot,
    persist_receipt,
    reset_metrics,
)
from mco.notifiers import ntfy
from mco.orchestrator.drumline import distill_job
from mco.orchestrator.presence import BROKEN
from mco.orchestrator import delivery


def _transport(payload):
    def handler(request):
        raise AssertionError(f"unexpected network {request.url}")
    return httpx.MockTransport(handler)


def _shadow_transport(choice, noul=0.1):
    def handler(request):
        body = {
            "model": "jev-latest",
            "answers": {},
            "usage": {"input_tokens": 1, "output_tokens": 1},
        }
        sent = request.content
        assert sent  # constructed a request
        questions = __import__("json").loads(sent)["questions"]
        for name, spec in questions.items():
            if spec["type"] == "choice":
                keys = list(spec["criteria"])
                probs = {k: (0.8 if k == choice else 0.2 / max(len(keys) - 1, 1)) for k in keys}
                if choice not in probs:
                    probs[choice] = 0.8
                body["answers"][name] = {
                    "type": "choice",
                    "choice": choice,
                    "confidence": 0.8,
                    "probabilities": probs,
                }
            else:
                body["answers"][name] = {"type": "noul", "noul": noul}
        return httpx.Response(200, json=body)
    return httpx.MockTransport(handler)


def test_disabled_drumline_fleet_notify_make_no_request():
    reset_metrics()
    provider = JevProvider(JevConfig(), transport=_transport(None))
    drum = annotate_drumline(title="t", content="c", deterministic_kind="handoff", provider=provider)
    fleet = annotate_fleet(instance_id="codex-mac", deterministic_state="broken", broken=True, stalled=False, provider=provider)
    note = annotate_notification(title="t", message="m", priority=3, allowed=True, provider=provider)
    assert drum["kind"] == "handoff"
    assert drum["suggested_kind"] is None
    assert drum["mutates_history"] is False
    assert fleet["suggested_action"] is None
    assert fleet["authorizes_effect"] is False
    assert note["allowed"] is True
    assert note["budget_owner"] == "code"
    assert metrics_snapshot()["disabled"] == 3
    assert metrics_snapshot()["calls"] == 3


def test_shadow_does_not_rewrite_drumline_kind():
    provider = JevProvider(JevConfig(mode="shadow"), api_key="k", transport=_shadow_transport("incident", noul=0.9))
    out = annotate_drumline(title="stall", content="chain stalled", deterministic_kind="handoff", provider=provider)
    assert out["kind"] == "handoff"
    assert out["suggested_kind"] == "incident"
    assert "sensitivity" in out["flags"] or "staleness" in out["flags"] or "contradiction" in out["flags"]
    assert out["mutates_history"] is False


def test_distill_still_stores_handoff_when_jev_suggests_incident(tmp_path):
    from mco.localstore import LocalStore
    store = LocalStore(tmp_path / "d.db")
    job = {
        "id": "job-1",
        "title": "Packet",
        "status": "completed",
        "target_agent_role": "codex",
        "output_payload": {"result": "Decided to retry later.\nNext steps: review."},
        "org_id": "default",
    }
    row = distill_job(store, job)
    assert row is not None
    stored = store.table("agent_context").select("*").execute().data
    assert stored[0]["kind"] == "handoff"
    store.close()


def test_fleet_classification_happens_after_broken_state():
    provider = JevProvider(JevConfig(mode="shadow"), api_key="k", transport=_shadow_transport("reroute"))
    out = annotate_fleet(
        instance_id="claude-mac",
        deterministic_state=BROKEN,
        broken=True,
        stalled=True,
        provider=provider,
    )
    assert out["deterministic_state"] == BROKEN
    assert out["suggested_action"] == "reroute"
    assert out["authorizes_effect"] is False


def test_notification_annotation_cannot_override_budget(monkeypatch):
    ntfy._last_sent.clear()
    ntfy._routine_sends.clear()
    provider = JevProvider(JevConfig(mode="shadow"), api_key="k", transport=_shadow_transport("urgent", noul=0.99))
    allowed = ntfy._allowed("m", "t", 3, {}, 1000.0)
    note = annotate_notification(title="t", message="m", priority=3, allowed=allowed, provider=provider)
    assert note["allowed"] is True
    # Spend the budget the same way as production.
    for i in range(ntfy.MAX_ROUTINE_PER_HOUR):
        ntfy._allowed(f"job {i}", "t", 3, {}, 1000.0 + i)
    blocked = ntfy._allowed("one too many", "t", 3, {}, 1000.0 + 100)
    annotated = annotate_notification(title="t", message="one too many", priority=3, allowed=blocked, provider=provider)
    assert blocked is False
    assert annotated["allowed"] is False
    assert annotated["budget_owner"] == "code"
    ntfy._last_sent.clear()
    ntfy._routine_sends.clear()


def test_chain_stall_detection_unchanged(tmp_path):
    from datetime import datetime, timedelta, timezone
    from mco.localstore import LocalStore
    store = LocalStore(tmp_path / "stall.db")
    now = datetime.now(timezone.utc)
    store.table("agent_jobs").insert({
        "id": "s04", "title": "Packet s04", "status": "completed",
        "target_agent_role": "codex", "leased_by_instance_id": "codex-beast",
        "input_payload": {"prompt": "work", "expects_successor": True},
        "created_at": (now - timedelta(seconds=1260)).isoformat(),
        "completed_at": (now - timedelta(seconds=660)).isoformat(),
    }).execute()
    result = delivery.sweep(
        store,
        config={"MCO_DELIVERY_STALL_SECONDS": "600", "MCO_ROUTE_FALLBACKS": ""},
        online_roles=lambda *_a: set(),
    )
    assert "s04" in result.chain_stalled
    store.close()


def test_question_sets_are_versioned():
    assert QUESTION_SET_VERSION == "1"
    assert set(DRUMLINE_QUESTIONS) == {"kind", "inject", "contradiction", "staleness", "sensitivity"}
    assert set(FLEET_QUESTIONS) == {"action"}
    assert set(NOTIFY_QUESTIONS) == {"duplicate", "impact"}


def test_persist_receipt_appends_audit_and_never_deletes(tmp_path):
    from mco.localstore import LocalStore
    from mco.orchestrator.jev import JevConfig, JevProvider
    store = LocalStore(tmp_path / "a.db")
    provider = JevProvider(JevConfig())
    receipt = provider.decide(
        use_case_id="bitcadence-ops-drumline",
        question_set_version="1",
        state={"k": 1},
        questions=DRUMLINE_QUESTIONS,
    )
    persist_receipt(store, "job-9", receipt)
    persist_receipt(store, "job-9", receipt)
    events = store.table("agent_job_events").select("*").eq("job_id", "job-9").execute().data
    assert len(events) == 2
    assert all(e["event"] == "jev_shadow_receipt" for e in events)
    store.close()
