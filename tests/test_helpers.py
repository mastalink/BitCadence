"""Slice 5: Helpers. Friendly names, a health light with a word, what each is doing,
Add a helper (same path as `bitcadence helpers add`) and Fix it (dry run, then confirm)."""
import json
import subprocess
import sys
import time
from pathlib import Path

import pytest
from mco.orchestrator.auth import require_agent
from fastapi import FastAPI
from fastapi.testclient import TestClient
from typer.testing import CliRunner

import mco.cli as cli
from mco import helpers, plain
from mco.orchestrator.helpers_routes import helpers_router

ROOT = Path(__file__).parents[1]
SRC = ROOT / "src/mco/console_src"
runner = CliRunner()


def wake(pid, instance, started=1.0, files=()):
    return helpers.Proc(pid, [sys.executable, "-m", "mco.cli", "wake", "--exec", "x", "--instance", instance],
                        started, list(files))


def agent(instance, state="standby", **extra):
    return {"instance_id": instance, "role": "codex", "state": state, "effective_status": "online",
            "last_seen_seconds": 12, **extra}


# -- names, light, plain words -------------------------------------------------
def test_friendly_names():
    assert helpers.friendly_name("claude-worker_3") == "Claude worker 3"
    assert helpers.friendly_name("") == "A helper"


@pytest.mark.parametrize("state,light,word", [
    ("working", "green", "Working"), ("standby", "green", "Ready"), ("broken", "red", "Stuck"),
    ("offline", "grey", "Not connected"), ("disabled", "grey", "Paused")])
def test_health_light_always_has_a_word(state, light, word):
    assert helpers.health(agent("a", state)) == {"light": light, "word": word}


def test_a_finding_turns_an_online_helper_red():
    finding = helpers.scan(["fixer"], procs=[wake(1, "fixer", 1), wake(2, "fixer", 2)], own_pid=0)[0]
    assert helpers.health(agent("fixer"), [finding]) == {"light": "red", "word": "Stuck"}
    assert helpers.health(agent("other"), [finding])["word"] == "Ready"


def test_describe_says_what_each_is_doing_and_leaks_no_token():
    job = {"leased_by_instance_id": "scout", "status": "in_progress", "title": "Check open pull requests"}
    rows = helpers.describe([agent("scout", "working", auth_token_hash="secret", token="mco_tok_x"),
                             agent("tester")], [job])
    assert rows[0]["doing"] == "Working on: Check open pull requests" and rows[0]["name"] == "Scout"
    assert rows[1]["doing"] == "Waiting for a job" and rows[1]["last_heard"] == "just now"
    assert "secret" not in json.dumps(rows) and "mco_tok" not in json.dumps(rows)


# -- detection is a dry run ----------------------------------------------------
def test_duplicate_wake_process_is_found_and_the_oldest_is_kept():
    procs = [wake(10, "fixer", 5), wake(11, "fixer", 9), wake(12, "other", 1)]
    (found,) = helpers.scan(["fixer", "other"], procs=procs, own_pid=0)
    assert found.kind == helpers.DUPLICATE_WAKER and found.kept == 10 and found.pids == [11]
    assert "Would: " + found.would in helpers.repair([found])[0]


def test_a_single_wake_process_is_fine():
    assert helpers.scan(["fixer"], procs=[wake(10, "fixer")], own_pid=0) == []


def test_venv_launcher_and_its_child_are_one_copy():
    # A Windows venv pythonw.exe re-runs the same command line as its child.
    launcher, child = wake(10, "fixer", 5), wake(11, "fixer", 5.1)
    child.ppid = 10
    assert helpers.scan(["fixer"], procs=[launcher, child], own_pid=0) == []


def test_real_duplicate_stops_the_extra_launcher_and_its_child_only():
    first, first_child = wake(10, "fixer", 5), wake(11, "fixer", 5.1)
    second, second_child = wake(20, "fixer", 9), wake(21, "fixer", 9.1)
    first_child.ppid, second_child.ppid = 10, 20
    (found,) = helpers.scan(["fixer"], procs=[first, first_child, second, second_child], own_pid=0)
    assert found.kept == 10 and sorted(found.pids) == [20, 21]
    assert "2 copies" in found.summary


def test_locked_log_is_found_for_a_foreign_holder(tmp_path):
    log = tmp_path / "fixer.log"
    log.write_text("x")
    holder = helpers.Proc(77, [sys.executable, "-m", "mco.cli", "tail"], 3, [str(log)])
    (found,) = helpers.scan(["fixer"], procs=[wake(10, "fixer", files=[str(log)]), holder],
                            directory=tmp_path, own_pid=0)
    assert found.kind == helpers.LOCKED_LOG and found.pids == [77]
    assert "can't save its notes" in found.summary


