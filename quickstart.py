"""
BitCadence Programmatic Quickstart
===================================
A simple demonstration of loading settings, auto-unlocking the AES-256-GCM
secret store, and interacting with configuration profiles programmatically.
"""

import sys
from rich.console import Console
from rich.panel import Panel

from mco.config import get_config
from mco.security import get_secret_store

console = Console()

def run_quickstart():
    console.print(Panel.fit(
        "[bold green]BitCadence Programmatic Quickstart Demonstration[/bold green]\n"
        "Bootstrapping the system settings and verifying credentials container...",
        border_style="green"
    ))

    # 1. Initialize configuration manager
    config = get_config()
    store = get_secret_store()

    # 2. Attempt to unlock secret store automatically
    console.print("[cyan]1. Checking Secret Store state...[/cyan]")
    is_init = store.is_initialized()
    console.print(f"   Secret store initialized: {'[green]Yes[/green]' if is_init else '[yellow]No[/yellow]'}")
    
    if is_init:
        console.print("   Attempting auto-unlock (Windows Credential Manager / Env)...")
        unlocked = store.auto_unlock()
        console.print(f"   Unlocked: {'[green]Success (Memory active)[/green]' if unlocked else '[yellow]Locked[/yellow]'}")
    else:
        console.print("   [dim]Secret store not initialized. Run 'mco setup' to configure encryption.[/dim]")

    # 3. Retrieve and print profile
    profile = config.get("MCO_PROFILE") or "Not configured"
    console.print(f"\n[cyan]2. Profile:[/cyan] [bold yellow]{profile}[/bold yellow]")

    # 4. Display masked settings
    console.print("\n[cyan]3. Active Environment Settings:[/cyan]")
    masked_settings = config.get_masked_config()
    if masked_settings:
        for k, v in masked_settings.items():
            console.print(f"   - {k}: {v}")
    else:
        console.print("   [dim](No settings found. Please run 'mco setup' first.)[/dim]")

    console.print(Panel.fit(
        "[bold green]✓ Quickstart Diagnostics Completed successfully![/bold green]",
        border_style="green"
    ))

if __name__ == "__main__":
    run_quickstart()
