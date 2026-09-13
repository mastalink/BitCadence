"""Local process lifecycle; independent of the graphical interface."""
from __future__ import annotations

import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

import httpx
import psutil

from mco import service
from mco.agentd.logs import LogAggregator
from mco.agentd.supervisor import Supervisor, WorkerRuntime
from mco.fleet import FLEET_CONFIG_PATH, WorkerConfig, load_fleet


def matches_component(argv: list[str], name: str, config: WorkerConfig, port: int) -> bool:
    """Only recognize exact CLI commands, never arbitrary Python/MCP processes."""
    try:
        if config.mode == "poll" and config.exec_command:
            expected = service._poll_argv(config.exec_command)
            return (bool(argv) and Path(argv[0]).name.casefold() == Path(expected[0]).name.casefold()
                    and argv[1:] == expected[1:])
        index = argv.index("-m")
        if argv[index + 1] != "mco.cli":
            return False
        args = argv[index + 2:]
        def option(key, default=None):
            return args[args.index(key) + 1] if key in args else default
        if name == "gateway":
            return args[0] == "serve" and int(option("--port", "18789")) == port
        if name == "scheduler":
            return args[:2] == ["schedule", "run"]
        return (args[0] == {"waker": "wake", "poll": "poll"}.get(config.mode)
                and option("--role") == config.role
                and option("--instance") == config.instance)
    except (ValueError, IndexError):
        return False


def stop_tree(process: psutil.Process) -> None:
    """Stop a verified process and its descendants, including executor children."""
    try:
        children = process.children(recursive=True)
        targets = [process, *children]
        for child in targets:
            try:
                child.terminate()
            except psutil.NoSuchProcess:
                pass
        _, alive = psutil.wait_procs(targets, timeout=5)
        for child in alive:
            try:
                child.kill()
            except psutil.NoSuchProcess:
                pass
        _, alive = psutil.wait_procs(alive, timeout=5)
        if alive:
            raise RuntimeError("Some processes did not stop: " + ", ".join(str(p.pid) for p in alive))
    except psutil.NoSuchProcess:
        pass


class StackSupervisor(Supervisor):
    def __init__(self, *args, port=18789, **kwargs):
        super().__init__(*args, **kwargs)
        self.port = port

    def _argv(self, config):
        if config.worker == "gateway":
            argv = service._serve_argv("127.0.0.1", self.port)
        elif config.worker == "scheduler":
            argv = service._scheduler_argv()
        else:
            argv = super()._argv(config)
        # pythonw drops standard streams; CREATE_NO_WINDOW already hides Python.
        if config.worker in {"gateway", "scheduler"} or config.mode == "waker":
            argv[0] = str(Path(sys.executable).with_name("python.exe")) if os.name == "nt" else sys.executable
        return argv

    def stop(self, name):
        runtime = self.workers.get(name)
        if runtime and runtime.process and runtime.process.poll() is None:
            stop_tree(psutil.Process(runtime.process.pid))
        super().stop(name)


