"""
BitCadence setup - guided wizard + settings menu.

Two ways in, same steps underneath:

- **Guided** walks through everything in order with plain-English
  explanations and safe defaults. Pressing Enter at every prompt produces a
  working Local-Only install. Built for the least technical user we know.
- **Menu** jumps straight to any one setting (profile, database, token,
  connectors, notifications, security, guardrails) and returns to the menu,
  so nothing ever requires sitting through the whole wizard again.

Every step is a small function `step_*(config) -> None` that reads current
values, explains itself in one or two sentences, prompts with a default, and
persists via `config.set` (sensitive keys are encrypted automatically when
the secret store is unlocked).
"""

from __future__ import annotations

import os
import secrets as _secrets
import subprocess
import sys

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table

from mco.config import EnvironmentProfile, SENSITIVE_KEYS, get_config

console = Console()


# ── helpers ───────────────────────────────────────────────────────────────────

def _save(config, key: str, value: str) -> None:
    """Persist a value; sensitive keys ride the encrypted store when available.

    config.set() now refuses to silently write credentials to plaintext .env.
    In the wizard we are talking to a human, so when the store genuinely can't
    take the value (no OS keychain, no master password yet) we say so out loud
    and store it plainly as a *deliberate*, visible choice - the operator can
    enable encryption in the Security step and re-save.
    """
    try:
        config.set(key, value)
    except RuntimeError as exc:
        console.print(f"[yellow]{exc}[/yellow]")
        console.print(
            f"[yellow]Storing {key} in plain .env for now - "
            "enable encryption in the Security step to protect it.[/yellow]"
        )
        config.set(key, value, encrypt=False)


def _current(config, key: str) -> str:
    val = config.get(key) or ""
    return "" if val == "encrypted_in_secret_store" else str(val)


def _mask(val: str) -> str:
    if not val:
        return "[dim]not set[/dim]"
    if len(val) <= 6:
        return "****"
    return val[:4] + "*" * 8


def _copy_to_clipboard(text: str) -> bool:
    if os.name != "nt":
        return False
    try:
        subprocess.run(["cmd", "/c", "clip"], input=text.encode(), check=True, timeout=10)
        return True
    except Exception:
        return False


def _header(title: str, blurb: str) -> None:
    console.print()
    console.print(f"[bold cyan]{title}[/bold cyan]")
    console.print(f"[dim]{blurb}[/dim]")


# ── steps ─────────────────────────────────────────────────────────────────────

def step_operator(config) -> None:
    _header("Your name", "Shown in the console and stamped on things you approve.")
    name = Prompt.ask("Name", default=_current(config, "OPERATOR_NAME") or "Operator")
    config.set("OPERATOR_NAME", name)
    console.print(f"[green][OK][/green] Hi, {name}.")


def step_profile(config) -> None:
    _header("Where should BitCadence keep its data?",
            "Local-Only is the right answer unless you know you need a cloud database.")
    console.print("  [1] [bold]On this computer[/bold] (Local-Only)  - no accounts, no setup, works now  [green]<- recommended[/green]")
    console.print("  [2] In the cloud (Cloud-Heavy)      - a Supabase database you provide")
    console.print("  [3] Both (Hybrid)                   - local tools plus the cloud database")
    current = _current(config, "MCO_PROFILE")
    default = {"Local-Only": "1", "Cloud-Heavy": "2", "Hybrid": "3"}.get(current, "1")
    choice = Prompt.ask("Choose", choices=["1", "2", "3"], default=default)
    profile = {"1": EnvironmentProfile.LOCAL_ONLY,
               "2": EnvironmentProfile.CLOUD_HEAVY,
               "3": EnvironmentProfile.HYBRID}[choice]
    config.set("MCO_PROFILE", profile)
    console.print(f"[green][OK][/green] Profile: [bold]{profile}[/bold]")
    if profile == EnvironmentProfile.LOCAL_ONLY:
        console.print("[dim]Everything (jobs, audit trail, Drumline memory) saves to ~/.mco/local.db automatically.[/dim]")


