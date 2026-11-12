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


# --- ADR 0002 R10: the log contract ---------------------------------------


def test_both_streams_are_drained_concurrently_under_a_flood(tmp_path: Path) -> None:
    """A child that floods stderr first would deadlock a reader that drains
    stdout to the end before touching stderr. Both pipes are far over the OS
    pipe buffer, so this only finishes if they are drained at the same time."""
    import subprocess
    import sys

    code = (
        "import sys;"
        "[sys.stderr.write('e' * 200 + '\\n') for _ in range(5000)]; sys.stderr.flush();"
        "[sys.stdout.write('o' * 200 + '\\n') for _ in range(5000)]; sys.stdout.flush()"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", code],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    logs = LogAggregator(tmp_path)
    threads = logs.attach("flood", process)
    assert len(threads) == 2
    assert process.wait(timeout=60) == 0
    for thread in threads:
        thread.join(timeout=60)
        assert not thread.is_alive()
    lines = logs.tail("flood", 10_000)
    assert sum("  stderr  " in line for line in lines) == 5000
    assert sum("  stdout  " in line for line in lines) == 5000
    logs.close()


def test_output_with_no_newline_is_read_in_bounded_pieces(tmp_path: Path) -> None:
    from mco.agentd.logs import MAX_LINE_BYTES

    class Endless(BytesIO):
        largest = 0

        def readline(self, limit=-1):
            assert limit == MAX_LINE_BYTES  # never an unbounded read
            piece = super().readline(limit)
            Endless.largest = max(Endless.largest, len(piece))
            return piece

    logs = LogAggregator(tmp_path)
    logs._drain("codex", "stdout", Endless(b"x" * (MAX_LINE_BYTES * 3 + 7)))
    assert Endless.largest == MAX_LINE_BYTES
    assert len(logs.tail("codex")) == 4
    logs.close()


def test_a_full_disk_drops_lines_but_never_stops_draining(tmp_path: Path, monkeypatch) -> None:
    logs = LogAggregator(tmp_path)
    logs.write("codex", "stdout", "before")
    disk_full = {"on": True}
    full_disk = RotatingFileHandlerProbe.install(monkeypatch, disk_full)
    for handler in logs._handlers.values():  # the files that are already open, too
        handler.stream = full_disk(handler.stream)

    stream = BytesIO(b"lost 1\nlost 2\nlost 3\n")
    logs._drain("codex", "stdout", stream)  # must return, not raise, not hang
    assert stream.closed
    health = logs.health()
    assert health["dropped_lines"] == 6  # 3 lines x (unified + per-worker)
    assert "No space left" in str(health["last_write_error"])

    disk_full["on"] = False  # space comes back: logging resumes by itself
    logs.write("codex", "stdout", "after")
    assert [line.rsplit("  ", 1)[-1] for line in logs.tail("codex")] == ["before", "after"]
    logs.close()


class RotatingFileHandlerProbe:
    """Makes every log file write fail with ENOSPC while the flag is on."""

    @staticmethod
    def install(monkeypatch, flag):
        import errno
        from logging.handlers import RotatingFileHandler

        real_open = RotatingFileHandler._open

        class FullDisk:
            def __init__(self, stream):
                self._stream = stream

            def write(self, data):
                if flag["on"]:
                    raise OSError(errno.ENOSPC, "No space left on device")
                return self._stream.write(data)

            def __getattr__(self, name):
                return getattr(self._stream, name)

        monkeypatch.setattr(RotatingFileHandler, "_open", lambda self: FullDisk(real_open(self)))
        return FullDisk


def test_logs_rotate_at_the_size_cap_and_keep_a_bounded_history(tmp_path: Path) -> None:
    logs = LogAggregator(tmp_path, max_bytes=2_000, backup_count=2)
    for index in range(400):
        logs.write("codex", "stdout", f"line {index:04d} " + "x" * 40)
    logs.close()
    files = sorted(p.name for p in tmp_path.glob("codex.log*"))
    assert files == ["codex.log", "codex.log.1", "codex.log.2"]
    assert all(p.stat().st_size <= 2_000 for p in tmp_path.glob("codex.log*"))
    assert "line 0399" in (tmp_path / "codex.log").read_text(encoding="utf-8")
