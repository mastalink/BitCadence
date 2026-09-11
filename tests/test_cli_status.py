from typer.testing import CliRunner

import mco.cli as cli


class _FakeConfig:
    def get(self, key, default=None):
        if key == "MCO_PROFILE":
            return "Local-Only"
        return default

    def get_masked_config(self):
        return {
            "MCO_LOCAL_TOKEN": "s3****",
            "OPERATOR_NAME": "joe",
            "SUPABASE_URL": "ht****************",
            "PATH": "C:\\Windows\\System32",
            "APPDATA": "C:\\Users\\joe\\AppData\\Roaming",
        }


class _FakeStore:
    _path = "C:\\Users\\joe\\.mco\\secrets.enc"
    is_unlocked = False

    def is_initialized(self):
        return False


def test_status_hides_unrelated_environment_by_default(monkeypatch):
    monkeypatch.setattr(cli, "get_config", lambda: _FakeConfig())
    monkeypatch.setattr(cli, "get_secret_store", lambda: _FakeStore())

    result = CliRunner().invoke(cli.app, ["status"])

    assert result.exit_code == 0
    assert "MCO_LOCAL_TOKEN" in result.output
    assert "OPERATOR_NAME" in result.output
    assert "SUPABASE_URL" in result.output
    assert "PATH" not in result.output
    assert "APPDATA" not in result.output


def test_status_all_includes_unrelated_environment(monkeypatch):
    monkeypatch.setattr(cli, "get_config", lambda: _FakeConfig())
    monkeypatch.setattr(cli, "get_secret_store", lambda: _FakeStore())

    result = CliRunner().invoke(cli.app, ["status", "--all"])

    assert result.exit_code == 0
    assert "PATH" in result.output
    assert "APPDATA" in result.output
