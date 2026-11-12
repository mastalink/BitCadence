from __future__ import annotations

from pathlib import Path

from mco.agentd.tokens import AgentdTokenStore


def test_control_and_log_capabilities_use_distinct_owner_only_files(
    tmp_path: Path, monkeypatch
) -> None:
    hardened: list[Path] = []
    monkeypatch.setattr(
        "mco.agentd.tokens._harden_owner_only", lambda path: hardened.append(path)
    )
    store = AgentdTokenStore(tmp_path / "agentd.token", tmp_path / "agentd.logs.token")

    control = store.control_token()
    logs = store.logs_token()

    assert control and logs and control != logs
    assert (tmp_path / "agentd.token").read_text(encoding="utf-8").strip() == control
    assert (tmp_path / "agentd.logs.token").read_text(encoding="utf-8").strip() == logs
    assert hardened == [tmp_path / "agentd.token", tmp_path / "agentd.logs.token"]


def test_tokens_are_hardened_once_then_served_from_memory(tmp_path: Path, monkeypatch) -> None:
    """The control API asks for the token on every request; hardening shells out."""
    hardened: list[Path] = []
    monkeypatch.setattr("mco.agentd.tokens._harden_owner_only", hardened.append)
    store = AgentdTokenStore(tmp_path / "agentd.token", tmp_path / "agentd.logs.token")
    assert len({store.control_token() for _ in range(5)}) == 1
    assert hardened == [tmp_path / "agentd.token"]
    # A new process reads the same secret back rather than minting another.
    again = AgentdTokenStore(tmp_path / "agentd.token", tmp_path / "agentd.logs.token")
    assert again.control_token() == store.control_token()


def test_token_files_are_owner_only(tmp_path: Path) -> None:
    import os

    import pytest

    if os.name == "nt":
        pytest.skip("POSIX mode bits; Windows uses an ACL to the user SID")
    store = AgentdTokenStore(tmp_path / "agentd.token", tmp_path / "agentd.logs.token")
    store.control_token()
    assert (tmp_path / "agentd.token").stat().st_mode & 0o777 == 0o600


def test_real_token_files_are_created_and_restricted_on_this_platform(tmp_path: Path) -> None:
    """No fakes: the real hardening path (chmod, or icacls to the user SID) runs."""
    store = AgentdTokenStore(tmp_path / "agentd.token", tmp_path / "agentd.logs.token")
    assert store.control_token() != store.logs_token()
    assert len(store.control_token()) >= 43  # 256 bits or more of randomness
    assert not list(tmp_path.glob(".agentd*.tmp"))
