"""
OS service integration for BitCadence gateway and waker processes.

The service managers run the same foreground commands as an operator would.
This module keeps artifact rendering separate from install actions so tests can
validate Windows Task Scheduler XML, systemd units, launchd plists, and command
argv without touching the real host service manager.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import os
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path
from typing import Iterable
from xml.sax.saxutils import escape

SERVICE_NAME = "BitCadence-gateway"

# Installs from before a rename registered the gateway under an older brand:
# "BatonCadenceGateway" (pre-standardisation), then "BatonCadence-gateway"
# (pre-BitCadence). Those machines still exist, and on them every
# `mco restart` / `service restart|status|logs|uninstall` silently addressed a
# task that does not exist - reporting "cannot find the file specified" while a
# perfectly healthy gateway ran under the old name. Resolve legacy names too.
LEGACY_GATEWAY_NAMES = ("BatonCadence-gateway", "BatonCadenceGateway")

# Every brand a service may be registered under, current first. Discovery must
# see all of them so an install predating the rename stays listable, stoppable,
# and uninstallable instead of becoming an orphan the CLI cannot address.
BRAND_PREFIXES = ("BitCadence", "BatonCadence")
_BRAND_SLUGS = tuple(p.lower() for p in BRAND_PREFIXES)

SCHEDULER_SERVICE_NAME = "BitCadence-scheduler"
SYSTEMD_UNIT_NAME = "bitcadence-gateway.service"
LAUNCHD_LABEL = "com.bitcadence.gateway"
WINDOWS_RESTART_INTERVAL = "PT1M"
WINDOWS_RESTART_COUNT = 3


def _debrand(slug: str) -> str | None:
    """The suffix after a brand slug, or None when `slug` carries no brand."""
    for brand in _BRAND_SLUGS:
        if slug.startswith(f"{brand}-"):
            return slug[len(brand) + 1:]
    return None


@dataclass(frozen=True)
class ServiceSpec:
    name: str
    kind: str
    argv: list[str]
    description: str
    restart_on_failure: bool
    role: str | None = None
    instance: str | None = None
    poll_interval: float | None = None

    @property
    def unit_name(self) -> str:
        return f"{_service_token(self.name)}.service"

    @property
    def launchd_label(self) -> str:
        token = _service_token(self.name)
        # A spec carrying a legacy name keeps its legacy label, so restart and
        # uninstall address the plist that is actually on disk.
        brand = next((b for b in _BRAND_SLUGS if token.startswith(f"{b}-")), _BRAND_SLUGS[0])
        return f"com.{brand}.{_debrand(token) or token}"

    @property
    def log_path(self) -> Path:
        if self.kind == "gateway":
            return gateway_log_path()
        return Path.home() / ".mco" / "logs" / f"{_service_token(self.name)}.log"


def gateway_log_path() -> Path:
    return Path.home() / ".mco" / "logs" / "gateway.log"


def _slug(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", value.strip()).strip("-").lower()
    return slug or "default"


def _service_token(name: str) -> str:
    return _slug(name)


def _waker_service_name(role: str, instance: str | None = None) -> str:
    parts = ["BitCadence", "wake", _slug(role)]
    if instance:
        parts.append(_slug(instance))
    return "-".join(parts)


def _poll_service_name(role: str, instance: str | None = None) -> str:
    parts = ["BitCadence", "poll", _slug(role)]
    if instance:
        parts.append(_slug(instance))
    return "-".join(parts)


def _service_python() -> str:
    """The interpreter a background service should run under.

    On Windows, `python.exe` is a console application: Task Scheduler allocates
    a console for it and the window flashes across whatever the operator is
    doing - every trigger, forever. `pythonw.exe` is the same interpreter built
    as a GUI subsystem binary, so no console is ever allocated. Fall back to
    `sys.executable` if it is missing (some embedded/portable installs ship
    only python.exe).
    """
    if os.name != "nt":
        return sys.executable
    # os.path rather than pathlib, deliberately: tests force os.name to "nt"
    # to exercise this branch on Linux CI, and pathlib.Path() consults os.name
    # at construction - it would try to build a WindowsPath on a POSIX host and
    # raise NotImplementedError. String joins carry no such platform coupling,
    # so the Windows branch stays testable everywhere.
    candidate = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    return candidate if os.path.exists(candidate) else sys.executable


def _serve_argv(host: str, port: int) -> list[str]:
    """Argv that runs the gateway in the foreground."""
    return [_service_python(), "-m", "mco.cli", "serve", "--host", host, "--port", str(port)]


def _wake_argv(role: str, exec_command: str, instance: str | None = None, min_interval: float = 10.0) -> list[str]:
    """Argv that runs the event-driven worker waker in the foreground.

    Deliberately carries no `--token`: the waker resolves its own identity from
    `~/.mco/tokens/<instance>.token` (see `mco.waker.resolve_agent_token`). A
    secret in argv would be readable by `schtasks /query`, `ps`, and any process
    listing on the box, and it would go stale the moment the token is rotated -
    the service definition would then need rewriting, which is exactly the kind
    of two-place update that strands fleets.
    """
    argv = [
        _service_python(),
        "-m",
        "mco.cli",
        "wake",
        "--role",
        role,
        "--exec",
        exec_command,
        "--min-interval",
        _format_interval(min_interval),
    ]
    if instance:
        argv.extend(["--instance", instance])
    return argv


def _poll_argv(exec_command: str) -> list[str]:
    """Argv that runs the configured worker wrapper once.

    On Windows, a poll worker is usually a `.cmd`/`.bat`, and Task Scheduler
    always allocates a console for one - so every poll interval flashes a
    black window across whatever the operator is doing. A worker that runs
    every 30 minutes, forever, makes that a permanent visual tic on the
    machine.

    Routing through `conhost.exe --headless` gives the batch file its console
    without ever showing it. The scripts themselves already hide their inner
    PowerShell; this covers the outer window Task Scheduler creates, which the
    script cannot suppress from inside itself.
    """
    argv = shlex.split(exec_command, posix=os.name != "nt")
    if os.name == "nt" and argv and _is_console_script(argv[0]):
        return ["conhost.exe", "--headless", *argv]
    return argv


def _is_console_script(path: str) -> bool:
    """True for the batch-file types Windows opens a console window for."""
    return path.lower().endswith((".cmd", ".bat"))


def _scheduler_argv(interval: float = 30.0) -> list[str]:
    """Argv that runs the schedule/loop scheduler in the foreground."""
    return [
        _service_python(), "-m", "mco.cli", "schedule", "run",
        "--interval", _format_interval(interval),
    ]


def _format_interval(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else str(value)


def _gateway_spec(host: str, port: int) -> ServiceSpec:
    return ServiceSpec(
        name=SERVICE_NAME,
        kind="gateway",
        argv=_serve_argv(host, port),
        description="BitCadence gateway",
        restart_on_failure=False,
    )


def _waker_spec(
    role: str,
    exec_command: str,
    instance: str | None = None,
    min_interval: float = 10.0,
) -> ServiceSpec:
    return ServiceSpec(
        name=_waker_service_name(role, instance),
        kind="wake",
        role=role,
        instance=instance,
        argv=_wake_argv(role, exec_command, instance=instance, min_interval=min_interval),
        description=f"BitCadence waker for {role}{('/' + instance) if instance else ''}",
        restart_on_failure=True,
    )


def _scheduler_spec(interval: float = 30.0) -> ServiceSpec:
    # restart_on_failure=True: a scheduler that quietly died is the failure
    # operators actually suffer - nobody notices until the nightly job hasn't
    # run for a week.
    return ServiceSpec(
        name=SCHEDULER_SERVICE_NAME,
        kind="scheduler",
        argv=_scheduler_argv(interval),
        description="BitCadence scheduler (schedules and loops)",
        restart_on_failure=True,
    )


def _poll_spec(
    role: str,
    exec_command: str,
    instance: str | None = None,
    poll_interval: float = 1800.0,
) -> ServiceSpec:
    return ServiceSpec(
        name=_poll_service_name(role, instance),
        kind="poll",
        role=role,
        instance=instance,
        argv=_poll_argv(exec_command),
        description=f"BitCadence polling worker for {role}{('/' + instance) if instance else ''}",
        restart_on_failure=False,
        poll_interval=poll_interval,
    )


def _windows_task_xml(host: str, port: int) -> str:
    return _service_windows_task_xml(_gateway_spec(host, port))


def _waker_windows_task_xml(
    role: str,
    exec_command: str,
    instance: str | None = None,
    min_interval: float = 10.0,
) -> str:
    return _service_windows_task_xml(_waker_spec(role, exec_command, instance, min_interval))


def _poll_windows_task_xml(
    role: str,
    exec_command: str,
    instance: str | None = None,
    poll_interval: float = 1800.0,
) -> str:
    return _service_windows_task_xml(_poll_spec(role, exec_command, instance, poll_interval))


def _service_windows_task_xml(spec: ServiceSpec) -> str:
    command = escape(spec.argv[0])
    arguments = escape(" ".join(shlex.quote(part) for part in spec.argv[1:]))
    working_dir = escape(str(Path.home()))
    restart_xml = ""
    if spec.restart_on_failure:
        restart_xml = f"""    <RestartOnFailure>
      <Interval>{WINDOWS_RESTART_INTERVAL}</Interval>
      <Count>{WINDOWS_RESTART_COUNT}</Count>
    </RestartOnFailure>
