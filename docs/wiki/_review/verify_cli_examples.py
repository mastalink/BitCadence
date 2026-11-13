"""Check every `mco ...` and `bitcadence ...` example in docs/wiki against the real CLI.

Dry parse only: each example is resolved through the Typer/Click command tree
and its arguments are parsed with ``make_context``. Nothing is invoked, so
nothing touches a live board or gateway.

Usage:  python docs/wiki/_review/verify_cli_examples.py [wiki_dir]
Exit code 1 if any example does not parse.
"""
from __future__ import annotations

import os
import re
import shlex
import sys
import tempfile
from pathlib import Path

# Keep any path defaults and config lookups away from the real home directory.
_SANDBOX = tempfile.mkdtemp(prefix="mco-wiki-verify-")
for _var in ("HOME", "USERPROFILE"):
    os.environ[_var] = _SANDBOX

import click  # noqa: E402
import typer.main  # noqa: E402

from mco.cli import app  # noqa: E402

ROOT = typer.main.get_command(app)
FENCE = re.compile(r"^\s*```(\w*)\s*$")
PROMPT = re.compile(r"^\s*(?:PS>\s*|\$\s+)?((?:mco|bitcadence)\s.*|mco|bitcadence)$")
INLINE = re.compile(r"`((?:mco|bitcadence)(?:\s[^`]*)?)`")  # both names run the same command tree
SHELL_LANGS = {"", "bash", "sh", "shell", "powershell", "ps1", "pwsh"}


def extract(path: Path) -> list[tuple[int, str, bool]]:
    """Return (line_number, command_text, is_inline) for every mco example in a file."""
    found: list[tuple[int, str, bool]] = []
    lang: str | None = None
    pending: tuple[int, str] | None = None
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        fence = FENCE.match(line)
        if fence:
            if lang is None:
                lang = fence.group(1).lower()
            else:
                lang = None
            pending = None
            continue
        if lang is None:
            found.extend((number, m.group(1), True) for m in INLINE.finditer(line))
            continue
        if lang not in SHELL_LANGS:
            continue
        if pending:
            start, text = pending
            text += " " + line.strip()
        else:
            m = PROMPT.match(line)
            if not m:
                continue
            start, text = number, m.group(1).strip()
        if text.endswith(("`", "\\")):
            pending = (start, text[:-1].rstrip())
            continue
        pending = None
        found.append((start, text, False))
    return found


def normalise(text: str) -> list[str] | None:
    """Turn an example into argv, or None when it is a prose placeholder."""
    if "..." in text or "…" in text:
        return None
    text = re.sub(r"\[[^\]]*\]", "", text)  # drop [optional] groups
    tokens = shlex.split(text)
    return [t.split("|")[0] if "|" in t and not t.startswith(("-", "<")) else t for t in tokens]


def check(argv: list[str], bare_ok: bool = False) -> str | None:
    """Return an error string, or None when argv parses.

    A bare command name in prose or a heading (``bare_ok``) only has to resolve
    to a real command; it is not expected to carry its required arguments.
    """
    cmd: click.Command = ROOT
    ctx = click.Context(ROOT, info_name="mco")
    rest = argv[1:]
    path = ["mco"]
    # Duck-typed on purpose: Typer now ships its own click copy, so isinstance(cmd, click.Group)
    # is always False and silently skipped every subcommand check (found 2026-10-05).
    while hasattr(cmd, "get_command") and hasattr(cmd, "list_commands"):
        # Options that belong to the group itself (e.g. `bitcadence --no-menu`, `helpers --json`).
        group_opts = {o: p for p in getattr(cmd, "params", []) for o in (*p.opts, *p.secondary_opts)}
        while rest and rest[0].startswith("-") and rest[0].split("=")[0] in group_opts:
            param = group_opts[rest[0].split("=")[0]]
            takes_value = not getattr(param, "is_flag", False) and "=" not in rest[0] and getattr(param, "nargs", 1) != 0
            rest = rest[2:] if takes_value else rest[1:]
        if not rest:
            return None
        if rest[0] == '--help':
            # Click synthesizes this option; it does not necessarily live in params.
            return None
        if rest[0].startswith("-"):
            return f"{' '.join(path)}: unknown option {rest[0]}"
        name = rest[0]
        sub = cmd.get_command(ctx, name)
        if sub is None:
            return f"`{' '.join(path)}` has no subcommand `{name}`"
        path.append(name)
        rest = rest[1:]
        cmd = sub
        ctx = click.Context(cmd, info_name=name, parent=ctx)
    if "--help" in rest or (bare_ok and not rest):
        return None
    try:
        cmd.make_context(path[-1], list(rest), parent=ctx.parent, resilient_parsing=False)
    except Exception as exc:  # click's or Typer's own UsageError
        if not any(k.__name__ == "UsageError" for k in type(exc).__mro__):
            raise
        return f"`{' '.join(path)}`: {exc.format_message()}"
    return None


def main() -> int:
    wiki = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[1]
    failures = skipped = checked = 0
    for md in sorted(wiki.rglob("*.md")):
        for line, text, inline in extract(md):
            argv = normalise(text)
            if argv is None:
                skipped += 1
                print(f"SKIP  {md.relative_to(wiki)}:{line}: {text}")
                continue
            checked += 1
            error = check(argv, bare_ok=inline)
            if error:
                failures += 1
                print(f"FAIL  {md.relative_to(wiki)}:{line}: {text}\n      {error}")
    print(f"\n{checked} examples checked, {failures} failed, {skipped} skipped (placeholders)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
