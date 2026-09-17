"""Offline safety tests for the signed, fixed-document VIA release lane."""
import base64
import importlib.util
import io
from pathlib import Path

import pytest


MODULE = Path(__file__).parents[1] / "infra" / "aws" / "via_runner" / "deploy_handler.py"
SPEC = importlib.util.spec_from_file_location("via_deploy_runner", MODULE)
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


APPROVAL_ID = "11111111-1111-4111-8111-111111111111"
DIGEST = "a" * 64
IMAGE = "sha256:" + "b" * 64


def approval(**changes):
    value = {
        "schema_version": 1,
        "approval_id": APPROVAL_ID,
        "score_id": "via-cloud-launch",
        "score_digest": DIGEST,
        "action": "activate",
        "instance_id": "i-fixed",
        "release_directory": "/opt/via/releases/via-20260914-candidate",
        "image_digest": IMAGE,
        "evidence": [{"kind": "build", "sha256": "1" * 64}, {"kind": "test", "sha256": "2" * 64}, {"kind": "review", "sha256": "3" * 64}],
        "expires_at": "2027-01-01T00:00:00Z",
        "signature": base64.b64encode(b"signature").decode(),
    }
    value.update(changes)
    return value


class S3:
    def __init__(self, value):
        self.value = value
        self.writes = []

    def get_object(self, **kwargs):
        return {"Body": io.BytesIO(self.value)}

    def put_object(self, **kwargs):
        self.writes.append(kwargs)


class Kms:
    def verify(self, **kwargs):
        assert kwargs["SigningAlgorithm"] == "RSASSA_PSS_SHA_256"
        return {"SignatureValid": True}


class Ddb:
    def __init__(self):
        self.item = None

    def get_item(self, **kwargs):
        return {} if self.item is None else {"Item": self.item}

    def put_item(self, **kwargs):
        assert self.item is None and kwargs["ConditionExpression"] == "attribute_not_exists(approval_id)"
        self.item = kwargs["Item"]

    def update_item(self, **kwargs):
        assert self.item["status"]["S"] == "dispatching"
        self.item.update({"command_id": kwargs["ExpressionAttributeValues"][":command"], "status": kwargs["ExpressionAttributeValues"][":sent"]})


class Ssm:
    def __init__(self):
        self.sent = []

    def send_command(self, **kwargs):
        self.sent.append(kwargs)
        return {"Command": {"CommandId": "22222222-2222-4222-8222-222222222222"}}

    def get_command_invocation(self, **kwargs):
        return {"Status": "Success", "ResponseCode": 0, "StandardOutputContent": "must not be retained"}


def test_strict_approval_and_key_reject_unbounded_input():
    runner.validate_approval(approval(), score_digest=DIGEST, instance_id="i-fixed")
    with pytest.raises(runner.ApprovalError):
        runner.validate_approval(approval(score_digest="c" * 64), score_digest=DIGEST, instance_id="i-fixed")
    deployment = runner.Deployment(clients={"s3": S3(b"{}"), "kms": Kms(), "ssm": Ssm(), "dynamodb": Ddb()})
    with pytest.raises(runner.ApprovalError):
        deployment.read_approval("bucket", "score-deploy-approvals/pending/../../command.json")


def test_single_reservation_then_fixed_document_and_sanitized_receipt():
    s3, ssm, ddb = S3(b"{}"), Ssm(), Ddb()
    deployment = runner.Deployment(clients={"s3": s3, "kms": Kms(), "ssm": ssm, "dynamodb": ddb})
    item = approval()
    command_id = deployment.send(item, table="table", document_name="via-score-activate-release", document_version="4")
    assert command_id
    sent = ssm.sent[0]
    assert sent["DocumentName"] == "via-score-activate-release" and sent["DocumentVersion"] == "4"
    assert sent["InstanceIds"] == ["i-fixed"]
    assert sent["Parameters"] == {"ReleaseDirectory": [item["release_directory"]], "ImageDigest": [IMAGE]}
    assert deployment.send(item, table="table", document_name="wrong", document_version="wrong") == command_id
    status = deployment.command_status(command_id, "i-fixed")
    key = deployment.receipt("bucket", item, command_id, status)
    assert key.endswith(APPROVAL_ID + ".json")
    assert b"must not be retained" not in s3.writes[0]["Body"]