def test_the_helpers_own_copy_holding_its_log_is_not_a_problem(tmp_path):
    log = tmp_path / "fixer.log"
    log.write_text("x")
    assert helpers.scan(["fixer"], procs=[wake(10, "fixer", files=[str(log)])], directory=tmp_path, own_pid=0) == []


def test_a_stranger_holding_the_log_is_reported_but_never_stopped(tmp_path):
    log = tmp_path / "fixer.log"
    log.write_text("x")
    stranger = helpers.Proc(88, ["notepad.exe", str(log)], 3, [str(log)])
    (found,) = helpers.scan(["fixer"], procs=[stranger], directory=tmp_path, own_pid=0)
    stopped = []
    out = helpers.repair([found], confirmed=True, stop=stopped.append)
    assert found.pids == [] and found.unsafe == [88] and stopped == [] and "safely" in out[0]


def test_repair_changes_nothing_until_confirmed():
    (found,) = helpers.scan(["fixer"], procs=[wake(10, "fixer", 1), wake(11, "fixer", 2)], own_pid=0)
    stopped = []
    helpers.repair([found], stop=stopped.append)
    assert stopped == []
    out = helpers.repair([found], confirmed=True, stop=stopped.append)
    assert stopped == [11] and "working again" in out[0]


def test_real_processes_duplicate_wake_and_locked_log(tmp_path):
    """Real subprocesses and real psutil: two copies of one helper, and a foreign holder of its log."""
    # A Windows venv python.exe is a launcher that starts the real interpreter as a
    # child, which shows every copy twice; run the base interpreter directly.
    PY = getattr(sys, "_base_executable", sys.executable)
    log = tmp_path / "realhelper.log"
    code = "import time\ntime.sleep(60)"
    argv = [PY, "-c", code, "mco", "wake"]
    kids = [subprocess.Popen(argv + ["--instance", "realhelper"]) for _ in range(2)]
    holder = subprocess.Popen([PY, "-c", "import sys,time\nf=open(sys.argv[1],'a')\ntime.sleep(60)",
                               str(log), "mco"])
    try:
        deadline = time.time() + 15
        while True:  # processes take a moment to show their command lines
            found = helpers.scan(["realhelper"], directory=tmp_path)
            kinds = {f.kind for f in found}
            if helpers.DUPLICATE_WAKER in kinds or time.time() > deadline:
                break
            time.sleep(0.3)
        assert helpers.DUPLICATE_WAKER in kinds
        dup = next(f for f in found if f.kind == helpers.DUPLICATE_WAKER)
        assert len(dup.pids) == 1 and set(dup.pids + [dup.kept]) == {k.pid for k in kids}
        if helpers.LOCKED_LOG in kinds:  # open-file listing is best-effort on some platforms
            assert holder.pid in next(f for f in found if f.kind == helpers.LOCKED_LOG).pids
        helpers.repair([dup], confirmed=True)
        time.sleep(0.5)
        survivors = [k for k in kids if k.poll() is None]
        assert [k.pid for k in survivors] == [dup.kept]
    finally:
        for p in (*kids, holder):
            p.kill()
            p.wait()


# -- the CLI -------------------------------------------------------------------
class Gateway:
    def agents(self):
        return [agent("fixer"), agent("tester", "offline", effective_status="offline")]


@pytest.fixture
def gateway(monkeypatch):
    gw = Gateway()
    monkeypatch.setattr(cli, "_gateway_client", lambda: gw)
    return gw


def _dupes(monkeypatch):
    monkeypatch.setattr(helpers, "list_processes", lambda: [wake(10, "fixer", 1), wake(11, "fixer", 2)])


def test_helpers_command_shows_friendly_name_light_word_and_doing(gateway, monkeypatch):
    monkeypatch.setattr(helpers, "list_processes", lambda: [])
    out = runner.invoke(cli.app, ["helpers"]).output
    assert "Fixer" in out and "Ready" in out and "Waiting for a job" in out
    assert "Tester" in out and "Not connected" in out and "(+)" in out


def test_helpers_command_shows_a_stuck_light_when_a_second_copy_runs(gateway, monkeypatch):
    _dupes(monkeypatch)
    out = runner.invoke(cli.app, ["helpers"]).output
    assert "Stuck" in out and "(!)" in out and "bitcadence helpers fix" in out


