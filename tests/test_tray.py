"""Tray status light: icon states, daemon/gateway degradation, headless exit.

Talks to a small fake control-API server (ADR 0002 R7), not the real daemon.
Daemon and gateway tokens are separate; a missing agentd token is grey.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any
from urllib.parse import urlparse

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
    load_agentd_control_token,
    load_agentd_logs_token,
    load_daemon_url,
    menu_structure,
    preflight,
    worker_menu_label,
)


# ── fake control API + gateway ──────────────────────────────────────────────


class _Holder:
    def __init__(
        self,
        payload: Any,
        token: str = "secret",
        logs_token: str | None = None,
        log_lines: list[str] | None = None,
    ):
        self.payload = payload
        self.token = token
        self.logs_token = logs_token
        self.log_lines = log_lines or ["line-1"]
        self.posts: list[str] = []
        self.gets: list[str] = []
        self.auth_failures = 0
        self.log_auth_failures = 0
        self.tokens_seen: list[str] = []


def _handler(holder: _Holder):
    class Handler(BaseHTTPRequestHandler):
        def _bearer(self) -> str:
            got = self.headers.get("Authorization") or ""
            holder.tokens_seen.append(got)
            return got

        def _read_auth(self) -> bool:
            expected = f"Bearer {holder.token}" if holder.token else None
            got = self._bearer()
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
            parsed = urlparse(self.path)
            path = parsed.path
            if path == "/v1/logs":
                expected = f"Bearer {holder.logs_token}" if holder.logs_token else None
                got = self._bearer()
                if expected and got != expected:
                    holder.log_auth_failures += 1
                    self.send_response(401)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                holder.gets.append(self.path)
                self._json(200, {"lines": holder.log_lines})
                return
            if not self._read_auth():
                return
            holder.gets.append(self.path)
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
    built = []
    for item in workers:
        if len(item) == 3:
            name, state, mode = item
        else:
            name, state = item
            mode = "waker"
        built.append(
            {"name": name, "state": state, "role": "x", "instance": name, "mode": mode}
        )
    return {
        "daemon": daemon if daemon is not None else {"state": "running", "pid": 1},
        "workers": built,
        "gateway_reachable": gateway_reachable,
    }


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
    assert "latched until reset" in tooltip_text(ICON_RED, None)


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
    status = _status(("codex-beast", "stopped", "off"), ("grok-beast", "running"))
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
        app = TrayApp(
            daemon_url=durl,
            gateway_url=gurl,
            daemon_token="secret",
            gateway_token="secret",
        )
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
    assert not any(path.startswith("/v1/logs") for path in daemon.gets)


def test_daemon_unreachable_is_grey_and_does_not_raise():
    app = TrayApp(
        daemon_url="http://127.0.0.1:1",
        gateway_url="http://127.0.0.1:1",
        daemon_token="secret",
        gateway_token="secret",
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
            daemon_token="secret",
            gateway_token="secret",
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
        app = TrayApp(
            daemon_url=durl,
            gateway_url=gurl,
            daemon_token="secret",
            gateway_token="secret",
        )
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
        daemon_token="secret",
        service_starter=lambda: calls.append("start") or (True, "ok"),
    )
    app.refresh()
    app.start_all()
    assert calls == ["start"]


def test_start_all_posts_per_worker_when_daemon_up():
    daemon = _Holder(_status(("codex-beast", "stopped", "off"), ("grok-beast", "stopped")))
    dhttpd, durl = _serve(daemon)
    try:
        app = TrayApp(daemon_url=durl, gateway_url="http://127.0.0.1:1", daemon_token="secret")
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
        app = TrayApp(daemon_url=durl, gateway_url="http://127.0.0.1:1", daemon_token="secret")
        app.refresh()
        app.restart_worker("codex-beast")
    finally:
        dhttpd.shutdown()
    assert "/v1/workers/codex-beast/restart" in daemon.posts


def test_crashlooped_reset_posts_named_endpoint():
    daemon = _Holder(_status(("codex-beast", "crashlooped")))
    dhttpd, durl = _serve(daemon)
    try:
        app = TrayApp(daemon_url=durl, gateway_url="http://127.0.0.1:1", daemon_token="secret")
        app.refresh()
        assert app._snapshot.icon_state == ICON_RED
        app.reset_worker("codex-beast")
    finally:
        dhttpd.shutdown()
    assert "/v1/workers/codex-beast/reset" in daemon.posts


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
    workers = [{"name": "codex-beast", "state": "running", "mode": "waker"}]
    labels = {item["label"]: item for item in menu_structure(workers, True)}
    assert "Open Console" in labels
    assert "Start all" in labels
    assert "Stop all" in labels
    assert "Restart all" in labels
    assert "Quit" in labels
    worker = labels["codex-beast (running, waker)"]
    assert worker["children"][0]["action"] == "restart"
    assert worker["children"][0]["worker"] == "codex-beast"
    assert all(child["action"] != "reset" for child in worker["children"])


def test_menu_shows_mode_not_enabled():
    workers = [
        {"name": "grok-beast", "state": "stopped", "mode": "off"},
        {"name": "codex-beast", "state": "running", "mode": "poll"},
    ]
    labels = [item["label"] for item in menu_structure(workers, True)]
    assert "grok-beast (stopped, off)" in labels
    assert "codex-beast (running, poll)" in labels
    blob = " ".join(labels).lower()
    assert "enabled" not in blob
    assert "machine" not in blob


def test_menu_offers_reset_only_when_crashlooped():
    workers = [{"name": "codex-beast", "state": "crashlooped", "mode": "waker"}]
    labels = {item["label"]: item for item in menu_structure(workers, True)}
    worker = labels["codex-beast (crashlooped, waker)"]
    actions = [child["action"] for child in worker["children"]]
    assert "restart" in actions
    assert "reset" in actions


def test_menu_when_daemon_down():
    items = menu_structure([], False)
    labels = [i["label"] for i in items]
    assert "Daemon not running" in labels
    assert "Start all" in labels
    assert "machine" not in " ".join(labels).lower()


def test_bearer_token_sent_to_daemon():
    daemon = _Holder(_status(("codex-beast", "running")), token="agentd-secret")
    dhttpd, durl = _serve(daemon)
    try:
        app = TrayApp(daemon_url=durl, gateway_url="http://127.0.0.1:1", daemon_token="agentd-secret")
        snap = app.refresh()
        wrong = TrayApp(daemon_url=durl, gateway_url="http://127.0.0.1:1", daemon_token="nope")
        bad = wrong.refresh()
    finally:
        dhttpd.shutdown()
    assert snap.daemon_reachable is True
    assert bad.daemon_reachable is False
    assert daemon.auth_failures >= 1


def test_missing_agentd_token_is_grey_and_does_not_call_daemon():
    daemon = _Holder(_status(("codex-beast", "running")), token="agentd-secret")
    gateway = _Holder([{"id": "j1", "status": "needs_approval"}], token="gateway-secret")
    dhttpd, durl = _serve(daemon)
    ghttpd, gurl = _serve(gateway)
    try:
        app = TrayApp(
            daemon_url=durl,
            gateway_url=gurl,
            daemon_token="",
            gateway_token="gateway-secret",
        )
        snap = app.refresh()
    finally:
        dhttpd.shutdown()
        ghttpd.shutdown()
    assert snap.daemon_reachable is False
    assert snap.icon_state == ICON_GREY
    assert snap.workers == []
    assert daemon.gets == []
    assert daemon.auth_failures == 0
    assert snap.approval_count == 1


def test_does_not_send_gateway_token_to_daemon():
    daemon = _Holder(_status(("codex-beast", "running")), token="agentd-secret")
    gateway = _Holder([{"id": "j1", "status": "needs_approval"}], token="gateway-secret")
    dhttpd, durl = _serve(daemon)
    ghttpd, gurl = _serve(gateway)
    try:
        app = TrayApp(
            daemon_url=durl,
            gateway_url=gurl,
            daemon_token="gateway-secret",
            gateway_token="gateway-secret",
        )
        snap = app.refresh()
    finally:
        dhttpd.shutdown()
        ghttpd.shutdown()
    assert snap.daemon_reachable is False
    assert daemon.auth_failures >= 1
    assert snap.approval_count == 1


def test_separate_tokens_go_to_the_right_place():
    daemon = _Holder(_status(("codex-beast", "running")), token="agentd-secret")
    gateway = _Holder([{"id": "j1", "status": "needs_approval"}], token="gateway-secret")
    dhttpd, durl = _serve(daemon)
    ghttpd, gurl = _serve(gateway)
    try:
        app = TrayApp(
            daemon_url=durl,
            gateway_url=gurl,
            daemon_token="agentd-secret",
            gateway_token="gateway-secret",
        )
        snap = app.refresh()
    finally:
        dhttpd.shutdown()
        ghttpd.shutdown()
    assert snap.daemon_reachable is True
    assert snap.approval_count == 1
    assert any(t == "Bearer agentd-secret" for t in daemon.tokens_seen)
    assert any(t == "Bearer gateway-secret" for t in gateway.tokens_seen)
    assert not any(t == "Bearer gateway-secret" for t in daemon.tokens_seen)
    assert not any(t == "Bearer agentd-secret" for t in gateway.tokens_seen)


def test_load_agentd_token_does_not_fall_back_to_gateway_token(monkeypatch, tmp_path):
    missing = tmp_path / "agentd.token"
    monkeypatch.setattr("mco.tray.app.AGENTD_TOKEN_PATH", missing)
    monkeypatch.delenv("MCO_AGENTD_TOKEN", raising=False)
    monkeypatch.setenv("MCO_LOCAL_TOKEN", "gateway-secret")
    monkeypatch.setenv("MCO_AGENT_TOKEN", "also-gateway")
    token = load_agentd_control_token()
    assert token == ""
    assert token not in {"also-gateway", "gateway-secret"}


def test_load_agentd_token_reads_file_not_gateway(monkeypatch, tmp_path):
    path = tmp_path / "agentd.token"
    path.write_text("daemon-only\n", encoding="utf-8")
    monkeypatch.setattr("mco.tray.app.AGENTD_TOKEN_PATH", path)
    monkeypatch.delenv("MCO_AGENTD_TOKEN", raising=False)
    monkeypatch.setenv("MCO_LOCAL_TOKEN", "gateway-secret")
    assert load_agentd_control_token() == "daemon-only"


def test_load_daemon_url_is_user_scoped(monkeypatch):
    monkeypatch.delenv("MCO_AGENTD_URL", raising=False)
    from mco.agentd.control import CONTROL_HOST, CONTROL_PORT, CONTROL_PORT_BASE
    url = load_daemon_url()
    assert url.startswith(f"http://{CONTROL_HOST}:")
    port = int(url.rsplit(":", 1)[1])
    assert CONTROL_PORT_BASE <= port < CONTROL_PORT_BASE + 1000
    assert port == CONTROL_PORT


def test_fetch_logs_degrades_without_log_token():
    daemon = _Holder(
        _status(("codex-beast", "running")),
        token="agentd-secret",
        logs_token="log-secret",
    )
    dhttpd, durl = _serve(daemon)
    try:
        app = TrayApp(
            daemon_url=durl,
            gateway_url="http://127.0.0.1:1",
            daemon_token="agentd-secret",
            logs_token="",
        )
        app.refresh()
        assert app.fetch_logs() is None
        assert daemon.log_auth_failures == 0
        assert not any("/v1/logs" in path for path in daemon.gets)
    finally:
        dhttpd.shutdown()


def test_fetch_logs_degrades_when_control_token_lacks_log_capability():
    daemon = _Holder(
        _status(("codex-beast", "running")),
        token="agentd-secret",
        logs_token="log-secret",
        log_lines=["hello"],
    )
    dhttpd, durl = _serve(daemon)
    try:
        app = TrayApp(
            daemon_url=durl,
            gateway_url="http://127.0.0.1:1",
            daemon_token="agentd-secret",
            logs_token="agentd-secret",
        )
        assert app.fetch_logs() is None
        assert daemon.log_auth_failures >= 1
        ok = TrayApp(
            daemon_url=durl,
            gateway_url="http://127.0.0.1:1",
            daemon_token="agentd-secret",
            logs_token="log-secret",
        )
        assert ok.fetch_logs() == ["hello"]
    finally:
        dhttpd.shutdown()


def test_worker_menu_label_uses_mode():
    assert worker_menu_label({"name": "x", "state": "running", "mode": "off"}) == "x (running, off)"
    assert worker_menu_label({"name": "x", "state": "running"}) == "x (running)"


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
    assert "machine" not in message.lower()


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


def test_load_logs_token_empty_when_missing(monkeypatch, tmp_path):
    monkeypatch.setattr("mco.tray.app.AGENTD_LOGS_TOKEN_PATH", tmp_path / "nope")
    monkeypatch.delenv("MCO_AGENTD_LOGS_TOKEN", raising=False)
    assert load_agentd_logs_token() == ""
