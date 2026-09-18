import hashlib
import json
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from mco import cli
from mco.orchestrator.score_bridge import GatewayBoard, ScoreBridge


class MockClient:
    def __init__(self, token="test-token", instance_id="conductor-1"):
        self.token = token
        self.instance_id = instance_id
        self._jobs = {}

    def _client(self):
        class MockHttp:
            def __enter__(self_inner):
                return self_inner
            def __exit__(self_inner, *args):
                pass
            def request(self_inner, method, path, **kwargs):
                class MockResp:
                    def raise_for_status(self_resp):
                        pass
                    def json(self_resp):
                        if path == "/api/jobs/capabilities":
                            return {"create_with_id": 1}
                        if path == "/api/jobs" and method == "POST":
                            payload = kwargs.get("json", {})
                            job = dict(payload, source_agent_id="conductor-1", org_id="default", status="pending")
                            MockClient._jobs_store[payload["id"]] = job
                            return {"job": job}
                        if path.startswith("/api/jobs/") and method == "GET":
                            job_id = path.split("/api/jobs/")[1].split("/")[0]
                            job = MockClient._jobs_store.get(job_id, {"id": job_id, "status": "pending"})
                            if path.endswith("/events"):
                                return [{"job_id": job_id, "event": "status:completed", "actor_id": job.get("target_agent_id", "worker-1"), "actor_role": job.get("target_agent_role", "worker")}]
                            return {"job": job}
                        return {}
                return MockResp()
        return MockHttp()


MockClient._jobs_store = {}


def make_repo_write_score(worktree_path):
    task = dict(
        id="write_task",
        goal="G01",
        title="Write Code",
        instructions="Modify repo",
        role="worker",
        review_role="reviewer",
        depends_on=[],
        resources=[str(worktree_path)],
        capabilities=["repository:write"],
        evidence=["commit_sha"],
        max_attempts=1,
        timeout_seconds=300,
        max_cost_cents=0,
        checkpoint=None,
        commit={
            "worktree_path": str(worktree_path),
            "target_branch": "feat/test-branch",
            "allowed_paths": ["src/*"],
            "expected_before_sha": "0" * 40,
        },
    )
    return dict(
        score_version=1,
        id="repo-write-score",
        revision=1,
        objective="Write Code",
        constraints=["None"],
        budget_cents=0,
        max_parallel=1,
        tasks=[task],
        launch_requires=["write_task"],
    )


@pytest.fixture(autouse=True)
def mock_gateway_client(monkeypatch):
    MockClient._jobs_store.clear()
    client = MockClient()
    monkeypatch.setattr(cli, "_gateway_client", lambda: client)


def test_score_start_without_flag_fails_dark_by_default(tmp_path):
    score_data = make_repo_write_score(tmp_path / "wt")
    score_file = tmp_path / "score.json"
    score_file.write_text(json.dumps(score_data), encoding="utf-8")

    db_path = tmp_path / "score.db"
    root_path = tmp_path / "artifacts"

    runner = CliRunner()
    result = runner.invoke(cli.app, [
        "score", "start", str(score_file),
        "--run-id", "run-dark-start",
        "--target", "worker=worker-1",
        "--target", "reviewer=reviewer-1",
        "--db", str(db_path),
        "--artifact-root", str(root_path),
    ])

    assert result.exit_code == 1
    assert "Could not start the run" in result.output
    assert "Only read-only audit authority supported" in result.output


def test_score_tick_without_flag_fails_dark_by_default(tmp_path):
    wt_dir = tmp_path / "wt"
    wt_dir.mkdir()
    score_data = make_repo_write_score(wt_dir)
    db_path = tmp_path / "score.db"
    root_path = tmp_path / "artifacts"

    cred_hash = hashlib.sha256(b"test-token").hexdigest()
    fake_executor = MagicMock()
    fake_executor.grants = None
    init_bridge = ScoreBridge(db_path, root_path, live_executor=fake_executor)
    init_bridge.initialize(
        "run-dark-tick", score_data, principal="conductor-1", org="default",
        targets={"worker": "worker-1", "reviewer": "reviewer-1"},
        credential_hash=cred_hash,
    )

    board = GatewayBoard(MockClient())
    planned = init_bridge.plan("run-dark-tick")
    assert len(planned) == 1
    work_id = planned[0]
    init_bridge.dispatch("run-dark-tick", board)

    # Worker completes and signals ready
    MockClient._jobs_store[work_id].update({
        "status": "completed",
        "leased_by_instance_id": "worker-1",
        "output_payload": {"result": json.dumps({"ready": True, "commit_sha": "abc1234"})},
    })

    # Now call `mco score tick` WITHOUT `--live-repository-write`
    runner = CliRunner()
    result = runner.invoke(cli.app, [
        "score", "tick",
        "--run-id", "run-dark-tick",
        "--db", str(db_path),
        "--artifact-root", str(root_path),
    ])

    assert result.exit_code == 1
    assert "Live executor required for repository:write task" in " ".join(result.output.split())


