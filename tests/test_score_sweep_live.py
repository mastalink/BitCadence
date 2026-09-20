"""Unattended repository adapter opt-in must match the manual conductor."""
import pytest

from mco.orchestrator import routes, score_sweep
from mco.orchestrator.score_adapters_live import LiveScoreAdapterExecutor
from mco.localstore import LocalStore


def config(tmp_path, **overrides):
    return {"MCO_LOCAL_TOKEN": "test-only-token",
            "MCO_SCORE_DB": str(tmp_path / "runs.db"),
            "MCO_SCORE_ARTIFACT_ROOT": str(tmp_path / "artifacts"), **overrides}


@pytest.mark.parametrize("setting", [None, "false", "", False])
def test_sweep_defaults_to_no_live_executor(tmp_path, monkeypatch, setting):
    def unexpected_store():
        raise AssertionError("Disabled adapter must not open authority store")
    monkeypatch.setattr(routes, "get_db_client", unexpected_store)
    cfg = config(tmp_path)
    if setting is not None:
        cfg["MCO_SCORE_LIVE_REPOSITORY_WRITE"] = setting
    conductor = score_sweep.open_conductor(cfg)
    assert conductor.bridge.live_executor is None


def test_sweep_opt_in_installs_real_grant_checked_executor(tmp_path, monkeypatch):
    from mco.orchestrator import score_authority
    monkeypatch.setattr(score_authority, "get_config", lambda: {
        "MCO_SCORE_GRANT_KEY": "test-only-grant-verification-key-not-for-production",
    })
    store = LocalStore(tmp_path / "board.db")
    monkeypatch.setattr(routes, "get_db_client", lambda: store)
    conductor = score_sweep.open_conductor(config(tmp_path, MCO_SCORE_LIVE_REPOSITORY_WRITE="true"))
    executor = conductor.bridge.live_executor
    assert isinstance(executor, LiveScoreAdapterExecutor)
    # No grant is issued as a side effect of enabling the runtime adapter.
    assert store.table("score_grants").select("*").execute().data == []
    assert executor.grants is not None


@pytest.mark.parametrize("setting", ["yes", "1", "typo", 1, None])
def test_invalid_opt_in_fails_before_creating_run_store(tmp_path, setting):
    with pytest.raises(ValueError, match="must be true or false"):
        score_sweep.open_conductor(config(tmp_path, MCO_SCORE_LIVE_REPOSITORY_WRITE=setting))
    assert not (tmp_path / "runs.db").exists()


def test_missing_authority_store_refuses_live_runtime(tmp_path, monkeypatch):
    monkeypatch.setattr(routes, "get_db_client", lambda: None)
    with pytest.raises(RuntimeError, match="authority store"):
        score_sweep.open_conductor(config(tmp_path, MCO_SCORE_LIVE_REPOSITORY_WRITE="true"))
    assert not (tmp_path / "runs.db").exists()
