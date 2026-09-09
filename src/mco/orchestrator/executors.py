"""
Default role executors — the "configure the agents" layer.

Each registered executor has the signature the listener expects:
    async def executor(job: dict, prompt: str) -> tuple[Optional[str], Optional[str]]
returning (output, error). These run the local coding-agent CLIs so a leased job
actually runs instead of being mock-completed.

Security: launching uses asyncio.create_subprocess_exec with an explicit argv
list (NO shell), so the job prompt is passed as a single argument and cannot
inject commands. Per-role command templates can be overridden via env, e.g.
    MCO_EXEC_CODEX="codex exec {prompt}"
The literal token {prompt} is replaced with the job prompt as one argv element.
"""

import asyncio
import os
import shlex
import shutil
from typing import List, Optional, Tuple

from mco.orchestrator.listener import register_executor

# Hard cap so a hung CLI can't wedge a worker forever.
DEFAULT_TIMEOUT = float(os.environ.get("MCO_EXEC_TIMEOUT", "900"))

# role -> argv template. {prompt} is substituted at run time.
ROLE_COMMANDS: dict[str, List[str]] = {
    "codex": ["codex", "exec", "{prompt}"],
    "claude": ["claude", "-p", "{prompt}"],
    "antigravity": ["gemini", "-p", "{prompt}"],
    "gemini": ["gemini", "-p", "{prompt}"],
}


def resolve_argv(template: List[str], prompt: str) -> Optional[List[str]]:
    """Resolve a command template to a launchable argv, or None if the CLI is missing.

    Substitutes {prompt}, resolves the binary on PATH, and on Windows wraps
    .cmd/.bat shims (npm globals) through the command interpreter so they launch.
    """
    if not template:
        return None
    binary = shutil.which(template[0])
    if binary is None:
        return None
    argv = [binary] + [a.replace("{prompt}", prompt) for a in template[1:]]
    if os.name == "nt" and binary.lower().endswith((".cmd", ".bat")):
        comspec = os.environ.get("COMSPEC", "cmd.exe")
        argv = [comspec, "/c"] + argv
    return argv


async def run_argv(argv: List[str], timeout: float = DEFAULT_TIMEOUT) -> Tuple[Optional[str], Optional[str]]:
    """Launch argv (no shell), capture stdout. (output, None) on success, (None, error) otherwise."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except (FileNotFoundError, OSError) as exc:
        return None, f"Failed to launch {argv[0]!r}: {exc}"

    try:
        out_b, err_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.CancelledError:
        await stop_process_tree(proc)
        raise
    except asyncio.TimeoutError:
        await stop_process_tree(proc)
        return None, f"Execution timed out after {timeout:.0f}s"

    out = out_b.decode(errors="replace").strip()
    err = err_b.decode(errors="replace").strip()
    if proc.returncode == 0:
        return (out or "(no output)"), None
    return None, f"exit {proc.returncode}: {(err or out)[:2000]}"


def make_cli_executor(role: str):
    """Build an executor coroutine for a role from its template (or env override)."""

    async def _executor(job: dict, prompt: str) -> Tuple[Optional[str], Optional[str]]:
        override = os.environ.get(f"MCO_EXEC_{role.upper()}")
        # posix=False keeps Windows backslash paths intact.
        template = shlex.split(override, posix=False) if override else ROLE_COMMANDS.get(role)
        if not template:
            return None, f"No executor command configured for role '{role}'"
        argv = resolve_argv(template, prompt)
        if argv is None:
            return None, f"CLI '{template[0]}' not found on PATH for role '{role}'"
        return await run_argv(argv)

    return _executor


def register_default_executors(roles: Optional[List[str]] = None) -> List[str]:
    """Register CLI executors for the given roles (defaults to all known roles)."""
    roles = roles or list(ROLE_COMMANDS.keys())
    for role in roles:
        register_executor(role, make_cli_executor(role))
    return roles


async def stop_process_tree(proc):
    """Stop only this owned subprocess and its verified descendants."""
    import psutil
    try:
        parent = psutil.Process(proc.pid)
        children = parent.children(recursive=True)
        for child in reversed(children):
            try:
                child.kill()
            except psutil.NoSuchProcess:
                pass
        parent.kill()
    except psutil.NoSuchProcess:
        pass
    await proc.wait()
