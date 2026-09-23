"""J03 shadow ops: Drumline, watchdog, and ntfy annotations never authorize effects."""

from __future__ import annotations

import copy
import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from mco.localstore import LocalStore
from mco.notifiers import ntfy
from mco.orchestrator import delivery, drumline, presence
from mco.orchestrator.audit import get_events, record_event, verify_chain
from mco.orchestrator.drumline import KINDS, distill_job, recall, remember
from mco.orchestrator.jev import JevConfig, JevProvider, _digest
from mco.orchestrator.jev_ops import (
    annotate_drumline_output,
    annotate_model_route,
    annotate_notification,
    annotate_watchdog,
    metrics_snapshot,
    provider_from_config,
    reset_metrics,
    suggestion_must_not_authorize,
)
from mco.orchestrator.jev_questions import (
    CLAUDE_CODE_MODEL_ROUTE,
    CODEX_TASK_ROUTE,
    DRUMLINE_OPS,
    NOTIFY_QUALITY,
    WATCHDOG_SYMPTOM,
    all_registries,
    get_registry,
    registry_digest,
)
from tests.test_chain_stall import _done, _successor, _sweep
from tests.test_delivery_watchdog import BASE, STALL, _events, _job, _later, _online, _row
from tests.test_presence_state import THRESHOLD as PRESENCE_THRESHOLD
from tests.test_presence_state import _agent, _describe, _job as _presence_job
from tests.test_routes import FakeDB


SECRET = "secret-value"


def _now():
    return datetime.now(timezone.utc)


@pytest.fixture
def db(tmp_path):
    store = LocalStore(tmp_path / "test.db")
    yield store
    store.close()


@pytest.fixture(autouse=True)
def _clean_ntfy():
    ntfy._last_sent.clear()
    ntfy._routine_sends.clear()
    ntfy._last_rate_limit_log[0] = 0.0
    yield
    ntfy._last_sent.clear()
    ntfy._routine_sends.clear()


def _shadow_provider(payload, mode="shadow"):
    def handler(_request):
        return httpx.Response(
            200, json=payload, headers={"x-typesafe-request-id": "req-j03"},
        )

    model = "jev-latest" if mode == "shadow" else "jev-1.13.0"
    return JevProvider(
        JevConfig(mode=mode, model=model),
        api_key=SECRET,
        transport=httpx.MockTransport(handler),
        sleep=lambda _seconds: None,
    )


def _drumline_payload(kind="incident", inject="skip", relevance=0.1):
    return {
        "model": "jev-1.13.0",
        "answers": {
            "kind": {
                "type": "choice", "choice": kind, "confidence": 0.9,
                "probabilities": {"fact": 0.05, "decision": 0.05, "handoff": 0.1, "incident": 0.8},
            },
            "inject": {
                "type": "choice", "choice": inject, "confidence": 0.9,
                "probabilities": {"inject": 0.1, "skip": 0.8, "review": 0.1},
            },
            "contradiction": {"type": "noul", "noul": 0.2},
            "staleness": {"type": "noul", "noul": 0.1},
            "sensitivity": {"type": "noul", "noul": 0.0},
            "relevance": {
                "type": "score", "score": relevance, "confidence": 0.8,
                "probabilities": {"low": 0.9, "high": 0.1},
            },
        },
        "usage": {"input_tokens": 10, "output_tokens": 4},
    }


def _model_route_payload(tier="haiku"):
    probabilities = {t: (0.8 if t == tier else 0.1) for t in ("haiku", "sonnet", "opus")}
    return {
        "model": "jev-1.13.0",
        "answers": {
            "tier": {"type": "choice", "choice": tier, "confidence": 0.85, "probabilities": probabilities},
        },
        "usage": {"input_tokens": 12, "output_tokens": 2},
    }


def _watchdog_payload(action="noise"):
    return {
        "model": "jev-1.13.0",
        "answers": {
            "action": {
                "type": "choice", "choice": action, "confidence": 0.7,
                "probabilities": {
                    "retry": 0.1, "reroute": 0.1, "escalate": 0.1,
                    "operator-review": 0.1, "noise": 0.6,
                },
            },
        },
        "usage": {"input_tokens": 8, "output_tokens": 2},
    }


