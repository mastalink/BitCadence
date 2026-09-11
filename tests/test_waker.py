import asyncio
import sys

import pytest

from mco.waker import Waker


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def inbox(self):
        self.calls += 1
        if self.responses:
            return self.responses.pop(0)
        return []


def _event(event="job_pending", role="codex", instance=""):
    return {
        "type": "event",
        "payload": {
            "event": event,
            "job": {
                "id": "j1",
                "target_agent_role": role,
                "target_agent_id": instance,
            },
        },
    }


def _marker_cmd(marker, sleep=0.0):
    code = (
        "import pathlib,time; "
        f"p=pathlib.Path({str(marker)!r}); "
        "p.write_text((p.read_text() if p.exists() else '') + 'x'); "
        f"time.sleep({sleep})"
    )
    return f'"{sys.executable}" -c "{code}"'


def _exit_cmd(code):
    return f'"{sys.executable}" -c "import sys; sys.exit({code})"'


async def _wait_for_marker(marker, timeout=2.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if marker.exists():
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"marker was not written: {marker}")


@pytest.mark.asyncio
async def test_matching_job_pending_spawns(tmp_path):
    marker = tmp_path / "spawned.txt"
    client = FakeClient([[{"id": "j1"}]])
    waker = Waker(_marker_cmd(marker), "codex", "codex-beast", min_interval=0, client=client)

    await waker.handle_message(_event(role="CoDeX"))
    await waker.wait_for_idle()

    assert marker.read_text() == "x"
    assert client.calls == 1


@pytest.mark.asyncio
async def test_non_matching_events_do_not_spawn(tmp_path):
    marker = tmp_path / "spawned.txt"
    client = FakeClient([[{"id": "j1"}]])
    waker = Waker(_marker_cmd(marker), "codex", "codex-beast", min_interval=0, client=client)

    await waker.handle_message(_event(role="reviewer"))
    await waker.handle_message(_event(role="codex", instance="other-instance"))
    await waker.handle_message(_event(event="job_needs_approval", role="codex"))
    await waker.handle_message(_event(event="job_leased", role="codex"))
    await asyncio.sleep(0)

    assert not marker.exists()
    assert client.calls == 0


@pytest.mark.asyncio
async def test_burst_while_child_runs_sets_dirty_for_one_extra_drain(tmp_path):
    marker = tmp_path / "spawned.txt"
    client = FakeClient([[{"id": "j1"}], [{"id": "j2"}], [{"id": "j3"}]])
    waker = Waker(_marker_cmd(marker, sleep=0.25), "codex", "codex-beast", min_interval=0, client=client)

    await waker.handle_message(_event(role="codex"))
    await _wait_for_marker(marker)
    for _ in range(5):
        await waker.handle_message(_event(role="codex"))
    await waker.wait_for_idle()

    assert marker.read_text() == "xx"
    assert client.calls == 2


@pytest.mark.asyncio
async def test_reconnect_sweep_spawns_without_event(tmp_path):
    marker = tmp_path / "spawned.txt"
    client = FakeClient([[{"id": "j1"}]])
    waker = Waker(_marker_cmd(marker), "codex", "codex-beast", min_interval=0, client=client)

    waker.on_connected()
    await waker.wait_for_idle()

    assert marker.read_text() == "x"
    assert client.calls == 1


@pytest.mark.asyncio
async def test_empty_inbox_blocks_spawn_even_for_matching_event(tmp_path):
    marker = tmp_path / "spawned.txt"
    client = FakeClient([[]])
    waker = Waker(_marker_cmd(marker), "codex", "codex-beast", min_interval=0, client=client)

    await waker.handle_message(_event(role="codex"))
    await waker.wait_for_idle()

    assert not marker.exists()
    assert client.calls == 1


@pytest.mark.asyncio
async def test_nonzero_exec_does_not_kill_waker(tmp_path):
    marker = tmp_path / "spawned.txt"
    client = FakeClient([[{"id": "j1"}], [{"id": "j2"}]])
    waker = Waker(_exit_cmd(1), "codex", "codex-beast", min_interval=0, client=client)

    await waker.handle_message(_event(role="codex"))
    await waker.wait_for_idle()

    waker.exec_command = _marker_cmd(marker)
    await waker.handle_message(_event(role="codex"))
    await waker.wait_for_idle()

    assert marker.read_text() == "x"
    assert client.calls == 2


# ── token resolution (the multi-agent identity bug) ───────────────────────────

import mco.waker as waker_mod
from mco.waker import (
    WakerTokenError,
    agent_token_path,
    read_agent_token_file,
    resolve_agent_token,
)


class _Cfg:
    def __init__(self, **values):
        self._v = values

    def get(self, key, default=None):
        return self._v.get(key, default)


def _token_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(waker_mod, "AGENT_TOKEN_DIR", tmp_path)
    return tmp_path


def _clear_env(monkeypatch):
    monkeypatch.delenv("MCO_AGENT_TOKEN", raising=False)
    monkeypatch.delenv("MCO_LOCAL_TOKEN", raising=False)


def test_explicit_token_wins(tmp_path, monkeypatch):
    _clear_env(monkeypatch); _token_dir(tmp_path, monkeypatch)
    (tmp_path / "codex-beast.token").write_text("from-file", encoding="utf-8")
    monkeypatch.setenv("MCO_AGENT_TOKEN", "from-env")
    assert resolve_agent_token("codex-beast", explicit="explicit") == "explicit"


def test_env_beats_the_token_file(tmp_path, monkeypatch):
    _clear_env(monkeypatch); _token_dir(tmp_path, monkeypatch)
    (tmp_path / "codex-beast.token").write_text("from-file", encoding="utf-8")
    monkeypatch.setenv("MCO_AGENT_TOKEN", "from-env")
    assert resolve_agent_token("codex-beast") == "from-env"


