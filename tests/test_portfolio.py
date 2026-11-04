"""Portfolio page: roadmap math, flow flags, adapters degrade gracefully, routes are wired."""
import json
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

from mco import portfolio
from mco.orchestrator import portfolio_routes
from mco.orchestrator.auth import require_scopes

NOW = datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc)


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def test_roadmap_progress():
    p = portfolio.roadmap_progress([{"title": "a", "done": True}, {"title": "b"}, {"title": "c"}])
    assert (p["done"], p["total"], p["percent"], p["next_milestone"]) == (1, 3, 33, "b")
    assert portfolio.roadmap_progress([])["percent"] is None


def test_missing_config_is_not_an_error(tmp_path):
    cfg = portfolio.load_config(tmp_path / "nope.json")
    snap = portfolio.build_portfolio(cfg, [], [], gpu={"flags": []}, saints={"flags": []}, now=NOW)
    assert snap["config_missing"] and snap["projects"] == []


def test_example_config_loads_and_has_no_pii():
    text = (portfolio.ROOT / "configs" / "portfolio.example.json").read_text(encoding="utf-8")
    cfg = json.loads(text)
    assert cfg["projects"]
    assert "@" not in text.replace("owner/repo", "")  # no emails; names stay out of the template


def test_flags_and_health():
    cfg = {"projects": [
        {"id": "a", "name": "A", "priority": 2, "done_definition": "x", "job_keywords": ["alpha"],
         "roadmap": [{"title": "m1", "done": True}, {"title": "m2"}]},
        {"id": "b", "name": "B", "priority": 1},
        {"id": "c", "name": "C", "status": "paused", "priority": 5},
    ]}
    jobs = [{"id": "1", "title": "alpha build", "status": "failed", "created_at": "2026-11-02"}]
    agents = [{"instance_id": "w1", "effective_status": "offline"}, {"instance_id": "w2", "effective_status": "online"}]
    snap = portfolio.build_portfolio(cfg, jobs, agents, gpu={"flags": []}, saints={"flags": []},
                                     prs=lambda r: [], now=NOW)
    by = {p["id"]: p for p in snap["projects"]}
    assert [p["id"] for p in snap["projects"]] == ["b", "a", "c"]  # active by priority, paused last
    assert by["a"]["health"] == "red" and by["a"]["progress"]["percent"] == 50
    assert by["b"]["health"] == "amber"  # no roadmap, no done definition
    assert by["c"]["health"] == "paused"
    codes = {f["code"] for f in snap["flags"]}
    assert {"jobs_failing", "no_roadmap", "worker_offline"} <= codes
    assert snap["flags"][0]["severity"] == "red"
    assert snap["board"]["workers_online"] == 1
    assert snap["controls"]["enabled"] is False


def test_saints_adapter(tmp_path):
    def ep(name, stage, pub, hold=None):
        p = tmp_path / f"{name}.json"
        p.write_text(json.dumps({"id": name, "stage": stage, "publish_at": iso(pub), "hold_reason": hold}))
        return str(p)

    paths = [ep("late", "art", NOW - timedelta(hours=2)), ep("soon", "motion", NOW + timedelta(days=1)),
             ep("far", "script", NOW + timedelta(days=30)), ep("ok", "scheduled", NOW + timedelta(days=1)),
             ep("held", "assemble", NOW + timedelta(days=20), hold="bad upload")]
    reg = tmp_path / "registry.json"
    reg.write_text(json.dumps({"episodes": paths + [str(tmp_path / "gone.json")]}))
    s = portfolio.read_saints({"registry_path": str(reg)}, now=NOW)
    assert s["episodes"] == 5 and s["by_stage"]["scheduled"] == 1
    codes = sorted(f["code"] for f in s["flags"])
    assert codes == ["episode_due_soon", "episode_held", "episode_overdue"]
    assert portfolio.read_saints({"registry_path": str(tmp_path / "x.json")})["available"] is False


def test_gpu_adapter_degrades_and_flags_stale_lock(tmp_path):
    lock = tmp_path / "gpu_lock.json"
    lock.write_text(json.dumps({"holders": [{"holder": "builder", "lane": "beast",
                                             "expires_at": iso(NOW - timedelta(hours=1))}]}))

    def dead(url):
        raise OSError("down")

    g = portfolio.read_gpu({"gpu_lock_path": str(lock), "comfy_urls": ["http://127.0.0.1:1"]},
                           http=dead, now=NOW)
    assert g["comfy"][0]["running"] is None and g["available"]
    assert g["flags"][0]["code"] == "gpu_lock_stale"
    g2 = portfolio.read_gpu({"comfy_urls": ["http://x"]},
                            http=lambda u: {"queue_running": [1], "queue_pending": [1, 2]}, now=NOW)
    assert g2["comfy"][0] == {"url": "http://x", "running": 1, "pending": 2}


def test_routes(tmp_path, monkeypatch):
    cfgp = tmp_path / "p.json"
    cfgp.write_text(json.dumps({"projects": [{"id": "x", "name": "X"}]}))
    monkeypatch.setenv("BITCADENCE_PORTFOLIO_CONFIG", str(cfgp))
    monkeypatch.setattr(portfolio_routes, "_jobs", lambda: [])
    import mco.orchestrator.routes as r
    monkeypatch.setattr(r, "get_agents", lambda caller: [], raising=False)
    app = FastAPI()
    app.include_router(portfolio_routes.portfolio_router)
    app.include_router(portfolio_routes.page_router)
    app.dependency_overrides[require_scopes("jobs:read")] = lambda: {}
    c = TestClient(app)
    html = c.get("/portfolio")
    assert html.status_code == 200 and "Portfolio" in html.text
    # Auth is enforced: no credentials, no data.
    assert c.get("/api/portfolio/flags").status_code in (401, 403)
