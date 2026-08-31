"""Pipe path for scripts/install.sh: do not die on missing /dev/tty.

Growth G1: `bash scripts/install.sh --no-prompt` with no TTY died at ~2s
(`/dev/tty: No such device or address`) because `exec </dev/tty` ran
before flags were parsed. Same kill on advertised `curl | bash`.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INSTALL_SH = ROOT / "scripts" / "install.sh"
WEBSITE_SH = ROOT / "website" / "install.sh"
HELPERS_MARK = "# ---- helpers ----------------------------------------------------------------"


def _prefix_through_tty_guard() -> str:
    text = INSTALL_SH.read_text(encoding="utf-8")
    assert HELPERS_MARK in text
    return text.split(HELPERS_MARK, 1)[0] + "\necho SURVIVED\nexit 0\n"


def test_flags_parsed_before_tty_redirect():
    text = INSTALL_SH.read_text(encoding="utf-8")
    assert text.index("--no-prompt|-y") < text.index(
        'if [ "$NO_PROMPT" -eq 0 ] && [ ! -t 0 ]; then'
    )


def test_no_unconditional_dev_tty_exec():
    text = INSTALL_SH.read_text(encoding="utf-8")
    assert "if [ ! -t 0 ]; then\n    exec </dev/tty\nfi" not in text


def _run_prefix(tmp_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    probe = tmp_path / "probe.sh"
    probe.write_text(_prefix_through_tty_guard(), encoding="utf-8")
    return subprocess.run(
        ["bash", str(probe), *args],
        input="",
        capture_output=True,
        text=True,
        timeout=5,
    )


def test_pipe_no_prompt_survives(tmp_path):
    r = _run_prefix(tmp_path, "--no-prompt")
    assert "No such device or address" not in r.stderr
    assert "SURVIVED" in r.stdout
    assert r.returncode == 0


def test_pipe_without_flags_survives(tmp_path):
    """Advertised curl|bash: stdin is a pipe, no --no-prompt."""
    r = _run_prefix(tmp_path)
    assert "No such device or address" not in r.stderr
    assert "SURVIVED" in r.stdout
    assert r.returncode == 0


def test_website_bootstrap_skips_tty_when_missing():
    text = WEBSITE_SH.read_text(encoding="utf-8")
    # Unconditional form (no "$@", no openability probe) must be gone.
    assert 'exec bash "$_self" </dev/tty' not in text
    assert "(exec </dev/tty) 2>/dev/null" in text
    assert 'exec bash "$_self" "$@"' in text

def test_no_prompt_git_pull_does_not_abort_install():
    """CI PR checkout is detached HEAD; git pull exits 128. --no-prompt must continue."""
    text = INSTALL_SH.read_text(encoding="utf-8")
    assert 'if git -C "$ROOT" pull --ff-only origin main; then' in text
    assert "Could not fast-forward onto origin/main" in text

