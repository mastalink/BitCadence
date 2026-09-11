"""Fail CI before applying a plan that exceeds the small lab's resource bounds."""
import json
import sys
from datetime import datetime, timezone


def resources(module):
    yield from module.get("resources", [])
    for child in module.get("child_modules", []):
        yield from resources(child)


def check(plan):
    planned = list(resources(plan["planned_values"]["root_module"]))
    nodes = [r["values"] for r in planned if r["type"] == "aws_instance"]
    assert len(nodes) == 3, "Exactly one hub and two spokes required"
    assert sorted(n["instance_type"] for n in nodes) == ["t3.micro", "t3.micro", "t3.small"]
    forbidden = {"aws_nat_gateway", "aws_db_instance", "aws_lb", "aws_grafana_workspace", "aws_prometheus_workspace", "aws_eip"}
    assert not any(r["type"] in forbidden for r in planned), "Unexpected always-on service"
    for node in nodes:
        assert node["metadata_options"][0]["http_tokens"] == "required"
        assert node["root_block_device"][0]["encrypted"] is True
        assert node["credit_specification"][0]["cpu_credits"] == "standard"
        assert node.get("key_name") in (None, ""), "SSH keys are not part of this lab"
    for resource in planned:
        if resource["type"] == "aws_security_group":
            for rule in resource["values"].get("ingress", []):
                assert not rule.get("cidr_blocks") and not rule.get("ipv6_cidr_blocks"), "No CIDR ingress permitted"
        if resource["type"] == "aws_scheduler_schedule":
            expression = resource["values"]["schedule_expression"]
            expires = datetime.fromisoformat(expression[3:-1]).replace(tzinfo=timezone.utc)
            assert 0 < (expires - datetime.now(timezone.utc)).total_seconds() <= 7300
    assert any(r["type"] == "aws_scheduler_schedule" for r in planned), "Automatic shutdown required"
    print("Plan accepted: 3 small nodes, private ingress, encrypted disks, IMDSv2, bounded shutdown")


if __name__ == "__main__":
    check(json.load(open(sys.argv[1], encoding="utf-8")))
