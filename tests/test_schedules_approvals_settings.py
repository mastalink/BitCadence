"""Redesign slice 6: plain schedules, no raw 403s, settings without jargon.

Spec: design/redesign-v1/07-schedules.html, 08-approvals.html, 09-settings.html, CLI.md.
Nothing here touches the real ~/.mco, the real registry or a real gateway.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import httpx
import pytest
from mco.orchestrator.auth import require_agent
from fastapi import FastAPI
from fastapi.testclient import TestClient
from typer.testing import CliRunner

import mco.cli as cli
from mco import approver, plain, scheduler
from mco import schedules_plain as sp
from mco.orchestrator.schedules_routes import schedules_router

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src/mco/console_src"
runner = CliRunner()

HAND_WRITTEN = """# my own notes: keep me
launchers:
  nightly-audit:
    role: reviewer
    title: Nightly dependency audit
    instructions: Look for new CVEs.
  console:
    url: http://127.0.0.1:18789/console

schedules:
  # existing one
  nightly-audit:
    launcher: nightly-audit
    cron: "0 3 * * *"
    timezone: America/New_York
"""


@pytest.fixture
def schedules_file(tmp_path, monkeypatch):
    path = tmp_path / "schedules.yaml"
    path.write_text(HAND_WRITTEN, encoding="utf-8")
    monkeypatch.setattr(scheduler, "SCHEDULES_CONFIG_PATH", path)
    monkeypatch.setattr("mco.launcher._state_path", lambda p=None: tmp_path / "state.json")
    monkeypatch.setattr(sp, "detect_timezone", lambda: "America/New_York")
    return path


# -- words <-> schedule --------------------------------------------------------
@pytest.mark.parametrize("words,cron,back", [
    ("every weekday at 2 AM", "0 2 * * 1-5", "Every weekday at 2:00 AM"),
    ("Every day at 14:30", "30 14 * * *", "Every day at 2:30 PM"),
    ("daily at 7 am", "0 7 * * *", "Every day at 7:00 AM"),
    ("every monday, wednesday and friday at 9:15 pm", "15 21 * * 1,3,5",
     "Every Monday, Wednesday and Friday at 9:15 PM"),
    ("every sunday at 12 pm", "0 12 * * 0", "Every Sunday at 12:00 PM"),
    ("every hour", "0 * * * *", "Every hour"),
])
def test_plain_words_become_a_schedule_and_back(words, cron, back):
    picked = sp.parse_phrase(words)
    made = sp.build_cron(picked["frequency"], picked["hour"], picked["minute"], picked["days"])
    assert made == cron and sp.describe_cron(made) == back
    scheduler.parse_cron(made)  # the real scheduler accepts everything we write


def test_a_bad_schedule_says_what_to_try_in_the_words_of_the_spec():
    with pytest.raises(sp.ScheduleWordsError) as bad:
        sp.parse_phrase("every second tuesday")
    assert str(bad.value) == 'I didn\'t understand "every second tuesday". Try "every weekday at 2 AM".'
    assert "invalid cron" not in str(bad.value)


def test_a_time_with_no_am_or_pm_is_asked_about_not_guessed():
    with pytest.raises(sp.ScheduleWordsError, match="morning or the afternoon"):
        sp.parse_phrase("every day at 2")


def test_unusual_cron_gets_a_safe_sentence_not_a_cron_string():
    assert sp.describe_cron("*/7 3 1 * *") == "On a custom schedule"
    assert sp.describe_every(1800) == "Every 30 minutes" and sp.describe_every(86400) == "Every day"


# -- the file -------------------------------------------------------------------
def test_add_schedule_appends_keeps_comments_and_stays_loadable(schedules_file):
    saved = sp.add_schedule("nightly-audit", "0 2 * * 1-5")
    text = schedules_file.read_text(encoding="utf-8")
    assert text.startswith("# my own notes: keep me") and "# existing one" in text
    assert saved["words"] == "Every weekday at 2:00 AM" and saved["id"] == "nightly-audit-2"
    _, loaded = scheduler.load_config(schedules_file)
    assert loaded["nightly-audit-2"].cron.raw == "0 2 * * 1-5"
    assert loaded["nightly-audit-2"].timezone == "America/New_York"  # never silently UTC


def test_add_schedule_refuses_unknown_and_local_launchers(schedules_file):
    before = schedules_file.read_text(encoding="utf-8")
    for bad in ("nope", "console"):
        with pytest.raises(sp.ScheduleWordsError):
            sp.add_schedule(bad, "0 2 * * *")
    assert schedules_file.read_text(encoding="utf-8") == before


def test_add_schedule_without_any_file_says_there_is_nothing_to_schedule(tmp_path, monkeypatch):
    monkeypatch.setattr(scheduler, "SCHEDULES_CONFIG_PATH", tmp_path / "missing.yaml")
    with pytest.raises(sp.ScheduleWordsError, match="nothing to schedule"):
        sp.add_schedule("x", "0 2 * * *")
    assert not (tmp_path / "missing.yaml").exists()


def test_list_is_plain_words_with_no_yaml_or_cron(schedules_file):
    data = sp.list_schedules()
    assert data["can_schedule"] == [{"id": "nightly-audit", "label": "Nightly dependency audit"}]
    item = data["schedules"][0]
    assert item["when"] == "Every day at 3:00 AM" and item["on"] is True
    assert "cron" not in json.dumps(data).lower() and "yaml" not in json.dumps(data).lower()


def test_set_enabled_flips_only_that_line(schedules_file):
    sp.set_enabled("nightly-audit", False)
    text = schedules_file.read_text(encoding="utf-8")
    assert "enabled: false" in text and "# existing one" in text
    assert sp.list_schedules()["schedules"][0]["next"] == "Off"
    sp.set_enabled("nightly-audit", True)
    assert "enabled: true" in schedules_file.read_text(encoding="utf-8")
    with pytest.raises(sp.ScheduleWordsError):
        sp.set_enabled("ghost", True)


# -- the CLI path (production code, not the helper) -------------------------------
def test_schedule_add_command_writes_the_file_from_plain_words(schedules_file):
    result = runner.invoke(cli.app, ["schedule", "add", "--what", "Nightly dependency audit",
                                     "--when", "every weekday at 2 AM", "--yes"])
    assert result.exit_code == 0, result.output
    assert "Saved. Every weekday at 2:00 AM." in result.output
    assert "0 2 * * 1-5" in schedules_file.read_text(encoding="utf-8")


def test_schedule_add_command_gives_the_plain_error_for_bad_words(schedules_file):
    before = schedules_file.read_text(encoding="utf-8")
    result = runner.invoke(cli.app, ["schedule", "add", "--what", "nightly-audit",
                                     "--when", "every second tuesday", "--yes"])
    assert result.exit_code == 1
    assert 'Try "every weekday at 2 AM".' in result.output
    assert schedules_file.read_text(encoding="utf-8") == before


def test_schedule_add_asks_what_how_often_and_what_time(schedules_file, monkeypatch):
    monkeypatch.setattr(plain, "interactive", lambda: True)
    answers = iter(["2", "2 AM", "y"])  # Every weekday, at 2 AM, save
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    assert plain.do_schedule_add() == 0
    assert "0 2 * * 1-5" in schedules_file.read_text(encoding="utf-8")


def test_schedule_add_declined_changes_nothing(schedules_file, monkeypatch, capsys):
    monkeypatch.setattr(plain, "interactive", lambda: True)
    answers = iter(["1", "7 AM", "n"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    before = schedules_file.read_text(encoding="utf-8")
    assert plain.do_schedule_add() == 0
    assert "Every day at 7:00 AM" in capsys.readouterr().out
    assert schedules_file.read_text(encoding="utf-8") == before


def test_bare_schedule_shows_sentences_not_a_cron_table(schedules_file):
    result = runner.invoke(cli.app, ["schedule"])
    assert result.exit_code == 0
    assert "Nightly dependency audit: Every day at 3:00 AM" in result.output
    assert "0 3 * * *" not in result.output and "cron" not in result.output.lower()


def test_schedule_on_and_off_by_plain_name(schedules_file):
    assert runner.invoke(cli.app, ["schedule", "off", "Nightly dependency"]).exit_code == 0
    assert "enabled: false" in schedules_file.read_text(encoding="utf-8")
    result = runner.invoke(cli.app, ["schedule", "on", "nightly-audit"])
    assert "is on" in result.output and runner.invoke(cli.app, ["schedule", "on", "ghost"]).exit_code == 1


def test_old_schedule_commands_still_work(schedules_file):
    assert runner.invoke(cli.app, ["schedule", "list"]).exit_code == 0
    assert runner.invoke(cli.app, ["schedule", "disable", "nightly-audit"]).exit_code == 0


def test_the_menu_item_runs_the_plain_schedules(monkeypatch):
    from mco import menu

    seen = []
    monkeypatch.setattr(plain, "do_schedules", lambda **kw: seen.append(1) or 0)
    menu._verbs(object(), lambda: "")["schedule"]()
    assert seen


# -- the gateway path the console calls ---------------------------------------------
def _app():
    app = FastAPI()
    app.include_router(schedules_router)
    # Override the shared auth dependency; newer FastAPI wraps included routers,
    # so per-route dependency objects are no longer listed in app.routes.
    app.dependency_overrides[require_agent] = lambda: {
                "org_id": "default", "instance_id": "me", "role": "human", "scopes": ["admin"]}
    return TestClient(app)


def test_schedule_routes_need_a_login():
    app = FastAPI()
    app.include_router(schedules_router)
    http = TestClient(app)
    assert http.get("/api/schedules").status_code == 401
    assert http.post("/api/schedules", json={}).status_code == 401
    assert http.post("/api/schedules/x/enabled", json={}).status_code == 401


def test_routes_list_preview_add_and_toggle(schedules_file):
    http = _app()
    data = http.get("/api/schedules").json()
    assert [f["label"] for f in data["frequencies"]][:2] == ["Every day", "Every weekday"]
    assert http.post("/api/schedules/preview", json={"frequency": "weekday", "time": "02:00"}).json() == {
        "words": "Every weekday at 2:00 AM"}
    res = http.post("/api/schedules", json={"what": "nightly-audit", "frequency": "days", "time": "09:30",
                                            "days": [1, 3]})
    assert res.json()["words"] == "Every Monday and Wednesday at 9:30 AM"
    assert "30 9 * * 1,3" in schedules_file.read_text(encoding="utf-8")
    assert http.post("/api/schedules/nightly-audit/enabled", json={"on": False}).json()["on"] is False
    assert http.post("/api/schedules/nightly-audit/enabled", json={"on": "yes"}).status_code == 400


def test_routes_refuse_bad_input_in_plain_words(schedules_file):
    http = _app()
    assert "Pick at least one day" in http.post(
        "/api/schedules/preview", json={"frequency": "days", "time": "02:00", "days": []}).json()["detail"]
    assert http.post("/api/schedules", json={"what": "nope", "frequency": "day", "time": "02:00"}).status_code == 400
    assert http.post("/api/schedules/preview", json={"frequency": "day", "time": "25:00"}).status_code == 400
    assert http.post("/api/schedules/preview",
                     json={"frequency": "day", "time": "02:00", "days": ["x"]}).status_code == 400


def test_the_gateway_serves_the_schedule_routes():
    paths = {getattr(r, "path", None) for r in cli.create_app().routes} | set(cli.create_app().openapi()["paths"])
    assert {"/api/schedules", "/api/schedules/preview", "/api/schedules/{schedule_id}/enabled"} <= paths


# -- approvals: no raw 403 --------------------------------------------------------
def _denied():
    request = httpx.Request("POST", "http://x/api/jobs/aaaa1111/approve")
    raise httpx.HTTPStatusError("403 Forbidden", request=request,
                                response=httpx.Response(403, request=request, text="missing scope jobs:approve"))


class Denied:
    """Gateway whose approve is refused until the fix has run."""

    def __init__(self):
        self.fixed = False
        self.calls = []

    def jobs(self, **_):
        return [{"id": "aaaa1111", "title": "Publish release notes", "status": "needs_approval",
                 "target_agent_role": "codex"}]

    def agents(self):
        return [{"instance_id": "codex-1", "role": "codex", "effective_status": "online", "state": "standby"}]

    def settings(self):
        return {"groups": {}}

    def approve(self, job_id):
        if not self.fixed:
            _denied()
        self.calls.append(job_id)
        return {"job": {"id": job_id, "title": "Publish release notes"}}


def _grant_stub(client, monkeypatch, record):
    def grant(account=None):
        record.append("granted")
        client.fixed = True
        return "Done. You can approve now."

    monkeypatch.setattr(approver, "grant", grant)


def test_a_missing_approver_right_offers_a_one_key_fix_then_approves(monkeypatch, capsys):
    gw, record = Denied(), []
    _grant_stub(gw, monkeypatch, record)
    monkeypatch.setattr(plain, "interactive", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")
    assert plain.do_approve(gw, "Publish") == 0
    out = capsys.readouterr().out
    assert record == ["granted"] and gw.calls == ["aaaa1111"]
    assert "You can't approve yet because your account isn't an approver." in out
    assert "Done. You can approve now." in out and "Approved:" in out
    assert "403" not in out and "Forbidden" not in out and "scope" not in out


def test_the_fix_prompt_is_the_one_key_form_and_enter_accepts_it(monkeypatch):
    gw, record = Denied(), []
    _grant_stub(gw, monkeypatch, record)
    monkeypatch.setattr(plain, "interactive", lambda: True)
    prompts = []
    monkeypatch.setattr("builtins.input", lambda prompt="": prompts.append(prompt) or "")
    assert plain.do_approve(gw, "Publish") == 0 and record == ["granted"]
    assert prompts == ["Fix it? [Y/n] "]


def test_saying_no_to_the_fix_changes_nothing(monkeypatch, capsys):
    gw, record = Denied(), []
    _grant_stub(gw, monkeypatch, record)
    monkeypatch.setattr(plain, "interactive", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt="": "n")
    assert plain.do_approve(gw, "Publish") == 1
    assert record == [] and gw.calls == [] and "Run: bitcadence fix" in capsys.readouterr().out


def test_without_a_terminal_the_right_is_never_granted(monkeypatch):
    gw, record = Denied(), []
    _grant_stub(gw, monkeypatch, record)
    monkeypatch.setattr(cli, "_gateway_client", lambda: gw)
    result = runner.invoke(cli.app, ["approve", "Publish"])
    assert result.exit_code == 1 and record == []
    assert "isn't an approver" in result.output and "403" not in result.output


def test_a_repair_that_is_not_possible_here_says_what_to_do(monkeypatch, capsys):
    gw = Denied()

    def cannot(account=None):
        raise approver.CannotRepair("Ask the person who runs BitCadence to make you an approver.")

    monkeypatch.setattr(approver, "grant", cannot)
    monkeypatch.setattr(plain, "interactive", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")
    assert plain.do_approve(gw, "Publish") == 1
    assert "Ask the person who runs BitCadence" in capsys.readouterr().out


def test_fix_finds_the_missing_right_and_repairs_after_a_yes(monkeypatch, capsys):
    gw = Denied()
    account = {"instance_id": "me", "role": "claude", "scopes": ["jobs:read"]}
    monkeypatch.setattr(approver, "own_account", lambda: account)
    granted = []
    monkeypatch.setattr(approver, "grant", lambda acct=None: granted.append(acct) or "Done. You can approve now.")
    monkeypatch.setattr(plain, "interactive", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")
    assert plain.do_fix(gw) == 0
    out = capsys.readouterr().out
    assert granted == [account] and "isn't an approver" in out and "Done. You can approve now." in out


def test_fix_does_not_grant_without_a_yes(monkeypatch):
    account = {"instance_id": "me", "role": "claude", "scopes": ["jobs:read"]}
    monkeypatch.setattr(approver, "own_account", lambda: account)

    def never(acct=None):
        pytest.fail("granted without asking")

    monkeypatch.setattr(approver, "grant", never)
    assert plain.do_fix(Denied()) == 0  # no terminal, no --yes


def _real_own_account():
    """The conftest guard stubs approver.own_account; call the original for this one test."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("approver_real", approver.__file__)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module._db, module._token = approver._db, approver._token
    return module.own_account()