def _notify_payload(urgency="routine", duplicate=0.9, impact="low"):
    return {
        "model": "jev-1.13.0",
        "answers": {
            "duplicate": {"type": "noul", "noul": duplicate},
            "urgency": {
                "type": "choice", "choice": urgency, "confidence": 0.8,
                "probabilities": {"routine": 0.7, "elevated": 0.1, "urgent": 0.1, "emergency": 0.1},
            },
            "impact": {
                "type": "choice", "choice": impact, "confidence": 0.8,
                "probabilities": {"low": 0.8, "medium": 0.1, "high": 0.1},
            },
        },
        "usage": {"input_tokens": 6, "output_tokens": 2},
    }


def _failing_provider(status=None, timeout=False, malformed=False):
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if timeout:
            raise httpx.ReadTimeout("slow", request=request)
        if malformed:
            return httpx.Response(200, json={"model": "jev-1.13.0"})
        return httpx.Response(status, text="must-not-leak-" + SECRET)

    return JevProvider(
        JevConfig(mode="shadow", max_retries=1),
        api_key=SECRET,
        transport=httpx.MockTransport(handler),
        sleep=lambda _seconds: None,
    ), calls


# ── 1. Disabled-mode parity ──────────────────────────────────────────────────


class TestDisabledModeParity:
    def test_drumline_remember_recall_distill_match_deterministic_path(self):
        db = FakeDB()
        entry = remember(
            db, title="Prod DB is read-only on Sundays",
            content="Maintenance window 02:00-06:00 UTC.",
            kind="fact", tags=["Postgres", " ops "], created_by="joe",
        )
        assert entry["kind"] == "fact"
        assert entry["tags"] == ["postgres", "ops"]
        remember(db, title="Dynatrace token rotated", content="New scope problems.write", tags=["dynatrace"])
        remember(db, title="Office wifi password", content="Ask reception")
        hits = recall(db, query="dynatrace problems token")
        assert len(hits) == 1
        assert "Dynatrace" in hits[0]["title"]
        distilled = distill_job(db, {
            "id": "j1", "title": "Triage P-99",
            "target_agent_role": "claude",
            "input_payload": {"prompt": "Investigate high CPU on web-01"},
            "output_payload": {"result": "Root cause: runaway cron. Disabled job foo."},
        })
        assert distilled["kind"] == "handoff"
        assert distilled["source_job_id"] == "j1"
        assert "incident" not in KINDS
        snap = metrics_snapshot()
        assert snap["calls"] >= 1
        assert snap["disabled"] >= 1

    def test_disabled_recall_order_is_deterministic(self):
        db = FakeDB()
        for i in range(8):
            remember(db, title=f"note {i}", content=f"body {i}")
        hits = recall(db, limit=3)
        assert [h["title"] for h in hits] == ["note 7", "note 6", "note 5"]

    def test_delivery_still_rekicks_reroutes_escalates(self, db):
        _job(db, age_seconds=STALL + 5)
        first = delivery.sweep(db, config=BASE, online_roles=_online("grok"))
        assert first.rekicked == ["job-1"]
        assert _events(db) == [delivery.REKICKED]
        result = delivery.sweep(db, now=_later(STALL), config=BASE, online_roles=_online("grok"))
        assert result.rerouted == ["job-1"]
        assert _row(db)["target_agent_role"] == "grok"
        _job(db, age_seconds=STALL + 5, job_id="job-2")
        delivery.sweep(db, config=BASE, online_roles=_online())
        escalated = delivery.sweep(db, now=_later(STALL), config=BASE, online_roles=_online())
        assert escalated.escalated == ["job-2"]
        assert "jev_decision" not in _events(db, "job-2")

    def test_ntfy_budget_urgent_bypass_and_dedup_unchanged(self):
        from tests.test_ntfy_budget import (
            test_identical_message_is_not_repeated_within_the_window,
            test_routine_traffic_has_an_hourly_budget,
            test_urgent_messages_are_still_de_duplicated,
            test_urgent_messages_ignore_the_budget,
        )
        for fn in (
            test_identical_message_is_not_repeated_within_the_window,
            test_routine_traffic_has_an_hourly_budget,
            test_urgent_messages_ignore_the_budget,
            test_urgent_messages_are_still_de_duplicated,
        ):
            ntfy._last_sent.clear()
            ntfy._routine_sends.clear()
            fn()


# ── 2. Shadow annotations do not apply ───────────────────────────────────────