class DesktopController:
    def __init__(self, *, fleet_path=FLEET_CONFIG_PATH, port=18789, adapter=None,
                 runtime_dir=None, cwd=None):
        self.fleet_path = Path(fleet_path)
        self.username = psutil.Process().username()
        self.port = port
        self.url = f"http://127.0.0.1:{port}"
        self.runtime_dir = Path(runtime_dir or Path.home() / ".mco" / "desktop")
        self.logs = LogAggregator(self.runtime_dir / "logs")
        if adapter is None:
            from .windows import DesktopWindowsAdapter
            adapter = DesktopWindowsAdapter(pidfile=self.runtime_dir / "processes.json")
        self.supervisor = StackSupervisor(adapter, port=port, logs=self.logs,
            pidfile=self.runtime_dir / "processes.json", state_file=self.runtime_dir / "state.json",
            cwd=Path(cwd or Path.cwd()),
            env_factory=lambda: dict(os.environ, MCO_GATEWAY_URL=self.url, PYTHONUNBUFFERED="1"))
        self.supervisor.startup()
        try:
            self.reload()
        except Exception:
            self.supervisor.shutdown()
            raise

    def task_names(self):
        return [config.active_service_name for name, runtime in self.supervisor.workers.items()
                if name not in {"gateway", "scheduler"}
                and (config := runtime.config).active_service_name]

    @staticmethod
    def scheduled_task(name, *args):
        return subprocess.run(["schtasks.exe", *args, "/tn", name], capture_output=True,
            text=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), timeout=15)

    def active_tasks(self):
        if os.name != "nt":
            return []
        enabled = []
        for name in self.task_names():
            result = self.scheduled_task(name, "/query", "/xml")
            if result.returncode == 0:
                root = ET.fromstring(result.stdout)
                setting = root.find("{*}Settings/{*}Enabled")
                if setting is None or setting.text != "false":
                    enabled.append(name)
        return enabled

    def take_control(self):
        """Explicit migration: disable recurring tasks before stopping their trees."""
        for name in self.active_tasks():
            result = self.scheduled_task(name, "/change", "/disable")
            if result.returncode:
                raise RuntimeError(f"Could not disable {name}: {result.stderr.strip()}")
        self.stop_all()
        self.start_all()

    def reload(self):
        configs = load_fleet(self.fleet_path) if self.fleet_path.exists() else {}
        if {"gateway", "scheduler"} & configs.keys():
            raise ValueError("Worker names 'gateway' and 'scheduler' are reserved")
        configs = {name: WorkerConfig(name, name, None, "waker", None, 10, 30)
                   for name in ("gateway", "scheduler")} | configs
        for name in set(self.supervisor.workers) - configs.keys():
            self.stop(name)
            del self.supervisor.workers[name]
        for name, config in configs.items():
            old = self.supervisor.workers.get(name)
            if old and old.config != config:
                self.stop(name)
            if old:
                old.config = config
            else:
                self.supervisor.workers[name] = WorkerRuntime(config)

    def external(self, name):
        config = self.supervisor.workers[name].config
        owned = self.supervisor.workers[name].process
        excluded = set()
        if owned:
            try:
                root = psutil.Process(owned.pid)
                excluded = {root.pid, *(p.pid for p in root.children(recursive=True))}
            except psutil.NoSuchProcess:
                pass
        found = []
        for process in psutil.process_iter(["pid", "cmdline", "username"]):
            try:
                if (process.pid not in excluded and process.username() == self.username
                        and matches_component(process.cmdline(), name, config, self.port)):
                    found.append(process)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return found

    def ready(self):
        try:
            response = httpx.get(self.url + "/readyz", timeout=1, trust_env=False)
            return response.status_code == 200 and response.json().get("status") in {"ready", "degraded"}
        except (httpx.HTTPError, ValueError):
            return False

    def start(self, name):
        runtime = self.supervisor.workers[name]
        if runtime.config.mode == "off" or (runtime.process and runtime.process.poll() is None):
            return
        task_name = runtime.config.active_service_name if name not in {"gateway", "scheduler"} else None
        if task_name and task_name in self.active_tasks():
            raise RuntimeError("Scheduled workers are still enabled. Use 'Move workers into app' first.")
        if self.external(name):
            return  # Already running; the UI identifies it as external.
        if name == "gateway":
            with socket.socket() as probe:
                if probe.connect_ex(("127.0.0.1", self.port)) == 0:
                    raise RuntimeError(f"Port {self.port} is occupied; refusing to start a duplicate gateway")
        elif not self.ready():
            raise RuntimeError("Start the gateway and wait for it to become ready first")
        self.supervisor.reset(name)

    def stop(self, name):
        config = self.supervisor.workers[name].config
        task = config.active_service_name if name not in {"gateway", "scheduler"} else None
        if task and task in self.active_tasks():
            result = self.scheduled_task(task, "/change", "/disable")
            if result.returncode:
                raise RuntimeError(f"Could not disable {task}: {result.stderr.strip()}")
        self.supervisor.stop(name)
        # These are exact mco.cli/role/instance matches belonging to this user.
        for process in self.external(name):
            stop_tree(process)
        if task and os.name == "nt":
            self.scheduled_task(task, "/end")

    def start_all(self):
        runtime = self.supervisor.workers["gateway"]
        if not (runtime.process and runtime.process.poll() is None) and not self.ready():
            self.start("gateway")
        deadline = time.monotonic() + 30
        while not self.ready():
            if time.monotonic() >= deadline:
                raise RuntimeError("Gateway did not become ready in 30 seconds. See gateway logs.")
            time.sleep(.25)
        for name in self.supervisor.workers:
            if name != "gateway":
                self.start(name)

    def stop_all(self):
        errors = []
        for task in self.active_tasks():
            result = self.scheduled_task(task, "/change", "/disable")
            if result.returncode:
                errors.append(f"Could not disable {task}: {result.stderr.strip()}")
        for name in reversed(list(self.supervisor.workers)):
            try:
                self.stop(name)
            except Exception as exc:
                errors.append(f"{name}: {exc}")
        for task in self.task_names():
            if os.name == "nt":
                self.scheduled_task(task, "/end")
        if errors:
            raise RuntimeError("; ".join(errors))

    def rows(self):
        self.supervisor.tick()
        rows = []
        for name, runtime in self.supervisor.workers.items():
            others = self.external(name)
            state = runtime.state.value
            pid = str(runtime.process.pid) if runtime.process else ""
            if others:
                state = "running externally" if not runtime.process else "duplicate detected"
                pid = ", ".join(filter(None, [pid, *(str(p.pid) for p in others)]))
            elif runtime.config.mode == "off":
                state = "disabled in fleet"
            rows.append((name, state, pid, runtime.last_error or ""))
        return rows

    def close(self):
        # App exit stops its children; external processes remain unless Stop all was used.
        self.supervisor.shutdown()