def test_the_repair_edits_only_this_computers_account(tmp_path, monkeypatch):
    from mco.localstore import LocalStore
    from mco.orchestrator.auth import has_scope, hash_token

    db = LocalStore(tmp_path / "board.db")
    for who in ("me", "someone-else"):
        db.table("agent_registry").insert({"instance_id": who, "role": "claude", "status": "offline",
                                           "auth_token_hash": hash_token("tok-" + who), "scopes": ["jobs:read"]}).execute()
    monkeypatch.setattr(approver, "_db", lambda: db)
    monkeypatch.setattr(approver, "_token", lambda: "tok-me")
    account = _real_own_account()
    assert account["instance_id"] == "me" and approver.is_missing(account)
    assert approver.grant(account) == "Done. You can approve now."
    rows = {r["instance_id"]: r for r in db.table("agent_registry").select("*").execute().data}
    assert has_scope(rows["me"], "jobs:approve") and not has_scope(rows["someone-else"], "jobs:approve")


def test_the_repair_refuses_a_managed_database(monkeypatch):
    monkeypatch.setattr(approver, "_db", lambda: object())
    with pytest.raises(approver.CannotRepair, match="managed somewhere else"):
        approver.grant({"instance_id": "me"})


def test_no_gateway_route_can_grant_the_right():
    paths = [getattr(r, "path", "") for r in cli.create_app().routes] + list(cli.create_app().openapi()["paths"])
    assert not [p for p in paths if "approver" in p]
    routes = (ROOT / "src/mco/orchestrator/schedules_routes.py").read_text(encoding="utf-8")
    assert "approver" not in routes and "grant" not in routes


