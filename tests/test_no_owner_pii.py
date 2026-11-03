"""Guard: the repository is public, so owner identity must never be committed.

Fails on any tracked text file containing an owner identity term. The terms are
stored only as SHA-256 hashes so this file does not itself publish them. Text is
split into lowercase words and each word, and each adjacent word pair, is hashed
and compared. The generated console bundle is excluded (its payload is base64;
the readable sources in src/mco/console_src are scanned instead). The
financial-institution keyword list in score_intake.py and the ranker test are
content filters, not identity, and are exempt for the one two-word phrase kept
in FED_PAIR only.
"""
import hashlib
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

WORD = re.compile(r"[a-z0-9]+")
ONE = {'7db8465900fdbd3c7022368c1ea4ff91d037d559ebd898b6f25358d63a0f7e55', 'a3441069e422a14127a80e5f3323cffb5972097b39353462337c951006879bb9', 'de7ee76e7373c19bf7418e69cadecba96107bbb6d99524c516e1d37605dca829', '7ee8118150e0ce023742beba6f10bf23aabbf0bc2c182f36fd1a6753cd21b4c6', '78675cc176081372c43abab3ea9fb70c74381eb02dc6e93fb6d44d161da6eeb3', 'f1c6127b16ff422bfa09b98334abe44c5f88327e2a2185a2fe00ce26cdcf0075', 'f3f93a385e23b9b1cf3dc433529a797bdaa668843309813f5269fef54208620f', 'd789bee2093991e797fda9fbf1da3c2ca2df6d528716c24c7a4cf6431365e76c'}
TWO = {'b08972729dfe864ee81f1849e9865fea8aaba053f38a9c1001c9149781a81738', 'a002470277fb916092903400d7003337f4fa1c39a82b5891fd753643697ab934', '0a87834dc5a999179c5e6fdba8c0e947d8f7e05ea1c40bfbb929c2f90b4c95ff', '857c94689f6ba59b88d7dd333432586ded4ee7bfb353788c6e17c72538773508'}
FED_PAIR = '857c94689f6ba59b88d7dd333432586ded4ee7bfb353788c6e17c72538773508'
FED_KEYWORD_FILES = {
    "src/mco/orchestrator/score_intake.py",
    "tests/test_job_ranker.py",
}
SELF = "tests/test_no_owner_pii.py"
SKIP = {SELF, "src/mco/static/console.html"}


def _h(s):
    return hashlib.sha256(s.encode()).hexdigest()


def _line_hits(line):
    words = WORD.findall(line.lower())
    if any(_h(w) in ONE for w in words):
        return "identity"
    for a, b in zip(words, words[1:]):
        digest = _h(a + " " + b)
        if digest == FED_PAIR:
            return "fed"
        if digest in TWO:
            return "identity"
    return None


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
        try:
            data = (ROOT / rel).read_bytes()
        except OSError:
            continue
        if b"\0" in data:
            continue
        for n, line in enumerate(data.decode("utf-8", "replace").splitlines(), 1):
            kind = _line_hits(line)
            if kind == "identity" or (kind == "fed" and rel not in FED_KEYWORD_FILES):
                hits.append(f"{rel}:{n}")
    assert not hits, "owner PII found (locations only): " + ", ".join(hits[:50])