class TestShadowDoesNotApply:
    def test_recall_order_unchanged_when_jev_relevance_disagrees(self):
        import mco.orchestrator.jev_ops as jev_ops
        db = FakeDB()
        remember(db, title="Dynatrace token rotated", content="New scope problems.write", tags=["dynatrace"])
        remember(db, title="Office wifi password", content="Ask reception")
        jev_ops.provider_override = _shadow_provider(_drumline_payload(kind="incident", relevance=0.99))
        hits = recall(db, query="dynatrace problems token")
        assert [h["title"] for h in hits] == ["Dynatrace token rotated"]
        assert drumline.last_recall_annotations
        assert drumline.last_recall_annotations[0]["kind"] == "incident"
        assert drumline.last_recall_annotations[0]["applied"] is False

    def test_drumline_kind_unchanged_when_jev_disagrees(self, db):
        provider = _shadow_provider(_drumline_payload(kind="incident", inject="skip"))
        entry = remember(db, title="UNIQUE_STATE_TITLE_XYZ", content="handoff body",
                         kind="handoff", source_job_id="job-dl")
        annotation = annotate_drumline_output(
            provider, title=entry["title"], content=entry["content"],
            deterministic_kind="handoff", job_id="job-dl", db=db,
        )
        stored = db.table("agent_context").select("*").eq("id", entry["id"]).execute().data[0]
        assert stored["kind"] == "handoff"
        assert annotation["kind"] == "incident"
        assert annotation["inject"] == "skip"
        assert annotation["applied"] is False
        assert suggestion_must_not_authorize(annotation) is True
        assert annotation["receipt"].outcome == "shadow"
        assert metrics_snapshot()["disagreement"] >= 1

    def test_watchdog_still_escalates_when_jev_says_noise(self, db):
        provider = _shadow_provider(_watchdog_payload("noise"))
        _job(db, age_seconds=STALL + 5)
        delivery.sweep(db, config=BASE, online_roles=_online(), jev_provider=provider)
        result = delivery.sweep(
            db, now=_later(STALL), config=BASE, online_roles=_online(), jev_provider=provider,
        )
        assert result.escalated == ["job-1"]
        assert _row(db)["target_agent_role"] == "codex"
        names = _events(db)
        assert delivery.REKICKED in names and delivery.ESCALATED in names
        assert names.count("jev_decision") >= 1
        assert result.jev_annotations[-1]["action"] == "noise"
        assert result.jev_annotations[-1]["applied"] is False
        assert suggestion_must_not_authorize(result.jev_annotations[-1]) is True

    def test_watchdog_still_reroutes_when_jev_says_retry(self, db):
        provider = _shadow_provider(_watchdog_payload("retry"))
        _job(db, age_seconds=STALL + 5)
        delivery.sweep(db, config=BASE, online_roles=_online("grok"), jev_provider=provider)
        result = delivery.sweep(
            db, now=_later(STALL), config=BASE, online_roles=_online("grok"), jev_provider=provider,
        )
        assert result.rerouted == ["job-1"]
        assert _row(db)["target_agent_role"] == "grok"
        assert result.jev_annotations[-1]["action"] == "retry"
        assert result.jev_annotations[-1]["applied"] is False

    def test_ntfy_keeps_caller_priority_when_jev_demotes(self, monkeypatch):
        provider = _shadow_provider(_notify_payload(urgency="routine", duplicate=0.99))
        monkeypatch.setattr(ntfy, "get_ntfy_config", lambda: {
            "server": "https://example.invalid", "topic": "t", "token": None, "levels": [],
        })
        posted = []

        class _Resp:
            def raise_for_status(self):
                return None

        def _post(url, **kwargs):
            posted.append({"url": url, **kwargs})
            return _Resp()

        monkeypatch.setattr(ntfy.requests, "post", _post)
        for i in range(ntfy.MAX_ROUTINE_PER_HOUR):
            assert ntfy.notify(f"job {i}", title="routine", priority=3) is True
        assert ntfy.notify("one too many", title="routine-extra", priority=3) is False
        assert ntfy.notify("a job is stuck", title="Alarm", priority=5, jev_provider=provider) is True
        assert posted[-1]["headers"]["Priority"] == "5"
        annotation = annotate_notification(
            provider, title="Alarm", message="a job is stuck", deterministic_priority=5,
        )
        assert annotation["urgency"] == "routine"
        assert annotation["suggested_priority"] == 2
        assert annotation["applied"] is False
        assert suggestion_must_not_authorize(annotation) is True

    def test_suppressed_ntfy_does_not_call_jev(self, monkeypatch):
        def forbidden(_request):
            raise AssertionError("Jev must not run when ntfy suppressed the send")

        provider = JevProvider(
            JevConfig(mode="shadow"), api_key=SECRET,
            transport=httpx.MockTransport(forbidden),
        )
        monkeypatch.setattr(ntfy, "get_ntfy_config", lambda: {
            "server": "https://example.invalid", "topic": "t", "token": None, "levels": [],
        })
        monkeypatch.setattr(ntfy.requests, "post", lambda *a, **k: (_ for _ in ()).throw(AssertionError("post")))
        ntfy._last_sent[("BitCadence", "hello")] = 10_000_000_000.0
        assert ntfy.notify("hello", title="BitCadence", jev_provider=provider) is False