"""
    if spec.kind == "poll":
        triggers = f"""    <TimeTrigger>
      <Repetition>
        <Interval>{_windows_duration(spec.poll_interval or 1800.0)}</Interval>
        <StopAtDurationEnd>false</StopAtDurationEnd>
      </Repetition>
      <StartBoundary>2000-01-01T00:00:00</StartBoundary>
      <Enabled>true</Enabled>
    </TimeTrigger>
"""
    else:
        triggers = """    <BootTrigger>
      <Enabled>true</Enabled>
    </BootTrigger>
    <LogonTrigger>
      <Enabled>true</Enabled>
    </LogonTrigger>
"""
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>{escape(spec.description)}</Description>
  </RegistrationInfo>
  <Triggers>
{triggers.rstrip()}
  </Triggers>
  <Principals>
    <Principal id="Author">
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
{restart_xml}    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{command}</Command>
      <Arguments>{arguments}</Arguments>
      <WorkingDirectory>{working_dir}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""


def _windows_duration(seconds: float) -> str:
    remaining = max(1, int(seconds))
    days, remaining = divmod(remaining, 24 * 60 * 60)
    hours, remaining = divmod(remaining, 60 * 60)
    minutes, seconds = divmod(remaining, 60)
    date = f"{days}D" if days else ""
    time_parts = ""
    if hours:
        time_parts += f"{hours}H"
    if minutes:
        time_parts += f"{minutes}M"
    if seconds or not (date or time_parts):
        time_parts += f"{seconds}S"
    if time_parts:
        return f"P{date}T{time_parts}"
    return f"P{date}"


def _windows_task_xml_path(name: str = SERVICE_NAME) -> Path:
    return Path.home() / ".mco" / "service" / f"{name}.xml"


def _windows_create_cmd(xml_path: Path, name: str = SERVICE_NAME) -> list[str]:
    return ["schtasks", "/Create", "/TN", name, "/XML", str(xml_path), "/F"]


def _write_windows_task_xml(host: str, port: int) -> Path:
    path = _windows_task_xml_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_windows_task_xml(host, port), encoding="utf-16")
    return path


def _write_service_windows_task_xml(spec: ServiceSpec) -> Path:
    path = _windows_task_xml_path(spec.name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_service_windows_task_xml(spec), encoding="utf-16")
    return path


def _systemd_unit_path(unit_name: str = SYSTEMD_UNIT_NAME) -> Path:
    return Path.home() / ".config" / "systemd" / "user" / unit_name


def _systemd_unit_text(host: str, port: int) -> str:
    return _service_systemd_unit_text(_gateway_spec(host, port))


def _waker_systemd_unit_text(
    role: str,
    exec_command: str,
    instance: str | None = None,
    min_interval: float = 10.0,
) -> str:
    return _service_systemd_unit_text(_waker_spec(role, exec_command, instance, min_interval))


def _poll_systemd_unit_text(
    role: str,
    exec_command: str,
    instance: str | None = None,
    poll_interval: float = 1800.0,
) -> str:
    return _service_systemd_unit_text(_poll_spec(role, exec_command, instance, poll_interval))


def _poll_systemd_timer_text(
    role: str,
    exec_command: str,
    instance: str | None = None,
    poll_interval: float = 1800.0,
) -> str:
    return _service_systemd_timer_text(_poll_spec(role, exec_command, instance, poll_interval))


def _service_systemd_unit_text(spec: ServiceSpec) -> str:
    exec_start = " ".join(shlex.quote(part) for part in spec.argv)
    log_path = _systemd_log_path(spec)
    if spec.kind == "poll":
        return f"""[Unit]