def test_conductor_default_has_no_live_executor(tmp_path):
    conductor, _ = cli._conductor(tmp_path / "score.db", tmp_path / "artifacts")
    assert conductor.bridge.live_executor is None


def test_conductor_wires_live_executor_when_flag_is_true(tmp_path, monkeypatch):
    mock_db = MagicMock()
    mock_grant_svc_cls = MagicMock()
    mock_grant_svc_inst = MagicMock()
    mock_grant_svc_cls.return_value = mock_grant_svc_inst

    mock_live_exec_cls = MagicMock()
    mock_live_exec_inst = MagicMock()
    mock_live_exec_cls.return_value = mock_live_exec_inst

    import mco.orchestrator.routes as routes
    import mco.orchestrator.score_authority as score_authority
    import mco.orchestrator.score_adapters_live as score_adapters_live

    monkeypatch.setattr(routes, "get_db_client", lambda: mock_db)
    monkeypatch.setattr(score_authority, "GrantService", mock_grant_svc_cls)
    monkeypatch.setattr(score_adapters_live, "LiveScoreAdapterExecutor", mock_live_exec_cls)

    conductor, _ = cli._conductor(tmp_path / "score.db", tmp_path / "artifacts", live_repository_write=True)

    mock_grant_svc_cls.assert_called_once_with(mock_db)
    mock_live_exec_cls.assert_called_once_with(db=mock_db, grant_service=mock_grant_svc_inst)
    assert conductor.bridge.live_executor is mock_live_exec_inst


def test_score_start_with_flag_wires_live_executor(tmp_path, monkeypatch):
    mock_db = MagicMock()
    mock_grant_svc = MagicMock()
    mock_live_exec = MagicMock()

    import mco.orchestrator.routes as routes
    import mco.orchestrator.score_authority as score_authority
    import mco.orchestrator.score_adapters_live as score_adapters_live

    monkeypatch.setattr(routes, "get_db_client", lambda: mock_db)
    monkeypatch.setattr(score_authority, "GrantService", lambda db: mock_grant_svc)
    monkeypatch.setattr(score_adapters_live, "LiveScoreAdapterExecutor", lambda db, grant_service: mock_live_exec)

    score_data = make_repo_write_score(tmp_path / "wt")
    score_file = tmp_path / "score.json"
    score_file.write_text(json.dumps(score_data), encoding="utf-8")

    db_path = tmp_path / "score.db"
    root_path = tmp_path / "artifacts"

    runner = CliRunner()
    result = runner.invoke(cli.app, [
        "score", "start", str(score_file),
        "--run-id", "run-live-start",
        "--target", "worker=worker-1",
        "--target", "reviewer=reviewer-1",
        "--db", str(db_path),
        "--artifact-root", str(root_path),
        "--live-repository-write",
    ])

    assert result.exit_code == 0
    assert "ready for score" in result.output


def test_score_tick_with_flag_invokes_live_executor(tmp_path, monkeypatch):
    mock_db = MagicMock()
    mock_grant_svc = MagicMock()
    mock_live_exec = MagicMock()
    mock_live_exec.grants = None
    mock_live_exec.execute.return_value = {
        "status": "succeeded",
        "observed_state": {"commit_sha": "abc1234"},
    }

    import mco.orchestrator.routes as routes
    import mco.orchestrator.score_authority as score_authority
    import mco.orchestrator.score_adapters_live as score_adapters_live

    monkeypatch.setattr(routes, "get_db_client", lambda: mock_db)
    monkeypatch.setattr(score_authority, "GrantService", lambda db: mock_grant_svc)
    monkeypatch.setattr(score_adapters_live, "LiveScoreAdapterExecutor", lambda db, grant_service: mock_live_exec)

    wt_dir = tmp_path / "wt"
    wt_dir.mkdir()
    score_data = make_repo_write_score(wt_dir)
    db_path = tmp_path / "score.db"
    root_path = tmp_path / "artifacts"

    cred_hash = hashlib.sha256(b"test-token").hexdigest()
    # Initialize the run
    init_bridge = ScoreBridge(db_path, root_path, live_executor=mock_live_exec)
    init_bridge.initialize(
        "run-live-tick", score_data, principal="conductor-1", org="default",
        targets={"worker": "worker-1", "reviewer": "reviewer-1"},
        credential_hash=cred_hash,
    )
    board = GatewayBoard(MockClient())
    planned = init_bridge.plan("run-live-tick")
    work_id = planned[0]
    init_bridge.dispatch("run-live-tick", board)

    MockClient._jobs_store[work_id].update({
        "status": "completed",
        "leased_by_instance_id": "worker-1",
        "output_payload": {"result": json.dumps({"ready": True, "commit_sha": "abc1234"})},
    })

    runner = CliRunner()
    result = runner.invoke(cli.app, [
        "score", "tick",
        "--run-id", "run-live-tick",
        "--db", str(db_path),
        "--artifact-root", str(root_path),
        "--live-repository-write",
    ])

    assert result.exit_code == 0
    assert "error=" not in result.output
    mock_live_exec.execute.assert_called_once()
