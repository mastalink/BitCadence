"""Isolated test harness CLI wrapper.
Explicitly constructs and passes LiveScoreAdapterExecutor into ScoreBridge
for testing repository:write scores without modifying production CLI or score_sweep.
"""
import sys
from mco import cli
from mco.localstore import get_local_store
from mco.orchestrator.score_adapters_live import LiveScoreAdapterExecutor
from mco.orchestrator.score_authority import GrantService, configured_grant_key
from mco.orchestrator.score_conductor import Conductor, open_bridge

_orig_conductor = cli._conductor

def _isolated_conductor(database, root):
    conductor, client = _orig_conductor(database, root)
    store = get_local_store()
    key = configured_grant_key()
    grant_svc = GrantService(store, verification_key=key) if key else None
    live_exec = LiveScoreAdapterExecutor(db=store, grant_service=grant_svc) if (store and key) else None
    bridge = open_bridge(database, root, live_executor=live_exec)
    return Conductor(bridge, conductor.board), client

cli._conductor = _isolated_conductor

if __name__ == "__main__":
    cli.app()