Description={spec.description}
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart={exec_start}
WorkingDirectory=%h
Environment=HOME=%h
StandardOutput=append:{log_path}
StandardError=append:{log_path}
"""
    return f"""[Unit]
Description={spec.description}
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart={exec_start}
Restart=always
RestartSec=5
WorkingDirectory=%h
Environment=HOME=%h
StandardOutput=append:{log_path}
StandardError=append:{log_path}

[Install]
WantedBy=default.target
"""


def _service_systemd_timer_text(spec: ServiceSpec) -> str:
    return f"""[Unit]
Description=Run {spec.description} every {_format_interval(spec.poll_interval or 1800.0)} seconds

[Timer]
OnBootSec=0
OnUnitActiveSec={_format_interval(spec.poll_interval or 1800.0)}
Unit={spec.unit_name}
Persistent=true

[Install]
WantedBy=timers.target
"""


def _systemd_log_path(spec: ServiceSpec) -> str:
    token = _service_token(spec.name)
    if spec.kind == "gateway":
        return "%h/.mco/logs/gateway.log"
    return f"%h/.mco/logs/{token}.log"


def _launchd_plist_path(label: str = LAUNCHD_LABEL) -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"


def _launchd_plist_xml(host: str, port: int) -> str:
    return _service_launchd_plist_xml(_gateway_spec(host, port))


def _waker_launchd_plist_xml(
    role: str,
    exec_command: str,
    instance: str | None = None,
    min_interval: float = 10.0,
) -> str:
    return _service_launchd_plist_xml(_waker_spec(role, exec_command, instance, min_interval))


def _poll_launchd_plist_xml(
    role: str,
    exec_command: str,
    instance: str | None = None,
    poll_interval: float = 1800.0,
) -> str:
    return _service_launchd_plist_xml(_poll_spec(role, exec_command, instance, poll_interval))


def _service_launchd_plist_xml(spec: ServiceSpec) -> str:
    args_xml = "\n".join(f"      <string>{escape(part)}</string>" for part in spec.argv)
    log_path = escape(str(spec.log_path))
    working_dir = escape(str(Path.home()))
    keep_alive = "  <key>KeepAlive</key>\n  <true/>\n" if spec.restart_on_failure or spec.kind == "gateway" else ""
    start_interval = ""
    if spec.kind == "poll":
        start_interval = f"  <key>StartInterval</key>\n  <integer>{max(1, int(spec.poll_interval or 1800.0))}</integer>\n"
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
 "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>{spec.launchd_label}</string>
  <key>ProgramArguments</key>
  <array>
{args_xml}
  </array>
  <key>WorkingDirectory</key>
  <string>{working_dir}</string>
  <key>RunAtLoad</key>
  <true/>
{keep_alive}{start_interval}  <key>StandardOutPath</key>
  <string>{log_path}</string>
  <key>StandardErrorPath</key>
  <string>{log_path}</string>
</dict>
</plist>
"""


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True)