def step_database(config) -> None:
    profile = _current(config, "MCO_PROFILE")
    _header("Cloud database (Supabase)",
            "Only needed for the Cloud-Heavy/Hybrid profiles - it lets agents on OTHER computers join your mesh.")
    if profile == EnvironmentProfile.LOCAL_ONLY:
        console.print("[yellow]Your profile is Local-Only, so this is optional - you can skip it.[/yellow]")
        if not Confirm.ask("Configure a cloud database anyway?", default=False):
            return
    url = Prompt.ask("Supabase URL", default=_current(config, "SUPABASE_URL"))
    key = Prompt.ask("Supabase Key (anon/service)", default=_current(config, "SUPABASE_KEY"))
    if url and key:
        _save(config, "SUPABASE_URL", url)
        _save(config, "SUPABASE_KEY", key)
        console.print("[green][OK][/green] Database saved.")
    else:
        console.print("[yellow]Skipped (both URL and key are needed).[/yellow]")


def step_local_token(config) -> None:
    _header("Your access token",
            "This is the password you paste into the console (Settings -> Connection) to leave demo mode.")
    token = _current(config, "MCO_LOCAL_TOKEN")
    if token:
        console.print(f"  Current token: [bold white]{token}[/bold white]")
        if not Confirm.ask("Make a new one? (the old one stops working)", default=False):
            if _copy_to_clipboard(token):
                console.print("[green][OK][/green] Copied to your clipboard - paste it in the console with Ctrl+V.")
            return
    token = "mco_tok_" + _secrets.token_hex(24)
    # Deliberately plaintext: this is the local operator bootstrap token. The
    # wizard prints it, tells the user "it also lives in the .env file", and
    # operator tooling greps it from .env - encrypting it would break that
    # documented contract. It authenticates only against the local gateway.
    config.set("MCO_LOCAL_TOKEN", token, encrypt=False)
    console.print(f"  Your token: [bold white]{token}[/bold white]")
    if _copy_to_clipboard(token):
        console.print("[green][OK][/green] Saved and copied to your clipboard (paste with Ctrl+V).")
    else:
        console.print("[green][OK][/green] Saved (it also lives in the .env file in this folder).")


def step_connectors(config) -> None:
    _header("Enterprise connectors (optional)",
            "Connect ServiceNow and/or Dynatrace so incidents flow in and agents can act back.")
    if Confirm.ask("Connect ServiceNow?", default=bool(_current(config, "SERVICENOW_INSTANCE_URL"))):
        url = Prompt.ask("  ServiceNow instance URL (https://devXXXXXX.service-now.com)",
                         default=_current(config, "SERVICENOW_INSTANCE_URL"))
        user = Prompt.ask("  Username", default=_current(config, "SERVICENOW_USERNAME") or "admin")
        pw = Prompt.ask("  Password", password=True, default="") or _current(config, "SERVICENOW_PASSWORD")
        if url:
            config.set("SERVICENOW_INSTANCE_URL", url)
            config.set("SERVICENOW_USERNAME", user)
            if pw:
                _save(config, "SERVICENOW_PASSWORD", pw)
            _test_connector("servicenow")
    if Confirm.ask("Connect Dynatrace?", default=bool(_current(config, "DYNATRACE_BASE_URL"))):
        url = Prompt.ask("  Dynatrace URL (https://abc12345.live.dynatrace.com)",
                         default=_current(config, "DYNATRACE_BASE_URL"))
        tok = Prompt.ask("  API token (needs problems.read + problems.write)",
                         password=True, default="") or _current(config, "DYNATRACE_API_TOKEN")
        if url:
            config.set("DYNATRACE_BASE_URL", url)
            if tok:
                _save(config, "DYNATRACE_API_TOKEN", tok)
            _test_connector("dynatrace")


def _test_connector(name: str) -> None:
    console.print(f"  [dim]Testing the {name} connection...[/dim]")
    try:
        from mco.connectors import build_connectors, get_connector, reset_connectors
        reset_connectors()
        build_connectors(force=True)
        conn = get_connector(name)
        if not conn:
            console.print(f"  [yellow][SKIP][/yellow] {name} not fully configured yet.")
            return
        health = conn.health()
        if health.get("ok"):
            console.print(f"  [green][OK][/green] Connected: {health.get('detail')}")
        else:
            console.print(f"  [red][FAIL][/red] {health.get('detail')}")
            console.print("  [dim]Check the URL and credentials, then run setup again (menu option 5).[/dim]")
    except Exception as e:
        console.print(f"  [red][FAIL][/red] {e}")


