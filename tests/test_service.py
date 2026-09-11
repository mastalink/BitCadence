"""Service-manager integration: argv shaping, artifact rendering, dispatch."""

import sys
import xml.dom.minidom as minidom

import pytest

import mco.service as service

TASK_SCHEDULER_NS = "http://schemas.microsoft.com/windows/2004/02/mit/task"


class _RunResult:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _restart_on_failure_values(xml):
    doc = minidom.parseString(xml.encode("utf-16"))
    restart_nodes = doc.getElementsByTagNameNS(TASK_SCHEDULER_NS, "RestartOnFailure")
    assert len(restart_nodes) == 1
    restart = restart_nodes[0]

    interval_nodes = restart.getElementsByTagNameNS(TASK_SCHEDULER_NS, "Interval")
    count_nodes = restart.getElementsByTagNameNS(TASK_SCHEDULER_NS, "Count")
    assert len(interval_nodes) == 1
    assert len(count_nodes) == 1

    interval = interval_nodes[0].firstChild.nodeValue
    count = int(count_nodes[0].firstChild.nodeValue)
    return interval, count


def _task_scheduler_duration_seconds(duration):
    match = service.re.fullmatch(
        r"P(?:(?P<days>\d+)D)?(?:T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+)S)?)?",
        duration,
    )
    assert match, f"Unsupported Task Scheduler duration: {duration}"
    values = {name: int(value or 0) for name, value in match.groupdict().items()}
    return (
        values["days"] * 24 * 60 * 60
        + values["hours"] * 60 * 60
        + values["minutes"] * 60
        + values["seconds"]
    )


def test_serve_argv_runs_the_gateway():
    argv = service._serve_argv("0.0.0.0", 9000)
    assert argv[0] == service._service_python()
    assert argv[-5:] == ["serve", "--host", "0.0.0.0", "--port", "9000"]


def test_wake_argv_runs_the_waker_with_selector_values():
    argv = service._wake_argv("opencode", "opencode run", instance="opencode-beast", min_interval=2.5)
    assert argv[0] == service._service_python()
    assert argv[1:4] == ["-m", "mco.cli", "wake"]
    assert "--role" in argv and "opencode" in argv
    assert "--exec" in argv and "opencode run" in argv
    assert "--instance" in argv and "opencode-beast" in argv
    assert "--min-interval" in argv and "2.5" in argv


def test_waker_name_is_derived_from_kind_role_and_instance():
    spec = service._waker_spec("opencode", "opencode run", instance="opencode-beast")
    assert spec.name == "BitCadence-wake-opencode-opencode-beast"
    assert spec.name != service.SERVICE_NAME
    assert spec.unit_name == "bitcadence-wake-opencode-opencode-beast.service"
    assert spec.launchd_label == "com.bitcadence.wake-opencode-opencode-beast"


def test_poll_name_is_derived_from_kind_role_and_instance():
    spec = service._poll_spec("opencode", "C:/worker/run.cmd", instance="opencode-beast")
    assert spec.name == "BitCadence-poll-opencode-opencode-beast"
    assert spec.name != service.SERVICE_NAME
    assert spec.unit_name == "bitcadence-poll-opencode-opencode-beast.service"
    assert spec.launchd_label == "com.bitcadence.poll-opencode-opencode-beast"


def test_backend_name_is_platform_appropriate():
    name = service.backend_name()
    assert name in ("Windows Task Scheduler", "macOS launchd", "systemd --user")


def test_windows_task_xml_has_boot_and_logon_triggers():
    xml = service._windows_task_xml("127.0.0.1", 18789)
    minidom.parseString(xml.encode("utf-16"))
    assert "<BootTrigger>" in xml
    assert "<LogonTrigger>" in xml
    assert "<Command>" in xml and service._service_python() in xml
    assert "-m mco.cli serve --host 127.0.0.1 --port 18789" in xml


def test_windows_waker_task_xml_has_restart_on_failure_settings():
    xml = service._waker_windows_task_xml(
        "opencode",
        "opencode run",
        instance="opencode-beast",
        min_interval=5,
    )
    minidom.parseString(xml.encode("utf-16"))
    assert "<BootTrigger>" in xml
    assert "<LogonTrigger>" in xml
    assert "<RestartOnFailure>" in xml
    interval, count = _restart_on_failure_values(xml)
    assert _task_scheduler_duration_seconds(interval) >= 60
    assert 1 <= count <= 255
    assert "-m mco.cli wake --role opencode --exec" in xml
    assert "--instance opencode-beast" in xml


