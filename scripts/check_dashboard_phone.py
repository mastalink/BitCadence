"""Read-only real-browser dashboard phone audit against a disposable gateway.

Run with the project venv and PYTHONPATH=src. Never contacts the live gateway;
all browser mutations and off-origin requests are blocked. Outputs screenshots
and measured assertions; exits nonzero on a usability regression.
"""
import json
import os
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlparse

import httpx
from playwright.sync_api import sync_playwright
import capture_wiki
from capture_wiki import TOKEN, free_port, seed, start_gateway


def main():
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "output/playwright/app-phone-dashboard")
    out.mkdir(parents=True, exist_ok=True)
    home = Path(tempfile.mkdtemp(prefix="dashboard-phone-"))
    port = free_port()
    # This dense read-only audit exceeds the normal operator request budget.
    capture_wiki.BOOT = "import os; os.environ['MCO_RATE_LIMIT']='0'; " + capture_wiki.BOOT
    proc = start_gateway(home, port)
    base = f"http://127.0.0.1:{port}"
    results, failures, mutations, errors = [], [], [], []

    def check(case, actual, passed):
        results.append(dict(case=case, actual=actual, passed=bool(passed)))
        if not passed:
            failures.append(case)

    try:
        seed(base)
        # Synthetic credential record only; no /test or real provider request.
        admin = httpx.Client(base_url=base, headers={"Authorization": f"Bearer {TOKEN}"})
        response = admin.post("/api/llm-connections", json={"name": "demo-phone-connection", "provider": "openai", "model": "demo-model-for-readability", "api_key": "demo-not-a-real-key"})
        response.raise_for_status()
        with sync_playwright() as p:
            browser = p.chromium.launch(**({"channel": "msedge"} if os.name == "nt" else {}))
            context = browser.new_context(is_mobile=True, has_touch=True)

            def guard(route):
                req = route.request
                if not req.url.startswith(base + "/") or req.method not in ("GET", "HEAD", "OPTIONS"):
                    mutations.append({"method": req.method, "path": urlparse(req.url).path})
                    route.abort()
                elif os.environ.get("DASHBOARD_PHONE_HTML") and urlparse(req.url).path == "/dashboard":
                    route.fulfill(status=200, content_type="text/html", body=Path(os.environ["DASHBOARD_PHONE_HTML"]).read_text(encoding="utf-8"))
                else:
                    route.continue_()
            context.route("**/*", guard)
            page = context.new_page()
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.on("dialog", lambda d: d.dismiss())
            for width in (320, 390, 430, 1280):
                page.close()
                page = context.new_page()
                page.on("pageerror", lambda e: errors.append(str(e)))
                page.on("dialog", lambda d: d.dismiss())
                page.add_init_script("localStorage.clear()")
                page.set_viewport_size({"width": width, "height": 800})
                page.goto(base + "/dashboard")
                page.evaluate("localStorage.removeItem('mco_token')")
                page.reload()
                page.locator("#lock-token").wait_for(state="visible")
                lock = page.locator("#lock .card").bounding_box()
                check(f"{width} lock fits", lock, lock["x"] >= 0 and lock["x"] + lock["width"] <= width)
                check(f"{width} viewport", page.evaluate("innerWidth"), page.evaluate("innerWidth") == width)
                page.screenshot(path=str(out / f"{width}-lock.png"))
                page.fill("#lock-token", TOKEN)
                page.get_by_role("button", name="Unlock", exact=True).click()
                page.locator("#shell.on").wait_for()
                for view in ("ops", "agents", "models", "workflows", "settings"):
                    nav = page.locator(f"#nav-{view}")
                    nav.scroll_into_view_if_needed()
                    nav.click()
                    page.locator(f"#view-{view}.on").wait_for()
                    if view == "agents":
                        page.locator("#agents button").first.wait_for()
                    if view == "models":
                        page.locator("#models button").first.wait_for()
                    if view == "settings":
                        page.locator("#settings-groups .card").first.wait_for()
                    metrics = page.evaluate("""() => {
                      const main=document.querySelector('main').getBoundingClientRect();
                      const visible = el => !!el.getClientRects().length;
                      const controls=[...document.querySelectorAll('main button, aside button, main input:not([type=checkbox]), main table input[type=checkbox], main select, main textarea')].filter(visible);
                      return {width:innerWidth,document:document.documentElement.scrollWidth, mainWidth:main.width,
                        smallTargets:controls.filter(el=>el.getBoundingClientRect().height<43.5).map(el=>el.textContent||el.id),
                        smallInputs:controls.filter(el=>el.matches('input:not([type=checkbox]),select,textarea')&&parseFloat(getComputedStyle(el).fontSize)<16).map(el=>el.id||el.dataset.key),
                        tables:[...document.querySelectorAll('.view.on table')].map(el=>({width:el.getBoundingClientRect().width,region:el.parentElement.getAttribute('role'),label:el.parentElement.getAttribute('aria-label'),focus:el.parentElement.tabIndex}))};
                    }""")
                    phone = width < 760
                    check(f"{width} {view} page fits", metrics, metrics["document"] <= width + 1 and (not phone or metrics["mainWidth"] >= width - 2))
                    if phone:
                        check(f"{width} {view} touch targets", metrics["smallTargets"], not metrics["smallTargets"])
                        check(f"{width} {view} readable inputs", metrics["smallInputs"], not metrics["smallInputs"])
                        check(f"{width} {view} readable accessible tables", metrics["tables"], all(t["width"] >= 480 and t["region"] == "region" and t["label"] and t["focus"] == 0 for t in metrics["tables"]))
                    page.screenshot(path=str(out / f"{width}-{view}.png"), full_page=True)
                # Open forms via real controls, cancel, and exercise a short viewport.
                for height in (800, 480):
                    page.set_viewport_size({"width": width, "height": height})
                    for view, opener in (("agents", "+ Register agent"), ("agents", "Edit agent"), ("models", "+ Add connection"), ("models", "Edit connection"), ("ops", "Live Look")):
                        page.locator(f"#nav-{view}").click()
                        if opener.startswith("Edit "):
                            page.locator("#" + ("agents" if view == "agents" else "models")).get_by_role("button", name="Edit", exact=True).first.click()
                        else:
                            page.get_by_role("button", name=opener, exact=True).click()
                        page.locator("#modal-bg.on").wait_for()
                        modal = page.locator("#modal")
                        box = modal.bounding_box()
                        bounds = modal.evaluate("el=>({client:el.clientWidth,scroll:el.scrollWidth})")
                        check(f"{width}x{height} {opener} modal fits", {"box":box, **bounds}, box["x"] >= 0 and box["x"]+box["width"] <= width+1 and box["y"] >= 0 and box["y"]+box["height"] <= height+1 and bounds["scroll"] <= bounds["client"]+1)
                        inputs = modal.locator("input:not([type=checkbox]):visible,select:visible,textarea:visible")
                        if phone:
                            check(f"{width}x{height} {opener} readable fields", inputs.evaluate_all("els=>els.map(el=>({width:el.getBoundingClientRect().width,font:parseFloat(getComputedStyle(el).fontSize)}))"), all(v["width"]>=180 and v["font"]>=16 for v in inputs.evaluate_all("els=>els.map(el=>({width:el.getBoundingClientRect().width,font:parseFloat(getComputedStyle(el).fontSize)}))")))
                        close = modal.get_by_role("button", name="Close" if view == "ops" else "Cancel", exact=True)
                        close.scroll_into_view_if_needed()
                        cb = close.bounding_box()
                        check(f"{width}x{height} {opener} cancel reachable", cb, cb["x"] >= 0 and cb["x"]+cb["width"]<=width+1 and cb["y"]>=0 and cb["y"]+cb["height"]<=height+1)
                        page.screenshot(path=str(out / f"{width}x{height}-{view}-{opener.replace(' ', '-').replace('+', 'add')}-modal.png"))
                        close.click()
                        check(f"{width}x{height} {opener} cancel closes", page.locator("#modal-bg").get_attribute("class"), not page.locator("#modal-bg.on").count())
                # Browser-native confirmations are dismissed, never approved.
                for view, table, action in (("agents", "agents", "Reset token"), ("agents", "agents", "Delete"), ("models", "models", "Delete")):
                    page.locator(f"#nav-{view}").click()
                    page.locator("#" + table).get_by_role("button", name=action, exact=True).first.click()
                # Each table can be scrolled by keyboard to reveal its action columns.
                if phone:
                    page.locator("#nav-ops").click()
                    region = page.get_by_role("region", name="Job board", exact=True)
                    if region.count():
                        region.focus()
                        page.keyboard.press("ArrowRight")
                        page.wait_for_timeout(200)
                        check(f"{width} keyboard table scroll", region.evaluate("el=>el.scrollLeft"), region.evaluate("el=>el.scrollLeft") > 0)
            check("no browser mutations or external calls", mutations, not mutations)
            check("no browser JavaScript errors", errors, not errors)
            browser.close()
    finally:
        proc.kill()
        proc.wait()
        (out / "results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(json.dumps({"checks":len(results), "failures":failures,"artifacts":str(out.resolve())}, indent=2))
    return bool(failures)


if __name__ == "__main__":
    raise SystemExit(main())