class TestModelRoute:
    """claude-code-model-route: advisory only, no answer ever applies itself."""

    def test_disabled_mode_suggests_nothing(self):
        provider = JevProvider(JevConfig(mode="disabled"))
        outcome = annotate_model_route(provider, task="Fix a typo in README.md")
        assert outcome["tier"] is None
        assert outcome["applied"] is False
        assert outcome["receipt"].outcome == "disabled"
        assert metrics_snapshot()["disabled"] >= 1

    def test_shadow_suggestion_is_advisory_only_and_persisted(self, db):
        provider = _shadow_provider(_model_route_payload(tier="haiku"))
        outcome = annotate_model_route(
            provider, task="Rename a variable in one file", job_id="job-mr", db=db,
        )
        assert outcome["tier"] == "haiku"
        assert outcome["applied"] is False
        assert suggestion_must_not_authorize(outcome) is True
        assert outcome["receipt"].outcome == "shadow"
        assert _events(db, "job-mr").count("jev_decision") == 1

    def test_disagreement_with_deterministic_tier_is_counted(self):
        provider = _shadow_provider(_model_route_payload(tier="opus"))
        outcome = annotate_model_route(provider, task="hard task", deterministic_tier="sonnet")
        assert outcome["tier"] == "opus"
        snap = metrics_snapshot()
        assert snap["disagreement"] >= 1

    def test_out_of_set_answer_is_treated_as_no_suggestion(self):
        provider = _shadow_provider(_model_route_payload(tier="gpt-5"))
        outcome = annotate_model_route(provider, task="anything")
        assert outcome["tier"] is None

    def test_provider_failure_falls_back_to_no_suggestion(self):
        provider, _calls = _failing_provider(status=429)
        outcome = annotate_model_route(provider, task="anything")
        assert outcome["tier"] is None
        assert outcome["receipt"].outcome == "fallback"
        assert outcome["applied"] is False


# ── 3. Jev cannot delete or rewrite history ──────────────────────────────────


class TestCannotRewriteHistory:
    def test_inject_skip_does_not_prevent_remember(self, db):
        import mco.orchestrator.jev_ops as jev_ops
        jev_ops.provider_override = _shadow_provider(_drumline_payload(inject="skip"))
        entry = remember(db, title="keep me", content="important fact", kind="fact",
                         source_job_id="job-skip")
        assert entry is not None
        rows = db.table("agent_context").select("*").execute().data
        assert any(r["title"] == "keep me" for r in rows)
        assert rows[0]["kind"] == "fact"

    def test_second_annotate_appends_and_leaves_prior_events(self, db):
        provider = _shadow_provider(_drumline_payload(kind="incident", inject="skip"))
        record_event(db, "job-hist", "created", "agent-1", "codex", {"ok": True})
        first = get_events(db, "job-hist")
        assert verify_chain(db, "job-hist")["ok"] is True
        annotate_drumline_output(
            provider, title="t", content="c", deterministic_kind="fact",
            job_id="job-hist", db=db,
        )
        mid = get_events(db, "job-hist")
        assert [e["event"] for e in mid] == ["created", "jev_decision"]
        assert mid[0]["hash"] == first[0]["hash"]
        assert mid[0]["detail"] == first[0]["detail"]
        annotate_drumline_output(
            provider, title="t", content="c", deterministic_kind="fact",
            job_id="job-hist", db=db,
        )
        final = get_events(db, "job-hist")
        assert [e["event"] for e in final] == ["created", "jev_decision", "jev_decision"]
        assert final[0]["hash"] == first[0]["hash"]
        assert final[1]["hash"] == mid[1]["hash"]
        assert verify_chain(db, "job-hist")["ok"] is True

    def test_jev_cannot_update_or_delete_context_or_events(self, db):
        record_event(db, "job-immut", "created", "agent-1", "codex")
        entry = remember(db, title="immutable", content="do not touch", kind="decision",
                         source_job_id="job-immut")
        ctx_before = db.table("agent_context").select("*").execute().data
        events_before = get_events(db, "job-immut")
        provider = _shadow_provider(_drumline_payload(kind="incident", inject="skip"))
        annotate_drumline_output(
            provider, title="immutable", content="do not touch",
            deterministic_kind="decision", job_id="job-immut", db=db,
        )
        ctx_after = db.table("agent_context").select("*").execute().data
        events_after = get_events(db, "job-immut")
        assert ctx_after == ctx_before
        assert events_after[:len(events_before)] == events_before
        assert events_after[-1]["event"] == "jev_decision"
        with pytest.raises(Exception):
            db.table("agent_job_events").update({"event": "tamper"}).eq("job_id", "job-immut").execute()
        assert entry["kind"] == "decision"


