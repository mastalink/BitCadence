"""Re-capture the wiki screenshots from the current console (/console).

Starts a throwaway gateway on a spare port, with HOME pointed at a temp
directory (so ~/.mco, the live board and real tokens are never touched), seeds
DEMO data through the REST API, and drives the console with Playwright.
Output goes to docs/wiki/img/. Only the fixed demo token is ever shown.
"""
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
IMG = ROOT / "docs/wiki/img"
TOKEN = "mco_tok_demo0000"


# The helpers page scans this machine's processes. Demo captures must never show
# (or wait on) the real ones, so the throwaway gateway sees an empty process list.
BOOT = (
    "import sys; import mco.helpers as h; h.list_processes = lambda: []; "
    "sys.argv = ['mco', 'serve', '--port', sys.argv[1]]; from mco.cli import app; app()"
)


SCHEDULES = """launchers:
  nightly-audit:
    role: reviewer
    title: Nightly dependency audit
  weekly-summary:
    role: researcher
    title: Weekly summary of helper activity
schedules:
"""


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_gateway(home, port):
    env = {k: v for k, v in os.environ.items() if not k.startswith(("MCO_", "SUPABASE", "NTFY"))}
    env.update(USERPROFILE=str(home), HOME=str(home), HOMEDRIVE="", MCO_LOCAL_TOKEN=TOKEN,
               MCO_ENV_FILE=str(home / "demo.env"), PYTHONPATH=str(ROOT / "src"),
               MCO_LOCAL_HUMAN_GRANTS="false")
    (home / "demo.env").write_text("", encoding="utf-8")
    (home / ".mco").mkdir(exist_ok=True)
    (home / ".mco" / "schedules.yaml").write_text(SCHEDULES, encoding="utf-8")
    proc = subprocess.Popen([sys.executable, "-c", BOOT, str(port)],
                            cwd=home, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(60):
        try:
            if httpx.get(f"http://127.0.0.1:{port}/healthz", timeout=1).status_code == 200:
                return proc
        except httpx.HTTPError:
            time.sleep(0.5)
    proc.kill()
    raise SystemExit("gateway did not start")


def seed(base):
    """Fill the throwaway board with DEMO data only (fake names, fake work)."""
    from mco.orchestrator.client import GatewayClient

    admin = httpx.Client(base_url=base, headers={"Authorization": f"Bearer {TOKEN}"}, timeout=30)
    leases = Path(tempfile.mkdtemp(prefix="wikicap-leases-"))
    workers = {}
    for name, role in [("claude-desktop", "claude"), ("codex-1", "codex"),
                       ("gemini-1", "gemini"), ("antigravity-1", "antigravity")]:
        reg = admin.post("/api/agents", json={"instance_id": name, "role": role, "org": "default",
                                              "scopes": ["jobs:read", "jobs:write", "context:write", "context:read"]})
        reg.raise_for_status()
        workers[role] = GatewayClient(base, reg.json()["token"], role, name, lease_store_dir=leases)

    def job(title, role, desc="", approval=False, priority=0):
        r = admin.post("/api/jobs", json={"title": title, "description": desc, "target_agent_role": role,
                                          "requires_approval": approval, "priority": priority})
        r.raise_for_status()
        return r.json().get("job", {}).get("id")

    done = [("Draft the release notes", "claude"), ("Index new docs into memory", "claude"),
            ("Check the nightly backup", "codex")]
    for title, role in done:
        job(title, role, "Demo job.", priority=9)
        c = workers[role].lease_next()
        tid = c["job"]["id"]
        workers[role].complete(tid, "Done. Nothing needed your attention.")
    job("Backfill audit events to cold storage", "codex", "Demo job.", priority=9)
    t = workers["codex"].lease_next()["job"]["id"]
    workers["codex"].fail(t, "The storage target was full.")
    job("Review pull request 42", "claude", "Demo job.", priority=8)
    workers["claude"].lease_next()
    job("Summarize this week's helper activity", "gemini", "Demo job.")
    job("Compare two vendor quotes", "antigravity", "Demo job.")
    job("Deploy the hotfix to production", "deploy", "Demo job. Needs a person to approve.", approval=True)
    job("Rotate the service keys", "deploy", "Demo job. Needs a person to approve.", approval=True)
    for title, content, tags in [
        ("Release day is Thursday", "The team ships on Thursday afternoons.", ["release"]),
        ("Backups run at 2 AM", "The nightly backup starts at 2 AM and takes about ten minutes.", ["ops"]),
    ]:
        workers["claude"].post_context(title, content, tags) if hasattr(workers["claude"], "post_context") else             admin.post("/api/context", json={"title": title, "content": content, "tags": tags}).raise_for_status()
    for what, freq, when in [("nightly-audit", "day", "02:00"),
                             ("weekly-summary", "days", "09:00")]:
        admin.post("/api/schedules", json={"what": what, "frequency": freq, "time": when,
                                           "days": [1] if freq == "days" else [],
                                           "timezone": "America/New_York"}).raise_for_status()


def main():
    home = Path(tempfile.mkdtemp(prefix="wikicap-"))
    port = free_port()
    proc = start_gateway(home, port)
    try:
        from playwright.sync_api import sync_playwright
        base = f"http://127.0.0.1:{port}"
        seed(base)
        if os.environ.get('CAP_DEBUG'):
            h = {'Authorization': f'Bearer {TOKEN}'}
            for u in ('/api/schedules', '/api/settings', '/api/connect-ai', '/api/helpers', '/api/schedules', '/api/connect-ai', '/api/settings'):
                r = httpx.get(base + u, headers=h, timeout=20)
                print(u, r.status_code, r.text[:600], flush=True)
        with sync_playwright() as p:
            b = p.chromium.launch()
            pg = b.new_page(viewport={"width": 1280, "height": 800})
            out = Path(sys.argv[1]) if len(sys.argv) > 1 else IMG
            out.mkdir(parents=True, exist_ok=True)
            pg.goto(base + "/console")
            time.sleep(1)
            pg.click("text=Advanced")
            pg.get_by_role("button", name="Settings").first.click()
            time.sleep(1)
            pg.evaluate("document.querySelectorAll('details').forEach(d=>d.open=true)")
            pg.fill("input[type=password]", TOKEN)
            pg.click("main button:has-text('Connect')")
            time.sleep(2)

            def shot(name, nav, wait=2.0, full=False):
                pg.get_by_role("button", name=nav, exact=False).first.click()
                time.sleep(wait)
                pg.screenshot(path=str(out / name), full_page=full)

            for name, nav in [("01-console-overview.png", "Home"), ("03-console-projects.png", "Projects"),
                              ("05-console-approvals.png", "Approvals"),
                              ("06-console-governance.png", "Governance"),
                              ("08-console-helpers.png", "Helpers"), ("22-console-connect-ai.png", "Connect an AI"),
                              ("09-console-drumline-memory.png", "Drumline"),
                              ("10-console-activity-audit.png", "Activity")]:
                shot(name, nav, wait=3)
            shot("23-console-schedules.png", "Schedules", wait=3, full=True)

            # Job board, the new-job composer and the job drawer
            shot("04-console-job-board.png", "Job Board", wait=3)
            pg.get_by_role("button", name="New job", exact=False).first.click()
            time.sleep(1.5)
            pg.screenshot(path=str(out / "14-job-create-modal.png"))
            pg.get_by_role("button", name="Cancel").click()
            time.sleep(1)
            pg.get_by_text("Review pull request 42").first.click()
            time.sleep(2)
            pg.screenshot(path=str(out / "15-job-detail-drawer.png"))
            pg.mouse.click(120, 780)  # click outside the drawer to close it
            time.sleep(1)

            # Ask: a drafted plan (nothing is started)
            shot("21-console-ask.png", "Ask for something", wait=2)
            pg.fill("textarea", "Research open pull requests, run the tests, then ask me to publish")
            pg.get_by_role("button", name="Draft a plan").click()
            time.sleep(4)
            pg.screenshot(path=str(out / "21-console-ask.png"), full_page=True)

            # Helpers: the add-a-helper panel (opened, never submitted)
            pg.get_by_role("button", name="Helpers").first.click()
            time.sleep(2)
            pg.get_by_role("button", name="Add a helper").click()
            time.sleep(1.5)
            pg.screenshot(path=str(out / "17-helper-add-panel.png"))
            pg.keyboard.press("Escape")

            # Drumline: the agent exchange tab
            pg.get_by_role("button", name="Drumline").first.click()
            time.sleep(2)
            pg.get_by_text("Agent Exchange").first.click()
            time.sleep(2)
            pg.screenshot(path=str(out / "18-agent-exchange.png"))

            # Settings with the advanced section open; the token field stays masked
            pg.get_by_role("button", name="Settings").first.click()
            time.sleep(3)
            pg.evaluate("document.querySelectorAll('details').forEach(d=>d.open=true)")
            time.sleep(1)
            pg.screenshot(path=str(out / "02-console-settings.png"), full_page=True)

            # The minimal dashboard
            pg2 = b.new_page(viewport={"width": 1280, "height": 800})
            pg2.goto(base + "/dashboard")
            pg2.fill("input", TOKEN)
            pg2.get_by_role("button", name="Unlock").click()
            time.sleep(3)
            pg2.screenshot(path=str(out / "13-minimal-dashboard.png"))
            b.close()
    finally:
        proc.kill()


if __name__ == "__main__":
    main()
