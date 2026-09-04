"""Tray status light: icon states, daemon/gateway degradation, headless exit.

Talks to a small fake control-API server (ADR 0002 §4.4), not the real daemon.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typer.testing import CliRunner

from mco.tray.icons import (
    ICON_AMBER,
    ICON_GREEN,
    ICON_GREY,
    ICON_RED,
    icon_state_from_status,
    should_show_approval_badge,
    tooltip_text,
)
from mco.tray.app import (
    HEADLESS_MESSAGE,
    TrayApp,
    display_available,
    menu_structure,
    preflight,
)


# ── fake control API + gateway ──────────────────────────────────────────────


class _Holder:
    def __init__(self, payload: Any, token: str = "secret"):
        self.payload = payload
        self.token = token
        self.posts: list[str] = []
        self.gets: list[str] = []
        self.auth_failures = 0


def _handler(holder: _Holder):
    class Handler(BaseHTTPRequestHandler):
        def _read_auth(self) -> bool:
            expected = f"Bearer {holder.token}" if holder.token else None
            got = self.headers.get("Authorization")
            if expected and got != expected:
                holder.auth_failures += 1
                self.send_response(401)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return False
            return True

        def _json(self, code: int, body: Any) -> None:
            raw = json.dumps(body).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            if not self._read_auth():
                return
            holder.gets.append(self.path)
            path = self.path.split("?", 1)[0]
            if path == "/v1/status":
                self._json(200, holder.payload)
                return
            if path == "/v1/workers":
                workers = (holder.payload or {}).get("workers") if isinstance(holder.payload, dict) else []
                self._json(200, workers or [])
                return
            if path == "/api/jobs":
                self._json(200, holder.payload)
                return
            self.send_error(404)

        def do_POST(self):
            if not self._read_auth():
                return
            length = int(self.headers.get("Content-Length") or 0)
            if length:
                self.rfile.read(length)
            holder.posts.append(self.path.split("?", 1)[0])
            self._json(200, {"ok": True})

        def log_message(self, format, *args):
            return

    return Handler


def _serve(holder: _Holder):
    httpd = HTTPServer(("127.0.0.1", 0), _handler(holder))
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{httpd.server_address[1]}"
    return httpd, url


def _status(*workers, gateway_reachable=True, daemon=None):
    payload = {
        "daemon": daemon if daemon is not None else {"state": "running", "pid": 1},
        "workers": [
            {"name": name, "state": state, "role": "x", "instance": name}
            for name, state in workers
        ],
        "gateway_reachable": gateway_reachable,
    }
    return payload


# ── icon state selection (all four states) ──────────────────────────────────


def test_icon_green_when_all_workers_running():
    status = _status(("codex-beast", "running"), ("grok-beast", "running"))
    assert icon_state_from_status(status, True) == ICON_GREEN


def test_icon_amber_when_worker_restarting_or_backoff():
    assert icon_state_from_status(_status(("codex-beast", "starting")), True) == ICON_AMBER
    assert icon_state_from_status(_status(("codex-beast", "backoff")), True) == ICON_AMBER
    assert icon_state_from_status(_status(("codex-beast", "restarting")), True) == ICON_AMBER


def test_icon_red_when_any_worker_crashlooped():
    status = _status(("codex-beast", "running"), ("grok-beast", "crashlooped"))
    assert icon_state_from_status(status, True) == ICON_RED


def test_icon_grey_when_daemon_unreachable():
    status = _status(("codex-beast", "running"))
    assert icon_state_from_status(status, False) == ICON_GREY
    assert icon_state_from_status(None, False) == ICON_GREY
    assert icon_state_from_status(None, True) == ICON_GREY


def test_icon_grey_when_daemon_reports_stopped():
    status = _status(daemon={"state": "stopped"})
    assert icon_state_from_status(status, True) == ICON_GREY


def test_icon_amber_when_gateway_unreachable_but_daemon_up():
    status = _status(("codex-beast", "running"), gateway_reachable=False)
    assert icon_state_from_status(status, True) == ICON_AMBER


def test_red_wins_over_amber():
    status = _status(("a", "backoff"), ("b", "crashlooped"))
    assert icon_state_from_status(status, True) == ICON_RED


def test_stopped_workers_are_not_unhealthy():
    status = _status(("codex-beast", "stopped"), ("grok-beast", "running"))
    assert icon_state_from_status(status, True) == ICON_GREEN


# ── approval badge: live vs hidden ──────────────────────────────────────────


def test_badge_hidden_when_count_unknown():
    assert should_show_approval_badge(None) is False
    assert "awaiting" not in tooltip_text(ICON_GREEN, None)


def test_badge_hidden_when_count_zero_live():
    assert should_show_approval_badge(0) is False
    assert "awaiting" not in tooltip_text(ICON_GREEN, 0)


def test_badge_shown_when_count_live_and_nonzero():
    assert should_show_approval_badge(2) is True
    tip = tooltip_text(ICON_GREEN, 2)
    assert "2" in tip
    assert "awaiting approval" in tip


# ── fake-server integration ─────────────────────────────────────────────────


def test_refresh_reads_daemon_status_and_gateway_approvals():
    daemon = _Holder(_status(("codex-beast", "running"), ("grok-beast", "backoff")))
    gateway = _Holder([
        {"id": "j1", "status": "needs_approval", "title": "ship it"},
        {"id": "j2", "status": "pending", "title": "other"},
        {"id": "j3", "status": "needs_approval", "title": "also"},
    ])
    dhttpd, durl = _serve(daemon)
    ghttpd, gurl = _serve(gateway)
    try:
        app = TrayApp(daemon_url=durl, gateway_url=gurl, token="secret")
        snap = app.refresh()
    finally:
        dhttpd.shutdown()
        ghttpd.shutdown()

    assert snap.daemon_reachable is True
    assert snap.icon_state == ICON_AMBER
    assert [w["name"] for w in snap.workers] == ["codex-beast", "grok-beast"]
    assert snap.approval_count == 2
    assert should_show_approval_badge(snap.approval_count)
    assert any(path == "/v1/status" for path in daemon.gets)
    assert any(path.startswith("/api/jobs") for path in gateway.gets)


def test_daemon_unreachable_is_grey_and_does_not_raise():
    app = TrayApp(
        daemon_url="http://127.0.0.1:1",
        gateway_url="http://127.0.0.1:1",
        token="secret",
    )
    snap = app.refresh()
    assert snap.daemon_reachable is False
    assert snap.icon_state == ICON_GREY
    assert snap.workers == []
    assert snap.approval_count is None
    assert should_show_approval_badge(snap.approval_count) is False


def test_gateway_unreachable_hides_badge_but_keeps_workers():
    daemon = _Holder(_status(("codex-beast", "running")))
    dhttpd, durl = _serve(daemon)
    try:
        app = TrayApp(
            daemon_url=durl,
            gateway_url="http://127.0.0.1:1",
            token="secret",
        )
        snap = app.refresh()
    finally:
        dhttpd.shutdown()

    assert snap.daemon_reachable is True
    assert snap.workers[0]["name"] == "codex-beast"
    assert snap.approval_count is None
    assert should_show_approval_badge(snap.approval_count) is False
    assert "awaiting" not in tooltip_text(snap.icon_state, snap.approval_count)


def test_stale_approval_count_is_not_kept_when_gateway_drops():
    daemon = _Holder(_status(("codex-beast", "running")))
    gateway = _Holder([{"id": "j1", "status": "needs_approval"}])
    dhttpd, durl = _serve(daemon)
    ghttpd, gurl = _serve(gateway)
    try:
        app = TrayApp(daemon_url=durl, gateway_url=gurl, token="secret")
        first = app.refresh()
        assert first.approval_count == 1
        ghttpd.shutdown()
        ghttpd.server_close()
        second = app.refresh()
    finally:
        dhttpd.shutdown()

    assert second.daemon_reachable is True
    assert second.workers[0]["name"] == "codex-beast"
    assert second.approval_count is None
    assert should_show_approval_badge(second.approval_count) is False


def test_start_all_starts_daemon_service_when_unreachable():
    calls = []
    app = TrayApp(
        daemon_url="http://127.0.0.1:1",
        gateway_url="http://127.0.0.1:1",
        token="secret",
        service_starter=lambda: calls.append("start") or (True, "ok"),
    )
    app.refresh()
    app.start_all()
    assert calls == ["start"]


def test_start_all_posts_per_worker_when_daemon_up():
    daemon = _Holder(_status(("codex-beast", "stopped"), ("grok-beast", "stopped")))
    dhttpd, durl = _serve(daemon)
    try:
        app = TrayApp(daemon_url=durl, gateway_url="http://127.0.0.1:1", token="secret")
        app.refresh()
        app.start_all()
    finally:
        dhttpd.shutdown()
    assert "/v1/workers/codex-beast/start" in daemon.posts
    assert "/v1/workers/grok-beast/start" in daemon.posts


def test_per_worker_restart_posts_named_endpoint():
    daemon = _Holder(_status(("codex-beast", "running")))
    dhttpd, durl = _serve(daemon)
    try:
        app = TrayApp(daemon_url=durl, gateway_url="http://127.0.0.1:1", token="secret")
        app.refresh()
        app.restart_worker("codex-beast")
    finally:
        dhttpd.shutdown()
    assert "/v1/workers/codex-beast/restart" in daemon.posts


def test_open_console_is_the_door():
    opened = []
    app = TrayApp(
        daemon_url="http://127.0.0.1:1",
        gateway_url="http://127.0.0.1:18789",
        opener=lambda url: opened.append(url),
    )
    app.open_console()
    assert opened == ["http://127.0.0.1:18789/console"]


def test_menu_has_required_actions_and_per_worker_restart():
    workers = [{"name": "codex-beast", "state": "running"}]
    labels = {item["label"]: item for item in menu_structure(workers, True)}
    assert "Open Console" in labels
    assert "Start all" in labels
    assert "Stop all" in labels
    assert "Restart all" in labels
    assert "Quit" in labels
    worker = labels["codex-beast (running)"]
    assert worker["children"][0]["action"] == "restart"
    assert worker["children"][0]["worker"] == "codex-beast"


def test_menu_when_daemon_down():
    items = menu_structure([], False)
    labels = [i["label"] for i in items]
    assert "Daemon not running" in labels
    assert "Start all" in labels


def test_bearer_token_sent_to_daemon():
    daemon = _Holder(_status(("codex-beast", "running")), token="local-token")
    dhttpd, durl = _serve(daemon)
    try:
        app = TrayApp(daemon_url=durl, gateway_url="http://127.0.0.1:1", token="local-token")
        snap = app.refresh()
        wrong = TrayApp(daemon_url=durl, gateway_url="http://127.0.0.1:1", token="nope")
        bad = wrong.refresh()
    finally:
        dhttpd.shutdown()
    assert snap.daemon_reachable is True
    assert bad.daemon_reachable is False
    assert daemon.auth_failures >= 1


# ── headless / no display ───────────────────────────────────────────────────


def test_display_available_linux_headless(monkeypatch):
    monkeypatch.setattr("mco.tray.app.sys.platform", "linux")
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.delenv("MCO_TRAY_FORCE_HEADLESS", raising=False)
    assert display_available() is False


def test_display_available_linux_with_display(monkeypatch):
    monkeypatch.setattr("mco.tray.app.sys.platform", "linux")
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.delenv("MCO_TRAY_FORCE_HEADLESS", raising=False)
    assert display_available() is True


def test_preflight_headless_returns_clear_message(monkeypatch):
    monkeypatch.setattr("mco.tray.app.display_available", lambda: False)
    message = preflight()
    assert message == HEADLESS_MESSAGE
    assert "traceback" not in message.lower()


def test_mco_tray_exits_cleanly_without_display(monkeypatch):
    monkeypatch.setattr("mco.tray.app.display_available", lambda: False)
    from mco import cli

    result = CliRunner().invoke(cli.app, ["tray"])
    assert result.exit_code == 1
    combined = (result.output or "") + (result.stderr or "")
    assert "display" in combined.lower()
    assert "Traceback" not in combined
    assert "daemon does not depend" in combined.lower()


def test_mco_tray_is_registered():
    from mco import cli

    result = CliRunner().invoke(cli.app, ["tray", "--help"])
    assert result.exit_code == 0
    assert "status light" in result.output.lower() or "console" in result.output.lower()
