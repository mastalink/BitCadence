"""BitCadence Console -- full control-plane GUI served at /console.

A single self-contained HTML file (no build step, no node_modules) shipped as
package data. It talks to the exact same REST API as the minimal /dashboard,
with a richer UI: mission-control overview, job board with audit-trail drawer,
human-in-the-loop approvals inbox, visual workflow builder (drag-to-chain
steps, generates the same DAG as `mco workflow`), and agent fleet presence.

Auth model is identical to /dashboard: the page is public, every API call
carries the bearer token the operator pastes in Settings -> Connection
(kept in browser localStorage).
"""
from pathlib import Path

_STATIC = Path(__file__).parent / "static"
_CONSOLE_PATH = _STATIC / "console.html"
_FLOW_PATH = _STATIC / "flow.html"


def get_console_html() -> str:
    """Read the bundled console page from package data."""
    return _CONSOLE_PATH.read_text(encoding="utf-8")


def get_flow_html() -> str:
    """Read the Flow Control page (served at /flow).

    A single self-contained file - no build step, no node_modules, no CDN - so
    it works on an air-gapped install exactly as it does here. It renders the
    job board's real `depends_on` graph, not a decorative diagram: every edge
    is an actual dependency gate the orchestrator enforces.
    """
    return _FLOW_PATH.read_text(encoding="utf-8")
