data "aws_caller_identity" "current" {}

data "archive_file" "audit_runner" {
  type        = "zip"
  source_file = "${path.module}/audit_handler.py"
  output_path = "${path.module}/build/via_score_audit_runner.zip"
}

data "aws_iam_policy_document" "assume_lambda" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "audit" {
  name               = "${var.name}-audit-runner"
  assume_role_policy = data.aws_iam_policy_document.assume_lambda.json
}

data "aws_iam_policy_document" "audit" {
  statement {
    sid     = "WriteOnlySanitizedAuditArtifacts"
    actions = ["s3:PutObject"]
    resources = [
      "arn:aws:s3:::${var.evidence_bucket}/score-audits/*",
    ]
  }

  statement {
    sid       = "ListOnlyBackupPrefix"
    actions   = ["s3:ListBucket"]
    resources = ["arn:aws:s3:::${var.evidence_bucket}"]
    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["backups/*"]
    }
  }

  # These inventory APIs do not support resource-level authorization. The handler
  # pins every request to the one configured VIA resource and exposes no command
  # or URL input from EventBridge.
  statement {
    sid = "FixedReadOnlyInventory"
    actions = [
      "ec2:DescribeInstances",
      "ssm:DescribeInstanceInformation",
      "cloudwatch:DescribeAlarms",
      "sts:GetCallerIdentity",
    ]
    resources = ["*"]
  }

  statement {
    sid       = "ReadOnlyViaStack"
    actions   = ["cloudformation:ListStackResources"]
    resources = ["arn:aws:cloudformation:${var.region}:${data.aws_caller_identity.current.account_id}:stack/${var.via_stack_name}/*"]
  }

  statement {
    sid = "WriteOwnLogs"
    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]
    resources = ["${aws_cloudwatch_log_group.audit.arn}:*"]
  }
}

resource "aws_iam_role_policy" "audit" {
  name   = "${var.name}-audit-runner-policy"
  role   = aws_iam_role.audit.id
  policy = data.aws_iam_policy_document.audit.json
}

resource "aws_cloudwatch_log_group" "audit" {
  name              = "/aws/lambda/${var.name}-audit-runner"
  retention_in_days = var.log_retention_days
}

resource "aws_lambda_function" "audit" {
  function_name    = "${var.name}-audit-runner"
  description      = "BitCadence Score's fixed, read-only VIA cloud inventory runner."
  role             = aws_iam_role.audit.arn
  handler          = "audit_handler.handler"
  runtime          = "python3.12"
  filename         = data.archive_file.audit_runner.output_path
  source_code_hash = data.archive_file.audit_runner.output_base64sha256
  timeout          = 90
  memory_size      = 256

  environment {
    variables = {
      EVIDENCE_BUCKET = var.evidence_bucket
      INSTANCE_ID     = var.via_instance_id
      PUBLIC_API_BASE = var.public_api_base
      STACK_NAME      = var.via_stack_name
    }
  }

  depends_on = [aws_cloudwatch_log_group.audit]
}

resource "aws_cloudwatch_event_rule" "audit" {
  name                = "${var.name}-audit-schedule"
  description         = "Schedules only the fixed, read-only VIA Score audit."
  schedule_expression = var.schedule_expression
}

resource "aws_cloudwatch_event_target" "audit" {
  rule = aws_cloudwatch_event_rule.audit.name
  arn  = aws_lambda_function.audit.arn
  input = jsonencode({
    score_id      = "via-cloud-launch",
    score_task_id = "G01",
    mode          = "read_only_inventory",
  })
}

resource "aws_lambda_permission" "audit_eventbridge" {
  statement_id  = "AllowEventBridgeScheduledAudit"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.audit.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.audit.arn
}