def _base_status(installed: bool, running: bool = False, last_exit: str = "unknown", name: str = SERVICE_NAME) -> dict[str, object]:
    return {"name": name, "installed": installed, "running": running, "last_exit": last_exit}


def _parse_windows_status(stdout: str, name: str = SERVICE_NAME) -> dict[str, object]:
    data: dict[str, str] = {}
    for line in stdout.splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            data[key.strip().lower()] = value.strip()
    status = data.get("status", "")
    result = _base_status(
        installed=True,
        running=status.lower() == "running",
        last_exit=data.get("last run result") or data.get("last result") or "unknown",
        name=name,
    )
    if name == SERVICE_NAME:
        result.pop("name")
    return result


def _win_install(host: str, port: int) -> tuple[bool, str]:
    xml_path = _write_windows_task_xml(host, port)
    res = _run(_windows_create_cmd(xml_path))
    if res.returncode != 0:
        return False, res.stderr.strip() or res.stdout.strip() or "schtasks /Create failed"
    start = _run(["schtasks", "/Run", "/TN", SERVICE_NAME])
    if start.returncode != 0:
        return False, start.stderr.strip() or start.stdout.strip() or "schtasks /Run failed"
    return True, f"Registered scheduled task '{SERVICE_NAME}' with ONSTART and ONLOGON triggers."