# -- settings -----------------------------------------------------------------------
class SettingsGateway:
    def settings(self):
        return {"groups": {"safety": [{"key": "MCO_KILL_SWITCH", "value": ""}],
                           "notifications": [{"key": "NTFY_TOPIC", "type": "secret", "value": True}]}}


def test_settings_in_plain_words_has_no_tokens_ports_or_addresses(monkeypatch, capsys):
    monkeypatch.setenv("MCO_LOCAL_TOKEN", "mco_tok_" + "a" * 40 + "WXYZ")
    assert plain.do_settings(SettingsGateway()) == 0
    out = capsys.readouterr().out
    assert "Pause everything: Off" in out and "Tell me on my phone: On" in out
    for leak in ("mco_tok", "18789", "127.0.0.1", "http://", "MCO_", "NTFY"):
        assert leak not in out
    assert "--show-advanced" in out


def test_settings_show_advanced_adds_the_address_and_only_a_masked_sign_in(monkeypatch, capsys):
    secret = "mco_tok_" + "a" * 40 + "WXYZ"
    monkeypatch.setattr("mco.config.get_config", lambda: {"MCO_LOCAL_TOKEN": secret})
    monkeypatch.setattr(cli, "manage_settings", lambda *a, **k: None)
    assert plain.do_settings(SettingsGateway(), show_advanced=True) == 0
    out = capsys.readouterr().out
    assert "Where it listens: http://127.0.0.1:18789" in out and "mco_tok_...WXYZ" in out
    assert secret not in out and "aaaa" not in out


