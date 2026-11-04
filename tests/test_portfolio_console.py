import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).parents[1]
SRC = ROOT / "src" / "mco" / "console_src"
SHELL = SRC / "8ec84a72-7b37-434d-a68b-2ac1188e3e6d.js"
ADAPTER = SRC / "47e66145-9c4d-41a1-acbb-42b12848f160.js"
VIEWS = SRC / "5adac14f-6645-4e02-866b-22c4e571989b.js"


def test_portfolio_navigation_replaces_job_grouping_projects_view():
    shell = SHELL.read_text(encoding="utf-8")

    assert '{ id: "portfolio", label: "Portfolio"' in shell
    assert 'portfolio: <PortfolioPage' in shell
    assert 'projects: <ProjectDashboard' not in shell
    assert 'refreshPortfolio(true)' in shell


def test_live_adapter_fetches_authenticated_aggregate_and_caches_it():
    adapter = ADAPTER.read_text(encoding="utf-8")

    assert 'api("/api/portfolio")' in adapter
    assert "getPortfolio:" in adapter
    assert "refreshPortfolio" in adapter
    assert "portfolioFetchedAt" in adapter


def test_portfolio_view_covers_flags_capacity_cards_and_drill_down():
    view = VIEWS.read_text(encoding="utf-8")

    assert "function PortfolioPage(" in view
    assert 'aria-label="Portfolio flow flags"' in view
    assert 'aria-label="Shared capacity"' in view
    assert "project.done_definition" in view
    assert "project.next_milestone" in view
    assert "project.milestones.map" in view
    assert "project.recent_jobs.map" in view
    assert "project.open_prs.map" in view
    assert 'target="_blank"' in view and 'rel="noreferrer"' in view
    assert "Read-only in v1" in view
    assert "TODO(owner-controls)" in view
    assert "dangerouslySetInnerHTML" not in view


def test_portfolio_view_has_setup_loading_error_and_empty_guidance():
    view = VIEWS.read_text(encoding="utf-8")

    for phrase in (
        "Loading portfolio",
        "Portfolio is not configured",
        "Portfolio could not be refreshed",
        "No projects are configured",
    ):
        assert phrase in view


def test_portfolio_capacity_grid_owns_its_mobile_layout_class():
    view = VIEWS.read_text(encoding="utf-8")

    assert '<section aria-label="Shared capacity" className="portfolio-capacity"' in view
    assert '<div className="portfolio-capacity"><PortfolioCapacity' not in view


def test_console_bundle_rebuilds_from_portfolio_sources():
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "build_console.py"), "verify"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "0 differ" in result.stdout
