"""Source-checkout desktop launcher, suitable for a Windows shortcut."""
import os
from pathlib import Path
import sys

root = Path(__file__).resolve().parents[1]
paths = [str(root / "src"), str(root / ".codex" / "desktopdeps")]
sys.path[:0] = paths
os.environ["PYTHONPATH"] = os.pathsep.join(paths + [os.environ.get("PYTHONPATH", "")])
os.chdir(root)

from mco.desktop.app import main
main()