# ── 4. Deterministic fallback ────────────────────────────────────────────────


class TestDeterministicFallback:
    @pytest.mark.parametrize("kwargs", [
        {"status": 429},
        {"timeout": True},
        {"malformed": True},
    ])
    def test_provider_failures_fall_back_and_core_path_continues(self, kwargs):
        provider, _calls = _failing_provider(**kwargs)
        db = FakeDB()
        entry = remember(db, title="still stored", content="body", kind="fact")
        annotation = annotate_drumline_output(
            provider, title="still stored", content="body", deterministic_kind="fact",
        )
        assert entry["kind"] == "fact"
        assert annotation["receipt"].outcome == "fallback"
        assert annotation["kind"] is None
        assert annotation["applied"] is False
        assert suggestion_must_not_authorize(annotation) is True

    def test_watchdog_fallback_does_not_change_escalate(self, db):
        provider, _calls = _failing_provider(status=429)
        _job(db, age_seconds=STALL + 5)
        delivery.sweep(db, config=BASE, online_roles=_online(), jev_provider=provider)
        result = delivery.sweep(
            db, now=_later(STALL), config=BASE, online_roles=_online(), jev_provider=provider,
        )
        assert result.escalated == ["job-1"]
        assert result.jev_annotations[-1]["receipt"].outcome == "fallback"
        assert result.jev_annotations[-1]["applied"] is False


# ── 5. Question-set versioning ───────────────────────────────────────────────


class TestQuestionSetVersioning:
    def test_digest_is_stable_and_bound_to_version_and_questions(self):
        d1 = registry_digest(DRUMLINE_OPS)
        d2 = registry_digest(DRUMLINE_OPS)
        assert d1 == d2
        assert len(d1) == 64
        registry = get_registry(DRUMLINE_OPS)
        assert registry["version"] == "2"
        assert registry["use_case_id"] == DRUMLINE_OPS
        assert _digest(registry["questions"]) == d1
        mutated = copy.deepcopy(registry["questions"])
        mutated["kind"]["instructions"] = "CHANGED"
        assert _digest(mutated) != d1
        bound = {"version": registry["version"], "questions": registry["questions"]}
        assert _digest({**bound, "version": "3"}) != _digest(bound)

    def test_all_registries_are_frozen(self):
        registries = all_registries()
        assert set(registries) == {
            DRUMLINE_OPS, WATCHDOG_SYMPTOM, NOTIFY_QUALITY,
            CLAUDE_CODE_MODEL_ROUTE, CODEX_TASK_ROUTE,
        }
        for registry in registries.values():
            expected = "2" if registry["use_case_id"] == DRUMLINE_OPS else "1"
            assert registry["version"] == expected
            assert registry["questions"]
            assert "incident" not in KINDS


# ── 6. Broken-worker / chain-stall / push-budget / crash-loop regressions ───


