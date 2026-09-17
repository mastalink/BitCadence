"""Start the isolated gateway for repository:write S06b adversarial proof."""
import logging
import sys
import time

logging.basicConfig(
    level=logging.INFO,
    stream=sys.stdout,
    format="%(asctime)s.%(msecs)03dZ %(name)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logging.Formatter.converter = time.gmtime

# Explicitly construct and thread through live_executor for isolated test gateway
from mco.orchestrator import score_sweep
from mco.orchestrator.score_adapters_live import LiveScoreAdapterExecutor
from mco.orchestrator.score_authority import GrantService, configured_grant_key
from mco.orchestrator.routes import get_db_client
from mco.orchestrator.score_conductor import Conductor, open_bridge

_orig_open_conductor = score_sweep.open_conductor

def _isolated_open_conductor(config=None):
    c = _orig_open_conductor(config)
    client = get_db_client()
    key = configured_grant_key()
    grant_svc = GrantService(client, verification_key=key) if (client and key) else None
    live_exec = LiveScoreAdapterExecutor(db=client, grant_service=grant_svc) if (client and key) else None
    bridge = open_bridge(
        score_sweep.get_database(config),
        score_sweep.get_artifact_root(config),
        live_executor=live_exec,
    )
    return Conductor(bridge, c.board)

score_sweep.open_conductor = _isolated_open_conductor

from mco.cli import app

sys.argv = ["mco", "serve", "--host", "127.0.0.1", "--port", "18997"]
app()
