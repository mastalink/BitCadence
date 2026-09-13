"""Regression coverage for durable fenced lease proofs at the HTTP/MCP seam."""

import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import textwrap

import httpx
import pytest

import mco.mcp_server as mcp_mod
from mco.orchestrator.client import GatewayClient


BASE_URL = "http://gateway.test"
LEASE_A = {
    "lease_id": "lease-a",
    "lease_epoch": 3,
    "lease_incarnation": "store-1",
    "agent_instance_id": "codex-1",
}
LEASE_B = {
    "lease_id": "lease-b",
    "lease_epoch": 4,
    "lease_incarnation": "store-1",
    "agent_instance_id": "codex-1",
}


class FenceGateway:
    """A real HTTP boundary that accepts only the current lease attempt."""

    def __init__(self, leases, *, legacy=False):
        self.leases = iter(leases)
        self.legacy = legacy
        self.current_lease_id = None
        self.reports = []
        self.renewals = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path in {"/api/jobs/lease", "/api/jobs/lease_next"}:
            claim = next(self.leases)
            self.current_lease_id = claim.get("lease_id")
            payload = {"success": True}
            if request.url.path.endswith("lease_next"):
                payload["job"] = {"id": "job-1"}
            if not self.legacy:
                payload["lease"] = claim
            return httpx.Response(200, json=payload)

        body = json.loads(request.content)
        if request.url.path.endswith("/renew"):
            self.renewals.append(body)
            if self.legacy or body.get("lease_id") == self.current_lease_id:
                return httpx.Response(200, json={"success": True})
            return httpx.Response(409, json={"detail": "FENCED: stale or missing lease"})

        self.reports.append(body)
        if self.legacy or body.get("lease_id") == self.current_lease_id:
            return httpx.Response(200, json={"success": True})
        return httpx.Response(409, json={"detail": "FENCED: stale or missing lease"})


def _client(handler, lease_store_dir, *, token="token-a", role="codex",
            instance_id="codex-1"):
    return GatewayClient(
        base_url=BASE_URL,
        token=token,
        role=role,
        instance_id=instance_id,
        transport=httpx.MockTransport(handler),
        lease_store_dir=lease_store_dir,
    )


def test_reopened_client_forwards_persisted_lease_proof(tmp_path):
    gateway = FenceGateway([LEASE_A])

    _client(gateway, tmp_path).lease("job-1")
    proof_path = next(tmp_path.rglob("*.json"))
    if os.name != "nt":
        assert stat.S_IMODE(proof_path.stat().st_mode) == 0o600
        assert stat.S_IMODE(proof_path.parent.stat().st_mode) == 0o700

    _client(gateway, tmp_path).complete("job-1", "finished")

    assert gateway.reports == [{
        "status": "completed",
        "output_payload": {"result": "finished"},
        **LEASE_A,
    }]
    assert list(tmp_path.rglob("*.json")) == []


def test_lease_next_persists_proof_for_reconstructed_client(tmp_path):
    gateway = FenceGateway([LEASE_A])

    _client(gateway, tmp_path).lease_next()
    _client(gateway, tmp_path).complete("job-1", "finished")

    assert gateway.reports[0]["lease_id"] == "lease-a"


def test_separate_mcp_calls_survive_client_cache_reconstruction(
    tmp_path, monkeypatch
):
    gateway = FenceGateway([LEASE_A])

    def factory():
        return _client(gateway, tmp_path)

    monkeypatch.setattr(mcp_mod, "GatewayClient", factory)
    monkeypatch.setattr(mcp_mod, "_clients", {})
    mcp_mod.mco_lease_next()
    mcp_mod._clients.clear()  # Model a new MCP process/call boundary.
    mcp_mod.mco_complete("job-1", "finished")

    assert gateway.reports[0]["lease_id"] == "lease-a"


def test_in_process_cached_claim_survives_missing_disk_record(tmp_path):
    gateway = FenceGateway([LEASE_A])
    client = _client(gateway, tmp_path)
    client.lease("job-1")
    next(tmp_path.rglob("*.json")).unlink()

    client.complete("job-1", "finished")

    assert gateway.reports[0]["lease_id"] == "lease-a"