class TestRegressions:
    def test_chain_stall_without_follow_up_is_recorded(self, db):
        _done(db, "s04", title="S04: policy + human gate")
        result = _sweep(db)
        assert result.chain_stalled == ["s04"]
        assert delivery.CHAIN_STALLED in [e["event"] for e in get_events(db, "s04")]

    def test_chain_stall_kill_switch_disables(self, db):
        _done(db, "s04")
        assert _sweep(db, {"MCO_KILL_SWITCH": "on"}).chain_stalled == []

    def test_successor_by_explicit_chain_parent_counts(self, db):
        _done(db, "s03", worker="codex-beast")
        _successor(db, "review", source="someone-else", chain_parent="s03")
        assert _sweep(db).chain_stalled == []

    def test_pending_job_rekick_then_reroute_then_escalate(self, db):
        _job(db, age_seconds=STALL + 5)
        first = delivery.sweep(db, config=BASE, online_roles=_online("claude"))
        assert first.rekicked == ["job-1"]
        second = delivery.sweep(db, now=_later(STALL), config=BASE, online_roles=_online("claude"))
        assert second.rerouted == ["job-1"]
        assert _row(db)["target_agent_role"] == "claude"
        _job(db, age_seconds=STALL + 5, job_id="stuck")
        delivery.sweep(db, config=BASE, online_roles=_online())
        third = delivery.sweep(db, now=_later(STALL), config=BASE, online_roles=_online())
        assert third.escalated == ["stuck"]

    def test_reroute_cas_leaves_leased_job_alone(self, db):
        _job(db, age_seconds=STALL + 5)
        delivery.sweep(db, config=BASE, online_roles=_online("claude"))
        db.table("agent_jobs").update({"status": "leased"}).eq("id", "job-1").execute()
        result = delivery.sweep(db, now=_later(STALL), config=BASE, online_roles=_online("claude"))
        assert result.rerouted == []
        assert _row(db)["status"] == "leased"
        assert _row(db)["target_agent_role"] == "codex"

    def test_broken_worker_is_not_a_reroute_target(self, db):
        now = _now()
        db.table("agent_registry").insert({
            "instance_id": "claude-mac", "role": "claude", "status": "online",
            "last_seen_at": now.isoformat(),
        }).execute()
        db.table("agent_registry").insert({
            "instance_id": "codex-beast", "role": "codex", "status": "online",
            "last_seen_at": now.isoformat(),
        }).execute()
        db.table("agent_jobs").insert({
            "id": "stuck-on-codex", "title": "stuck", "status": "pending",
            "target_agent_role": "codex", "target_agent_id": "codex-beast",
            "created_at": (now - timedelta(seconds=STALL + 120)).isoformat(),
        }).execute()
        described = presence.describe_fleet(
            db,
            [
                {"instance_id": "codex-beast", "role": "codex", "status": "online",
                 "last_seen_at": now.isoformat()},
                {"instance_id": "claude-mac", "role": "claude", "status": "online",
                 "last_seen_at": now.isoformat()},
            ],
            threshold=PRESENCE_THRESHOLD, connected=set(), stall_seconds=STALL,
        )
        assert presence.available_roles(described) == {"claude"}

        def online_roles(db_client, org):
            rows = db_client.table("agent_registry").select("*").execute().data or []
            fleet = presence.describe_fleet(
                db_client, rows, threshold=PRESENCE_THRESHOLD, connected=set(), stall_seconds=STALL,
            )
            return presence.available_roles(fleet, org)

        config = {**BASE, "MCO_ROUTE_FALLBACKS": "grok:codex|claude"}
        _job(db, age_seconds=STALL + 5, job_id="from-grok", role="grok", instance="grok-1")
        delivery.sweep(db, config=config, online_roles=online_roles)
        result = delivery.sweep(db, now=_later(STALL), config=config, online_roles=online_roles)
        assert _row(db, "from-grok")["target_agent_role"] == "claude"
        assert result.rerouted == ["from-grok"]

    def test_presence_broken_derivation_still_holds(self, db):
        _presence_job(db, "stuck", role="codex", target="codex-beast", age=STALL + 120)
        row = _describe(db, [_agent("codex-beast", "codex")], {"codex-beast"})["codex-beast"]
        assert row["state"] == "broken"
        _presence_job(db, "moved", status="completed", role="claude")
        record_event(db, "moved", delivery.REROUTED, "system", "delivery",
                     {"from_role": "codex", "from_instance": "codex-beast", "to_role": "claude"})
        moved = _describe(db, [_agent("codex-beast", "codex")], {"codex-beast"})["codex-beast"]
        assert moved["state"] == "broken"

    def test_crashloop_safeguard_unchanged(self, tmp_path):
        from tests.test_agentd_supervisor import test_five_failures_in_window_crashloop_and_stop_restarting
        test_five_failures_in_window_crashloop_and_stop_restarting(tmp_path)