def test_windows_poll_task_xml_has_repetition_interval_without_waker_restart():
    xml = service._poll_windows_task_xml(
        "opencode",
        "worker-run --once",
        instance="opencode-beast",
        poll_interval=1800,
    )
    minidom.parseString(xml.encode("utf-16"))
    assert "<TimeTrigger>" in xml
    assert "<Repetition>" in xml
    assert "<Interval>PT30M</Interval>" in xml
    assert "<RestartOnFailure>" not in xml
    assert "worker-run" in xml
    assert "--once" in xml


def test_windows_install_uses_schtasks_xml_without_real_install(monkeypatch, tmp_path):
    calls = []
    xml_path = tmp_path / "BitCadence-gateway.xml"
    monkeypatch.setattr(service, "_windows_task_xml_path", lambda: xml_path)

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return _RunResult()

    monkeypatch.setattr(service.subprocess, "run", fake_run)

    ok_flag, detail = service._win_install("127.0.0.1", 18789)

    assert ok_flag
    assert "ONSTART and ONLOGON" in detail
    assert xml_path.exists()
    assert calls[0] == ["schtasks", "/Create", "/TN", service.SERVICE_NAME, "/XML", str(xml_path), "/F"]
    assert calls[1] == ["schtasks", "/Run", "/TN", service.SERVICE_NAME]


def test_windows_waker_install_uses_role_service_name_and_restart_xml(monkeypatch, tmp_path):
    calls = []
    xml_path = tmp_path / "BitCadence-wake-opencode-opencode-beast.xml"
    monkeypatch.setattr(service, "_windows_task_xml_path", lambda name=service.SERVICE_NAME: tmp_path / f"{name}.xml")

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return _RunResult()

    monkeypatch.setattr(service.subprocess, "run", fake_run)

    ok_flag, detail = service._win_install_service(
        service._waker_spec("opencode", "opencode run", instance="opencode-beast")
    )

    assert ok_flag
    assert "restart-on-failure" in detail
    assert xml_path.exists()
    interval, count = _restart_on_failure_values(xml_path.read_text(encoding="utf-16"))
    assert _task_scheduler_duration_seconds(interval) >= 60
    assert 1 <= count <= 255
    assert calls[0] == [
        "schtasks",
        "/Create",
        "/TN",
        "BitCadence-wake-opencode-opencode-beast",
        "/XML",
        str(xml_path),
        "/F",
    ]
    assert calls[1] == ["schtasks", "/Run", "/TN", "BitCadence-wake-opencode-opencode-beast"]


def test_windows_status_parses_running_and_last_exit():
    parsed = service._parse_windows_status(
        "TaskName: BitCadence-gateway\n"
        "Status: Running\n"
        "Last Run Result: 0x0\n"
    )
    assert parsed == {"installed": True, "running": True, "last_exit": "0x0"}


def test_systemd_unit_renders_execstart_restart_and_logs():
    unit = service._systemd_unit_text("127.0.0.1", 18789)
    assert "ExecStart=" in unit
    assert "serve --host 127.0.0.1 --port 18789" in unit
    assert "Restart=always" in unit
    assert "StandardOutput=append:%h/.mco/logs/gateway.log" in unit
    assert "StandardError=append:%h/.mco/logs/gateway.log" in unit


def test_systemd_waker_unit_restarts_always_and_logs_to_waker_log():
    unit = service._waker_systemd_unit_text("opencode", "opencode run", instance="opencode-beast")
    assert "ExecStart=" in unit
    assert "wake --role opencode --exec" in unit
    assert "Restart=always" in unit
    assert "RestartSec=5" in unit
    assert "StandardOutput=append:%h/.mco/logs/bitcadence-wake-opencode-opencode-beast.log" in unit
    assert "StandardError=append:%h/.mco/logs/bitcadence-wake-opencode-opencode-beast.log" in unit


def test_systemd_poll_unit_and_timer_run_worker_on_fixed_interval():
    unit = service._poll_systemd_unit_text("opencode", "worker-run --once", instance="opencode-beast")
    timer = service._poll_systemd_timer_text(
        "opencode",
        "worker-run --once",
        instance="opencode-beast",
        poll_interval=1800,
    )
    assert "Type=oneshot" in unit
    assert "ExecStart=worker-run --once" in unit
    assert "Restart=always" not in unit
    assert "StandardOutput=append:%h/.mco/logs/bitcadence-poll-opencode-opencode-beast.log" in unit
    assert "OnUnitActiveSec=1800" in timer
    assert "Unit=bitcadence-poll-opencode-opencode-beast.service" in timer
    assert "WantedBy=timers.target" in timer


