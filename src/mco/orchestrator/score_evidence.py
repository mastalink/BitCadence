"""Server-side Score evidence verification.

Worker output is only a set of claims.  This module fetches the claimed
artifacts through named, allowlisted locations and independently authenticates
test and code-review receipts before it creates a verdict the Score kernel can
consume.
"""
from __future__ import annotations

import copy
import hashlib
import hmac
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

from mco.orchestrator.score_authority import AuthorityError, require_receipt
from mco.orchestrator.score_providers import Provider, Run, Task, select


class EvidenceError(ValueError):
    """Evidence is absent, stale, unauthenticated, or policy-ineligible."""


@dataclass(frozen=True)
class EvidenceBinding:
    org_id: str
    score_digest: str
    run_id: str
    task_id: str
    attempt: int
    head_sha: str
    build_id: str
    deployment_id: str


@dataclass(frozen=True)
class VerifiedEvidence:
    """Opaque result produced only after all server checks complete."""

    binding: EvidenceBinding
    artifacts: Mapping[str, bytes]
    events: tuple[Mapping[str, object], Mapping[str, object]]
    reviewer_id: str


def select_reviewer(
    task: Task,
    run: Run,
    providers: Sequence[Provider],
    *,
    worker_payload: Mapping[str, object] | None = None,
) -> Provider:
    """Select from server records; a worker-nominated reviewer is ignored."""
    result = select(task, "review", providers, run)
    if result.status != "dispatch" or result.chosen is None:
        raise EvidenceError(result.error_class or "no_qualified_provider")
    return result.chosen


class EvidenceVerifier:
    def __init__(self, allowed_locations: Mapping[str, str | Path], *, max_artifact_bytes: int = 16 * 1024 * 1024):
        if not allowed_locations:
            raise EvidenceError("artifact_location_allowlist_required")
        self.locations = {name: Path(root).resolve() for name, root in allowed_locations.items()}
        if any(not isinstance(name, str) or not name for name in self.locations):
            raise EvidenceError("invalid_artifact_location")
        self.max_artifact_bytes = max_artifact_bytes

    def _artifacts(self, claims: object) -> Mapping[str, bytes]:
        if not isinstance(claims, dict) or not claims:
            raise EvidenceError("artifact_claims_required")
        fetched = {}
        for name, claim in claims.items():
            if not isinstance(name, str) or not isinstance(claim, dict) or set(claim) != {"location", "path", "sha256"}:
                raise EvidenceError("invalid_artifact_claim")
            location = claim.get("location")
            if location not in self.locations:
                raise EvidenceError("artifact_location_not_allowed")
            relative = claim.get("path")
            expected = claim.get("sha256")
            if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
                raise EvidenceError("invalid_artifact_path")
            if not isinstance(expected, str) or len(expected) != 64:
                raise EvidenceError("invalid_artifact_digest")
            root = self.locations[location]
            path = (root / relative).resolve()
            if root != path and root not in path.parents:
                raise EvidenceError("artifact_path_outside_allowlist")
            if not path.is_file() or path.stat().st_size > self.max_artifact_bytes:
                raise EvidenceError("artifact_missing_or_oversized")
            content = path.read_bytes()
            if not hmac.compare_digest(hashlib.sha256(content).hexdigest(), expected.lower()):
                raise EvidenceError("artifact_digest_mismatch")
            fetched[name] = content
        return fetched

    @staticmethod
    def _authenticate(receipt: object, binding: EvidenceBinding, grant: dict, action: str, now) -> dict:
        if not isinstance(receipt, dict):
            raise EvidenceError("receipt_required")
        try:
            require_receipt(receipt, org_id=binding.org_id, digest=binding.score_digest, grant=grant, action=action, now=now)
        except AuthorityError as exc:
            raise EvidenceError(str(exc)) from exc
        expected = {
            "run_id": binding.run_id,
            "task_id": binding.task_id,
            "attempt": binding.attempt,
            "head_sha": binding.head_sha,
            "build_id": binding.build_id,
            "deployment_id": binding.deployment_id,
        }
        if any(receipt.get(key) != value for key, value in expected.items()):
            raise EvidenceError("stale_or_wrong_build_receipt")
        return receipt

    def verify(
        self,
        *,
        binding: EvidenceBinding,
        worker_output: Mapping[str, object],
        test_receipt: dict,
        review_receipt: dict,
        grant: dict,
        mandatory_suites: Sequence[str],
        reviewer: Provider,
        contribution_instance_ids: Sequence[str],
        author_provider: str,
        require_cross_provider: bool,
        now=None,
    ) -> VerifiedEvidence:
        """Establish evidence without trusting any worker verification claim."""
        if not isinstance(worker_output, dict):
            raise EvidenceError("worker_output_required")
        artifacts = self._artifacts(worker_output.get("artifacts"))
        tests = self._authenticate(test_receipt, binding, grant, "evidence:test", now)
        review = self._authenticate(review_receipt, binding, grant, "evidence:review", now)
        if tests.get("event_type") != "test_receipt" or review.get("event_type") != "code_review":
            raise EvidenceError("receipt_event_types_must_be_separate")
        suites = tests.get("suites")
        if not isinstance(suites, list) or any(not isinstance(s, dict) for s in suites):
            raise EvidenceError("invalid_test_suites")
        by_name = {s.get("name"): s for s in suites if isinstance(s.get("name"), str)}
        for name in mandatory_suites:
            suite = by_name.get(name)
            if suite is None or suite.get("status") != "passed" or suite.get("skipped") is not False:
                raise EvidenceError("mandatory_suite_not_passed")
        contributors = set(contribution_instance_ids)
        if reviewer.instance_id in contributors or review.get("reviewer_id") != reviewer.instance_id:
            raise EvidenceError("reviewer_overlaps_contribution_set")
        if review.get("reviewer_provider") != reviewer.provider:
            raise EvidenceError("reviewer_identity_mismatch")
        if require_cross_provider and reviewer.provider == author_provider:
            raise EvidenceError("cross_provider_review_required")
        if review.get("verdict") != "pass":
            raise EvidenceError("review_did_not_pass")
        # Deliberately omit the worker payload from authenticated event content.
        # In particular, worker_output["verified_evidence"] has no authority.
        test_event = {"event_type": "test_receipt_ingested", "binding": copy.deepcopy(binding.__dict__), "receipt": copy.deepcopy(tests)}
        review_event = {"event_type": "code_review_ingested", "binding": copy.deepcopy(binding.__dict__), "receipt": copy.deepcopy(review)}
        return VerifiedEvidence(binding, fetched_copy(artifacts), (test_event, review_event), reviewer.instance_id)


def fetched_copy(artifacts: Mapping[str, bytes]) -> Mapping[str, bytes]:
    return {name: bytes(content) for name, content in artifacts.items()}
