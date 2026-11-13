"""The wiki's CLI-example checker must actually reject bad commands.

It once passed everything: Typer started shipping its own copy of click, so an
``isinstance(cmd, click.Group)`` test was always False and subcommands were never
resolved. These tests keep the checker honest and run it over the real wiki.
"""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("verify_cli_examples", ROOT / "docs/wiki/_review/verify_cli_examples.py")
verify = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verify)


def test_rejects_unknown_subcommand_and_options():
    assert verify.check(["mco", "teleport", "now"]) is not None
    assert verify.check(["bitcadence", "fix", "--sideways"]) is not None
    assert verify.check(["bitcadence", "--bogus"]) is not None


def test_accepts_real_commands_and_group_options():
    assert verify.check(["bitcadence", "fix", "--yes"]) is None
    assert verify.check(["bitcadence", "--no-menu"]) is None
    assert verify.check(["bitcadence", "helpers", "--json"]) is None
    assert verify.check(["mco", "schedule", "list"]) is None
    assert verify.check(["mco", "score", "--help"]) is None
    assert verify.check(["bitcadence", "helpers", "--json", "--help"]) is None


def test_every_wiki_example_parses(capsys):
    import sys
    argv, sys.argv = sys.argv, ["verify", str(ROOT / "docs/wiki")]
    try:
        assert verify.main() == 0, capsys.readouterr().out
    finally:
        sys.argv = argv