def step_guardrails(config) -> None:
    _header("Safety rails",
            "Gated roles ALWAYS wait for a human click before running - no agent writes to those platforms alone.")
    current = _current(config, "MCO_POLICY_GATED_ROLES")
    suggested = current
    if not suggested:
        configured = [n for n, k in [("servicenow", "SERVICENOW_INSTANCE_URL"),
                                     ("dynatrace", "DYNATRACE_BASE_URL")] if _current(config, k)]
        suggested = ",".join(configured)
    if suggested:
        if Confirm.ask(f"Require human approval for: [bold]{suggested}[/bold]?", default=True):
            config.set("MCO_POLICY_GATED_ROLES", suggested)
            console.print("[green][OK][/green] Those platforms now always wait for your approval.")
    else:
        console.print("[dim]No connectors configured - nothing to gate yet. (Set MCO_POLICY_GATED_ROLES later.)[/dim]")
    approvers = Prompt.ask("Who may approve paused jobs? (roles, comma-separated)",
                           default=_current(config, "MCO_APPROVER_ROLES") or "human,admin,operator")
    config.set("MCO_APPROVER_ROLES", approvers)


def step_notifications(config) -> None:
    _header("Phone notifications (optional)",
            "Get a push alert (free ntfy.sh app) when a job needs your approval, finishes, or fails.")
    if not Confirm.ask("Set up notifications?", default=bool(_current(config, "NTFY_URL"))):
        return
    console.print("[dim]Install the 'ntfy' app, subscribe to a topic name you invent (e.g. joes-agents-x7q2),[/dim]")
    console.print("[dim]then enter that topic URL here.[/dim]")
    url = Prompt.ask("  ntfy topic URL (https://ntfy.sh/your-topic)", default=_current(config, "NTFY_URL"))
    if url:
        config.set("NTFY_URL", url)
        console.print("[green][OK][/green] Notifications on.")


def step_webhook(config) -> None:
    _header("Inbound webhooks (optional)",
            "Lets platforms PUSH incidents to you (and enables the smoke test's simulated detections).")
    current = _current(config, "MCO_WEBHOOK_SECRET")
    if current and not Confirm.ask("A webhook secret exists. Replace it?", default=False):
        return
    if Confirm.ask("Enable webhook ingestion with a generated secret?", default=bool(current)):
        secret = _secrets.token_hex(24)
        _save(config, "MCO_WEBHOOK_SECRET", secret)
        console.print(f"[green][OK][/green] Secret set: [bold white]{secret}[/bold white]")
        console.print("[dim]Senders must put it in the X-MCO-Webhook-Secret header.[/dim]")


