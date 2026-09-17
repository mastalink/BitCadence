"""Workers that execute the canary's fixed handlers, so a Score run can finish.

The canary exists to prove the conductor, not a model: both sides are
deterministic Python (`score_canary.hash_artifact` / `verify_artifact`) with no
LLM anywhere. Until now nothing *ran* them as board workers, so the canary score
could be compiled and reviewed but never executed end to end.

Each worker leases jobs for its own role, does the one fixed thing, and returns
the strict JSON the bridge validates:

  work   -> {"artifacts": {<evidence name>: {"path": ..., "sha256": ...}}}
  review -> {"verdict": "pass"|"fail", "review_of": <exact evidence map>,
             "findings": [...]}

Separation of duty is enforced twice: the conductor refuses a score whose work
and review identities match, and `verify_artifact` fails if author == reviewer.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional

from mco.orchestrator.score_canary import (
    CANARY_ROLE_REVIEW,
    CANARY_ROLE_WORKER,
    hash_artifact,
    verify_artifact,
)

logger = logging.getLogger("mco.orchestrator.score_canary_worker")


class CanaryContractError(ValueError):
    """The job did not carry the score contract this worker requires."""


def _contract(job: dict) -> dict:
    contract = ((job.get("input_payload") or {}).get("score")) or {}
    for key in ("run_id", "task", "phase", "artifact_root", "required_evidence"):
        if key not in contract:
            raise CanaryContractError(f"score contract missing {key!r}")
    return contract


def _evidence_name(contract: dict) -> str:
    required = contract["required_evidence"]
    if not isinstance(required, list) or len(required) != 1:
        raise CanaryContractError(
            "canary expects exactly one required evidence name, got "
            f"{required!r} - a name like 'canary_artifact', not the fields inside it"
        )
    return required[0]


def work(job: dict, root: Optional[str | Path] = None) -> str:
    """Produce the deterministic artifact and report it by name and digest."""
    contract = _contract(job)
    name = _evidence_name(contract)
    reference = hash_artifact(root or contract["artifact_root"], contract["run_id"])
    return json.dumps({"artifacts": {name: reference}}, sort_keys=True)


def review(job: dict, reviewer: str, root: Optional[str | Path] = None) -> str:
    """Re-fetch the exact bytes and verify them against the claim."""
    contract = _contract(job)
    name = _evidence_name(contract)
    evidence = contract.get("review_of") or {}
    reference = evidence.get(name)
    author = job.get("source_agent_id") or contract.get("author") or ""
    if not isinstance(reference, dict):
        return json.dumps({"verdict": "fail", "review_of": evidence,
                           "findings": [f"no evidence named {name!r} to review"]}, sort_keys=True)

    outcome = verify_artifact(root or contract["artifact_root"], contract["run_id"], reference,
                              author=author or "unknown-author", reviewer=reviewer)
    findings = [] if outcome.get("passed") else [str(outcome.get("reason") or "verification failed")]
    return json.dumps({"verdict": "pass" if outcome.get("passed") else "fail",
                       "review_of": evidence, "findings": findings}, sort_keys=True)


def build_agent(role: str, instance_id: str, *, token: str = "", gateway: str = "",
                root: Optional[str | Path] = None, client: Any = None):
    """A BitCadenceAgent wired to the fixed handler for `role`."""
    from mco.sdk import BitCadenceAgent

    if role not in (CANARY_ROLE_WORKER, CANARY_ROLE_REVIEW):
        raise ValueError(f"unknown canary role {role!r}")
    agent = BitCadenceAgent(role=role, instance_id=instance_id, token=token,
                            gateway=gateway, client=client)

    @agent.handler
    def _handle(job, _prompt):   # noqa: ANN001 - SDK contract
        if role == CANARY_ROLE_WORKER:
            return work(job, root)
        return review(job, instance_id, root)

    return agent


def main(argv: Optional[list] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="python -m mco.orchestrator.score_canary_worker")
    parser.add_argument("--role", required=True, choices=[CANARY_ROLE_WORKER, CANARY_ROLE_REVIEW])
    parser.add_argument("--instance", required=True)
    parser.add_argument("--token-file", required=True)
    parser.add_argument("--gateway", default="http://127.0.0.1:18789")
    parser.add_argument("--root", default=None, help="artifact root (default: the job's contract)")
    parser.add_argument("--once", action="store_true", help="drain the inbox once and exit")
    args = parser.parse_args(argv)

    token = Path(args.token_file).read_text(encoding="utf-8").strip()
    agent = build_agent(args.role, args.instance, token=token, gateway=args.gateway, root=args.root)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    if args.once:
        executed = agent.run_once()
        logger.info("canary %s executed %s job(s)", args.role, executed)
        return 0
    agent.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
