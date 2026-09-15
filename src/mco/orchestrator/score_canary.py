"""Fixed non-LLM worker and reviewer handlers for the VIA Score conductor canary."""
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Optional, Union

CANARY_SCORE_ID = "via-score-conductor-canary"
CANARY_TASK_ID = "C01"
CANARY_ROLE_WORKER = "score-canary-worker"
CANARY_ROLE_REVIEW = "score-canary-review"
CANARY_RESOURCE_LANE = "score-canary-lane"


def default_canary_body(run_id: str) -> Dict[str, Any]:
    """Deterministic known body for canary task C01."""
    return {
        "score_id": CANARY_SCORE_ID,
        "run_id": run_id,
        "task_id": CANARY_TASK_ID,
        "phase": "canary_artifact",
        "status": "canary_ok",
        "magic": "canary-c01-deterministic",
    }


def hash_artifact(
    root_dir: Union[str, Path],
    run_id: str,
    *,
    body: Optional[Dict[str, Any]] = None,
    filename: str = "canary_artifact.json",
) -> Dict[str, str]:
    """Fixed non-LLM worker handler for C01.

    Writes one JSON artifact under score-runs/{run_id}/ with a known body
    and returns {"path": <rel_path>, "sha256": <sha256>}.
    """
    if not run_id or not isinstance(run_id, str):
        raise ValueError("run_id must be a non-empty string")

    payload = default_canary_body(run_id) if body is None else body
    raw_bytes = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    content_sha256 = hashlib.sha256(raw_bytes).hexdigest()

    run_dir = Path(root_dir) / "score-runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = run_dir / filename
    artifact_path.write_bytes(raw_bytes)

    rel_path = f"score-runs/{run_id}/{filename}"
    return {"path": rel_path, "sha256": content_sha256}


def verify_artifact(
    root_dir: Union[str, Path],
    run_id: str,
    evidence: Dict[str, str],
    *,
    author: str,
    reviewer: str,
    expected_body: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Fixed non-LLM review handler for C01.

    Fetches artifact from disk, computes sha256, and verifies exact bytes.
    Fails if author identity equals reviewer identity, or if bytes do not match.
    """
    if not author or not reviewer:
        return {
            "passed": False,
            "verified_evidence": False,
            "reason": "Author and reviewer identities must be non-empty strings",
        }

    # Strict independence check: author cannot review own work
    if author == reviewer:
        return {
            "passed": False,
            "verified_evidence": False,
            "reason": f"Independent reviewer required: author identity '{author}' equals reviewer '{reviewer}'",
        }

    if not isinstance(evidence, dict) or "path" not in evidence or "sha256" not in evidence:
        return {
            "passed": False,
            "verified_evidence": False,
            "reason": "Evidence missing 'path' or 'sha256' string keys",
        }

    claimed_path = evidence["path"]
    claimed_sha256 = evidence["sha256"]

    if not isinstance(claimed_path, str) or not isinstance(claimed_sha256, str):
        return {
            "passed": False,
            "verified_evidence": False,
            "reason": "'path' and 'sha256' in evidence must be strings",
        }

    # Path traversal protection: target must reside under score-runs/{run_id}
    root_path = Path(root_dir).resolve()
    base_dir = (root_path / "score-runs" / run_id).resolve()
    target_path = (root_path / claimed_path).resolve()

    try:
        target_path.relative_to(base_dir)
    except ValueError:
        return {
            "passed": False,
            "verified_evidence": False,
            "reason": f"Path traversal rejected: '{claimed_path}' outside score-runs/{run_id}",
        }

    if not target_path.is_file():
        return {
            "passed": False,
            "verified_evidence": False,
            "reason": f"Artifact file not found: {claimed_path}",
        }

    actual_bytes = target_path.read_bytes()
    actual_sha256 = hashlib.sha256(actual_bytes).hexdigest()

    if actual_sha256 != claimed_sha256:
        return {
            "passed": False,
            "verified_evidence": False,
            "reason": f"SHA-256 mismatch: claimed={claimed_sha256}, actual={actual_sha256}",
            "claimed_sha256": claimed_sha256,
            "actual_sha256": actual_sha256,
        }

    # Verify content equals known body
    target_body = default_canary_body(run_id) if expected_body is None else expected_body
    expected_bytes = json.dumps(target_body, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")

    if actual_bytes != expected_bytes:
        return {
            "passed": False,
            "verified_evidence": False,
            "reason": "Artifact content does not match expected canary body bytes",
        }

    return {
        "passed": True,
        "verified_evidence": True,
        "sha256": actual_sha256,
        "path": claimed_path,
    }
