"""Tests for the /dashboard Control Panel route and static integrity of its
embedded page (dashboard.py is a hand-authored single HTML string with no
build step, so a JS typo would otherwise only surface by clicking around in a
browser). These are structural checks, not a browser test."""

import re

from fastapi.testclient import TestClient

from mco.cli import create_app
from mco.dashboard import DASHBOARD_HTML


def test_dashboard_route_serves_html():
    http = TestClient(create_app())
    resp = http.get("/dashboard")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    assert "BitCadence" in resp.text


def test_dashboard_html_matches_the_python_string():
    http = TestClient(create_app())
    assert http.get("/dashboard").text == DASHBOARD_HTML


class TestStaticIntegrity:
    """Every onclick="fn(...)" in the markup must resolve to a function
    actually defined in <script>. Catches a typo'd handler name that would
    otherwise silently no-op in the browser."""

    def test_every_onclick_handler_is_defined(self):
        html = DASHBOARD_HTML
        script = html[html.index("<script>"):html.index("</script>")]
        defined = set(re.findall(r"(?:async\s+)?function\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(", script))
        called = set(re.findall(r'onclick="([A-Za-z_][A-Za-z0-9_]*)\(', html))
        called |= set(re.findall(r'onchange="([A-Za-z_][A-Za-z0-9_]*)\(', html))
        called |= set(re.findall(r'onkeydown="if\(event\.key===.Enter.\)([A-Za-z_][A-Za-z0-9_]*)\(', html))
        called -= {"if"}  # e.g. onclick="if(event.target===this)closeModal()" - a JS keyword, not a handler
        missing = called - defined
        assert not missing, f"onclick/onchange handlers with no matching function: {sorted(missing)}"

    def test_every_view_has_a_matching_nav_button(self):
        views = set(re.findall(r'id="view-([a-z]+)"', DASHBOARD_HTML))
        navs = set(re.findall(r'id="nav-([a-z]+)"', DASHBOARD_HTML))
        assert views == navs

    def test_nav_function_lists_every_view(self):
        html = DASHBOARD_HTML
        m = re.search(r'for \(const v of \[([^\]]+)\]\)', html)
        assert m, "nav() view list not found"
        listed = set(re.findall(r'"([a-z]+)"', m.group(1)))
        views = set(re.findall(r'id="view-([a-z]+)"', html))
        assert listed == views

    def test_esc_used_on_every_json_stringify_interpolation(self):
        """JSON.stringify(...) embedded in an HTML attribute must be run
        through esc() (it can contain unescaped quotes) - this is exactly the
        stored-XSS shape fixed earlier (dashboard.py esc()/openEdit)."""
        raw = re.findall(r'onclick=[\'"][a-zA-Z_]+\(([^)]*JSON\.stringify[^)]*)\)', DASHBOARD_HTML)
        for args in raw:
            assert "esc(JSON.stringify" in args, f"un-escaped JSON.stringify in onclick args: {args}"
