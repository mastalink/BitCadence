"""Phone setup/forms browser checks. Only a disposable gateway; provider APIs mocked.

Rebuilds the console into memory and serves it with a browser route, never writes
the tracked bundle. Helpers/connect-ai/health/plan fixtures are deliberate mocks.
All unapproved browser mutations and non-fixture network requests are blocked.
"""
import json
import os
import re
from pathlib import Path
import subprocess
import tempfile

from playwright.sync_api import sync_playwright
import build_console as bundle
import capture_wiki as fixture

ROOT = Path(__file__).resolve().parents[1]
LONG = "phone-fixture-" + "identifier" * 10


def derived_html():
    html = bundle.HTML.read_text(encoding="utf-8")
    manifest = bundle._read_manifest(html)
    for name, meta in json.loads(bundle.INDEX.read_text(encoding="utf-8")).items():
        manifest[meta["uuid"]]["data"] = bundle._encode(bundle._source_bytes(bundle.SRC / name), meta["compressed"])
    return bundle._write_manifest(html, manifest)


def main():
    out = ROOT / "output/playwright/setup-phone"
    out.mkdir(parents=True, exist_ok=True)
    home = Path(tempfile.mkdtemp(prefix="bitcadence-setup-phone-"))
    port = fixture.free_port()
    base = f"http://127.0.0.1:{port}"
    proc = fixture.start_gateway(home, port)
    checks, errors, blocked = [], [], []
    try:
        fixture.seed(base)
        html = derived_html()
        with sync_playwright() as p:
            browser = p.chromium.launch(channel="msedge" if os.name == "nt" else None)
            ctx = browser.new_context(viewport={"width": 320, "height": 900})

            def route(r):
                req = r.request
                path = req.url.removeprefix(base)
                payload = None
                if not req.url.startswith(base + "/"):
                    blocked.append(req.url)
                    return r.abort()
                if path == "/console":
                    return r.fulfill(status=200, content_type="text/html", body=html)
                if path == "/api/helpers":
                    payload = {"helpers": [{"id": "fixture", "name": LONG, "doing": "Review the community grant application and prepare the final illustrated report", "word": "Ready", "light": "green", "last_heard": "Just now", "can_fix": False}], "roles": [{"role": "codex", "label": "Codex"}]}
                elif path == "/api/connect-ai":
                    payload = {"apps": [{"app": "fixture", "name": LONG, "hint": "An example application connected to the gateway", "found": True, "connected": True}]}
                elif path == "/api/connect-ai/other":
                    payload = {"snippet": '{"url":"https://' + LONG + '.example.test/long-path"}'}
                elif path == "/api/integrations":
                    payload = [{"name": "servicenow", "health": {"ok": False, "detail": "Example service unreachable at https://" + LONG + ".example.test"}}]
                elif path == "/api/settings/test-connector":
                    payload = {"ok": False, "detail": "Fixture only: connection unavailable"}
                elif path == "/api/ask/plan":
                    body = req.post_data_json
                    steps = [{"id": "one", "label": "Research the community grant application and prepare a readable illustrated report", "icon": "1", "gate": False}, {"id": "two", "label": LONG, "icon": "2", "gate": True}]
                    payload = {"plan": {"steps": [s for s in steps if s["id"] not in body.get("remove", [])], "repeat": {"words": body["repeat"]} if body.get("repeat") else None}}
                if payload is not None:
                    return r.fulfill(status=200, json=payload)
                if req.method not in ("GET", "HEAD", "OPTIONS") and path != "/api/schedules/preview":
                    blocked.append(req.method + " " + path)
                    return r.abort()
                r.continue_()

            ctx.route("**/*", route)
            ctx.add_init_script('localStorage.setItem("bitcadence_page","settings");')
            pg = ctx.new_page()
            pg.on("pageerror", lambda e: errors.append(str(e)))

            def audit(label, width, controls=True):
                metrics = pg.locator("main").evaluate('''el => {
                    const bounds=el.getBoundingClientRect();
                    const over=[...el.querySelectorAll('input,select,textarea,button,.hp-card,.ask-card')].filter(e=>e.getClientRects().length).map(e=>({e,r:e.getBoundingClientRect()})).filter(({r})=>r.right>innerWidth+1||r.left<bounds.left-1).map(({e,r})=>({tag:e.tagName,text:e.textContent.slice(0,80),left:r.left,width:r.width}));
                    const small=[...el.querySelectorAll('input,select,textarea,button,summary')].filter(e=>e.getClientRects().length).filter(e=>{const r=e.getBoundingClientRect();return r.height<44||r.width<44}).map(e=>({tag:e.tagName,text:e.textContent.slice(0,80),width:e.getBoundingClientRect().width,height:e.getBoundingClientRect().height}));
                    const cramped=[...el.querySelectorAll('input,textarea')].filter(e=>e.getClientRects().length).filter(e=>e.getBoundingClientRect().width<180).map(e=>({id:e.id,width:e.getBoundingClientRect().width}));
                    const tinyText=[...el.querySelectorAll('input,select,textarea')].filter(e=>e.getClientRects().length).filter(e=>parseFloat(getComputedStyle(e).fontSize)<16).map(e=>({id:e.id,font:getComputedStyle(e).fontSize}));
                    const fragmented=[];const walker=document.createTreeWalker(el,NodeFilter.SHOW_TEXT);let node;
                    while(node=walker.nextNode()){
                      if(!node.parentElement.getClientRects().length||['STYLE','SCRIPT','OPTION'].includes(node.parentElement.tagName))continue;
                      for(const match of node.textContent.matchAll(/\\b[A-Za-z]{3,12}\\b/g)){
                        const range=document.createRange();range.setStart(node,match.index);range.setEnd(node,match.index+match[0].length);
                        const lines=new Set([...range.getClientRects()].filter(r=>r.width>.1).map(r=>Math.round(r.top)));
                        if(lines.size>1)fragmented.push(match[0]);
                      }
                    }
                    return {scroll:document.documentElement.scrollWidth,over,small,fragmented,cramped,tinyText};
                }''')
                pg.screenshot(path=str(out / f"{label}-{width}.png"), full_page=True)
                checks.append({"screen": label, "width": width, **metrics})

            def nav(name):
                pg.get_by_role("button", name="Menu", exact=True).click()
                pg.get_by_role("button", name=name, exact=True).first.click()

            for width in [320, 390, 430]:
                pg.set_viewport_size({"width": width, "height": 900})
                pg.goto(base + "/console")
                pg.get_by_role("heading", name="Settings", exact=True).last.wait_for()
                audit("disconnected-settings", width)
                for name, label in [("Helpers", "helpers"), ("Connect an AI", "connect-ai"), ("Schedules", "schedules")]:
                    nav(name)
                    pg.get_by_text("Connect to your BitCadence in Settings first", exact=False).wait_for()
                    audit("disconnected-" + label, width)
                nav("Settings")
            # Connect only to the temporary gateway; no saved real configuration.
            pg.locator('main input[type=password]').fill(fixture.TOKEN)
            pg.get_by_role("button", name="Connect", exact=True).last.click()
            pg.wait_for_function("window.BitCadenceStore.mode()==='live'")
            for dismiss in pg.get_by_role("button", name=re.compile("^Dismiss ")).all():
                dismiss.click()

            for width in [320, 390, 430]:
                pg.set_viewport_size({"width": width, "height": 900})
                nav("Settings")
                pg.get_by_role("button", name="Stop work…", exact=True).click()
                audit("stop-work-confirmation", width)
                pg.get_by_role("button", name="Cancel", exact=True).click()
                assert pg.get_by_role("button", name="Stop work now", exact=True).count() == 0
                pg.locator("main summary").filter(has_text="Server administration").click()
                pg.locator("#connector-SERVICENOW_INSTANCE_URL").wait_for()
                audit("connected-settings", width)
                pg.get_by_role("button", name="Technical details", exact=True).last.click()
                pg.get_by_text("Tenancy", exact=True).wait_for()
                audit("technical-settings", width)
                pg.get_by_role("button", name="Technical details", exact=True).last.click()
                pg.get_by_role("button", name="Test connection", exact=True).first.click()
                pg.get_by_text("Fixture only: connection unavailable", exact=False).wait_for()
                pg.locator("#connector-SERVICENOW_INSTANCE_URL").fill("https://" + LONG + ".example.test")
                assert pg.get_by_text("Save changes before testing.", exact=False).is_visible()
                pg.reload()
                pg.wait_for_function("window.BitCadenceStore.mode()==='live'")
                nav("Helpers")
                pg.get_by_text(LONG, exact=True).wait_for()
                audit("helpers", width)
                pg.get_by_role("button", name="+ Add a helper", exact=True).click()
                pg.locator("#hp-name").fill(LONG)
                audit("helper-add", width)
                pg.get_by_role("button", name="Cancel", exact=True).click()
                assert pg.locator("#hp-name").count() == 0
                nav("Connect an AI")
                pg.get_by_text(LONG, exact=True).wait_for()
                pg.get_by_role("button", name="Show me a code", exact=True).click()
                pg.locator("pre").wait_for()
                audit("connect-ai", width)
                nav("Schedules")
                pg.locator("#sc-what").wait_for()
                audit("schedules", width)
                nav("Ask for something")
                pg.locator("#ask-request").fill("Research a community report then ask me to publish")
                audit("ask-request", width)
                pg.get_by_role("button", name="Draft a plan", exact=True).click()
                pg.get_by_role("heading", name="Here's the plan", exact=True).wait_for()
                pg.get_by_role("button", name="Remove a step", exact=True).click()
                pg.get_by_role("button", name="Make it repeat", exact=True).click()
                audit("ask-plan", width)
                pg.get_by_role("button", name="Every weekday at 9 AM", exact=True).click()
                pg.get_by_role("button", name="Change my request", exact=True).click()
                assert pg.locator("#ask-request").input_value().startswith("Research")
            browser.close()
        result = {"checks": checks, "page_errors": errors, "blocked_requests": blocked,
                  "mocks": ["helpers", "connect-ai", "integrations health", "connector test", "ask plans"], "base": base}
        (out / "results.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        failures = [x for x in checks if x["scroll"] > x["width"] + 1 or any(x[k] for k in ["over", "small", "fragmented", "cramped", "tinyText"])]
        print(json.dumps({"checks": len(checks), "failures": failures, "errors": errors, "blocked": blocked, "output": str(out)}, indent=2))
        assert not failures and not errors and not blocked
    finally:
        if proc.poll() is None:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
            else:
                proc.terminate()
                proc.wait(timeout=10)


if __name__ == "__main__":
    main()

