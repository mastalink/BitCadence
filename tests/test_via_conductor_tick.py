import importlib.util
import json
from pathlib import Path


MODULE = Path(__file__).parents[1] / "infra" / "aws" / "via_runner" / "tick_handler.py"
SPEC = importlib.util.spec_from_file_location("via_conductor_tick", MODULE)
tick_handler = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(tick_handler)


def test_tick_runs_only_the_fixed_recovery_to_dispatch_sequence():
    seen = []
    tick = tick_handler.Tick({name: lambda name=name: seen.append(name) or {"status": "ok"} for name in tick_handler.STEPS})
    assert [row["step"] for row in tick.run()["steps"]] == list(tick_handler.STEPS)
    assert seen == list(tick_handler.STEPS)


def test_event_cannot_supply_model_shell_url_or_release_capability():
    response = tick_handler.handler({"model": "unapproved", "shell": "danger", "url": "https://bad.example", "release": True}, object())
    body = json.loads(response["body"])
    assert response["statusCode"] == 202
    assert [row["result"]["status"] for row in body["steps"]] == ["not_installed"] * 5
    assert "unapproved" not in response["body"]


def test_tick_iam_policy_is_log_only_and_has_no_release_or_signing_power():
    terraform = (Path(__file__).parents[1] / "infra" / "aws" / "via_runner" / "main.tf").read_text(encoding="utf-8")
    section = terraform.split('data "aws_iam_policy_document" "tick" {', 1)[1].split('resource "aws_iam_role_policy" "tick" {', 1)[0]
    assert 'actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]' in section
    for forbidden in ("kms:Sign", "ssm:SendCommand", "lambda:InvokeFunction", "s3:", "dynamodb:"):
        assert forbidden not in section
