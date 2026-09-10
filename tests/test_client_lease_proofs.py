"""Regression coverage for fenced lease proofs at the HTTP/MCP seam."""

import json

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

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/jobs/lease":
            claim = next(self.leases)
            self.current_lease_id = claim.get("lease_id")
            payload = {"success": True}
            if not self.legacy:
                payload["lease"] = claim
            return httpx.Response(200, json=payload)

        report = json.loads(request.content)
        self.reports.append(report)
        if self.legacy or report.get("lease_id") == self.current_lease_id:
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
    """Dropping process memory must not turn a valid completion into a 409."""
    gateway = FenceGateway([LEASE_A])

    _client(gateway, tmp_path).lease("job-1")
    _client(gateway, tmp_path).complete("job-1", "finished")

    assert gateway.reports == [{
        "status": "completed",
        "output_payload": {"result": "finished"},
        **LEASE_A,
    }]
    assert list(tmp_path.rglob("*.json")) == []


def test_separate_mcp_calls_forward_proof_with_fresh_clients(tmp_path, monkeypatch):
    """mco_lease and mco_complete each construct a new GatewayClient today."""
    gateway = FenceGateway([LEASE_A])
    clients = []

    def factory():
        client = _client(gateway, tmp_path)
        clients.append(client)
        return client

    monkeypatch.setattr(mcp_mod, "GatewayClient", factory)
    mcp_mod.mco_lease("job-1")
    mcp_mod.mco_complete("job-1", "finished")

    assert len(clients) == 2
    assert gateway.reports[0]["lease_id"] == "lease-a"


def test_reopened_client_keeps_legacy_gateway_compatible(tmp_path):
    """A gateway that returns no claim must continue receiving proofless writes."""
    gateway = FenceGateway([{}], legacy=True)

    _client(gateway, tmp_path).lease("job-1")
    _client(gateway, tmp_path).complete("job-1", "legacy result")

    assert gateway.reports == [{
        "status": "completed",
        "output_payload": {"result": "legacy result"},
    }]


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
    """Token, role, and instance changes must select a separate namespace."""
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


def test_stale_client_cannot_erase_replacement_proof(tmp_path):
    """Retiring fenced attempt A must leave live replacement B intact."""
    gateway = FenceGateway([LEASE_A, LEASE_B])
    stale = _client(gateway, tmp_path)
    stale.lease("job-1")
    replacement = _client(gateway, tmp_path)
    replacement.lease("job-1")

    with pytest.raises(httpx.HTTPStatusError) as exc:
        stale.complete("job-1", "stale result")
    assert exc.value.response.status_code == 409

    replacement.complete("job-1", "replacement result")
    assert [report["lease_id"] for report in gateway.reports] == [
        "lease-a",
        "lease-b",
    ]


def test_reopened_process_refuses_ambiguous_replacement_proofs(tmp_path):
    """Without process memory, output cannot safely choose attempt A or B."""
    gateway = FenceGateway([LEASE_A, LEASE_B])
    _client(gateway, tmp_path).lease("job-1")
    _client(gateway, tmp_path).lease("job-1")

    with pytest.raises(RuntimeError, match="Multiple lease proofs"):
        _client(gateway, tmp_path).complete("job-1", "ambiguous result")

    assert gateway.reports == []


def test_fenced_attempt_is_not_replayed_after_reopen(tmp_path):
    """A 409 is final for the rejected attempt, even after a restart."""
    gateway = FenceGateway([LEASE_A])
    client = _client(gateway, tmp_path)
    client.lease("job-1")
    gateway.current_lease_id = "replacement"

    with pytest.raises(httpx.HTTPStatusError):
        client.complete("job-1", "late result")
    with pytest.raises(RuntimeError, match="retired lease proof"):
        _client(gateway, tmp_path).complete("job-1", "late replay")

    assert len(gateway.reports) == 1
    assert gateway.reports[0]["lease_id"] == "lease-a"


def test_failure_report_uses_the_same_persisted_proof(tmp_path):
    """mco_fail is fenced by the same attempt claim as mco_complete."""
    gateway = FenceGateway([LEASE_A])
    _client(gateway, tmp_path).lease("job-1")

    _client(gateway, tmp_path).fail("job-1", "blocked")

    assert gateway.reports == [{
        "status": "failed",
        "error_message": "blocked",
        **LEASE_A,
    }]
