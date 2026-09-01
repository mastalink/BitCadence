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
