from pathlib import Path
from io import BytesIO

from mco.agentd.logs import LogAggregator


def test_aggregates_unified_and_worker_logs_and_tails(tmp_path: Path) -> None:
    logs = LogAggregator(tmp_path)
    logs.write("codex/beast", "stdout", "leased job J-2041")
    logs.write("claude", "stderr", "retrying")

    unified = logs.tail(lines=1)
    worker = logs.tail("codex/beast", lines=10)
    assert len(unified) == 1
    assert "claude  stderr  retrying" in unified[0]
    assert len(worker) == 1
    assert "codex/beast  stdout  leased job J-2041" in worker[0]
    assert (tmp_path / "codex-beast.log").exists()


def test_drain_decodes_binary_subprocess_pipes(tmp_path: Path) -> None:
    logs = LogAggregator(tmp_path)
    stream = BytesIO(b"hello " + bytes([0xFF]) + b" world\n")
    logs._drain("codex", "stdout", stream)
    assert "hello \ufffd world" in logs.tail("codex")[0]
