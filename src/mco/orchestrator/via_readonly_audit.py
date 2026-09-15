"""Bounded VIA inventory collector. No deployment or general command interface.

The optional SSM call executes ONLY the diagnostic constant below. Audit completion
means observations were collected, never that production is ready or approved.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
import re
import subprocess
import time
import urllib.request

PROFILE = "batoncadence"
REGION = "us-east-1"
INSTANCE = "i-03450a1e08a8c4803"
BUCKET = "via-evidence-314086896527-us-east-1"
BASE = "https://d3ao3uhnewexzf.cloudfront.net/api"
URLS = {
    "public_health": BASE + "/health",
    "lorain_confession": BASE + "/v1/churches?q=Lorain&service=confession&days=14&radius=40",
}
COMMANDS = {
    "identity": ("sts", "get-caller-identity"),
    "instance": ("ec2", "describe-instances", "--instance-ids", INSTANCE),
    "ssm": ("ssm", "describe-instance-information", "--filters", "Key=InstanceIds,Values=" + INSTANCE),
    "build_projects": ("codebuild", "list-projects"),
    "stack": ("cloudformation", "list-stack-resources", "--stack-name", "via-pilot"),
    "backups": ("s3api", "list-objects-v2", "--bucket", BUCKET, "--prefix", "backups/", "--max-items", "1000"),
    "alarms": ("cloudwatch", "describe-alarms", "--alarm-name-prefix", "via", "--max-items", "100"),
}

# Fixed read-only inspection, not a shell command supplied by a job or model.
DIAGNOSTIC = """python3 - <<'VIA_READONLY_DIAGNOSTIC'
import json, os, subprocess
result = {}
try:
    with open('/opt/via/current-deployment.json') as f:
        deployment = json.load(f)
    result['deployment'] = {k: deployment[k] for k in ('commit', 'git_sha', 'version', 'image', 'image_digest', 'deployed_at') if k in deployment and isinstance(deployment[k], (str, int))}
except Exception:
    result['deployment'] = {'state': 'unavailable'}
try:
    result['current_link'] = os.readlink('/opt/via/current')
except OSError:
    result['current_link'] = 'unavailable'
for key, command in [('containers', ['docker', 'ps', '--format', '{{.Names}}|{{.Image}}|{{.Status}}']), ('backup_timer', ['systemctl', 'is-active', 'via-backup.timer'])]:
    try:
        p = subprocess.run(command, capture_output=True, text=True, timeout=15)
        result[key] = p.stdout[:16000] if p.returncode == 0 else 'unavailable'
    except Exception:
        result[key] = 'unavailable'
