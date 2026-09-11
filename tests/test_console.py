"""Tests for the BitCadence Console route and loader."""

from fastapi.testclient import TestClient

from mco.cli import create_app
from mco.console import get_console_html


def test_gateway_client_falls_back_to_local_token(monkeypatch):
    """Local-Only zero-config: the operator CLI authenticates with
    MCO_LOCAL_TOKEN when no explicit MCO_AGENT_TOKEN is set. Without this,
    send/approve/workflow/sync/audit 401 on a fresh local install."""
    import mco.cli as cli
    monkeypatch.setattr(cli, "get_config",
                        lambda: {"MCO_LOCAL_TOKEN": "local-xyz"}, raising=True)
    assert cli._gateway_client().token == "local-xyz"


def test_gateway_client_prefers_explicit_agent_token(monkeypatch):
    """An explicit MCO_AGENT_TOKEN always wins over the local-token fallback."""
    import mco.cli as cli
    monkeypatch.setattr(cli, "get_config",
                        lambda: {"MCO_AGENT_TOKEN": "agent-abc",
                                 "MCO_LOCAL_TOKEN": "local-xyz"}, raising=True)
    assert cli._gateway_client().token == "agent-abc"


def test_get_console_html_reads_package_data():
    html = get_console_html()
    assert "<!DOCTYPE html>" in html or "<!doctype html>" in html.lower()
    assert "BitCadence" in html


def test_console_route_serves_page():
    http = TestClient(create_app())
    resp = http.get("/console")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    assert "BitCadence" in resp.text


def test_console_route_requires_no_auth_like_dashboard():
    """The page itself is public; every API call it makes carries the bearer
    token the operator pastes (same model as /dashboard)."""
    http = TestClient(create_app())
    assert http.get("/console").status_code == 200
    assert http.get("/dashboard").status_code == 200


# ── Flow Control page (/flow) ─────────────────────────────────────────────────

def test_flow_html_is_served_and_self_contained():
    from mco.console import get_flow_html
    html = get_flow_html()
    assert "<!DOCTYPE html>" in html
    assert "Flow Control" in html
    # Self-contained: an air-gapped install must render this identically, so no
    # CDN scripts, stylesheets, or remote fonts.
    for offender in ("http://cdn", "https://cdn", "unpkg.com", "jsdelivr", "googleapis.com"):
        assert offender not in html, f"flow.html must not reference {offender}"


def test_flow_html_reads_the_real_dependency_graph():
    from mco.console import get_flow_html
    html = get_flow_html()
    # Edges are `depends_on`, i.e. gates the orchestrator actually enforces -
    # not a decorative diagram drawn beside the data.
    assert "depends_on" in html
    assert "/api/jobs" in html


def test_flow_html_exposes_governance_actions():
    from mco.console import get_flow_html
    html = get_flow_html()
    for verb in ("approve", "reject", "retry", "cancel"):
        assert f"/{verb}" in html or f'"{verb}"' in html


def test_flow_design_mode_authors_the_runtime_workflow_schema():
    from mco.console import get_flow_html
    html = get_flow_html()
    for field in (
        "id", "role", "title", "instructions", "depends_on",
        "requires_approval", "max_retries", "escalate_to_role",
    ):
        assert field in html
    assert 'draggable="true"' in html
    assert "port out" in html
    assert "port in" in html
    assert "parseWorkflowYaml" in html
    assert "workflowToYaml" in html


def test_flow_design_mode_validates_ids_dependencies_and_cycles_before_export():
    from mco.console import get_flow_html
    html = get_flow_html()
    validation = html[html.index("function validateDraft"):html.index("function yamlScalar")]
    assert "Duplicate step id" in validation
    assert "depends on unknown step" in validation
    assert "dependency cycle" in validation
    assert "depends_on must be a YAML list" in html
    assert "Workflow is missing 'steps'" in html
    export_handler = html[html.index('$("design-export").onclick'):html.index('$("design-import").onclick')]
    assert "showValidation" in export_handler


def test_flow_yaml_export_is_distinct_from_confirmed_execution():
    from mco.console import get_flow_html
    html = get_flow_html()
    exporter = html[html.index("function workflowToYaml"):html.index("function stripYamlComment")]
    assert "/api/workflows" not in exporter
    runner = html[html.index("async function submitDraftWorkflow"):html.index('$("design-stage").addEventListener')]
    assert 'confirm(' in runner
    assert 'api("/api/workflows"' in runner
    assert "This creates" in runner
    assert "real" in runner
    assert "Exporting YAML does not" in runner


def test_flow_yaml_import_never_submits_jobs():
    from mco.console import get_flow_html
    html = get_flow_html()
    importer = html[html.index('$("yaml-apply").onclick'):html.index('$("yaml-copy").onclick')]
    assert "parseWorkflowYaml" in importer
    assert "/api/workflows" not in importer
    assert "No jobs were created" in importer


def test_flow_route_is_registered():
    from mco.cli import create_app
    routes = {getattr(r, "path", None) for r in create_app().routes}
    assert "/flow" in routes
    assert "/console" in routes