def test_helpers_fix_asks_first_and_declining_changes_nothing(gateway, monkeypatch):
    _dupes(monkeypatch)
    stopped = []
    monkeypatch.setattr(helpers, "_stop", stopped.append)
    monkeypatch.setattr(plain, "interactive", lambda: True)
    monkeypatch.setattr("builtins.input", lambda *_: "n")
    out = runner.invoke(cli.app, ["helpers", "fix"]).output
    assert "Would: Keep the oldest copy" in out and "left as it is" in out and stopped == []


def test_helpers_fix_repairs_after_a_yes(gateway, monkeypatch):
    _dupes(monkeypatch)
    stopped = []
    monkeypatch.setattr(helpers, "_stop", stopped.append)
    monkeypatch.setattr(plain, "interactive", lambda: True)
    monkeypatch.setattr("builtins.input", lambda *_: "y")
    out = runner.invoke(cli.app, ["helpers", "fix"]).output
    assert stopped == [11] and "Fixer is working again" in out


def test_bitcadence_fix_offers_the_helper_repair_with_a_confirm(gateway, monkeypatch):
    """The production `fix` verb reaches the same detection and repair."""
    _dupes(monkeypatch)
    stopped = []
    monkeypatch.setattr(helpers, "_stop", stopped.append)
    monkeypatch.setattr(plain, "interactive", lambda: True)
    monkeypatch.setattr("builtins.input", lambda *_: "y")
    monkeypatch.setattr(plain, "take_snapshot", lambda client: plain.Snapshot(helpers=client.agents()))
    out = runner.invoke(cli.app, ["fix"]).output
    assert "Found 1 problem" in out and stopped == [11]


def test_fix_without_a_terminal_never_repairs(gateway, monkeypatch):
    _dupes(monkeypatch)
    stopped = []
    monkeypatch.setattr(helpers, "_stop", stopped.append)
    runner.invoke(cli.app, ["helpers", "fix"])
    assert stopped == []


def test_helpers_add_command_calls_the_shared_add_path(monkeypatch):
    calls = []
    monkeypatch.setattr(helpers, "add_helper", lambda n, r: calls.append((n, r)) or
                        {"name": "Penny", "id": n, "role": r, "credential": "mco_tok_...abcd", "saved_to": "x"})
    result = runner.invoke(cli.app, ["helpers", "add", "--name", "penny", "--role", "codex"])
    assert calls == [("penny", "codex")] and "mco_tok_...abcd" in result.output


def test_add_helper_returns_only_a_masked_credential(monkeypatch, tmp_path):
    from mco import waker
    saved = {}

    class Config:
        def set(self, key, value, encrypt=False):
            saved[key] = value

    monkeypatch.setattr(waker, "AGENT_TOKEN_DIR", tmp_path / "tokens")
    monkeypatch.setattr(cli, "get_config", lambda: Config())
    token = "mco_tok_" + "b" * 48
    added = helpers.add_helper("Penny", "codex", register=lambda n, r: token)
    assert added["credential"] == "mco_tok_...bbbb" and token not in json.dumps(added)
    assert (tmp_path / "tokens" / "Penny.token").read_text() == token
    assert saved["MCO_SECRET_AGENT_TOKEN_PENNY"] == token


@pytest.mark.parametrize("name", ["", "bad name", "../x", "a" * 70])
def test_add_helper_rejects_bad_names_in_plain_words(name):
    with pytest.raises(helpers.HelperError):
        helpers.add_helper(name, "codex", register=lambda n, r: "t")


# -- the gateway path the console calls ----------------------------------------
def _app(monkeypatch, tmp_path, scopes=("agents:read", "agents:manage")):
    import mco.orchestrator.routes as board_routes
    from mco.localstore import LocalStore
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    board = LocalStore(tmp_path / "board.db")
    monkeypatch.setattr(board_routes, "get_db_client", lambda: board)
    monkeypatch.setattr(board_routes, "get_agents", lambda caller: [agent("fixer"), agent("scout", "working")])
    app = FastAPI()
    app.include_router(helpers_router)
    # Override the shared auth dependency; newer FastAPI wraps included routers,
    # so per-route dependency objects are no longer listed in app.routes.
    app.dependency_overrides[require_agent] = lambda: {
                "org_id": "default", "instance_id": "me", "role": "human", "scopes": list(scopes)}
    return TestClient(app), board