def test_systemd_install_writes_unit_and_enables_without_real_install(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(service, "_systemd_unit_path", lambda: tmp_path / "bc.service")
    monkeypatch.setattr(service, "gateway_log_path", lambda: tmp_path / "logs" / "gateway.log")
    monkeypatch.setattr(
        service.subprocess,
        "run",
        lambda cmd, **kwargs: calls.append(cmd) or _RunResult(),
    )

    ok_flag, _ = service._linux_install("127.0.0.1", 18789)

    assert ok_flag
    assert (tmp_path / "bc.service").read_text(encoding="utf-8") == service._systemd_unit_text("127.0.0.1", 18789)
    assert ["systemctl", "--user", "enable", "--now", service.SYSTEMD_UNIT_NAME] in calls


def test_systemd_waker_install_writes_distinct_unit_without_real_install(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(service, "_systemd_unit_path", lambda unit_name=service.SYSTEMD_UNIT_NAME: tmp_path / unit_name)
    monkeypatch.setattr(
        service.subprocess,
        "run",
        lambda cmd, **kwargs: calls.append(cmd) or _RunResult(),
    )

    spec = service._waker_spec("opencode", "opencode run", instance="opencode-beast")
    ok_flag, _ = service._linux_install_service(spec)

    assert ok_flag
    unit_path = tmp_path / "bitcadence-wake-opencode-opencode-beast.service"
    assert unit_path.read_text(encoding="utf-8") == service._service_systemd_unit_text(spec)
    assert ["systemctl", "--user", "enable", "--now", spec.unit_name] in calls


def test_systemd_poll_install_writes_service_and_timer_without_real_install(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(service, "_systemd_unit_path", lambda unit_name=service.SYSTEMD_UNIT_NAME: tmp_path / unit_name)
    monkeypatch.setattr(
        service.subprocess,
        "run",
        lambda cmd, **kwargs: calls.append(cmd) or _RunResult(),
    )

    spec = service._poll_spec("opencode", "worker-run --once", instance="opencode-beast", poll_interval=1800)
    ok_flag, _ = service._linux_install_poll_service(spec)

    assert ok_flag
    service_path = tmp_path / "bitcadence-poll-opencode-opencode-beast.service"
    timer_path = tmp_path / "bitcadence-poll-opencode-opencode-beast.timer"
    assert service_path.read_text(encoding="utf-8") == service._service_systemd_unit_text(spec)
    assert timer_path.read_text(encoding="utf-8") == service._service_systemd_timer_text(spec)
    assert ["systemctl", "--user", "enable", "--now", "bitcadence-poll-opencode-opencode-beast.timer"] in calls


def test_launchd_plist_is_valid_xml_and_logs_to_gateway_log():
    xml = service._launchd_plist_xml("127.0.0.1", 18789)
    minidom.parseString(xml)
    assert service.LAUNCHD_LABEL in xml
    assert "<key>RunAtLoad</key>" in xml
    assert "<key>KeepAlive</key>" in xml
    assert ".mco/logs/gateway.log" in xml.replace("\\", "/")


def test_launchd_waker_plist_has_keepalive_and_distinct_label():
    xml = service._waker_launchd_plist_xml("opencode", "opencode run", instance="opencode-beast")
    minidom.parseString(xml)
    assert "com.bitcadence.wake-opencode-opencode-beast" in xml
    assert "<key>KeepAlive</key>" in xml
    assert "<true/>" in xml
    assert ".mco/logs/bitcadence-wake-opencode-opencode-beast.log" in xml.replace("\\", "/")


def test_launchd_poll_plist_has_startinterval_and_distinct_label():
    xml = service._poll_launchd_plist_xml(
        "opencode",
        "worker-run --once",
        instance="opencode-beast",
        poll_interval=1800,
    )
    minidom.parseString(xml)
    assert "com.bitcadence.poll-opencode-opencode-beast" in xml
    assert "<key>StartInterval</key>" in xml
    assert "<integer>1800</integer>" in xml
    assert "<key>KeepAlive</key>" not in xml
    assert ".mco/logs/bitcadence-poll-opencode-opencode-beast.log" in xml.replace("\\", "/")


def test_install_dispatch_matches_platform(monkeypatch):
    seen = {}
    monkeypatch.setattr(service, "_win_install", lambda h, p: seen.setdefault("win", True) and (True, ""))
    monkeypatch.setattr(service, "_mac_install", lambda h, p: seen.setdefault("mac", True) and (True, ""))
    monkeypatch.setattr(service, "_linux_install", lambda h, p: seen.setdefault("linux", True) and (True, ""))
    service.install("127.0.0.1", 18789)
    assert len(seen) == 1  # exactly one backend was dispatched to


# ── service hygiene: hidden poll windows + legacy gateway task name ───────────

def test_windows_poll_argv_hides_the_console_window(monkeypatch):
    """A poll worker that runs forever must not flash a window every interval.

    Task Scheduler allocates a console for any .cmd/.bat. The script's own
    inner `-WindowStyle Hidden` cannot suppress the outer window, so the wrap
    has to happen here.
    """
    monkeypatch.setattr(service.os, "name", "nt")
    argv = service._poll_argv("C:/Users/x/.mco/bin/reviewer-run.cmd")
    assert argv[0] == "conhost.exe"
    assert "--headless" in argv
    assert argv[-1].endswith("reviewer-run.cmd")


def test_non_batch_poll_commands_are_left_alone(monkeypatch):
    monkeypatch.setattr(service.os, "name", "nt")
    argv = service._poll_argv("python.exe worker.py")
    assert argv[0] == "python.exe"


def test_poll_argv_unchanged_off_windows(monkeypatch):
    monkeypatch.setattr(service.os, "name", "posix")
    argv = service._poll_argv("/usr/local/bin/worker.sh --once")
    assert argv == ["/usr/local/bin/worker.sh", "--once"]


@pytest.mark.parametrize("legacy", ["BatonCadenceGateway", "BatonCadence-gateway"])
def test_legacy_gateway_task_name_is_resolved(monkeypatch, legacy):
    """Older installs registered the gateway under a previous brand.

    'BatonCadenceGateway' predates the dashed naming; 'BatonCadence-gateway'
    predates the BitCadence rename. On those machines every service command
    addressed a task that did not exist, reporting "cannot find the file
    specified" while a healthy gateway ran under the old name.
    """
    monkeypatch.setattr(service.os, "name", "nt")
    monkeypatch.setattr(service, "list_status", lambda: [{"name": legacy}])
    assert service.installed_gateway_task_name() == legacy
    assert service._resolve_target(None).name == legacy
    assert service._resolve_target("gateway").name == legacy


def test_current_gateway_name_wins_when_both_exist(monkeypatch):
    monkeypatch.setattr(service.os, "name", "nt")
    monkeypatch.setattr(service, "list_status",
                        lambda: [{"name": "BatonCadence-gateway"}, {"name": service.SERVICE_NAME}])
    assert service.installed_gateway_task_name() == service.SERVICE_NAME


def test_gateway_name_defaults_when_nothing_installed(monkeypatch):
    monkeypatch.setattr(service.os, "name", "nt")
    monkeypatch.setattr(service, "list_status", lambda: [])
    assert service.installed_gateway_task_name() == service.SERVICE_NAME


def _posix_home(monkeypatch, tmp_path, *unit_names):
    """A fake $HOME with systemd unit files for `unit_names` and nothing else."""
    units = tmp_path / ".config" / "systemd" / "user"
    units.mkdir(parents=True)
    for unit in unit_names:
        (units / unit).write_text("", encoding="utf-8")
    monkeypatch.setattr(service.os, "name", "posix")
    monkeypatch.setattr(service.sys, "platform", "linux")
    monkeypatch.setattr(service.Path, "home", staticmethod(lambda: tmp_path))


def test_posix_gateway_resolves_legacy_unit(monkeypatch, tmp_path):
    """Off Windows the legacy unit must still be addressable.

    Before, `installed_gateway_task_name` returned the current name
    unconditionally off Windows, so `mco service restart|uninstall` on a
    pre-rename Linux install addressed a unit that does not exist.
    """
    _posix_home(monkeypatch, tmp_path, "batoncadence-gateway.service")
    assert service.installed_gateway_task_name() == "BatonCadence-gateway"
    assert service._resolve_target(None).unit_name == "batoncadence-gateway.service"


def test_posix_gateway_prefers_current_unit(monkeypatch, tmp_path):
    _posix_home(
        monkeypatch, tmp_path,
        "batoncadence-gateway.service", "bitcadence-gateway.service",
    )
    assert service.installed_gateway_task_name() == service.SERVICE_NAME


def test_posix_gateway_lookup_spawns_no_processes(monkeypatch, tmp_path):
    """Resolution runs on every `mco service ...`; it must stay a stat.

    Going through list_status() here spawned `systemctl is-active` plus
    `systemctl show` per installed unit (or `launchctl print` on macOS) before
    every service command.
    """
    _posix_home(monkeypatch, tmp_path, "bitcadence-gateway.service")

    def _boom(*a, **k):
        raise AssertionError("resolution must not shell out")

    monkeypatch.setattr(service, "_run", _boom)
    monkeypatch.setattr(service, "list_status", _boom)
    assert service.installed_gateway_task_name() == service.SERVICE_NAME
# ── scheduler service (schedules and loops) ───────────────────────────────────

def test_scheduler_argv_runs_the_schedule_daemon():
    argv = service._scheduler_argv(45.0)
    assert argv[0] == service._service_python()
    assert argv[1:] == ["-m", "mco.cli", "schedule", "run", "--interval", "45"]


def test_scheduler_spec_restarts_on_failure():
    # A scheduler that quietly died on reboot is the failure nobody notices
    # until the nightly job hasn't run for a week.
    spec = service._scheduler_spec()
    assert spec.name == service.SCHEDULER_SERVICE_NAME
    assert spec.kind == "scheduler"
    assert spec.restart_on_failure is True


def test_scheduler_service_name_round_trips_through_resolution():
    # `mco service status/restart/uninstall BitCadence-scheduler` must find it
    # rather than falling through to the waker-name guess.
    resolved = service._target_from_name(service.SCHEDULER_SERVICE_NAME)
    assert resolved.kind == "scheduler"
    assert resolved.restart_on_failure is True


def test_scheduler_has_its_own_log_and_labels_per_platform():
    spec = service._scheduler_spec()
    assert spec.log_path.name == "bitcadence-scheduler.log"
    assert spec.launchd_label == "com.bitcadence.scheduler"
    assert spec.unit_name == "bitcadence-scheduler.service"


def test_windows_scheduler_task_xml_has_valid_restart_settings():
    # PR #27's bug class: XML that installs fine but silently never restarts.
    xml = service._service_windows_task_xml(service._scheduler_spec())
    interval, count = _restart_on_failure_values(xml)
    assert _task_scheduler_duration_seconds(interval) > 0
    assert count >= 1


def test_launchd_scheduler_plist_is_valid_xml():
    plist = service._service_launchd_plist_xml(service._scheduler_spec())
    minidom.parseString(plist.encode("utf-8"))
    assert "com.bitcadence.scheduler" in plist


def test_systemd_scheduler_unit_renders_execstart():
    unit = service._service_systemd_unit_text(service._scheduler_spec())
    assert "schedule" in unit and "run" in unit


# ── no console windows on Windows ────────────────────────────────────────────

def test_service_python_is_windowless_on_windows(monkeypatch, tmp_path):
    """python.exe is a console app: Task Scheduler flashes a window on every
    trigger, forever. pythonw.exe is the same interpreter with no console."""
    fake = tmp_path / "python.exe"
    fake.write_text("", encoding="utf-8")
    (tmp_path / "pythonw.exe").write_text("", encoding="utf-8")
    monkeypatch.setattr(service.os, "name", "nt")
    monkeypatch.setattr(service.sys, "executable", str(fake))
    assert service._service_python().endswith("pythonw.exe")


def test_service_python_falls_back_when_pythonw_absent(monkeypatch, tmp_path):
    """Embedded/portable installs may ship only python.exe - never point a
    service at an interpreter that is not there."""
    fake = tmp_path / "python.exe"
    fake.write_text("", encoding="utf-8")
    monkeypatch.setattr(service.os, "name", "nt")
    monkeypatch.setattr(service.sys, "executable", str(fake))
    assert service._service_python() == str(fake)


def test_service_python_untouched_off_windows(monkeypatch):
    monkeypatch.setattr(service.os, "name", "posix")
    assert service._service_python() == sys.executable


def test_every_owned_entrypoint_is_windowless(monkeypatch, tmp_path):
    """The gateway, waker and scheduler are all ours - none may allocate a
    console. Regression guard for the 'flashing terminals' report."""
    fake = tmp_path / "python.exe"
    fake.write_text("", encoding="utf-8")
    (tmp_path / "pythonw.exe").write_text("", encoding="utf-8")
    monkeypatch.setattr(service.os, "name", "nt")
    monkeypatch.setattr(service.sys, "executable", str(fake))
    for argv in (
        service._serve_argv("127.0.0.1", 18789),
        service._wake_argv("codex", "worker.cmd", instance="w1"),
        service._scheduler_argv(30.0),
    ):
        assert argv[0].endswith("pythonw.exe"), argv
