import hashlib
from datetime import datetime, timezone

import pytest

from mco.orchestrator.score_authority import grant_identity
from mco.orchestrator.score_evidence import (
    EvidenceBinding,
    EvidenceError,
    EvidenceVerifier,
    select_reviewer,
)
from mco.orchestrator.score_providers import Provider, Run, Task


NOW = datetime(2026, 9, 15, tzinfo=timezone.utc)
DIGEST = "a" * 64


def provider(instance_id="reviewer", provider_name="other"):
    return Provider(
        instance_id=instance_id,
        role="reviewer",
        provider=provider_name,
        capabilities=frozenset({"evidence:review"}),
        independence_class="cross_provider",
        authority_scopes=frozenset({"evidence:review"}),
        approved_digests=frozenset({DIGEST}),
        remaining_cost_cents=0,
    )


def run():
    return Run(DIGEST, frozenset({"evidence:review"}), 0, 0, "cross_provider")


def task(**changes):
    values = dict(
        task_id="S03",
        role="builder",
        review_role="reviewer",
        capabilities=frozenset({"evidence:review"}),
        max_cost_cents=0,
        author_id="author",
        author_provider="author-vendor",
        prior_author_ids=frozenset(),
        contribution_instance_ids=frozenset({"author"}),
    )
    values.update(changes)
    return Task(**values)


def authority():
    return {
        "org_id": "default",
        "digest": DIGEST,
        "actions": ["evidence:test", "evidence:review"],
        "resources": ["run"],
        "env": "test",
        "expires_at": "2026-10-01T00:00:00Z",
        "human_principal": "owner",
        "signature": "signed",
    }


def binding(**changes):
    values = dict(org_id="default", score_digest=DIGEST, run_id="run", task_id="S03", attempt=1, head_sha="head-1", build_id="build-1", deployment_id="deploy-1")
    values.update(changes)
    return EvidenceBinding(**values)


def receipt(event_type, **changes):
    grant = authority()
    values = dict(
        org_id="default",
        digest=DIGEST,
        grant_identity=grant_identity(grant),
        event_type=event_type,
        run_id="run",
        task_id="S03",
        attempt=1,
        head_sha="head-1",
        build_id="build-1",
        deployment_id="deploy-1",
    )
    values.update(changes)
    return values


@pytest.fixture
def evidence(tmp_path):
    content = b"verified bytes"
    (tmp_path / "artifact.json").write_bytes(content)
    claim = {"report": {"location": "run", "path": "artifact.json", "sha256": hashlib.sha256(content).hexdigest()}}
    tests = receipt("test_receipt", suites=[{"name": "unit", "status": "passed", "skipped": False}, {"name": "postgres-optional", "status": "skipped", "skipped": True}])
    review = receipt("code_review", reviewer_id="reviewer", reviewer_provider="other", verdict="pass")
    return EvidenceVerifier({"run": tmp_path}), claim, tests, review


def verify(evidence, **changes):
    verifier, claim, tests, review = evidence
    values = dict(
        binding=binding(),
        worker_output={"artifacts": claim},
        test_receipt=tests,
        review_receipt=review,
        grant=authority(),
        mandatory_suites=["unit"],
        reviewer=provider(),
        contribution_instance_ids=["author", "original-author"],
        author_provider="author-vendor",
        require_cross_provider=True,
        now=NOW,
    )
    values.update(changes)
    return verifier.verify(**values)


def test_artifact_fetch_requires_allowlisted_location_and_matching_digest(evidence):
    _, claim, _, _ = evidence
    claim["report"]["location"] = "worker-chosen"
    with pytest.raises(EvidenceError, match="location_not_allowed"):
        verify(evidence)
    claim["report"]["location"] = "run"
    claim["report"]["sha256"] = "0" * 64
    with pytest.raises(EvidenceError, match="digest_mismatch"):
        verify(evidence)


@pytest.mark.parametrize("field,value", [("head_sha", "old-head"), ("build_id", "old-build"), ("deployment_id", "old-deploy")])
def test_test_receipt_is_bound_to_exact_head_build_and_deployment(evidence, field, value):
    evidence[2][field] = value
    with pytest.raises(EvidenceError, match="stale_or_wrong_build"):
        verify(evidence)


def test_only_explicit_mandatory_suites_must_pass(evidence):
    # An explicitly optional Postgres suite may skip on this host.
    verify(evidence)
    evidence[2]["suites"][0].update(status="skipped", skipped=True)
    with pytest.raises(EvidenceError, match="mandatory_suite"):
        verify(evidence)


def test_reapplied_commit_contributor_cannot_review(evidence):
    reapplied_author = provider("original-author")
    with pytest.raises(EvidenceError, match="overlaps_contribution"):
        verify(evidence, reviewer=reapplied_author, review_receipt=receipt("code_review", reviewer_id="original-author", reviewer_provider="other", verdict="pass"))


def test_server_selects_reviewer_and_ignores_worker_nomination():
    selected = select_reviewer(task(), run(), [provider()], worker_payload={"reviewer_id": "author"})
    assert selected.instance_id == "reviewer"


def test_contribution_and_cross_provider_records_drive_selection():
    same_vendor = provider("same-vendor", "author-vendor")
    reapplied = provider("original-author", "other")
    with pytest.raises(EvidenceError, match="no_qualified_provider"):
        select_reviewer(task(contribution_instance_ids=frozenset({"author", "original-author"})), run(), [same_vendor, reapplied])


def test_worker_verified_claim_is_insufficient(evidence):
    verifier, claim, _, _ = evidence
    with pytest.raises(EvidenceError, match="receipt_required"):
        verifier.verify(
            binding=binding(), worker_output={"artifacts": claim, "verified_evidence": True},
            test_receipt=None, review_receipt=None, grant=authority(), mandatory_suites=["unit"],
            reviewer=provider(), contribution_instance_ids=["author"], author_provider="author-vendor",
            require_cross_provider=True, now=NOW,
        )


def test_test_and_review_are_separate_authenticated_events(evidence):
    result = verify(evidence, worker_output={"artifacts": evidence[1], "verified_evidence": True})
    assert [event["event_type"] for event in result.events] == ["test_receipt_ingested", "code_review_ingested"]
    assert result.events[0]["receipt"]["event_type"] == "test_receipt"
    assert result.events[1]["receipt"]["event_type"] == "code_review"


def test_combined_receipt_type_is_rejected(evidence):
    evidence[2]["event_type"] = "test_and_review"
    with pytest.raises(EvidenceError, match="separate"):
        verify(evidence)
