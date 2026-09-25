"""Client brief -> reviewed scope -> scaffolded Score, then a delivery bundle.

Two human gates: the owner reads the draft scope (and clears any flags) before
``approve_scope`` will scaffold a Score, and ``build_delivery`` only drafts the
client message; nothing here sends anything.
"""

from __future__ import annotations

import json
import re
import subprocess
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol

from mco.orchestrator.score_scaffold import DEFAULT_ALLOWED_PATHS, scaffold_score
from mco.orchestrator.scores import compile_score, load_score

NEEDS_SCOPING = "needs human scoping"

CLIENT_CONSTRAINTS = [
    "Never read, request, store or print secrets or credentials; use placeholders like .env.example.",
    "Push nothing anywhere except the client branch named in this Score.",
    "Delete all client data (briefs, samples, fixtures containing client data) after delivery.",
]

# (flag id, description, pattern). Every flag is blocking until the owner removes it.
GUARDRAILS = [
    ("third_party_credentials", "asks for credentials of third parties",
     r"\b(password|passwords|credentials?|api[- ]?keys?|login details|session cookies?)\b.{0,40}\b(of|for|from|belonging to)\b"
     r"|\b(steal|harvest|phish|guess)\b.{0,30}\b(passwords?|credentials?|tokens?)\b"),
    ("scraping_behind_login", "scraping behind logins or bot challenges",
     r"\b(scrap\w*|crawl\w*|harvest\w*)\b.{0,80}\b(behind (a |the )?(login|paywall|auth\w*)|log(ged)?[- ]?in|captcha|cloudflare|bot (challenge|protection|detection))\b"
     r"|\b(bypass|evade|defeat|solve)\b.{0,40}\b(captcha|bot (challenge|protection|detection)|cloudflare)\b"),
    ("spam_or_automated_messaging", "spam or automated mass messaging",
     r"\b(spam|mass[- ]?(email|dm|message|text)\w*|bulk (email|dm|sms|messag\w*)|auto[- ]?(dm|message)\w*|cold[- ]?blast)\b"),
    ("academic_dishonesty", "academic dishonesty",
     r"\b(write|do|complete|take)\b.{0,30}\b(my|his|her|their)\b.{0,20}\b(homework|essay|thesis|dissertation|exam|quiz|assignment)\b"
     r"|\b(cheat|plagiari[sz]\w*|proctor\w* (bypass|evasion))\b"),
    ("financial_institution_client", "work for a bank or financial institution (owner conflict rule)",
     r"\b(bank|banks|credit union|brokerage|federal reserve|lender|mortgage company|financial institution)\b"),
]


class Drafter(Protocol):
    def draft(self, brief: str, client: str) -> Dict[str, Any]: ...


def _sentences(brief: str) -> List[str]:
    parts = re.split(r"(?<=[.!?])\s+|\n+", brief)
    cleaned = [re.sub(r"^\s*(?:[-*]|\d+[.)])\s*", "", p).strip() for p in parts]
    return [p for p in cleaned if len(p) > 3]


class OfflineDrafter:
    """Deterministic template drafter: one step per brief sentence, plus a delivery step."""

    def draft(self, brief: str, client: str) -> Dict[str, Any]:
        items = _sentences(brief)[:8] or [NEEDS_SCOPING]
        steps = [{"id": f"S{i}", "title": item[:60].rstrip(".") or NEEDS_SCOPING, "brief": item}
                 for i, item in enumerate(items, 1)]
        steps.append({"id": f"S{len(steps) + 1}", "title": "Package the delivery",
                      "brief": "Write the README with setup, usage and known limits, and make sure the test command passes."})
        return {
            "summary": items[0][:240],
            "deliverables": [s["title"] for s in steps],
            "acceptance_tests": [f"A test exists and passes that demonstrates: {i}" for i in items]
            + ["The full test command exits 0 on a clean checkout."],
            "out_of_scope": [f"Anything not named in the brief ({NEEDS_SCOPING})."],
            "risks": [f"Requirements may be ambiguous: {NEEDS_SCOPING}."],
            "assumptions": [f"Hosting, data sources and access are unspecified: {NEEDS_SCOPING}."],
            "steps": steps,
            "test_command": "pytest -q",
            "allowed_paths": list(DEFAULT_ALLOWED_PATHS),
            "estimate_hours": float(len(steps) * 2),
            "suggested_price_usd": float(len(steps) * 2 * 100),
        }


def check_guardrails(brief: str) -> List[Dict[str, Any]]:
    flags = []
    for flag_id, description, pattern in GUARDRAILS:
        match = re.search(pattern, brief, flags=re.I | re.S)
        if match:
            flags.append({"id": flag_id, "description": description, "blocking": True,
                          "evidence": match.group(0)[:120]})
    return flags