def _win_install_service(spec: ServiceSpec) -> tuple[bool, str]:
    xml_path = _write_service_windows_task_xml(spec)
    res = _run(_windows_create_cmd(xml_path, spec.name))
    if res.returncode != 0:
        return False, res.stderr.strip() or res.stdout.strip() or "schtasks /Create failed"
    start = _run(["schtasks", "/Run", "/TN", spec.name])
    if start.returncode != 0:
        return False, start.stderr.strip() or start.stdout.strip() or "schtasks /Run failed"
    restart_text = " with restart-on-failure settings" if spec.restart_on_failure else ""
    return True, f"Registered scheduled task '{spec.name}' with ONSTART and ONLOGON triggers{restart_text}."


def _win_uninstall(name: str = SERVICE_NAME) -> tuple[bool, str]:
    res = _run(["schtasks", "/Delete", "/TN", name, "/F"])
    if res.returncode != 0:
        return False, res.stderr.strip() or res.stdout.strip() or "Task not found."
    return True, f"Removed scheduled task '{name}'."


def _win_status(name: str = SERVICE_NAME) -> dict[str, object]:
    res = _run(["schtasks", "/Query", "/TN", name, "/FO", "LIST", "/V"])
    if res.returncode != 0:
        return _base_status(False, name=name)
    return _parse_windows_status(res.stdout, name=name)


def _win_list_status() -> list[dict[str, object]]:
    res = _run(["schtasks", "/Query", "/FO", "LIST", "/V"])
    if res.returncode != 0:
        return []
    states: list[dict[str, object]] = []
    for block in re.split(r"\r?\n\r?\n+", res.stdout):
        task_name = _extract_windows_task_name(block)
        if not task_name:
            continue
        states.append(_parse_windows_status(block, name=task_name))
    return states


def _extract_windows_task_name(block: str) -> str | None:
    for line in block.splitlines():
        if line.lower().startswith("taskname:"):
            name = line.split(":", 1)[1].strip().lstrip("\\")
            base_name = name.rsplit("\\", 1)[-1]
            # "BatonCadenceGateway" (no separator) predates the dashed naming,
            # so match on the bare brand slug rather than a "brand-" prefix.
            if _slug(base_name).startswith(_BRAND_SLUGS):
                return base_name
    return None


def _win_restart(name: str = SERVICE_NAME) -> tuple[bool, str]:
    stop = _run(["schtasks", "/End", "/TN", name])
    if stop.returncode != 0 and "not currently running" not in (stop.stderr + stop.stdout).lower():
        return False, stop.stderr.strip() or stop.stdout.strip() or "schtasks /End failed"
    start = _run(["schtasks", "/Run", "/TN", name])
    if start.returncode != 0:
        return False, start.stderr.strip() or start.stdout.strip() or "schtasks /Run failed"
    return True, f"Restarted scheduled task '{name}'."


def _linux_install(host: str, port: int) -> tuple[bool, str]:
    spec = _gateway_spec(host, port)
    path = _systemd_unit_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    gateway_log_path().parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_service_systemd_unit_text(spec), encoding="utf-8")
    return _linux_enable_unit(spec.unit_name, path)


def _linux_install_service(spec: ServiceSpec) -> tuple[bool, str]:
    path = _systemd_unit_path(spec.unit_name)
    path.parent.mkdir(parents=True, exist_ok=True)
    spec.log_path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_service_systemd_unit_text(spec), encoding="utf-8")
    return _linux_enable_unit(spec.unit_name, path)


def _linux_install_poll_service(spec: ServiceSpec) -> tuple[bool, str]:
    service_path = _systemd_unit_path(spec.unit_name)
    timer_name = _systemd_timer_name(spec.unit_name)
    timer_path = _systemd_unit_path(timer_name)
    service_path.parent.mkdir(parents=True, exist_ok=True)
    spec.log_path.parent.mkdir(parents=True, exist_ok=True)
    service_path.write_text(_service_systemd_unit_text(spec), encoding="utf-8")
    timer_path.write_text(_service_systemd_timer_text(spec), encoding="utf-8")
    for cmd in (
        ["systemctl", "--user", "daemon-reload"],
        ["systemctl", "--user", "enable", "--now", timer_name],
    ):
        res = _run(cmd)
        if res.returncode != 0:
            return False, res.stderr.strip() or res.stdout.strip() or f"{' '.join(cmd)} failed"
    return True, (f"Installed systemd --user service at {service_path} and timer at {timer_path}.\n"
                  "For boot-without-login, run: sudo loginctl enable-linger $USER")


