"""Start the gateway with stdlib logging visible (score_sweep writes to stdlib)."""
import logging, sys
logging.basicConfig(level=logging.INFO, stream=sys.stdout,
                    format="%(asctime)s.%(msecs)03dZ %(name)s %(levelname)s %(message)s",
                    datefmt="%Y-%m-%dT%H:%M:%S")
logging.Formatter.converter = __import__("time").gmtime
from mco.cli import app
sys.argv = ["mco", "serve", "--host", "127.0.0.1", "--port", "18997"]
app()