def test_process_reopen_loads_claim_from_disk(tmp_path):
    script = textwrap.dedent(
        f"""
        import httpx
        from mco.orchestrator.client import GatewayClient

        claim = {LEASE_A!r}
        def handler(request):
            return httpx.Response(200, json={{"success": True, "lease": claim}})

        GatewayClient(
            base_url={BASE_URL!r}, token="token-a", role="codex",
            instance_id="codex-1", transport=httpx.MockTransport(handler),
            lease_store_dir={str(tmp_path)!r},
        ).lease("job-1")
        """
    )
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    subprocess.run([sys.executable, "-c", script], env=environment, check=True)

    gateway = FenceGateway([])
    gateway.current_lease_id = "lease-a"
    _client(gateway, tmp_path).complete("job-1", "recovered result")

    assert gateway.reports[0]["lease_id"] == "lease-a"


def test_stale_claimant_compare_and_delete_preserves_replacement(tmp_path):
    gateway = FenceGateway([LEASE_A, LEASE_B])
    stale = _client(gateway, tmp_path)
    stale.lease("job-1")
    replacement = _client(gateway, tmp_path)
    replacement.lease("job-1")

    with pytest.raises(httpx.HTTPStatusError) as exc:
        stale.complete("job-1", "stale result")
    assert exc.value.response.status_code == 409

    _client(gateway, tmp_path).complete("job-1", "replacement result")
    assert [report["lease_id"] for report in gateway.reports] == [
        "lease-a",
        "lease-b",
    ]


def test_new_lease_supersedes_old_durable_claim(tmp_path):
    gateway = FenceGateway([LEASE_A, LEASE_B])
    _client(gateway, tmp_path).lease("job-1")
    _client(gateway, tmp_path).lease("job-1")

    _client(gateway, tmp_path).complete("job-1", "replacement result")

    assert gateway.reports[0]["lease_id"] == "lease-b"
    assert len(list(tmp_path.rglob("*.superseded"))) == 1


def test_fenced_409_is_final_for_rejected_attempt(tmp_path, monkeypatch):
    result_spool = tmp_path / "results"
    monkeypatch.setenv("MCO_RESULT_SPOOL_DIR", str(result_spool))
    gateway = FenceGateway([LEASE_A])
    client = _client(gateway, tmp_path / "leases")
    client.lease("job-1")
    gateway.current_lease_id = "replacement"

    with pytest.raises(httpx.HTTPStatusError) as exc:
        client.complete("job-1", "late result")

    assert exc.value.response.status_code == 409
    assert len(gateway.reports) == 1
    assert not list((tmp_path / "leases").rglob("*.json"))
    assert len(list((tmp_path / "leases").rglob("*.rejected"))) == 1
    assert len(list(result_spool.rglob("*.rejected"))) == 1
    assert _client(gateway, tmp_path / "leases").flush_reports() == 0
    assert len(gateway.reports) == 1


@pytest.mark.parametrize(
    ("token", "role", "instance_id"),
    (
        ("token-b", "codex", "codex-1"),
        ("token-a", "reviewer", "codex-1"),
        ("token-a", "codex", "codex-2"),
    ),
)
def test_identity_change_cannot_reuse_another_identity_proof(
    tmp_path, token, role, instance_id
):
    gateway = FenceGateway([LEASE_A])
    _client(gateway, tmp_path).lease("job-1")

    with pytest.raises(httpx.HTTPStatusError) as exc:
        _client(
            gateway,
            tmp_path,
            token=token,
            role=role,
            instance_id=instance_id,
        ).complete("job-1", "wrong identity")

    assert exc.value.response.status_code == 409
    assert "lease_id" not in gateway.reports[0]
    assert len(list(tmp_path.rglob("*.json"))) == 1


def test_reopened_renew_forwards_persisted_proof(tmp_path):
    gateway = FenceGateway([LEASE_A])
    _client(gateway, tmp_path).lease("job-1")

    _client(gateway, tmp_path).renew("job-1")

    assert gateway.renewals == [LEASE_A]


def test_reopened_client_keeps_legacy_gateway_compatible(tmp_path):
    gateway = FenceGateway([{}], legacy=True)

    _client(gateway, tmp_path).lease("job-1")
    _client(gateway, tmp_path).complete("job-1", "legacy result")

    assert gateway.reports == [{
        "status": "completed",
        "output_payload": {"result": "legacy result"},
    }]