def _systemd_timer_name(unit_name: str) -> str:
    return unit_name.removesuffix(".service") + ".timer"


def _linux_enable_unit(unit_name: str, path: Path) -> tuple[bool, str]:
    for cmd in (
        ["systemctl", "--user", "daemon-reload"],
        ["systemctl", "--user", "enable", "--now", unit_name],
    ):
        res = _run(cmd)
        if res.returncode != 0:
            return False, res.stderr.strip() or res.stdout.strip() or f"{' '.join(cmd)} failed"
    return True, (f"Installed systemd --user unit at {path} and started it.\n"
                  "For boot-without-login, run: sudo loginctl enable-linger $USER")


def _linux_uninstall(unit_name: str = SYSTEMD_UNIT_NAME) -> tuple[bool, str]:
    _run(["systemctl", "--user", "disable", "--now", unit_name])
    timer_name = _systemd_timer_name(unit_name)
    _run(["systemctl", "--user", "disable", "--now", timer_name])
    path = _systemd_unit_path(unit_name)
    if path.exists():
        path.unlink()
    timer_path = _systemd_unit_path(timer_name)
    if timer_path.exists():
        timer_path.unlink()
    _run(["systemctl", "--user", "daemon-reload"])
    return True, f"Removed systemd --user unit '{unit_name}'."


def _linux_status(unit_name: str = SYSTEMD_UNIT_NAME, name: str = SERVICE_NAME) -> dict[str, object]:
    enabled = _run(["systemctl", "--user", "is-enabled", unit_name])
    path = _systemd_unit_path(unit_name)
    timer_name = _systemd_timer_name(unit_name)
    timer_enabled = _run(["systemctl", "--user", "is-enabled", timer_name])
    timer_installed = _systemd_unit_path(timer_name).exists() or timer_enabled.returncode == 0
    if enabled.returncode != 0 and not path.exists() and not timer_installed:
        return _base_status(False, name=name)
    active_target = timer_name if timer_installed else unit_name
    active = _run(["systemctl", "--user", "is-active", active_target])
    exit_code = _run(["systemctl", "--user", "show", unit_name, "-p", "ExecMainStatus", "--value"])
    return _base_status(
        installed=True,
        running=active.stdout.strip() == "active",
        last_exit=exit_code.stdout.strip() or "unknown",
        name=name,
    )


def _linux_list_status() -> list[dict[str, object]]:
    root = _systemd_unit_path().parent
    units = sorted(
        {path for brand in _BRAND_SLUGS for path in root.glob(f"{brand}*.service")}
    )
    return [_linux_status(path.name, name=_name_from_unit(path.name)) for path in units]


def _name_from_unit(unit_name: str) -> str:
    stem = unit_name.removesuffix(".service")
    for brand, display in zip(_BRAND_SLUGS, BRAND_PREFIXES):
        if stem == f"{brand}-gateway":
            return SERVICE_NAME if brand == _BRAND_SLUGS[0] else f"{display}-gateway"
        if stem.startswith(f"{brand}-"):
            return f"{display}-" + stem[len(brand) + 1:]
    return stem


def _linux_restart(unit_name: str = SYSTEMD_UNIT_NAME) -> tuple[bool, str]:
    res = _run(["systemctl", "--user", "restart", unit_name])
    if res.returncode != 0:
        return False, res.stderr.strip() or res.stdout.strip() or "systemctl restart failed"
    return True, f"Restarted {unit_name}."


def _mac_install(host: str, port: int) -> tuple[bool, str]:
    spec = _gateway_spec(host, port)
    path = _launchd_plist_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    gateway_log_path().parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_service_launchd_plist_xml(spec), encoding="utf-8")
    return _mac_load_plist(spec.launchd_label, path)


def _mac_install_service(spec: ServiceSpec) -> tuple[bool, str]:
    path = _launchd_plist_path(spec.launchd_label)
    path.parent.mkdir(parents=True, exist_ok=True)
    spec.log_path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_service_launchd_plist_xml(spec), encoding="utf-8")
    return _mac_load_plist(spec.launchd_label, path)