def draft_scope(brief: str, *, client: str = "", drafter: Optional[Drafter] = None) -> Dict[str, Any]:
    if not brief.strip():
        raise ValueError("the brief is empty")
    body = (drafter or OfflineDrafter()).draft(brief, client)
    scope = {"client": client or NEEDS_SCOPING, "status": "draft", "approved_at": None, **body,
             "constraints": list(CLIENT_CONSTRAINTS),
             "guardrail_checks": [g[0] for g in GUARDRAILS],
             "flags": check_guardrails(brief)}
    for key in ("summary", "deliverables", "acceptance_tests", "out_of_scope", "risks", "assumptions",
                "steps", "test_command", "allowed_paths", "estimate_hours", "suggested_price_usd"):
        if key not in scope:
            raise ValueError(f"drafter did not return {key}")
    return scope


def approve_scope(scope: Dict[str, Any], *, worktree: str, branch: str, before_sha: str) -> Dict[str, Any]:
    """Return a validated Score for an approved scope, or raise ValueError."""
    if scope.get("status") != "draft":
        raise ValueError(f"only a draft scope can be approved (status is {scope.get('status')!r})")
    blocking = [f for f in scope.get("flags", []) if f.get("blocking", True)]
    if blocking:
        raise ValueError("blocking guardrail flags: " + ", ".join(f.get("id", "?") for f in blocking))
    if not scope.get("steps"):
        raise ValueError("the scope has no steps")
    slug = re.sub(r"[^a-z0-9]+", "-", str(scope.get("client", "job")).lower()).strip("-") or "job"
    constraints = list(dict.fromkeys(list(scope.get("constraints", [])) + CLIENT_CONSTRAINTS))
    document = scaffold_score(
        score_id=f"client-job-{slug}", objective=scope["summary"], steps=scope["steps"],
        worktree_path=worktree, target_branch=branch, expected_before_sha=before_sha,
        allowed_paths=scope.get("allowed_paths") or None,
        test_command=scope.get("test_command") or "the project's full test suite",
        constraints=constraints)
    compile_score(load_score(json.dumps(document)))
    scope["status"] = "approved"
    scope["approved_at"] = datetime.now(timezone.utc).isoformat()
    return document


def _git(worktree: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(worktree), *args], capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        raise ValueError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def build_delivery(worktree: Path, out: Path, *, evidence_dir: Optional[Path] = None,
                   test_output: Optional[str] = None, client: str = "") -> List[str]:
    """Write archive, test output, README, review reports and a DRAFT client message into ``out``."""
    worktree = Path(worktree)
    if not (worktree / ".git").exists():
        raise ValueError(f"{worktree} is not a git worktree")
    head = _git(worktree, "rev-parse", "HEAD").strip()
    branch = _git(worktree, "rev-parse", "--abbrev-ref", "HEAD").strip()
    out.mkdir(parents=True, exist_ok=True)
    archive = out / "code.zip"
    _git(worktree, "archive", "--format=zip", f"--output={archive}", "HEAD")

    if test_output is None:
        found = worktree / "test-output.txt"
        test_output = found.read_text(encoding="utf-8") if found.exists() else "No test output was recorded."
    (out / "test-output.txt").write_text(test_output, encoding="utf-8")

    reviews: List[str] = []
    review_root = Path(evidence_dir) if evidence_dir else worktree / ".score" / "reviews"
    if review_root.is_dir():
        review_dir = out / "reviews"
        review_dir.mkdir(exist_ok=True)
        for report in sorted(p for p in review_root.iterdir() if p.is_file()):
            (review_dir / report.name).write_bytes(report.read_bytes())
            reviews.append(report.name)

    with zipfile.ZipFile(archive) as z:
        files = len(z.namelist())
    review_line = (f"{len(reviews)} independent review report(s) in reviews/." if reviews
                   else "No review reports were found in the run evidence.")
    (out / "README.md").write_text(
        f"# Delivery for {client or 'client'}\n\n- Branch: `{branch}`\n- Commit: `{head}`\n"
        f"- Files in code.zip: {files}\n- Test output: test-output.txt\n- {review_line}\n\n"
        "Client data used for this job is deleted after delivery.\n", encoding="utf-8")
    (out / "client-message.DRAFT.md").write_text(
        f"DRAFT - NOT SENT. The owner reviews and sends this.\n\nHi {client or 'there'},\n\n"
        f"The work is finished on branch `{branch}` (commit {head[:12]}). The attached bundle has the code, "
        "the test results, a README and the independent review notes. Tell me what you would like changed. "
        "Once you confirm, I will delete your data from my systems.\n\nThanks\n", encoding="utf-8")
    return sorted(p.name for p in out.iterdir())