def step_encryption(config) -> None:
    _header("Extra security (optional)",
            "Encrypts passwords/tokens with AES-256-GCM instead of keeping them in a plain file.")
    from mco.security import get_secret_store, WindowsCredentialProvider
    store = get_secret_store()
    if store.is_initialized() and store.is_unlocked:
        console.print("[green][OK][/green] Encryption is already on and unlocked.")
        _reencrypt_sensitive(config)
        return
    if store.is_initialized():
        console.print("[yellow]An encrypted store exists but is locked.[/yellow]")
        pw = Prompt.ask("Master password to unlock (or Enter to skip)", password=True, default="")
        if pw:
            import base64, json as _json
            from pathlib import Path
            env = _json.loads(Path(store._path).read_text(encoding="utf-8"))
            key = store.derive_key(pw, base64.b64decode(env["salt"]), env.get("iterations", 600000))
            if store.unlock(key):
                console.print("[green][OK][/green] Unlocked.")
                if os.name == "nt" and Confirm.ask(
                        "Re-save the key to Windows Credential Manager so it unlocks automatically?",
                        default=True):
                    try:
                        WindowsCredentialProvider.store_key(store._master_key)
                        console.print("[green][OK][/green] Auto-unlock repaired.")
                    except Exception as e:
                        console.print(f"[red][ERROR][/red] Credential Manager: {e}")
                _reencrypt_sensitive(config)
            else:
                console.print("[red]Wrong password.[/red]")
        return
    if not Confirm.ask("Turn on encryption?", default=False):
        console.print("[dim]Fine for a home machine - secrets stay in the .env file in this folder.[/dim]")
        return
    use_pw = Confirm.ask("Protect it with a master password? (No = a random key is generated)", default=False)
    if use_pw:
        # A password can always re-derive the key, so the store can never be
        # orphaned on this path. Saving to Credential Manager is convenience.
        pw = Prompt.ask("Choose a master password", password=True)
        salt = os.urandom(32)
        master_key = store.derive_key(pw, salt)
        store.initialize(master_key, salt=salt)
        console.print("[green][OK][/green] Encryption on.")
        if os.name == "nt" and Confirm.ask(
                "Remember the key in Windows Credential Manager so it unlocks automatically?", default=True):
            try:
                WindowsCredentialProvider.store_key(master_key)
                console.print("[green][OK][/green] It will unlock by itself from now on.")
            except Exception as e:
                console.print(f"[red][ERROR][/red] Credential Manager: {e}")
                console.print("[yellow]No problem - your master password still unlocks it.[/yellow]")
    else:
        # A random key the user never sees MUST be persisted before the store
        # exists - otherwise an interrupt right here orphans the store and
        # every later command warns "wrong master key?".
        if os.name != "nt":
            console.print("[yellow]Without Windows Credential Manager, a random key would have no saved copy.[/yellow]")
            console.print("[yellow]Use a master password instead (set MCO_MASTER_PASSWORD for auto-unlock).[/yellow]")
            return
        master_key = _secrets.token_bytes(32)
        try:
            WindowsCredentialProvider.store_key(master_key)
        except Exception as e:
            console.print(f"[red][ERROR][/red] Could not save the key to Windows Credential Manager: {e}")
            console.print("[yellow]Encryption NOT enabled - a random key with no saved copy would lock you out.[/yellow]")
            return
        store.initialize(master_key)
        console.print("[green][OK][/green] Encryption on. It will unlock by itself from now on.")
    _reencrypt_sensitive(config)


def _reencrypt_sensitive(config) -> None:
    """Move any plaintext sensitive values from .env into the unlocked store."""
    moved = []
    for key in sorted(SENSITIVE_KEYS):
        val = config.get(key)
        if val and val != "encrypted_in_secret_store":
            config.set(key, val, encrypt=True)
            moved.append(key)
    if moved:
        console.print(f"[green][OK][/green] Encrypted: {', '.join(moved)}")


def show_summary(config) -> None:
    from mco.security import get_secret_store
    table = Table(title="Your BitCadence setup", show_header=False, border_style="dim")
    table.add_column(style="bold", width=26)
    table.add_column()
    profile = _current(config, "MCO_PROFILE") or "[dim]not set[/dim]"
    table.add_row("Operator", _current(config, "OPERATOR_NAME") or "[dim]not set[/dim]")
    table.add_row("Profile", profile)
    table.add_row("Cloud database", "configured" if _current(config, "SUPABASE_URL") else "[dim]none (local store)[/dim]")
    table.add_row("Access token", _mask(_current(config, "MCO_LOCAL_TOKEN")))
    table.add_row("ServiceNow", _current(config, "SERVICENOW_INSTANCE_URL") or "[dim]not connected[/dim]")
    table.add_row("Dynatrace", _current(config, "DYNATRACE_BASE_URL") or "[dim]not connected[/dim]")
    table.add_row("Gated roles", _current(config, "MCO_POLICY_GATED_ROLES") or "[dim]none[/dim]")
    table.add_row("Notifications", "on" if _current(config, "NTFY_URL") else "[dim]off[/dim]")
    table.add_row("Webhooks", "on" if _current(config, "MCO_WEBHOOK_SECRET") else "[dim]off[/dim]")
    store = get_secret_store()
    table.add_row("Encryption", "on" if store.is_initialized() else "[dim]off (plain .env)[/dim]")
    console.print()
    console.print(table)