def test_settings_command_runs_the_plain_view_and_old_key_forms_still_work(monkeypatch):
    seen = []
    monkeypatch.setattr(cli, "_gateway_client", lambda: SettingsGateway())
    monkeypatch.setattr(plain, "do_settings", lambda client, **kw: seen.append(kw) or 0)
    assert runner.invoke(cli.app, ["settings"]).exit_code == 0
    assert runner.invoke(cli.app, ["settings", "--show-advanced"]).exit_code == 0
    assert seen == [{"show_advanced": False}, {"show_advanced": True}]
    managed = []
    monkeypatch.setattr(cli, "manage_settings", lambda k, v, u: managed.append((k, v, u)))
    runner.invoke(cli.app, ["settings", "MCO_KILL_SWITCH", "true"])
    assert managed == [("MCO_KILL_SWITCH", "true", False)]


def test_the_menu_settings_item_runs_the_plain_view(monkeypatch):
    from mco import menu

    seen = []
    monkeypatch.setattr(plain, "do_settings", lambda client, **kw: seen.append(1) or 0)
    menu._verbs(object(), lambda: "")["settings"]()
    assert seen


# -- the console ----------------------------------------------------------------------
def _read(prefix):
    return next(SRC.glob(prefix + "*")).read_text(encoding="utf-8")