def _mac_load_plist(label: str, path: Path) -> tuple[bool, str]:
    _run(["launchctl", "unload", str(path)])
    res = _run(["launchctl", "load", str(path)])
    if res.returncode != 0:
        return False, res.stderr.strip() or res.stdout.strip() or "launchctl load failed"
    return True, f"Installed LaunchAgent {label} at {path} and loaded it."


def _mac_uninstall(label: str = LAUNCHD_LABEL) -> tuple[bool, str]:
    path = _launchd_plist_path(label)
    if path.exists():
        _run(["launchctl", "unload", str(path)])
        path.unlink()
    return True, f"Removed LaunchAgent '{label}'."


def _mac_status(label: str = LAUNCHD_LABEL, name: str = SERVICE_NAME) -> dict[str, object]:
    path = _launchd_plist_path(label)
    if not path.exists():
        return _base_status(False, name=name)
    res = _run(["launchctl", "print", f"gui/{os.getuid()}/{label}"])
    output = res.stdout + res.stderr
    return _base_status(
        installed=True,
        running=res.returncode == 0 and "state = running" in output.lower(),
        last_exit=_extract_launchd_last_exit(output),
        name=name,
    )


def _mac_list_status() -> list[dict[str, object]]:
    root = _launchd_plist_path().parent
    plists = sorted(
        {path for brand in _BRAND_SLUGS for path in root.glob(f"com.{brand}*.plist")}
    )
    return [_mac_status(path.stem, name=_name_from_launchd_label(path.stem)) for path in plists]


def _name_from_launchd_label(label: str) -> str:
    if label == LAUNCHD_LABEL:
        return SERVICE_NAME
    for brand, display in zip(_BRAND_SLUGS, BRAND_PREFIXES):
        if label.startswith(f"com.{brand}."):
            return f"{display}-" + label[len(brand) + 5:]
    return label


def _extract_launchd_last_exit(output: str) -> str:
    for line in output.splitlines():
        lower = line.strip().lower()
        if lower.startswith("last exit code"):
            _, value = line.split("=", 1)
            return value.strip()
    return "unknown"


def _mac_restart(label: str = LAUNCHD_LABEL) -> tuple[bool, str]:
    path = _launchd_plist_path(label)
    if not path.exists():
        return False, "LaunchAgent is not installed."
    _run(["launchctl", "unload", str(path)])
    res = _run(["launchctl", "load", str(path)])
    if res.returncode != 0:
        return False, res.stderr.strip() or res.stdout.strip() or "launchctl load failed"
    return True, f"Restarted {label}."


def install(host: str, port: int) -> tuple[bool, str]:
    if os.name == "nt":
        return _win_install(host, port)
    if sys.platform == "darwin":
        return _mac_install(host, port)
    return _linux_install(host, port)


def install_waker(
    role: str,
    exec_command: str,
    instance: str | None = None,
    min_interval: float = 10.0,
) -> tuple[bool, str]:
    spec = _waker_spec(role, exec_command, instance=instance, min_interval=min_interval)
    if os.name == "nt":
        return _win_install_service(spec)
    if sys.platform == "darwin":
        return _mac_install_service(spec)
    return _linux_install_service(spec)


def install_scheduler(interval: float = 30.0) -> tuple[bool, str]:
    """Install the schedule/loop scheduler as a boot-persistent OS service."""
    spec = _scheduler_spec(interval)
    if os.name == "nt":
        return _win_install_service(spec)
    if sys.platform == "darwin":
        return _mac_install_service(spec)
    return _linux_install_service(spec)


def install_poll(
    role: str,
    exec_command: str,
    instance: str | None = None,
    poll_interval: float = 1800.0,
) -> tuple[bool, str]:
    spec = _poll_spec(role, exec_command, instance=instance, poll_interval=poll_interval)
    if os.name == "nt":
        return _win_install_service(spec)
    if sys.platform == "darwin":
        return _mac_install_service(spec)
    return _linux_install_poll_service(spec)


def uninstall(selector: str | None = SERVICE_NAME) -> tuple[bool, str]:
    target = _resolve_target(selector)
    if os.name == "nt":
        return _win_uninstall(target.name)
    if sys.platform == "darwin":
        return _mac_uninstall(target.launchd_label)
    return _linux_uninstall(target.unit_name)


def status(selector: str | None = SERVICE_NAME) -> dict[str, object] | list[dict[str, object]]:
    if selector is None:
        return list_status()
    target = _resolve_target(selector)
    if os.name == "nt":
        return _win_status(target.name)
    if sys.platform == "darwin":
        return _mac_status(target.launchd_label, target.name)
    return _linux_status(target.unit_name, target.name)