def _next_steps() -> None:
    console.print(Panel.fit(
        "[bold green]You're set up![/bold green]\n\n"
        "  1. Double-click the [bold]BitCadence[/bold] icon on your Desktop\n"
        "     (or run: [bold]mco serve[/bold])\n"
        "  2. Your browser opens the console\n"
        "  3. Paste your access token (it's in your clipboard) and click Connect\n\n"
        "[dim]Change anything later with: mco setup[/dim]",
        border_style="green"))


# ── modes ─────────────────────────────────────────────────────────────────────

GUIDED_FLOW = [
    ("Your name", step_operator),
    ("Data location", step_profile),
    ("Cloud database", step_database),
    ("Access token", step_local_token),
    ("Connectors", step_connectors),
    ("Safety rails", step_guardrails),
    ("Notifications", step_notifications),
    ("Webhooks", step_webhook),
    ("Security", step_encryption),
]

MENU = [
    ("Your name", step_operator),
    ("Data location (profile)", step_profile),
    ("Cloud database (Supabase)", step_database),
    ("Access token (view / copy / regenerate)", step_local_token),
    ("Enterprise connectors (ServiceNow, Dynatrace)", step_connectors),
    ("Safety rails (approval gates)", step_guardrails),
    ("Phone notifications (ntfy)", step_notifications),
    ("Inbound webhooks", step_webhook),
    ("Security (encryption)", step_encryption),
]


def run_guided(config) -> None:
    console.print(Panel.fit(
        "[bold cyan]Guided setup[/bold cyan]\n"
        "I'll walk you through everything. Every question has a safe default -\n"
        "[bold]just press Enter[/bold] if you're not sure. Nothing here can break your computer.",
        border_style="cyan"))
    profile_dependent = {step_database}
    total = len(GUIDED_FLOW)
    for i, (label, step) in enumerate(GUIDED_FLOW, 1):
        console.print(f"\n[bold]Step {i} of {total}[/bold] [dim]- {label}[/dim]")
        # Local-Only users never need the database step in guided mode.
        if step in profile_dependent and _current(config, "MCO_PROFILE") == EnvironmentProfile.LOCAL_ONLY:
            console.print("[dim]Skipped - not needed for Local-Only. (It's in the menu if you ever want it.)[/dim]")
            continue
        try:
            step(config)
        except KeyboardInterrupt:
            console.print("\n[yellow]Setup paused - run 'mco setup' to continue any time.[/yellow]")
            return
    show_summary(config)
    _next_steps()


def run_menu(config) -> None:
    while True:
        console.print("\n[bold cyan]Settings menu[/bold cyan]")
        for i, (label, _) in enumerate(MENU, 1):
            console.print(f"  [bold]{i}[/bold]. {label}")
        console.print("  [bold]s[/bold]. Show my current setup")
        console.print("  [bold]g[/bold]. Run the full guided setup instead")
        console.print("  [bold]q[/bold]. Done")
        choice = Prompt.ask("Pick one", choices=[str(i) for i in range(1, len(MENU) + 1)] + ["s", "g", "q"],
                            default="s")
        if choice == "q":
            show_summary(config)
            console.print("[green]Saved. Run 'mco setup' any time to change things.[/green]")
            return
        if choice == "s":
            show_summary(config)
            continue
        if choice == "g":
            run_guided(config)
            return
        try:
            MENU[int(choice) - 1][1](config)
        except KeyboardInterrupt:
            console.print("\n[yellow]Cancelled - back to the menu.[/yellow]")


def run_setup(guided: bool = False, menu: bool = False) -> None:
    """Entry point for `mco setup` (and `mco setup --guided` / `--menu`)."""
    config = get_config()
    console.print(Panel.fit(
        "[bold cyan]BitCadence Setup[/bold cyan]\n"
        "Configure your orchestrator - guided, or straight to one setting.",
        border_style="cyan"))
    if guided:
        run_guided(config)
        return
    if menu:
        run_menu(config)
        return
    first_run = not (_current(config, "MCO_PROFILE"))
    console.print("\n  [bold]1[/bold]. [bold]Guide me through everything[/bold] (recommended, ~2 minutes)")
    console.print("  [bold]2[/bold]. Take me to the settings menu")
    choice = Prompt.ask("Pick one", choices=["1", "2"], default="1" if first_run else "2")
    if choice == "1":
        run_guided(config)
    else:
        run_menu(config)