def test_helpers_routes_need_a_login():
    app = FastAPI()
    app.include_router(helpers_router)
    http = TestClient(app)
    assert http.get("/api/helpers").status_code == 401
    assert http.post("/api/helpers/add", json={}).status_code == 401
    assert http.post("/api/helpers/fix", json={}).status_code == 401


def test_list_route_returns_the_plain_view(monkeypatch, tmp_path):
    http, _ = _app(monkeypatch, tmp_path)
    monkeypatch.setattr(helpers, "list_processes", lambda: [wake(10, "fixer", 1), wake(11, "fixer", 2)])
    data = http.get("/api/helpers").json()
    fixer = next(h for h in data["helpers"] if h["id"] == "fixer")
    assert (fixer["name"], fixer["word"], fixer["light"], fixer["can_fix"]) == ("Fixer", "Stuck", "red", True)
    assert data["problems"][0]["kind"] == helpers.DUPLICATE_WAKER
    assert "mco_tok" not in json.dumps(data)


def test_fix_route_is_a_dry_run_until_confirmed(monkeypatch, tmp_path):
    http, _ = _app(monkeypatch, tmp_path)
    stopped = []
    monkeypatch.setattr(helpers, "_stop", stopped.append)
    monkeypatch.setattr(helpers, "list_processes", lambda: [wake(10, "fixer", 1), wake(11, "fixer", 2)])
    dry = http.post("/api/helpers/fix", json={}).json()
    assert dry["dry_run"] is True and stopped == [] and dry["results"][0].startswith("Would:")
    assert http.post("/api/helpers/fix", json={"confirm": "yes"}).json()["dry_run"] is True
    done = http.post("/api/helpers/fix", json={"confirm": True}).json()
    assert done["dry_run"] is False and stopped == [11]


def test_add_route_uses_the_shared_add_path_and_masks_the_credential(monkeypatch, tmp_path):
    http, _ = _app(monkeypatch, tmp_path)
    calls = []
    monkeypatch.setattr(helpers, "add_helper", lambda n, r: calls.append((n, r)) or
                        {"name": "Penny", "id": n, "role": r, "credential": "mco_tok_...abcd", "saved_to": "C:/secret/path"})
    res = http.post("/api/helpers/add", json={"name": "penny", "role": "codex"})
    assert calls == [("penny", "codex")] and res.json()["helper"]["credential"] == "mco_tok_...abcd"
    assert "secret" not in res.text
    monkeypatch.setattr(helpers, "add_helper", lambda n, r: (_ for _ in ()).throw(helpers.HelperError("Pick another name.")))
    assert http.post("/api/helpers/add", json={"name": "x", "role": "y"}).status_code == 400


def test_the_gateway_serves_the_helpers_routes():
    paths = {getattr(r, "path", None) for r in cli.create_app().routes} | set(cli.create_app().openapi()["paths"])
    assert {"/api/helpers", "/api/helpers/add", "/api/helpers/fix"} <= paths


# -- the console ---------------------------------------------------------------
def _read(prefix):
    return next(SRC.glob(prefix + "*")).read_text(encoding="utf-8")


def test_console_helpers_page_calls_the_helpers_api_and_is_in_the_bundle():
    page, store, shell = (_read(p) for p in ("2ed3f6b1", "47e66145", "8ec84a72"))
    assert 'api("/api/helpers")' in store and 'api("/api/helpers/add"' in store and 'api("/api/helpers/fix"' in store
    assert "store.helpersList()" in page and "store.addHelper(" in page and "store.fixHelpers(false)" in page
    assert "store.fixHelpers(true)" in page
    assert page.index("store.fixHelpers(false)") < page.index("store.fixHelpers(true)")
    assert "<HelpersPage />" in shell and 'label: "Helpers"' in shell
    bundle = (ROOT / "src/mco/static/console.html").read_text(encoding="utf-8")
    assert "__bundler" in bundle
    result = subprocess.run([sys.executable, str(ROOT / "scripts/build_console.py"), "verify"], cwd=ROOT,
                            capture_output=True, text=True)
    assert result.returncode == 0 and "0 differ" in result.stdout


def test_console_helpers_page_follows_the_design_rules():
    page = _read("2ed3f6b1")
    for text in ("Your helpers", "Add a helper", "Fix it", "Nothing has changed yet.", "Yes, fix it", "Last heard from"):
        assert text in page
    assert "min-height:48px" in page and ":focus-visible" in page and "prefers-reduced-motion" in page
    assert "aria-live" in page and "HP_MARK" in page  # a shape and a word, never colour alone
    assert "token" not in page.split("function HelpersPage")[1].split("// ----- Settings")[0].lower()
