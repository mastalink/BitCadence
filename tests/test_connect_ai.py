"""Slice 7: Connect an AI. One tap per app (Claude, Codex, Gemini, Antigravity, Cursor) that finds
the app and writes its connection settings after backing up the original; same code behind
`bitcadence connect` and the console page."""
import json
import subprocess
import sys
from pathlib import Path

import pytest
from mco.orchestrator.auth import require_agent
from fastapi import FastAPI
from fastapi.testclient import TestClient
from typer.testing import CliRunner

import mco.cli as cli
from mco import connect_ai, plain
from mco.orchestrator.connect_routes import connect_router

ROOT = Path(__file__).parents[1]
SRC = ROOT / "src/mco/console_src"
runner = CliRunner()


@pytest.fixture
def apps(tmp_path, monkeypatch):
    """Fake computer: Claude (json, with another server), Codex (toml), Cursor (json); no Gemini."""
    claude = tmp_path / "claude_desktop_config.json"
    claude.write_text(json.dumps({"mcpServers": {"other": {"command": "x"}}, "theme": "dark"}), encoding="utf-8")
    codex = tmp_path / "config.toml"
    codex.write_text('model = "gpt"\n\n[mcp_servers.other]\ncommand = "y"\n', encoding="utf-8")
    cursor = tmp_path / "mcp.json"
    cursor.write_text("{not json", encoding="utf-8")
    table = {"claude": [claude], "codex": [codex], "cursor": [cursor], "gemini": [tmp_path / "nope.json"]}
    monkeypatch.setattr(connect_ai, "targets", lambda: table)
    return table


# -- the engine ----------------------------------------------------------------
def test_status_says_found_and_connected(apps):
    rows = {r["app"]: r for r in connect_ai.status()}
    assert rows["claude"]["found"] and not rows["claude"]["connected"]
    assert not rows["gemini"]["found"]
    assert rows["codex"]["name"] == "ChatGPT / Codex"


def test_json_connect_backs_up_first_keeps_other_settings_and_is_idempotent(apps):
    path = apps["claude"][0]
    original = path.read_text(encoding="utf-8")
    res = connect_ai.connect("claude")
    assert res["changed"] and (path.parent / res["backup"]).read_text(encoding="utf-8") == original
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["theme"] == "dark" and "other" in data["mcpServers"] and "bitcadence" in data["mcpServers"]
    again = connect_ai.connect("claude")
    assert again == {"app": "claude", "changed": False, "backup": None}
    assert not list(path.parent.glob("*.tmp"))


def test_toml_connect_appends_a_block_and_disconnect_removes_only_it(apps):
    path = apps["codex"][0]
    original = path.read_text(encoding="utf-8")
    assert connect_ai.connect("codex")["changed"]
    text = path.read_text(encoding="utf-8")
    assert "[mcp_servers.bitcadence]" in text and 'model = "gpt"' in text and "[mcp_servers.other]" in text
    assert connect_ai.status({"codex": [path]})[0]["connected"]
    assert connect_ai.disconnect("codex")["changed"]
    assert path.read_text(encoding="utf-8").strip() == original.strip()
    assert connect_ai.disconnect("codex")["changed"] is False


def test_first_backup_is_never_overwritten(apps):
    path = apps["claude"][0]
    original = path.read_text(encoding="utf-8")
    connect_ai.connect("claude")
    connect_ai.disconnect("claude")
    first = path.with_suffix(path.suffix + ".bak")
    assert first.read_text(encoding="utf-8") == original


def test_unreadable_settings_are_left_alone(apps):
    path = apps["cursor"][0]
    with pytest.raises(connect_ai.ConnectError) as err:
        connect_ai.connect("cursor")
    assert err.value.kind == "unreadable" and path.read_text(encoding="utf-8") == "{not json"
    assert not path.with_suffix(".json.bak").exists()


def test_missing_and_unknown_apps(apps):
    with pytest.raises(connect_ai.ConnectError) as err:
        connect_ai.connect("gemini")
    assert err.value.kind == "not_found" and "Gemini" in str(err.value)
    with pytest.raises(connect_ai.ConnectError) as err:
        connect_ai.connect("emacs")
    assert err.value.kind == "unknown"


def test_check_reports_our_side_honestly(apps, monkeypatch):
    assert connect_ai.check("claude")["ok"] is False
    connect_ai.connect("claude")
    assert connect_ai.check("claude")["ok"] is True
    monkeypatch.setattr(connect_ai, "server_entry", lambda: {"command": "Z:/gone/python.exe", "args": []})
    assert "moved" in connect_ai.check("claude")["message"]


def test_other_snippet_is_the_usual_shape():
    data = json.loads(connect_ai.other_snippet())
    assert data["mcpServers"]["bitcadence"]["args"] == ["-m", "mco.cli", "mcp"]


