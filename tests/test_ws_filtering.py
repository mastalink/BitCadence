import pytest

from mco.cli import ConnectionIdentity, ConnectionManager, _is_admin_scope_role


class FakeWebSocket:
    def __init__(self):
        self.messages = []

    async def send_json(self, message):
        self.messages.append(message)


def _event(title, role, instance_id=None):
    job = {
        "id": title,
        "title": title,
        "target_agent_role": role,
    }
    if instance_id is not None:
        job["target_agent_id"] = instance_id
    return {
        "type": "event",
        "payload": {
            "event": "job_pending",
            "job": job,
        },
    }


@pytest.mark.asyncio
async def test_broadcast_filters_non_admin_agent_to_its_mailbox():
    manager = ConnectionManager()
    codex_socket = FakeWebSocket()
    admin_socket = FakeWebSocket()
    manager.register(
        codex_socket,
        ConnectionIdentity(role="codex", instance_id="codex-beast", is_admin=False),
    )
    manager.register(
        admin_socket,
        ConnectionIdentity(role="admin", instance_id="operator", is_admin=True),
    )

    matching_role = _event("matching role-wide job", "codex")
    wrong_role = _event("wrong role job", "claude")
    sibling_instance = _event("sibling instance job", "codex", "codex-other")

    for message in (matching_role, wrong_role, sibling_instance):
        await manager.broadcast(message)

    assert [m["payload"]["job"]["title"] for m in codex_socket.messages] == [
        "matching role-wide job",
    ]
    assert [m["payload"]["job"]["title"] for m in admin_socket.messages] == [
        "matching role-wide job",
        "wrong role job",
        "sibling instance job",
    ]


@pytest.mark.parametrize("role", ["operator", "human"])
def test_approver_roles_receive_firehose_events(role):
    identity = ConnectionIdentity(
        role=role,
        instance_id=f"{role}-instance",
        is_admin=_is_admin_scope_role(role),
    )

    assert ConnectionManager._can_receive(
        identity,
        {"target_agent_role": "codex", "target_agent_id": "codex-beast"},
    )


def test_jobless_events_fail_closed_for_non_admin_connections():
    assert not ConnectionManager._can_receive(
        ConnectionIdentity(role="codex", instance_id="codex-beast", is_admin=False),
        None,
    )
    assert ConnectionManager._can_receive(
        ConnectionIdentity(role="admin", instance_id="admin", is_admin=True),
        None,
    )
    assert ConnectionManager._can_receive(
        ConnectionIdentity(
            role="operator",
            instance_id="operator",
            is_admin=_is_admin_scope_role("operator"),
        ),
        None,
    )
