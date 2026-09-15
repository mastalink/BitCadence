"""Fixed, read-only VIA inventory Lambda.

This module deliberately has no generic command, URL, role-assumption, secret,
or deployment interface. EventBridge input is recorded as metadata only; it cannot
alter an AWS call, target, artifact destination, or public HTTP request.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import urllib.error
import urllib.request
import uuid

try:  # Lambda includes boto3; tests inject clients and do not need the SDK installed.
    import boto3
except ModuleNotFoundError:  # pragma: no cover - exercised only by offline harnesses
    boto3 = None


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def sanitize(value):
    """Do not persist credentials, account IDs, IP addresses, or raw errors."""
    if isinstance(value, dict):
        return {
            sanitize(str(key)): "[redacted]"
            if re.search(r"(?i)secret|password|token|credential", str(key))
            else sanitize(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [sanitize(item) for item in value]
    if not isinstance(value, str):
        return value
    value = re.sub(r"(?i)(?:AKIA|ASIA)[A-Z0-9]{16}", "[redacted-key]", value)
    value = re.sub(r"\b\d{12}\b", "[account]", value)
    value = re.sub(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", "[ip]", value)
    value = re.sub(r"(?i)(secret|password|token|credential)\s*[:=]\s*\S+", r"\1=[redacted]", value)
    return value[:20_000]


def failure(reason: str) -> dict:
    return {"checked_at": now(), "status": "failed", "reason": reason}


def public_json(url: str) -> dict:
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "VIA-Score-Audit/1"})
        with urllib.request.urlopen(request, timeout=20) as response:
            if response.geturl() != url:
                return failure("unexpected_redirect")
            raw = response.read(2_000_001)
            if len(raw) > 2_000_000:
                return failure("oversize_response")
            return {"checked_at": now(), "status": "success", "data": json.loads(raw)}
    except (OSError, ValueError, TypeError, urllib.error.URLError):
        return failure("public_request_or_schema_failed")


def public_summary(name: str, raw: dict) -> dict:
    if raw.get("status") != "success":
        return raw
    value = raw["data"]
    if name == "health":
        if not isinstance(value, dict) or not any(key in value for key in ("status", "ok")):
            return failure("unknown_health_schema")
        return {"checked_at": raw["checked_at"], "status": "success", "data": {key: value[key] for key in ("status", "ok", "version") if key in value and isinstance(value[key], (str, bool, int))}}
    rows = value if isinstance(value, list) else value.get("churches", value.get("results", value.get("items"))) if isinstance(value, dict) else None
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        return failure("unknown_church_response_schema")
    occurrences = [item for row in rows for item in row.get("occurrences", []) if isinstance(item, dict)]
    return {"checked_at": raw["checked_at"], "status": "success", "data": {
        "church_count": len(rows),
        "confession_occurrence_count": sum(item.get("service") == "confession" for item in occurrences),
        "churches_with_confession_occurrences": sum(any(item.get("service") == "confession" for item in row.get("occurrences", []) if isinstance(item, dict)) for row in rows),
        "note": "Counts reflect this fixed public query, not a county census or extraction-coverage claim.",
    }}


class Audit:
    def __init__(self, *, clients=None, opener=public_json):
        clients = clients or {}
        if boto3 is None and not clients:
            raise RuntimeError("boto3 is required in the Lambda runtime")
        factory = boto3.client if boto3 is not None else None
        self.ec2 = clients.get("ec2") or factory("ec2")
        self.ssm = clients.get("ssm") or factory("ssm")
        self.cf = clients.get("cloudformation") or factory("cloudformation")
        self.s3 = clients.get("s3") or factory("s3")
        self.cloudwatch = clients.get("cloudwatch") or factory("cloudwatch")
        self.sts = clients.get("sts") or factory("sts")
        self.opener = opener

    def aws(self, name: str, operation) -> dict:
        try:
            return {"checked_at": now(), "status": "success", "data": sanitize(operation())}
        except Exception:  # Provider error details can contain sensitive metadata.
            return failure(name + "_unavailable_or_denied")

    def collect(self, *, instance_id: str, stack_name: str, bucket: str, api_base: str) -> dict:
        api_base = api_base.rstrip("/")
        checks = {
            "identity": self.aws("identity", lambda: {"principal_type": "assumed_role" if ":assumed-role/" in self.sts.get_caller_identity().get("Arn", "") else "unexpected"}),
            "instance": self.aws("instance", lambda: self._instance(instance_id)),
            "ssm": self.aws("ssm", lambda: self._ssm(instance_id)),
            "stack": self.aws("stack", lambda: self._stack(stack_name)),
            "backups": self.aws("backups", lambda: self._backups(bucket)),
            "alarms": self.aws("alarms", self._alarms),
            "public_health": public_summary("health", self.opener(api_base + "/health")),
            "lorain_confession": public_summary("lorain", self.opener(api_base + "/v1/churches?q=Lorain&service=confession&days=14&radius=40")),
        }
        issues = [name + ":" + check["status"] for name, check in checks.items() if check["status"] != "success"]
        if checks["identity"].get("data", {}).get("principal_type") != "assumed_role":
            issues.append("runner_identity_not_assumed_role")
        return sanitize({
            "schema_version": 1,
            "collected_at": now(),
            "mode": "read_only_inventory",
            "audit_complete": not issues,
            "production_readiness": "not_determined",
            "issues": issues,
            "checks": checks,
            "unknowns": ["host_release_and_migration", "database_restore_verified", "collection_cloud_end_to_end_acceptance", "android_device_acceptance"],
            "limits": ["No secret, environment, log, SSM command, IAM, deploy, service restart, or source-data mutation access.", "Backup inventory is a bounded first page; it is not restoration proof."],
        })

    def _instance(self, instance_id: str) -> dict:
        reservations = self.ec2.describe_instances(InstanceIds=[instance_id]).get("Reservations", [])
        instances = [item for reservation in reservations for item in reservation.get("Instances", []) if item.get("InstanceId") == instance_id]
        return {"matched_instance_count": len(instances), "instances": [{"state": item.get("State", {}).get("Name"), "type": item.get("InstanceType"), "metadata_tokens": item.get("MetadataOptions", {}).get("HttpTokens")} for item in instances]}

    def _ssm(self, instance_id: str) -> dict:
        info = self.ssm.describe_instance_information(Filters=[{"Key": "InstanceIds", "Values": [instance_id]}]).get("InstanceInformationList", [])
        return {"instances": [{key: item.get(key) for key in ("PingStatus", "PlatformName", "AgentVersion")} for item in info if item.get("InstanceId") == instance_id]}

    def _stack(self, stack_name: str) -> dict:
        rows = self.cf.list_stack_resources(StackName=stack_name).get("StackResourceSummaries", [])
        return {"resources": [{key: row.get(key) for key in ("LogicalResourceId", "ResourceType", "ResourceStatus")} for row in rows]}

    def _backups(self, bucket: str) -> dict:
        rows = self.s3.list_objects_v2(Bucket=bucket, Prefix="backups/", MaxKeys=1000)
        contents = rows.get("Contents", [])
        latest = max(contents, key=lambda item: item.get("LastModified", ""), default={})
        return {"inspected_object_count": len(contents), "listing_truncated": bool(rows.get("IsTruncated")), "latest_inspected_backup_modified": latest.get("LastModified"), "latest_inspected_backup_bytes": latest.get("Size"), "restore_verified": False}

    def _alarms(self) -> dict:
        rows = self.cloudwatch.describe_alarms(AlarmNamePrefix="via", MaxRecords=100)
        alarms = rows.get("MetricAlarms", []) + rows.get("CompositeAlarms", [])
        return {"alarms": [{key: alarm.get(key) for key in ("AlarmName", "StateValue", "StateUpdatedTimestamp")} for alarm in alarms], "listing_truncated": bool(rows.get("NextToken"))}


def handler(event, context):
    # Invocation input remains non-authoritative by design; no event value controls work.
    del event
    audit = Audit()
    bucket = os.environ["EVIDENCE_BUCKET"]
    report = audit.collect(instance_id=os.environ["INSTANCE_ID"], stack_name=os.environ["STACK_NAME"], bucket=bucket, api_base=os.environ["PUBLIC_API_BASE"])
    key = "score-audits/{:%Y/%m/%d}/{}.json".format(dt.datetime.now(dt.timezone.utc), uuid.uuid4())
    body = json.dumps(report, separators=(",", ":"), default=str).encode("utf-8")
    audit.s3.put_object(Bucket=bucket, Key=key, Body=body, ContentType="application/json", ServerSideEncryption="AES256")
    return {"statusCode": 200, "artifact_key": key, "audit_complete": report["audit_complete"]}
