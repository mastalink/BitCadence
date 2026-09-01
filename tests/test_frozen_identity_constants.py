"""Golden values for identifiers that must survive a rebrand.

The BitCadence rename swept four string literals that look like branding but
are not: an AES-GCM AAD and three uuid5 namespaces. Renaming them does not
fail a test that builds fresh state - it fails on machines that already hold
data, which is the one case unit tests never construct by accident. So these
tests pin the exact bytes.

If one of these fails, the fix is almost never to update the expected value.
It is to put the literal back. Changing any of them requires a data migration
(re-encrypt every secret / rewrite every derived primary key), and shipping
that change without the migration is silent, irreversible data loss.
"""

import uuid

from mco.secret_vault import SecretRef

# One fixed identity, so every expectation below is a constant, not a
# re-derivation of the code under test.
REF = SecretRef(org_id="acme", scope="conn-1", name="anthropic")


def test_secret_aad_is_frozen():
    """The AAD is authenticated with the ciphertext.

    Change it and every secret encrypted by an earlier version raises
    InvalidTag on decrypt - the ciphertext is intact but unreadable forever.
    """
    assert REF.aad == b'["batoncadence:v1","acme","conn-1","anthropic"]'


def test_secret_record_id_is_frozen():
    """record_id is the deterministic id a secret is stored under.

    Change the namespace and lookups repoint at a key that was never written,
    so existing credentials read back as absent.
    """
    assert REF.record_id == "8d1b7312-becf-57a7-90fd-1abff50455be"


def test_secret_local_key_is_frozen():
    """local_key derives from record_id and is the actual storage key."""
    assert REF.local_key == "MCO_SECRET_8D1B7312_BECF_57A7_90FD_1ABFF50455BE"


def test_role_mapping_id_is_frozen():
    """Calls the real derivation, not a re-implementation of it.

    Change the namespace and every stored mapping recomputes to a new id, so a
    save duplicates instead of updating and an SSO user can log in without the
    role their IdP group grants.
    """
    from mco.orchestrator.identity_routes import _mapping_payload

    mapping = _mapping_payload("idp-1", "acme", "Baton-Admins", "admin")
    assert mapping["id"] == "39f9c4ee-e329-5727-958b-b8b902a5c7d7"


def test_membership_id_is_frozen():
    """The membership id is derived inline inside an async DB route, so this
    guards the literal in the source rather than the route's return value.

    Change the namespace and the upsert inserts a duplicate row per
    (org_id, user_id) instead of updating - and the active-check reads only
    the first row, so a deactivated member can return via the live duplicate.
    """
    from pathlib import Path

    import mco.orchestrator.identity_routes as identity_routes

    src = Path(identity_routes.__file__).read_text(encoding="utf-8")
    assert 'f"batoncadence:membership:{org_id}:{user_id}"' in src
    # The id that literal must keep producing, for org "acme" / user "user-1".
    expected = uuid.uuid5(uuid.NAMESPACE_URL, "batoncadence:membership:acme:user-1")
    assert str(expected) == "83703b8f-41b2-50ad-b6b0-ce4672950399"


def test_version_lookup_covers_every_shipped_dist_name():
    """An install that predates a rename still carries the old dist metadata
    until it is reinstalled; dropping a name makes `mco --version` print
    "unknown" on exactly those machines."""
    import inspect

    from mco.cli import get_version

    src = inspect.getsource(get_version)
    for dist in ("bitcadence", "batoncadence", "mco"):
        assert f'"{dist}"' in src