# ── 7. Receipt schema ────────────────────────────────────────────────────────


class TestReceiptSchema:
    def test_receipt_fields_omit_secrets_and_raw_state(self):
        provider = _shadow_provider(_drumline_payload())
        annotation = annotate_drumline_output(
            provider,
            title="UNIQUE_STATE_TITLE_XYZ",
            content="UNIQUE_STATE_BODY_XYZ",
            deterministic_kind="fact",
        )
        receipt = annotation["receipt"]
        data = receipt.to_dict()
        for key in (
            "use_case_id", "question_set_version", "question_set_digest", "model",
            "state_digest", "answers", "probabilities", "confidence", "latency_ms",
            "usage", "mode", "outcome",
        ):
            assert key in data
        assert data["use_case_id"] == DRUMLINE_OPS
        assert data["question_set_version"] == "2"
        assert data["question_set_digest"] == registry_digest(DRUMLINE_OPS)
        assert data["mode"] == "shadow"
        blob = json.dumps(data)
        assert SECRET not in blob
        assert "api_key" not in blob
        assert "UNIQUE_STATE_TITLE_XYZ" not in blob
        assert "UNIQUE_STATE_BODY_XYZ" not in blob
        assert annotation["applied"] is False

    def test_disabled_receipt_and_provider_from_config_never_throw(self):
        class Boom:
            def get(self, *_a, **_k):
                raise RuntimeError("nope")

        provider = provider_from_config(Boom())
        assert provider.config.mode == "disabled"
        annotation = annotate_watchdog(
            None, deterministic_action="retry",
            state={"pending_seconds": 12, "reroutes": 0, "max_reroutes": 2,
                   "stall_seconds": 600, "crash_loop": False, "chain_stalled": False,
                   "broken": False, "reason": None, "worker_state": None},
        )
        assert annotation["applied"] is False
        assert annotation["action"] is None
        assert annotation["receipt"].outcome == "disabled"
        assert suggestion_must_not_authorize(annotation) is True

    def test_assist_mode_still_does_not_apply(self):
        provider = _shadow_provider(_watchdog_payload("noise"), mode="assist")
        annotation = annotate_watchdog(
            provider, deterministic_action="escalate",
            state={"pending_seconds": 1200, "reroutes": 2, "max_reroutes": 2,
                   "stall_seconds": 600, "crash_loop": False, "chain_stalled": False,
                   "broken": True, "reason": "limit", "worker_state": None},
        )
        assert annotation["action"] == "noise"
        assert annotation["applied"] is False
        assert annotation["receipt"].mode == "assist"
        assert suggestion_must_not_authorize(annotation) is True

    def test_metrics_reset(self):
        reset_metrics()
        assert metrics_snapshot() == {
            "calls": 0, "latency_ms_sum": 0, "fallbacks": 0, "errors": 0,
            "disabled": 0, "shadow": 0, "disagreement": 0,
        }
        annotate_drumline_output(
            None, title="t", content="c", deterministic_kind="fact",
        )
        snap = metrics_snapshot()
        assert snap["calls"] == 1
        assert snap["disabled"] == 1
        reset_metrics()
        assert metrics_snapshot()["calls"] == 0


class TestTypeSafeWireSchema:
    """Every registered question set must pass the same shape rules TypeSafe enforces."""

    def test_every_registry_passes_the_wire_schema(self):
        from mco.orchestrator.jev import _validate_questions
        for registry in all_registries().values():
            _validate_questions(registry["questions"])

    def test_score_criteria_must_be_an_ordered_list(self):
        from mco.orchestrator.jev import JevProtocolError, _validate_questions
        bad = {"relevance": {"type": "score", "instructions": "x", "criteria": {"0": "low", "1": "high"}}}
        with pytest.raises(JevProtocolError, match="ordered list"):
            _validate_questions(bad)
        with pytest.raises(JevProtocolError, match="ordered list"):
            _validate_questions({"fit": {"type": "score", "instructions": "x", "criteria": ["only one"]}})
        _validate_questions({"fit": {"type": "score", "instructions": "x", "criteria": ["low", "high"]}})