def test_console_schedules_page_calls_the_schedules_api_and_is_in_the_bundle():
    page, store, shell = (_read(p) for p in ("2ed3f6b1", "47e66145", "8ec84a72"))
    for path in ('api("/api/schedules")', 'api("/api/schedules/preview"', 'api("/api/schedules", {', '"/enabled"'):
        assert path in store
    assert "store.schedulesList()" in page and "store.schedulePreview(" in page
    assert "store.addSchedule(" in page and "store.setScheduleOn(" in page
    assert "<SchedulesPage />" in shell and 'label: "Schedules"' in shell
    result = subprocess.run([sys.executable, str(ROOT / "scripts/build_console.py"), "verify"], cwd=ROOT,
                            capture_output=True, text=True)
    assert result.returncode == 0 and "0 differ" in result.stdout


def test_console_schedules_page_follows_the_design_rules():
    page = _read("2ed3f6b1")
    mine = page.split("function SchedulesPage")[1].split("// ----- Settings")[0]
    for text in ("Pick a time like you would for an alarm.", "What should run?", "How often?",
                 "At what time?", "New schedule", "Next run:", "Time zone (IANA)"):
        assert text in mine
    assert "aria-pressed" in mine and "aria-live" in mine  # a word as well as a colour
    assert 'id="sc-zone"' in mine and 'timezone: zone' in mine