def list_status() -> list[dict[str, object]]:
    if os.name == "nt":
        return _win_list_status()
    if sys.platform == "darwin":
        return _mac_list_status()
    return _linux_list_status()


def restart(selector: str | None = SERVICE_NAME) -> tuple[bool, str]:
    target = _resolve_target(selector)
    if os.name == "nt":
        return _win_restart(target.name)
    if sys.platform == "darwin":
        return _mac_restart(target.launchd_label)
    return _linux_restart(target.unit_name)


def backend_name() -> str:
    if os.name == "nt":
        return "Windows Task Scheduler"
    if sys.platform == "darwin":
        return "macOS launchd"
    return "systemd --user"


def installed_gateway_task_name() -> str:
    """The gateway's task name as actually registered on this machine.

    Prefers the current name, falls back to a legacy one if that is what is
    really installed. Without this, service commands on an older install
    address a task that does not exist.

    Off Windows this stats the unit file / plist directly rather than going
    through list_status(): that helper spawns a process per installed service
    (`systemctl is-active` plus `systemctl show`, or `launchctl print`), and
    this runs on every `mco service ...` and `mco restart`.
    """
    if os.name == "nt":
        installed = {str(r.get("name", "")) for r in list_status()}
        if SERVICE_NAME in installed:
            return SERVICE_NAME
        for legacy in LEGACY_GATEWAY_NAMES:
            if legacy in installed:
                return legacy
        return SERVICE_NAME

    for name in (SERVICE_NAME, *LEGACY_GATEWAY_NAMES):
        if _gateway_artifact_exists(name):
            return name
    return SERVICE_NAME


def _gateway_artifact_exists(name: str) -> bool:
    """True when the gateway is registered under `name` on this POSIX host."""
    spec = replace(_gateway_spec("127.0.0.1", 18789), name=name)
    if sys.platform == "darwin":
        return _launchd_plist_path(spec.launchd_label).exists()
    return _systemd_unit_path(spec.unit_name).exists()


def _resolve_target(selector: str | None) -> ServiceSpec:
    if not selector or selector == "gateway" or selector == SERVICE_NAME or selector in LEGACY_GATEWAY_NAMES:
        spec = _gateway_spec("127.0.0.1", 18789)
        actual = installed_gateway_task_name()
        # Keep the spec's identity but address the task that is really there.
        return spec if actual == SERVICE_NAME else replace(spec, name=actual)
    for record in list_status():
        name = str(record.get("name", ""))
        if _selector_matches_name(selector, name):
            return _target_from_name(name)
    if _debrand(_slug(selector)) is not None:
        return _target_from_name(selector)
    return _target_from_name(_waker_service_name(selector))


def _selector_matches_name(selector: str, name: str) -> bool:
    if selector == name:
        return True
    slug_selector = _slug(selector)
    suffix = _debrand(_slug(name))
    if suffix is None:
        return False
    return suffix == f"wake-{slug_selector}" or suffix.startswith(f"wake-{slug_selector}-")


def _target_from_name(name: str) -> ServiceSpec:
    suffix = _debrand(_slug(name))
    if name == SERVICE_NAME or suffix == "gateway":
        return _gateway_spec("127.0.0.1", 18789)
    if name == SCHEDULER_SERVICE_NAME or suffix == "scheduler":
        return _scheduler_spec()
    kind = "poll" if (suffix or "").startswith("poll-") else "wake"
    return ServiceSpec(
        name=name,
        kind=kind,
        argv=[],
        description=name,
        restart_on_failure=kind == "wake",
    )


def tail_log(selector: str | None = SERVICE_NAME, lines: int = 80, follow: bool = False, sleep_seconds: float = 1.0) -> Iterable[str]:
    """Yield the tail of a service log, optionally following new lines."""
    path = log_path(selector)
    if not path.exists():
        return
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        buffer = handle.readlines()[-lines:]
        for line in buffer:
            yield line.rstrip("\n")
        if not follow:
            return
        while True:
            line = handle.readline()
            if line:
                yield line.rstrip("\n")
            else:
                time.sleep(sleep_seconds)


def log_path(selector: str | None = SERVICE_NAME) -> Path:
    return _resolve_target(selector).log_path
