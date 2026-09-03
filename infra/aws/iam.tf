# ── Identity: who may do what, underground ──────────────────────────────────
# One role per job, least privilege. The task EXECUTION role pulls images and
# reads secrets on ECS's behalf; each task ROLE is what the code inside can do.

data "aws_caller_identity" "me" {}

data "aws_iam_policy_document" "ecs_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

# ── Execution role (shared) ─────────────────────────────────────────────────

resource "aws_iam_role" "exec" {
  name               = "${var.name}-ecs-exec"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
}

resource "aws_iam_role_policy_attachment" "exec_base" {
  role       = aws_iam_role.exec.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

data "aws_iam_policy_document" "exec_secrets" {
  statement {
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [aws_secretsmanager_secret.core.arn]
  }
  statement {
    actions   = ["kms:Decrypt"]
    resources = [aws_kms_key.evidence.arn]
  }
  statement {
    actions   = ["ssm:GetParameters"]
    resources = ["arn:aws:ssm:${var.region}:${data.aws_caller_identity.me.account_id}:parameter/${var.name}/*"]
  }
}

resource "aws_iam_role_policy" "exec_secrets" {
  role   = aws_iam_role.exec.id
  policy = data.aws_iam_policy_document.exec_secrets.json
}

# ── Gateway task role ───────────────────────────────────────────────────────
# The gateway forwards committed events to the evidence vault. Put only: it
# must never be able to read back and rewrite, and Object Lock stops deletes.

resource "aws_iam_role" "gateway" {
  name               = "${var.name}-gateway"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
}

data "aws_iam_policy_document" "gateway" {
  statement {
    sid       = "EvidenceAppendOnly"
    actions   = ["s3:PutObject", "s3:PutObjectRetention"]
    resources = ["${aws_s3_bucket.evidence.arn}/ledger/*"]
  }
  statement {
    actions   = ["kms:GenerateDataKey", "kms:Encrypt"]
    resources = [aws_kms_key.evidence.arn]
  }
  statement {
    actions   = ["elasticfilesystem:ClientMount", "elasticfilesystem:ClientWrite"]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "gateway" {
  role   = aws_iam_role.gateway.id
  policy = data.aws_iam_policy_document.gateway.json
}

# ── Worker task role ────────────────────────────────────────────────────────
# A cloud worker can invoke exactly one model and nothing else in AWS. Its
# authority over work comes from its BitCadence token, not from IAM.

resource "aws_iam_role" "worker" {
  name               = "${var.name}-worker"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
}

data "aws_iam_policy_document" "worker" {
  statement {
    actions = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream", "bedrock:Converse", "bedrock:ConverseStream"]
    resources = [
      "arn:aws:bedrock:${var.region}::foundation-model/*",
      "arn:aws:bedrock:${var.region}:${data.aws_caller_identity.me.account_id}:inference-profile/*",
      "arn:aws:bedrock:*::foundation-model/*", # cross-region inference profiles resolve to other regions
    ]
  }
}

resource "aws_iam_role_policy" "worker" {
  role   = aws_iam_role.worker.id
  policy = data.aws_iam_policy_document.worker.json
}

# ── Conductor task role ─────────────────────────────────────────────────────
# The conductor is the only thing allowed to start a fault, and the only
# thing that writes evidence bundles and the public status page.

resource "aws_iam_role" "conductor" {
  name               = "${var.name}-conductor"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
}

data "aws_iam_policy_document" "conductor" {
  statement {
    sid       = "EvidenceBundles"
    actions   = ["s3:PutObject", "s3:PutObjectRetention", "s3:GetObject", "s3:GetObjectRetention", "s3:ListBucket"]
    resources = [aws_s3_bucket.evidence.arn, "${aws_s3_bucket.evidence.arn}/*"]
  }
  statement {
    # The tamper pavilion PROVES the lock by attempting a delete and expecting
    # AccessDenied from Object Lock. The permission is granted so that the
    # denial comes from the lock, not from IAM - otherwise the test proves
    # nothing about retention.
    sid       = "TamperProbe"
    actions   = ["s3:DeleteObject", "s3:DeleteObjectVersion"]
    resources = ["${aws_s3_bucket.evidence.arn}/*"]
  }
  statement {
    sid       = "StatusPage"
    actions   = ["s3:PutObject", "s3:DeleteObject"]
    resources = ["${aws_s3_bucket.status.arn}/*"]
  }
  statement {
    actions   = ["kms:GenerateDataKey", "kms:Encrypt", "kms:Decrypt"]
    resources = [aws_kms_key.evidence.arn]
  }
  statement {
    sid       = "StartFaults"
    actions   = ["fis:StartExperiment", "fis:GetExperiment", "fis:StopExperiment", "fis:ListExperiments"]
    resources = ["*"]
  }
  statement {
    actions   = ["ecs:ListTasks", "ecs:DescribeTasks", "ecs:DescribeServices"]
    resources = ["*"]
  }
  statement {
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.alerts.arn]
  }
  statement {
    # First run only: the conductor registers each cloud worker through the
    # public API, receives the once-shown token, stores it here, and rolls
    # that worker's service so ECS injects it. No internal store access.
    sid       = "SeedWorkerTokens"
    actions   = ["secretsmanager:GetSecretValue", "secretsmanager:PutSecretValue"]
    resources = [aws_secretsmanager_secret.core.arn]
  }
  statement {
    # Workers: rolled after token seeding. Gateway: scaled to zero and back to
    # exercise the dead-man in P4.
    sid       = "RollServices"
    actions   = ["ecs:UpdateService"]
    resources = ["arn:aws:ecs:${var.region}:${data.aws_caller_identity.me.account_id}:service/${var.name}/*"]
  }
  statement {
    sid       = "ReadDeadmanAlarm"
    actions   = ["cloudwatch:DescribeAlarms", "cloudwatch:DescribeAlarmHistory"]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "conductor" {
  role   = aws_iam_role.conductor.id
  policy = data.aws_iam_policy_document.conductor.json
}

# ── Collector task role (writes metrics to AMP, optionally Dynatrace) ───────

resource "aws_iam_role" "collector" {
  name               = "${var.name}-collector"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
}

resource "aws_iam_role_policy_attachment" "collector_amp" {
  role       = aws_iam_role.collector.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonPrometheusRemoteWriteAccess"
}

# ── FIS role: the hand that pulls the plug ──────────────────────────────────

data "aws_iam_policy_document" "fis_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["fis.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "fis" {
  name               = "${var.name}-fis"
  assume_role_policy = data.aws_iam_policy_document.fis_assume.json
}

resource "aws_iam_role_policy_attachment" "fis_ecs" {
  role       = aws_iam_role.fis.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSFaultInjectionSimulatorECSAccess"
}

resource "aws_iam_role_policy_attachment" "fis_network" {
  role       = aws_iam_role.fis.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSFaultInjectionSimulatorNetworkAccess"
}

# ── Scheduler role: rings the bell every N hours ────────────────────────────

data "aws_iam_policy_document" "scheduler_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["scheduler.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "scheduler" {
  name               = "${var.name}-scheduler"
  assume_role_policy = data.aws_iam_policy_document.scheduler_assume.json
}

data "aws_iam_policy_document" "scheduler" {
  statement {
    actions   = ["ecs:RunTask"]
    resources = [aws_ecs_task_definition.conductor.arn]
  }
  statement {
    actions   = ["iam:PassRole"]
    resources = [aws_iam_role.exec.arn, aws_iam_role.conductor.arn]
  }
}

resource "aws_iam_role_policy" "scheduler" {
  role   = aws_iam_role.scheduler.id
  policy = data.aws_iam_policy_document.scheduler.json
}

# ── Grafana workspace role ──────────────────────────────────────────────────

data "aws_iam_policy_document" "grafana_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["grafana.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "grafana" {
  name               = "${var.name}-grafana"
  assume_role_policy = data.aws_iam_policy_document.grafana_assume.json
}

resource "aws_iam_role_policy_attachment" "grafana_amp" {
  role       = aws_iam_role.grafana.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonPrometheusQueryAccess"
}

resource "aws_iam_role_policy_attachment" "grafana_cw" {
  role       = aws_iam_role.grafana.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonGrafanaCloudWatchAccess"
}
