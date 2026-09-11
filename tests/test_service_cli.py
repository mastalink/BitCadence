from typer.testing import CliRunner

from mco import cli
import mco.service as service


def test_service_install_waker_accepts_positional_exec(monkeypatch):
    seen = {}
    monkeypatch.setattr(service, "backend_name", lambda: "test backend")
    monkeypatch.setattr(
        service,
        "install_waker",
        lambda role, exec_command, instance=None, min_interval=10.0: (
            seen.update(
                role=role,
                exec_command=exec_command,
                instance=instance,
                min_interval=min_interval,
            )
            or (True, "installed")
        ),
    )

    result = CliRunner().invoke(
        cli.app,
        [
            "service",
            "install-waker",
            "opencode",
            "opencode run",
            "--instance",
            "opencode-beast",
            "--min-interval",
            "5",
        ],
    )

    assert result.exit_code == 0, result.output
    assert seen == {
        "role": "opencode",
        "exec_command": "opencode run",
        "instance": "opencode-beast",
        "min_interval": 5.0,
    }


def test_service_install_waker_accepts_exec_option(monkeypatch):
    seen = {}
    monkeypatch.setattr(service, "backend_name", lambda: "test backend")
    monkeypatch.setattr(
        service,
        "install_waker",
        lambda role, exec_command, instance=None, min_interval=10.0: (
            seen.update(role=role, exec_command=exec_command)
            or (True, "installed")
        ),
    )

    result = CliRunner().invoke(cli.app, ["service", "install-waker", "codex", "--exec", "codex run"])

    assert result.exit_code == 0, result.output
    assert seen == {"role": "codex", "exec_command": "codex run"}
