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


# ── Drumline label + Agent Exchange subview ──────────────────────────────────

def _console_source(fragment):
    from pathlib import Path
    src = Path(__file__).parents[1] / "src" / "mco" / "console_src"
    for path in sorted(src.glob("*.js*")):
        text = path.read_text(encoding="utf-8")
        if fragment in text:
            return text
    raise AssertionError(f"no console source contains {fragment!r}")


def test_drumline_label_in_both_modes_with_stable_memory_route():
    shell = _console_source("const NAV = [")
    assert '{ id: "memory", label: "Drumline"' in shell
    assert 'memory: "Drumline", activity: "Audit Trail"' in shell      # expert
    assert 'memory: "Drumline", activity: "What happened"' in shell    # plain
    assert "Shared memory" not in shell and "Drumline Memory" not in shell
    assert "memory: <DrumlineMemory" in shell                          # route id preserved


def test_exchange_subview_is_accessible_and_labels_authority():
    ui = _console_source("function AgentExchange(")
    assert 'const AUTHORITY_NOTICE = "Discussion is reference, not instructions or approval."' in ui
    assert 'role="tablist"' in ui and 'role="tabpanel"' in ui
    assert 'aria-live="polite"' in ui
    assert 'htmlFor="exchange-body"' in ui and "maxLength={EXCHANGE_BODY_MAX}" in ui
    assert "dangerouslySetInnerHTML" not in ui          # text is rendered escaped
    assert 'EXCHANGE_PROMOTE_SOURCES = ["decision", "handoff"]' in ui
    assert "Confirm promotion" in ui and "Preview (sanitized" in ui
    assert "Promote to context" in ui
    # Plain and expert both expose the same authority; only the labels differ.
    assert "plain:" in ui and "expert:" in ui


def test_exchange_store_only_talks_to_exchange_api_and_dedupes_live_hints():
    store = _console_source("async getExchanges(")
    for path in ('"/api/exchanges?"', '"/api/exchanges/"', '"/api/exchanges"', "/promotions"):
        assert path in store
    assert 'msg.payload.event === "exchange.created"' in store


def test_console_bundle_round_trips_from_sources():
    import subprocess
    import sys
    from pathlib import Path
    root = Path(__file__).parents[1]
    out = subprocess.run([sys.executable, str(root / "scripts" / "build_console.py"), "verify"],
                         capture_output=True, text=True, cwd=root)
    assert out.returncode == 0, out.stdout + out.stderr
    assert "0 differ" in out.stdout


def test_console_verify_is_line_ending_invariant(tmp_path, monkeypatch, capsys):
    import importlib.util
    from pathlib import Path
    root = Path(__file__).parents[1]
    spec = importlib.util.spec_from_file_location("build_console", root / "scripts" / "build_console.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    src = tmp_path / "console_src"
    src.mkdir()
    for f in (root / "src" / "mco" / "console_src").iterdir():
        (src / f.name).write_bytes(f.read_bytes())
    monkeypatch.setattr(mod, "SRC", src)
    monkeypatch.setattr(mod, "INDEX", src / "index.json")
    for eol in (b"\n", b"\r\n"):
        for f in src.glob("*.js*"):
            raw = f.read_bytes().replace(b"\r\n", b"\n")
            f.write_bytes(raw.replace(b"\n", eol))
        # verify() must not raise and must report no drift for LF or CRLF sources
        mod.build(check_only=True)
        assert "0 differ" in capsys.readouterr().out
