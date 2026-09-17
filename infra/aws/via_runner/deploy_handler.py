"""Signed-approval-only VIA release adapter for BitCadence Score.

This is deliberately not an arbitrary SSM shell bridge. It accepts one key in
the approved S3 prefix, verifies a KMS signature over a strict canonical
manifest, validates the exact Score digest and fixed VIA target, then invokes
one version-pinned SSM document. It records only sanitized status receipts.
"""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import io
import json
import os
from pathlib import PurePosixPath
import re
import time
import uuid

try:  # Lambda includes boto3; offline tests inject all clients.
    import boto3
except ModuleNotFoundError:  # pragma: no cover
    boto3 = None


APPROVAL_PREFIX = "score-deploy-approvals/pending/"
RECEIPT_PREFIX = "score-deploy-receipts/"
REQUIRED_EVIDENCE = {"build", "test", "review"}
RELEASE_RE = re.compile(r"^/opt/via/releases/via-[A-Za-z0-9._-]+$")
IMAGE_RE = re.compile(r"^sha256:[a-f0-9]{64}$")
SHA_RE = re.compile(r"^[a-f0-9]{64}$")


class ApprovalError(ValueError):
    """An approval is absent, stale, malformed, or outside its authority."""


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def canonical_message(approval: dict) -> bytes:
    """Stable bytes signed by KMS; the signature is never part of itself."""
    if not isinstance(approval, dict):
        raise ApprovalError("approval_not_object")
    unsigned = {key: value for key, value in approval.items() if key != "signature"}
    return json.dumps(unsigned, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def sanitize(value):
    if isinstance(value, dict):
        return {sanitize(str(key)): "[redacted]" if re.search(r"(?i)secret|password|token|credential|signature", str(key)) else sanitize(item) for key, item in value.items()}
    if isinstance(value, list):
        return [sanitize(item) for item in value]
    if not isinstance(value, str):
        return value
    value = re.sub(r"(?i)(?:AKIA|ASIA)[A-Z0-9]{16}", "[redacted-key]", value)
    value = re.sub(r"\b\d{12}\b", "[account]", value)
    return value[:10_000]


def parse_expiry(value: object) -> dt.datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ApprovalError("invalid_expiry")
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ApprovalError("invalid_expiry") from exc


def validate_approval(approval: dict, *, score_digest: str, instance_id: str) -> None:
    allowed = {"schema_version", "approval_id", "score_id", "score_digest", "action", "instance_id", "release_directory", "image_digest", "evidence", "expires_at", "signature"}
    if set(approval) != allowed or approval.get("schema_version") != 1:
        raise ApprovalError("unexpected_approval_shape")
    try:
        uuid.UUID(str(approval["approval_id"]))
    except (ValueError, TypeError, KeyError) as exc:
        raise ApprovalError("invalid_approval_id") from exc
    if approval.get("score_id") != "via-cloud-launch" or approval.get("score_digest") != score_digest:
        raise ApprovalError("score_identity_mismatch")
    if approval.get("action") != "activate" or approval.get("instance_id") != instance_id:
        raise ApprovalError("action_or_target_mismatch")
    if not isinstance(approval.get("release_directory"), str) or not RELEASE_RE.fullmatch(approval["release_directory"]):
        raise ApprovalError("invalid_release_directory")
    if not isinstance(approval.get("image_digest"), str) or not IMAGE_RE.fullmatch(approval["image_digest"]):
        raise ApprovalError("invalid_image_digest")
    if parse_expiry(approval.get("expires_at")) <= dt.datetime.now(dt.timezone.utc):
        raise ApprovalError("approval_expired")
    evidence = approval.get("evidence")
    if not isinstance(evidence, list) or len(evidence) != 3:
        raise ApprovalError("invalid_evidence_set")
    kinds = set()
    for item in evidence:
        if not isinstance(item, dict) or set(item) != {"kind", "sha256"} or item.get("kind") not in REQUIRED_EVIDENCE or not isinstance(item.get("sha256"), str) or not SHA_RE.fullmatch(item["sha256"]):
            raise ApprovalError("invalid_evidence")
        kinds.add(item["kind"])
    if kinds != REQUIRED_EVIDENCE:
        raise ApprovalError("missing_required_evidence")
    if not isinstance(approval.get("signature"), str):
        raise ApprovalError("missing_signature")


class Deployment:
    def __init__(self, *, clients=None, sleeper=time.sleep):
        clients = clients or {}
        if boto3 is None and not clients:
            raise RuntimeError("boto3 is required in the Lambda runtime")
        factory = boto3.client if boto3 is not None else None
        self.s3 = clients.get("s3") or factory("s3")
        self.kms = clients.get("kms") or factory("kms")
        self.ssm = clients.get("ssm") or factory("ssm")
        self.ddb = clients.get("dynamodb") or factory("dynamodb")
        self.sleeper = sleeper

    def read_approval(self, bucket: str, key: str) -> dict:
        if not isinstance(key, str) or not key.startswith(APPROVAL_PREFIX) or PurePosixPath(key).parent.as_posix() != APPROVAL_PREFIX.rstrip("/") or not re.fullmatch(APPROVAL_PREFIX + r"[0-9a-f-]{36}\.json", key):
            raise ApprovalError("approval_key_not_allowed")
        try:
            raw = self.s3.get_object(Bucket=bucket, Key=key)["Body"].read(65_537)
            if len(raw) > 65_536:
                raise ApprovalError("approval_oversize")
            return json.loads(raw)
        except (KeyError, TypeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ApprovalError("approval_unreadable") from exc

    def verify(self, approval: dict, key_arn: str) -> None:
        try:
            signature = base64.b64decode(approval["signature"], validate=True)
            result = self.kms.verify(KeyId=key_arn, Message=canonical_message(approval), Signature=signature, SigningAlgorithm="RSASSA_PSS_SHA_256", MessageType="RAW")
        except Exception as exc:
            raise ApprovalError("approval_signature_unverified") from exc
        if result.get("SignatureValid") is not True:
            raise ApprovalError("approval_signature_unverified")

    def status(self, table: str, approval_id: str) -> dict | None:
        row = self.ddb.get_item(TableName=table, Key={"approval_id": {"S": approval_id}}, ConsistentRead=True).get("Item")
        return row

    def send(self, approval: dict, *, table: str, document_name: str, document_version: str) -> str:
        prior = self.status(table, approval["approval_id"])
        if prior and prior.get("command_id", {}).get("S"):
            return prior["command_id"]["S"]
        if prior:
            # A crash after reserving but before persisting the SSM receipt is
            # an uncertain side effect. Fail closed rather than risk a second
            # production activation for the same approval.
            raise ApprovalError("dispatch_reservation_requires_recovery")
        manifest_hash = hashlib.sha256(canonical_message(approval)).hexdigest()
        self.ddb.put_item(
            TableName=table,
            Item={"approval_id": {"S": approval["approval_id"]}, "status": {"S": "dispatching"}, "manifest_sha256": {"S": manifest_hash}, "created_at": {"S": now()}},
            ConditionExpression="attribute_not_exists(approval_id)",
        )
        command = self.ssm.send_command(
            DocumentName=document_name,
            DocumentVersion=document_version,
            InstanceIds=[approval["instance_id"]],
            Parameters={"ReleaseDirectory": [approval["release_directory"]], "ImageDigest": [approval["image_digest"]]},
            TimeoutSeconds=240,
            Comment="BitCadence Score approval " + approval["approval_id"],
        )
        command_id = command.get("Command", {}).get("CommandId")
        if not isinstance(command_id, str) or not re.fullmatch(r"[0-9a-f-]{36}", command_id):
            raise ApprovalError("invalid_ssm_command_receipt")
        try:
            self.ddb.update_item(
                TableName=table,
                Key={"approval_id": {"S": approval["approval_id"]}},
                UpdateExpression="SET command_id = :command, #state = :sent",
                ConditionExpression="#state = :dispatching AND manifest_sha256 = :manifest",
                ExpressionAttributeNames={"#state": "status"},
                ExpressionAttributeValues={":command": {"S": command_id}, ":sent": {"S": "sent"}, ":dispatching": {"S": "dispatching"}, ":manifest": {"S": manifest_hash}},
            )
        except Exception as exc:
            raise ApprovalError("command_sent_persistence_uncertain") from exc
        return command_id

    def command_status(self, command_id: str, instance_id: str) -> dict:
        try:
            result = self.ssm.get_command_invocation(CommandId=command_id, InstanceId=instance_id, PluginName="activateReviewedRelease")
            return {"status": result.get("Status", "Unknown"), "response_code": result.get("ResponseCode")}
        except Exception:
            return {"status": "Pending", "response_code": None}

    def receipt(self, bucket: str, approval: dict, command_id: str, status: dict) -> str:
        key = RECEIPT_PREFIX + approval["approval_id"] + ".json"
        report = sanitize({"schema_version": 1, "recorded_at": now(), "approval_id": approval["approval_id"], "score_digest": approval["score_digest"], "action": approval["action"], "release_directory": approval["release_directory"], "image_digest": approval["image_digest"], "command_id": command_id, "command_status": status})
        self.s3.put_object(Bucket=bucket, Key=key, Body=json.dumps(report, separators=(",", ":")).encode("utf-8"), ContentType="application/json", ServerSideEncryption="AES256")
        return key


def handler(event, context):
    del context
    if not isinstance(event, dict) or set(event) != {"approval_key"}:
        raise ApprovalError("only_approval_key_event_is_allowed")
    deployment = Deployment()
    bucket = os.environ["EVIDENCE_BUCKET"]
    approval = deployment.read_approval(bucket, event["approval_key"])
    validate_approval(approval, score_digest=os.environ["SCORE_DIGEST"], instance_id=os.environ["INSTANCE_ID"])
    deployment.verify(approval, os.environ["APPROVAL_KEY_ARN"])
    command_id = deployment.send(approval, table=os.environ["DEPLOYMENT_TABLE"], document_name=os.environ["SSM_DOCUMENT_NAME"], document_version=os.environ["SSM_DOCUMENT_VERSION"])
    status = deployment.command_status(command_id, approval["instance_id"])
    receipt_key = deployment.receipt(bucket, approval, command_id, status)
    return {"statusCode": 202 if status["status"] in {"Pending", "InProgress", "Delayed"} else 200, "approval_id": approval["approval_id"], "command_status": status["status"], "receipt_key": receipt_key}
