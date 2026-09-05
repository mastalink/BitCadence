import os
from pathlib import Path
import subprocess
import sys
import time

import psutil
import pytest

from mco.desktop.controller import DesktopController, StackSupervisor, matches_component, stop_tree
from mco.fleet import WorkerConfig


def config(mode="waker"):
    return WorkerConfig("example", "reviewer", "reviewer-local", mode, "example.cmd", 10, 60)


def test_discovery_excludes_mcp_other_roles_and_other_ports():
    cfg = config()
    assert matches_component(["python", "-m", "mco.cli", "wake", "--role", "reviewer", "--instance", "reviewer-local"], "example", cfg, 18789)
    for args in (["mcp"], ["wake", "--role", "codex"], ["wake", "--role", "reviewer", "--instance", "another"]):
        assert not matches_component(["python", "-m", "mco.cli", *args], "example", cfg, 18789)
    assert not matches_component(["python", "-m", "mco.cli", "serve", "--port", "18890"], "gateway", cfg, 18789)


def test_poll_keeps_executor_command():
    from mco import service
    supervisor = StackSupervisor(None)
    assert supervisor._argv(config("poll")) == service._poll_argv("example.cmd")


def test_stop_tree_releases_descendants(tmp_path):
    child_pid = tmp_path / "child.pid"
    code = "import subprocess,sys,time,pathlib; p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(120)']); pathlib.Path(sys.argv[1]).write_text(str(p.pid)); time.sleep(120)"
    process = subprocess.Popen([sys.executable, "-c", code, str(child_pid)],
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    try:
        deadline = time.monotonic() + 10
        while not child_pid.exists() and time.monotonic() < deadline:
            time.sleep(.05)
        child = psutil.Process(int(child_pid.read_text()))
        stop_tree(psutil.Process(process.pid))
        assert process.wait(timeout=5) is not None
        assert not child.is_running()
    finally:
        if process.poll() is None:
            stop_tree(psutil.Process(process.pid))


@pytest.mark.skipif(os.name != "nt", reason="Windows desktop runtime")
def test_native_supervisor_launch_stop_and_singleton(tmp_path):
    from mco.agentd.platform.windows import SupervisorAlreadyRunning
    controller = DesktopController(fleet_path=tmp_path / "empty.toml", runtime_dir=tmp_path / "runtime")
    try:
        assert all(row[1] == "stopped" for row in controller.rows())
        with pytest.raises(SupervisorAlreadyRunning):
            DesktopController(fleet_path=tmp_path / "empty.toml", runtime_dir=tmp_path / "second")
        controller.supervisor._argv = lambda cfg: [sys.executable, "-u", "-c", "import time; print('desktop child started'); time.sleep(120)"]
        controller.supervisor.start("gateway")
        runtime = controller.supervisor.workers["gateway"]
        assert runtime.last_error is None
        assert runtime.process.poll() is None
        process = psutil.Process(runtime.process.pid)
        controller.supervisor.stop("gateway")
        assert not process.is_running()
    finally:
        controller.close()


@pytest.mark.skipif(os.name != "nt", reason="Windows desktop runtime")
def test_orphan_reaper_rejects_recycled_pid(tmp_path):
    from mco.desktop.windows import DesktopWindowsAdapter
    import json
    current = psutil.Process()
    pidfile = tmp_path / "pids.json"
    pidfile.write_text(json.dumps({"pid": current.pid, "created": current.create_time() - 100, "command": current.exe()}) + "\n")
    assert DesktopWindowsAdapter(pidfile=pidfile).reap_orphans(pidfile) == []
    assert current.is_running()


@pytest.mark.skipif(os.name != "nt", reason="Windows desktop runtime")
def test_real_gateway_readiness_and_stop(tmp_path):
    import socket
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    script = tmp_path / "gateway.py"
    script.write_text('''import os, pathlib, sys
for key in list(os.environ):
    if key.startswith(('MCO_', 'NTFY_', 'SUPABASE_')): os.environ.pop(key)
from mco import config
root = pathlib.Path(__file__).parent
settings = root / 'test.env'
settings.write_text('MCO_LOCAL_TOKEN=desktop-test-token\\nNTFY_TOPIC=\\n')
config._config_manager = config.ConfigManager(settings, root / 'secrets.enc')
from mco.localstore import LocalStore
from mco.orchestrator import routes
routes._db_client = LocalStore(root / 'test.db')
from mco.cli import create_app
import uvicorn
uvicorn.run(create_app(), host='127.0.0.1', port=int(sys.argv[1]))
''')
    controller = DesktopController(fleet_path=tmp_path / "empty.toml", runtime_dir=tmp_path / "runtime", port=port)
    try:
        controller.supervisor._argv = lambda cfg: [sys.executable, str(script), str(port)]
        controller.start("gateway")
        deadline = time.monotonic() + 20
        while not controller.ready() and time.monotonic() < deadline:
            time.sleep(.1)
        assert controller.ready(), controller.logs.tail("gateway")
        original_pid = controller.supervisor.workers["gateway"].process.pid
        controller.start("gateway")
        assert controller.supervisor.workers["gateway"].process.pid == original_pid
        controller.stop("gateway")
        assert not controller.ready()
    finally:
        controller.close()
