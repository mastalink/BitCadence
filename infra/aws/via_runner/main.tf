data "aws_caller_identity" "current" {}

data "archive_file" "audit_runner" {
  type        = "zip"
  source_file = "${path.module}/audit_handler.py"
  output_path = "${path.module}/build/via_score_audit_runner.zip"
}

data "archive_file" "deploy_runner" {
  type        = "zip"
  source_file = "${path.module}/deploy_handler.py"
  output_path = "${path.module}/build/via_score_deploy_runner.zip"
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

# ── Release lane ───────────────────────────────────────────────────────────
# A release requires a KMS-signed, score-digest-bound manifest. The Lambda has
# no event schedule or public invoke permission: a future authenticated Score
# conductor invokes it after its evidence/review adapter creates that manifest.

resource "aws_kms_key" "release_approval" {
  description              = "Signs explicit VIA Score release approvals."
  customer_master_key_spec = "RSA_2048"
  key_usage                = "SIGN_VERIFY"
  deletion_window_in_days  = 30
  enable_key_rotation      = false
}

resource "aws_kms_alias" "release_approval" {
  name          = "alias/${var.name}-release-approval"
  target_key_id = aws_kms_key.release_approval.key_id
}

resource "aws_dynamodb_table" "deployments" {
  name         = "${var.name}-deployment-receipts"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "approval_id"

  attribute {
    name = "approval_id"
    type = "S"
  }

  server_side_encryption { enabled = true }
  point_in_time_recovery { enabled = true }
}

resource "aws_ssm_document" "activate_release" {
  name            = "${var.name}-activate-release"
  document_type   = "Command"
  document_format = "JSON"
  content = jsonencode({
    schemaVersion = "2.2"
    description   = "Fixed VIA Score release activation. No shell command is accepted from callers."
    parameters = {
      ReleaseDirectory = {
        type           = "String"
        allowedPattern = "^/opt/via/releases/via-[A-Za-z0-9._-]+$"
      }
      ImageDigest = {
        type           = "String"
        allowedPattern = "^sha256:[a-f0-9]{64}$"
      }
    }
    mainSteps = [{
      action = "aws:runShellScript"
      name   = "activateReviewedRelease"
      inputs = {
        timeoutSeconds = "240"
        runCommand = [
          "set -euo pipefail",
          "exec /opt/via/activate-release.sh activate '{{ ReleaseDirectory }}' '{{ ImageDigest }}'",
        ]
      }
    }]
  })
}

resource "aws_iam_role" "deploy" {
  name               = "${var.name}-deploy-runner"
  assume_role_policy = data.aws_iam_policy_document.assume_lambda.json
}

data "aws_iam_policy_document" "deploy" {
  statement {
    sid       = "ReadOnlySignedApprovalPrefix"
    actions   = ["s3:GetObject"]
    resources = ["arn:aws:s3:::${var.evidence_bucket}/score-deploy-approvals/pending/*"]
  }

  statement {
    sid       = "WriteOnlyDeploymentReceipts"
    actions   = ["s3:PutObject"]
    resources = ["arn:aws:s3:::${var.evidence_bucket}/score-deploy-receipts/*"]
  }

  statement {
    sid       = "ReplaySafeReceiptState"
    actions   = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem"]
    resources = [aws_dynamodb_table.deployments.arn]
  }

  statement {
    sid       = "VerifyButNeverSignApprovals"
    actions   = ["kms:Verify"]
    resources = [aws_kms_key.release_approval.arn]
  }

  statement {
    sid       = "RunOnlyFixedActivationDocument"
    actions   = ["ssm:SendCommand"]
    resources = [
      aws_ssm_document.activate_release.arn,
      "arn:aws:ec2:${var.region}:${data.aws_caller_identity.current.account_id}:instance/${var.via_instance_id}",
    ]
  }

  # AWS does not expose a resource type or condition key that scopes this API
  # to the command ID that the Lambda just created. The handler retains only
  # status/response code and never stores command output.
  statement {
    sid       = "ReadOwnCommandStatusOnly"
    actions   = ["ssm:GetCommandInvocation"]
    resources = ["*"]
  }

  statement {
    sid     = "WriteOwnLogs"
    actions = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.deploy.arn}:*"]
  }
}

resource "aws_iam_role_policy" "deploy" {
  name   = "${var.name}-deploy-runner-policy"
  role   = aws_iam_role.deploy.id
  policy = data.aws_iam_policy_document.deploy.json
}

resource "aws_cloudwatch_log_group" "deploy" {
  name              = "/aws/lambda/${var.name}-deploy-runner"
  retention_in_days = var.log_retention_days
}

resource "aws_lambda_function" "deploy" {
  function_name    = "${var.name}-deploy-runner"
  description      = "Signed-approval-only VIA release adapter for BitCadence Score."
  role             = aws_iam_role.deploy.arn
  handler          = "deploy_handler.handler"
  runtime          = "python3.12"
  filename         = data.archive_file.deploy_runner.output_path
  source_code_hash = data.archive_file.deploy_runner.output_base64sha256
  timeout          = 120
  memory_size      = 256

  environment {
    variables = {
      APPROVAL_KEY_ARN    = aws_kms_key.release_approval.arn
      DEPLOYMENT_TABLE    = aws_dynamodb_table.deployments.name
      EVIDENCE_BUCKET     = var.evidence_bucket
      INSTANCE_ID         = var.via_instance_id
      SCORE_DIGEST        = var.score_digest
      SSM_DOCUMENT_NAME   = aws_ssm_document.activate_release.name
      SSM_DOCUMENT_VERSION = aws_ssm_document.activate_release.document_version
    }
  }

  depends_on = [aws_cloudwatch_log_group.deploy]
}
