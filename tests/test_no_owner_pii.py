"""Guard: the repository is public, so owner identity must never be committed.

Fails on any tracked text file containing the owner identity patterns. The
generated console bundle is excluded (its payload is base64; the readable
sources in src/mco/console_src are scanned instead). The financial-institution
keyword list in score_intake.py and the ranker test are content filters, not
identity, and are exempt for the phrase "federal reserve" only.
"""
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

IDENTITY = re.compile(
    r"Joseph|\bJoe\b|Arroyo|joe\.arroyo|ADHD|Cassie|Cassandra|\bElias\b|"
    r"Avon Lake|\bFRB\b|Chicago Fed|Fed job",
    re.IGNORECASE,
)
FED_RESERVE = re.compile(r"Federal Reserve", re.IGNORECASE)
FED_RESERVE_KEYWORD_FILES = {
    "src/mco/orchestrator/score_intake.py",
    "tests/test_job_ranker.py",
}
SELF = "tests/test_no_owner_pii.py"
SKIP = {SELF, "src/mco/static/console.html"}


def _tracked_files():
    out = subprocess.run(
        ["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True
    ).stdout.decode("utf-8", "replace")
    return [p for p in out.split("\0") if p]


def test_no_owner_identity_in_tracked_files():
    hits = []
    for rel in _tracked_files():
        if rel in SKIP:
            continue
        path = ROOT / rel
        try:
            data = path.read_bytes()
        except OSError:
            continue
        if b"\0" in data:
            continue
        for n, line in enumerate(data.decode("utf-8", "replace").splitlines(), 1):
            if IDENTITY.search(line):
                hits.append(f"{rel}:{n}")
            elif FED_RESERVE.search(line) and rel not in FED_RESERVE_KEYWORD_FILES:
                hits.append(f"{rel}:{n}")
    assert not hits, "owner PII found (locations only): " + ", ".join(hits[:50])