print(json.dumps(result))
VIA_READONLY_DIAGNOSTIC"""


def utc_now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def sanitize(value):
    """Defense in depth; raw service responses/stderr are never artifact fields."""
    if isinstance(value, dict):
        return {sanitize(str(k)): '[redacted]' if str(k) != 'metadata_tokens' and re.search(r'(?i)secret|password|token|credential', str(k)) else sanitize(v) for k, v in value.items()}
    if isinstance(value, list):
        return [sanitize(v) for v in value]
    if not isinstance(value, str):
        return value
    value = re.sub(r"(?i)(?:AKIA|ASIA)[A-Z0-9]{16}", "[redacted-key]", value)
    value = re.sub(r"\b\d{12}\b", "[account]", value)
    value = re.sub(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", "[ip]", value)
    value = re.sub(r"(?i)(secret|password|token|credential)\s*[:=]\s*\S+", r"\1=[redacted]", value)
    return value[:20000]


class Collector:
    def __init__(self, *, runner=subprocess.run, opener=urllib.request.urlopen, sleeper=time.sleep):
        self.runner = runner
        self.opener = opener
        self.sleeper = sleeper

    def _execute(self, args):
        # Private method; external API only accepts fixed observation names.
        argv = ["aws", *args, "--profile", PROFILE, "--region", REGION, "--output", "json", "--no-cli-pager"]
        try:
            p = self.runner(argv, capture_output=True, text=True, timeout=60, shell=False)
            if p.returncode:
                denied = any(s in p.stderr.lower() for s in ("accessdenied", "unauthorized", "forbidden"))
                return {"status": "denied" if denied else "failed", "reason": "aws_command_failed"}
            data = json.loads(p.stdout)
            if not isinstance(data, dict):
                raise ValueError("Expected object response")
            return {"status": "success", "data": data}
        except (OSError, subprocess.TimeoutExpired, ValueError):
            return {"status": "failed", "reason": "aws_unavailable_timeout_or_invalid_json"}

    def aws(self, name):
        if name not in COMMANDS:
            raise ValueError("Not an allowlisted observation")
        result = self._execute(COMMANDS[name])
        if result["status"] == "success":
            try:
                result["data"] = summarize(name, result["data"])
            except (KeyError, TypeError, ValueError, IndexError):
                result = {"status": "failed", "reason": "unexpected_response_shape"}
        return {"checked_at": utc_now(), **sanitize(result)}

    def public(self, name):
        if name not in URLS:
            raise ValueError("Not an allowlisted URL")
        try:
            request = urllib.request.Request(URLS[name], headers={"User-Agent": "VIA-readonly-audit/1"})
            with self.opener(request, timeout=30) as response:
                if response.geturl() != URLS[name]:
                    raise ValueError("Unexpected redirect")
                raw = response.read(2_000_001)
                if len(raw) > 2_000_000:
                    raise ValueError("Oversize response")
                data = json.loads(raw)
            summary = public_summary(name, data)
            result = {"status": "success", "data": summary}
        except (OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError):
            result = {"status": "failed", "reason": "public_request_or_schema_failed"}
        return {"checked_at": utc_now(), **sanitize(result)}

    def diagnostic(self):
        sent = self._execute(("ssm", "send-command", "--instance-ids", INSTANCE,
                              "--document-name", "AWS-RunShellScript", "--timeout-seconds", "120",
                              "--parameters", json.dumps({"commands": [DIAGNOSTIC]})))
        if sent["status"] != "success":
            return {"checked_at": utc_now(), **sent}
        command_id = sent.get("data", {}).get("Command", {}).get("CommandId", "")
        if not re.fullmatch(r"[0-9a-fA-F-]{36}", command_id):
            return {"checked_at": utc_now(), "status": "failed", "reason": "invalid_command_receipt"}
        for attempt in range(8):
            result = self._execute(("ssm", "get-command-invocation", "--command-id", command_id, "--instance-id", INSTANCE))
            data = result.get("data", {})
            if result["status"] == "success" and data.get("Status") == "Success":
                try:
                    payload = json.loads(data.get("StandardOutputContent", ""))
                    if not isinstance(payload, dict):
                        raise ValueError("Invalid diagnostic payload")
                    allowed = {k: payload[k] for k in ("deployment", "current_link", "containers", "backup_timer") if k in payload}
                    if isinstance(allowed.get("deployment"), dict):
                        allowed["deployment"] = {k: v for k, v in allowed["deployment"].items() if k in ("state", "commit", "git_sha", "version", "image", "image_digest", "deployed_at") and isinstance(v, (str, int))}
                    if len(allowed) != 4 or any(v == "unavailable" or v == {"state": "unavailable"} for v in allowed.values()):
                        return {"checked_at": utc_now(), "status": "partial", "data": sanitize(allowed), "reason": "host_observations_unavailable"}
                    return {"checked_at": utc_now(), "status": "success", "data": sanitize(allowed)}
                except (ValueError, TypeError):
                    break
            if result["status"] == "denied":
                return {"checked_at": utc_now(), "status": "denied", "reason": "diagnostic_receipt_access_denied"}
            if data.get("Status") in ("Failed", "Cancelled", "TimedOut"):
                break
            if attempt < 7:
                self.sleeper(2)
        return {"checked_at": utc_now(), "status": "failed", "reason": "diagnostic_not_completed_or_invalid"}

    def collect(self, *, include_ssm_diagnostic=False):
        checks = {name: self.aws(name) for name in COMMANDS}
        checks.update({name: self.public(name) for name in URLS})
        if include_ssm_diagnostic:
            checks["host_diagnostic"] = self.diagnostic()
        issues = [name + ":" + c["status"] for name, c in checks.items() if c["status"] != "success"]
        principal = checks["identity"].get("data", {}).get("principal_type")
        if principal == "root":
            issues.append("root_principal_not_suitable_for_autonomous_execution")
        return {"schema_version": 1, "collected_at": utc_now(), "mode": "read_only_inventory",
                "audit_complete": not any(c["status"] != "success" for c in checks.values()),
                "production_readiness": "not_determined", "issues": issues, "checks": checks,
                "unknowns": ([] if include_ssm_diagnostic else ["host_deployment_not_queried"]) + ["database_migration_head", "database_restore_verified", "runner_least_privilege_verified",
                             "collector_cloud_end_to_end_acceptance", "android_device_acceptance"],
                "limits": ["Backup listing is bounded to 1000 keys; latest means latest in inspected page(s).",
                           "Alarm inventory is VIA-prefix only; no account-wide completeness claim.",
                           "No secret/environment/log reads; no deployment or service changes."]}


def summarize(name, data):
    if not isinstance(data, dict):
        raise ValueError("Expected object response")
    if name == "identity":
        arn = data.get("Arn", "")
        return {"principal_type": "root" if arn.endswith(":root") else "assumed_role" if ":assumed-role/" in arn else "iam_user" if ":user/" in arn else "unknown"}
    if name == "instance":
        instances = [i for r in data.get("Reservations", []) for i in r.get("Instances", []) if i.get("InstanceId") == INSTANCE]
        return {"matched_instance_count": len(instances), "instances": [{"state": i.get("State", {}).get("Name"), "type": i.get("InstanceType"), "launch_time": i.get("LaunchTime"), "instance_profile_name": i.get("IamInstanceProfile", {}).get("Arn", "").split("/")[-1], "metadata_tokens": i.get("MetadataOptions", {}).get("HttpTokens")} for i in instances]}
    if name == "ssm":
        return {"instances": [{k: i.get(k) for k in ("PingStatus", "LastPingDateTime", "AgentVersion", "PlatformName")} for i in data.get("InstanceInformationList", []) if i.get("InstanceId") == INSTANCE]}
    if name == "build_projects":
        return {"project_names": data.get("projects", [])}
    if name == "stack":
        return {"resources": [{k: r.get(k) for k in ("LogicalResourceId", "ResourceType", "ResourceStatus")} for r in data.get("StackResourceSummaries", [])]}
    if name == "backups":
        objects = data.get("Contents", [])
        latest = max(objects, key=lambda o: o.get("LastModified", ""), default={})
        return {"inspected_object_count": len(objects), "listing_truncated": bool(data.get("NextToken") or data.get("NextContinuationToken") or data.get("IsTruncated")), "latest_inspected_backup_modified": latest.get("LastModified"), "latest_inspected_backup_bytes": latest.get("Size"), "restore_verified": False}
    if name == "alarms":
        return {"alarms": [{k: a.get(k) for k in ("AlarmName", "StateValue", "StateUpdatedTimestamp")} for a in data.get("MetricAlarms", []) + data.get("CompositeAlarms", [])], "listing_truncated": bool(data.get("NextToken"))}
    raise ValueError("Not allowlisted")


def public_summary(name, data):
    if name == "public_health":
        if not isinstance(data, dict) or not any(k in data for k in ("status", "ok")):
            raise ValueError("Unknown health response shape")
        return {k: data[k] for k in ("status", "ok", "version") if k in data and isinstance(data[k], (str, bool, int))}
    rows = data if isinstance(data, list) else data.get("churches", data.get("results", data.get("items")))
    if not isinstance(rows, list):
        raise ValueError("Unknown church response shape")
    freshness = []
    # Preserve only recognized non-personal freshness fields, never full records.
    def walk(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key in ("last_success_at", "last_checked_at", "last_successful_fetch_at", "last_confirmed_at", "checked_at", "checkedAt", "confirmed_at") and isinstance(item, str):
                    freshness.append({"field": key, "timestamp": item})
                elif isinstance(item, (dict, list)):
                    walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)
    walk(rows)
    occurrence_fields = ("occurrences", "confession_occurrences", "services", "schedules")
    recognized = [r for r in rows if isinstance(r, dict) and any(k in r for k in occurrence_fields)]
    if any(not isinstance(r, dict) for r in rows):
        raise ValueError("Invalid church record")
    occurrences = [o for r in rows for o in r.get("occurrences", []) if isinstance(o, dict)]
    return {"church_count": len(rows), "churches_with_nonempty_schedule_field": sum(any(bool(r.get(k)) for k in occurrence_fields) for r in recognized) if recognized else None,
            "confession_occurrence_count": sum(o.get("service") == "confession" for o in occurrences),
            "churches_with_confession_occurrences": sum(any(isinstance(o, dict) and o.get("service") == "confession" for o in r.get("occurrences", [])) for r in rows),
            "response_shape": "array" if isinstance(data, list) else "object",
            "schedule_field_recognized_count": len(recognized), "freshness_observations": freshness,
            "note": "Counts reflect this public query, not a certified county census or extraction coverage."}


def collect(*, include_ssm_diagnostic=False, collector=None):
    """Collect the fixed inventory; importing this module makes no live calls."""
    return (collector or Collector()).collect(include_ssm_diagnostic=include_ssm_diagnostic)


def write_report(report, artifact_dir):
    """Write a sanitized report exclusively beneath the explicit artifact directory."""
    destination = Path(artifact_dir).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    path = destination / ("via-readonly-audit-" + dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + ".json")
    with path.open("x", encoding="utf-8") as handle:
        json.dump(sanitize(report), handle, indent=2)
        handle.write("\n")
    return path


def run_audit(artifact_dir, *, include_ssm_diagnostic=False, collector=None):
    """Parent job calls this after dispatch. Returns the report path."""
    return write_report(collect(include_ssm_diagnostic=include_ssm_diagnostic,
                                collector=collector), artifact_dir)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact-dir", required=True)
    parser.add_argument("--include-ssm-diagnostic", action="store_true")
    args = parser.parse_args()
    print(run_audit(args.artifact_dir, include_ssm_diagnostic=args.include_ssm_diagnostic))


if __name__ == "__main__":
    main()