def test_console_shows_no_raw_403_and_approvals_page_explains_it():
    store, approvals = _read("47e66145"), _read("43b328d0")
    for action in ("Approve failed", "Reject failed"):
        assert f'toast("err", "{action}", plainFailure(e))' in store
    assert 'toast("err", "Approve failed", e.message)' not in store
    assert "error.denialKind" in store and "Access denied" in store
    assert "You can't approve this yet." in approvals
    assert 'role="alert"' in approvals and "Ask your operator" in approvals
    assert "one-key fix" not in approvals


def test_console_settings_keep_tokens_ports_and_addresses_under_show_advanced():
    page = _read("2ed3f6b1")
    plain_part = page.split("function PlainSettings")[1].split("Object.assign(window")[0]
    for text in ("Stop work", "Tell me on my phone", "Memory", "Server administration"):
        assert text in plain_part
    assert '!live ? children : <details' in plain_part
    assert "Halts active attempts" in plain_part
    settings_fn = page.split("function Settings(")[1].split("function PlainSettings")[0]
    assert 'tone === "plain" ? <PlainSettings>{body}</PlainSettings> : body' in settings_fn
    assert "Gateway URL" in settings_fn and "Agent token" in settings_fn  # still reachable, inside {body}


def test_parse_time_trims_spaces_and_rejects_padding_bombs():
    import time
    import pytest
    from mco import schedules_plain
    assert schedules_plain.parse_time("  2:30 pm  ") == (14, 30)
    started = time.perf_counter()
    with pytest.raises(schedules_plain.ScheduleWordsError):
        schedules_plain.parse_time("2" + " " * 5000 + "x")
    assert time.perf_counter() - started < 1.0