def test_per_instance_token_file_is_actually_read(tmp_path, monkeypatch):
    """The regression this whole change exists for.

    `~/.mco/tokens/<instance>.token` was a documented convention that NOTHING
    consumed - operators wrote tokens there and wakers ignored them.
    """
    _clear_env(monkeypatch); _token_dir(tmp_path, monkeypatch)
    (tmp_path / "codex-beast.token").write_text("  file-token\n", encoding="utf-8")
    assert resolve_agent_token("codex-beast") == "file-token"


def test_each_instance_gets_its_own_identity(tmp_path, monkeypatch):
    # One machine, several agents: a single global MCO_AGENT_TOKEN cannot
    # express more than one identity.
    _clear_env(monkeypatch); _token_dir(tmp_path, monkeypatch)
    (tmp_path / "codex-beast.token").write_text("codex-tok", encoding="utf-8")
    (tmp_path / "grok-beast.token").write_text("grok-tok", encoding="utf-8")
    assert resolve_agent_token("codex-beast") == "codex-tok"
    assert resolve_agent_token("grok-beast") == "grok-tok"


def test_operator_token_is_refused_for_a_named_agent(tmp_path, monkeypatch):
    """The silent failure that killed the fleet for days.

    MCO_LOCAL_TOKEN is the operator token; it cannot authenticate as a named
    agent. Previously the waker used it anyway and died with a generic
    "Authentication failed" from the far end of a WebSocket.
    """
    _clear_env(monkeypatch); _token_dir(tmp_path, monkeypatch)
    cfg = _Cfg(MCO_LOCAL_TOKEN="operator-token")
    with pytest.raises(WakerTokenError) as exc:
        resolve_agent_token("codex-beast", config=cfg)
    message = str(exc.value)
    assert "codex-beast" in message
    assert "operator token" in message
    assert "mco reset-token" in message   # tells you how to fix it


def test_local_token_still_works_for_a_single_agent_host(tmp_path, monkeypatch):
    # No instance id => single-agent convenience path stays intact.
    _clear_env(monkeypatch); _token_dir(tmp_path, monkeypatch)
    cfg = _Cfg(MCO_LOCAL_TOKEN="operator-token")
    assert resolve_agent_token("", config=cfg) == "operator-token"


def test_config_token_used_when_no_file_exists(tmp_path, monkeypatch):
    _clear_env(monkeypatch); _token_dir(tmp_path, monkeypatch)
    cfg = _Cfg(MCO_AGENT_TOKEN="cfg-token")
    assert resolve_agent_token("codex-beast", config=cfg) == "cfg-token"


def test_missing_or_empty_token_file_is_not_a_crash(tmp_path, monkeypatch):
    _token_dir(tmp_path, monkeypatch)
    assert read_agent_token_file("nope") is None
    (tmp_path / "blank.token").write_text("   \n", encoding="utf-8")
    assert read_agent_token_file("blank") is None
    assert read_agent_token_file("") is None


def test_token_path_is_per_instance(tmp_path, monkeypatch):
    _token_dir(tmp_path, monkeypatch)
    assert agent_token_path("codex-beast").name == "codex-beast.token"


# ── path traversal in instance ids (CWE-22) ───────────────────────────────────

from mco.waker import UnsafeInstanceId, describe_token_path


@pytest.mark.parametrize("evil", [
    "../../../../etc/passwd",
    r"..\..\..\windows\win.ini",
    "a/b",
    "a\b",
    "..",
    ".",
    "with space",
    "semi;colon",
    "",
    "   ",
])
def test_token_path_rejects_traversal_and_separators(evil, tmp_path, monkeypatch):
    """instance_id is interpolated into a filename and comes from --instance,
    AGENT_INSTANCE_ID, or a config file. Unvalidated, it reads arbitrary files."""
    monkeypatch.setattr(waker_mod, "AGENT_TOKEN_DIR", tmp_path)
    with pytest.raises(UnsafeInstanceId):
        agent_token_path(evil)


@pytest.mark.parametrize("ok", ["codex-beast", "grok_beast", "agent.1", "A1"])
def test_token_path_accepts_real_instance_ids(ok, tmp_path, monkeypatch):
    monkeypatch.setattr(waker_mod, "AGENT_TOKEN_DIR", tmp_path)
    path = agent_token_path(ok)
    assert path.name == f"{ok}.token"
    assert tmp_path.resolve() in path.parents


def test_resolved_path_stays_inside_the_token_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(waker_mod, "AGENT_TOKEN_DIR", tmp_path)
    assert tmp_path.resolve() in agent_token_path("codex-beast").parents


def test_reading_an_unsafe_id_returns_none_rather_than_raising(tmp_path, monkeypatch):
    # One probe in a resolution chain: a bad id means "no token here".
    monkeypatch.setattr(waker_mod, "AGENT_TOKEN_DIR", tmp_path)
    assert read_agent_token_file("../../../../etc/passwd") is None


def test_error_message_construction_never_raises(tmp_path, monkeypatch):
    """The diagnostic must not blow up while being built for a bad id."""
    _clear_env(monkeypatch)
    monkeypatch.setattr(waker_mod, "AGENT_TOKEN_DIR", tmp_path)
    assert "invalid instance id" in describe_token_path("../evil")
    with pytest.raises(WakerTokenError) as exc:
        resolve_agent_token("../evil", config=_Cfg(MCO_LOCAL_TOKEN="op"))
    assert "../evil" in str(exc.value)