# -- the CLI: the production path calls the same engine --------------------------
def test_cli_connect_calls_the_shared_engine(apps, monkeypatch):
    calls = []
    real = connect_ai.connect
    monkeypatch.setattr(connect_ai, "connect", lambda app, table=None: calls.append(app) or real(app, table))
    result = runner.invoke(cli.app, ["connect", "claude", "--yes"])
    assert result.exit_code == 0 and calls == ["claude"]
    assert "Connected. Restart Claude to see it." in result.output


def test_cli_connect_test_disconnect_and_other(apps):
    runner.invoke(cli.app, ["connect", "codex", "--yes"])
    ok = runner.invoke(cli.app, ["connect", "codex", "--test"])
    assert ok.exit_code == 0 and "set up" in ok.output
    gone = runner.invoke(cli.app, ["connect", "codex", "--disconnect", "--yes"])
    assert gone.exit_code == 0 and "disconnected" in gone.output
    assert runner.invoke(cli.app, ["connect", "codex", "--test"]).exit_code == 1
    other = runner.invoke(cli.app, ["connect", "other"])
    assert other.exit_code == 0 and "mcpServers" in other.output


def test_cli_missing_app_is_plain(apps):
    result = runner.invoke(cli.app, ["connect", "gemini", "--yes"])
    assert result.exit_code == 1 and "I couldn't find Gemini on this computer." in result.output
    assert "Traceback" not in result.output


def test_menu_connect_item_runs_the_same_function():
    from mco import menu
    src = Path(menu.__file__).read_text(encoding="utf-8")
    assert '"connect": lambda: plain.do_connect("")' in src


# -- the gateway path the console calls ------------------------------------------
def _http(scopes=("agents:read", "agents:manage")):
    app = FastAPI()
    app.include_router(connect_router)
    # Override the shared auth dependency; newer FastAPI wraps included routers,
    # so per-route dependency objects are no longer listed in app.routes.
    app.dependency_overrides[require_agent] = lambda: {
                "org_id": "default", "instance_id": "me", "role": "human", "scopes": list(scopes)}
    return TestClient(app)


def test_routes_need_a_login():
    app = FastAPI()
    app.include_router(connect_router)
    http = TestClient(app)
    assert http.get("/api/connect-ai").status_code == 401
    assert http.post("/api/connect-ai/claude/connect").status_code == 401


def test_routes_list_connect_test_disconnect(apps):
    http = _http()
    listing = http.get("/api/connect-ai").json()["apps"]
    assert {a["app"] for a in listing} == {"claude", "codex", "cursor", "gemini"}
    res = http.post("/api/connect-ai/claude/connect").json()
    assert res["changed"] and "close and reopen" in res["message"]
    assert http.post("/api/connect-ai/claude/test").json()["ok"] is True
    assert http.post("/api/connect-ai/claude/disconnect").json()["changed"] is True
    body = json.dumps([listing, res])
    assert str(apps["claude"][0].parent) not in body  # no file paths leave the machine


def test_routes_map_problems_to_plain_errors(apps):
    http = _http()
    missing = http.post("/api/connect-ai/gemini/connect")
    assert missing.status_code == 404 and "Gemini" in missing.json()["detail"]
    assert http.post("/api/connect-ai/cursor/connect").status_code == 409
    assert http.get("/api/connect-ai/other").json()["snippet"].startswith("{")


def test_the_gateway_serves_the_connect_routes():
    paths = {getattr(r, "path", None) for r in cli.create_app().routes} | set(cli.create_app().openapi()["paths"])
    assert {"/api/connect-ai", "/api/connect-ai/{app}/connect", "/api/connect-ai/{app}/disconnect",
            "/api/connect-ai/{app}/test"} <= paths


# -- the console -----------------------------------------------------------------
def _read(prefix):
    return next(SRC.glob(prefix + "*")).read_text(encoding="utf-8")


def test_console_connect_page_calls_the_api_and_is_in_the_bundle():
    page, store, shell = (_read(p) for p in ("2ed3f6b1", "47e66145", "8ec84a72"))
    assert 'api("/api/connect-ai")' in store and '"/api/connect-ai/" + encodeURIComponent(app)' in store
    assert "store.connectList()" in page and "store.connectAction(app," in page
    assert "<ConnectPage />" in shell and 'label: "Connect an AI"' in shell
    result = subprocess.run([sys.executable, str(ROOT / "scripts/build_console.py"), "verify"], cwd=ROOT,
                            capture_output=True, text=True)
    assert result.returncode == 0 and "0 differ" in result.stdout


def test_console_connect_page_follows_the_design_rules():
    page = _read("2ed3f6b1").split("function ConnectPage")[1].split("// ----- Schedules page")[0]
    for text in ("Changes apply to the gateway computer", "Send a test", "Disconnect", "Another app", "Install it there"):
        assert text in page
    assert "aria-live" in page and 'aria-hidden="true"' in page  # a shape and a word, never colour alone
    assert "token" not in page.lower() and "JSON" not in page
