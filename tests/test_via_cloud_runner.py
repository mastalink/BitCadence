"""Offline safety tests for the deployed, fixed-function Score audit runner."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace


MODULE = Path(__file__).parents[1] / "infra" / "aws" / "via_runner" / "audit_handler.py"
SPEC = importlib.util.spec_from_file_location("via_cloud_runner", MODULE)
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


class Client:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def __getattr__(self, name):
        def call(**kwargs):
            self.calls.append((name, kwargs))
            return self.response
        return call


def test_collect_is_fixed_read_only_and_does_not_expose_raw_values():
    ec2 = Client({"Reservations": [{"Instances": [{"InstanceId": "i-fixed", "State": {"Name": "running"}, "InstanceType": "t3.medium", "MetadataOptions": {"HttpTokens": "required"}}]}]})
    ssm = Client({"InstanceInformationList": [{"InstanceId": "i-fixed", "PingStatus": "Online", "PlatformName": "Amazon Linux", "AgentVersion": "1"}]})
    cf = Client({"StackResourceSummaries": []})
    s3 = Client({"Contents": []})
    cw = Client({"MetricAlarms": [], "CompositeAlarms": []})
    sts = Client({"Arn": "arn:aws:sts::123456789012:assumed-role/via-score/audit"})
    public = lambda url: {"checked_at": "now", "status": "success", "data": {"status": "ok"} if url.endswith("/health") else []}
    audit = runner.Audit(clients={"ec2": ec2, "ssm": ssm, "cloudformation": cf, "s3": s3, "cloudwatch": cw, "sts": sts}, opener=public)
    report = audit.collect(instance_id="i-fixed", stack_name="via-pilot", bucket="evidence", api_base="https://api.example")
    assert report["audit_complete"] is True
    assert ec2.calls == [("describe_instances", {"InstanceIds": ["i-fixed"]})]
    assert ssm.calls == [("describe_instance_information", {"Filters": [{"Key": "InstanceIds", "Values": ["i-fixed"]}]})]
    assert s3.calls == [("list_objects_v2", {"Bucket": "evidence", "Prefix": "backups/", "MaxKeys": 1000})]
    assert "123456789012" not in json.dumps(report)


def test_public_failures_and_sanitizer_remain_explicit():
    assert runner.public_summary("health", {"checked_at": "now", "status": "success", "data": {}})["status"] == "failed"
    assert runner.sanitize({"token": "private", "note": "AKIAABCDEFGHIJKLMNOP 123456789012 10.0.0.1 password=x"}) == {"token": "[redacted]", "note": "[redacted-key] [account] [ip] password=[redacted]"}
