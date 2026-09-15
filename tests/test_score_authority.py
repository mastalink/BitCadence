from datetime import datetime, timezone

import pytest

from mco.orchestrator.score_authority import AuthorityError, grant_identity, require_grant, require_receipt


NOW = datetime(2026, 9, 15, tzinfo=timezone.utc)


def grant(org="acme", digest="a" * 64):
    return {"org_id": org, "digest": digest, "actions": ["release:approve"], "resources": ["via"], "env": "production", "expires_at": "2026-10-01T00:00:00Z", "human_principal": "owner", "signature": "signed"}


def test_changed_digest_cannot_inherit_grant_or_approval():
    with pytest.raises(AuthorityError, match="org_and_digest"):
        require_grant(grant(), org_id="acme", digest="b" * 64, action="release:approve", now=NOW)


def test_stale_digest_receipt_is_rejected():
    active = grant()
    receipt = {"org_id": "acme", "digest": "b" * 64, "grant_identity": grant_identity(active)}
    with pytest.raises(AuthorityError, match="stale"):
        require_receipt(receipt, org_id="acme", digest="a" * 64, grant=active, action="release:approve", now=NOW)


def test_same_document_bytes_cannot_share_authority_across_orgs():
    shared_digest = "a" * 64
    with pytest.raises(AuthorityError, match="org_and_digest"):
        require_grant(grant("acme", shared_digest), org_id="globex", digest=shared_digest, action="release:approve", now=NOW)
