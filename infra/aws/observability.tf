# ── The greenbelt: where the city can be seen ───────────────────────────────
# The gateway exposes Prometheus metrics at /metrics (bearer-gated). A
# collector scrapes them into Amazon Managed Prometheus, Managed Grafana reads
# that, and - if a tenant was given - the same collector forwards the metrics
# to Dynatrace's OTLP endpoint. There is no OTel tracing yet; this is honest
# about what exists today (a metrics snapshot) and does not pretend otherwise.

resource "aws_prometheus_workspace" "city" {
  alias = var.name
}

locals {
  dynatrace_on = var.dynatrace_base_url != ""

  # HCL cannot put a heredoc inside a ternary, so each optional block is a
  # complete string first and selected second.
  dynatrace_exporter_block = <<-EOT
    otlphttp/dynatrace:
      endpoint: ${var.dynatrace_base_url}/api/v2/otlp
      headers:
        Authorization: "Api-Token $${env:DYNATRACE_API_TOKEN}"
  EOT

  dynatrace_pipeline_block = <<-EOT
    metrics/dynatrace:
      receivers: [prometheus]
      processors: [cumulativetodelta, batch]
      exporters: [otlphttp/dynatrace]
  EOT

  dynatrace_exporter = local.dynatrace_on ? local.dynatrace_exporter_block : ""
  dynatrace_pipeline = local.dynatrace_on ? local.dynatrace_pipeline_block : ""

  collector_config = <<-EOT
    extensions:
      sigv4auth:
        region: ${var.region}
        service: aps

    receivers:
      prometheus:
        config:
          scrape_configs:
            - job_name: bitcadence-gateway
              scrape_interval: 15s
              metrics_path: /metrics
              authorization:
                type: Bearer
                credentials: $${env:MCO_METRICS_TOKEN}
              static_configs:
                - targets: ["gateway.city.local:18789"]
                  labels:
                    service_name: bitcadence-gateway
                    deployment_environment: ${var.name}

    processors:
      batch: {}
      # Dynatrace wants delta temporality for OTLP metrics; AMP wants cumulative.
      cumulativetodelta: {}

    exporters:
      prometheusremotewrite:
        endpoint: ${aws_prometheus_workspace.city.prometheus_endpoint}api/v1/remote_write
        auth:
          authenticator: sigv4auth
    ${indent(6, local.dynatrace_exporter)}
    service:
      extensions: [sigv4auth]
      pipelines:
        metrics/amp:
          receivers: [prometheus]
          processors: [batch]
          exporters: [prometheusremotewrite]
    ${indent(8, local.dynatrace_pipeline)}
  EOT
}

resource "aws_ssm_parameter" "collector_config" {
  name  = "/${var.name}/collector-config"
  type  = "String"
  value = local.collector_config
}

resource "aws_ecs_task_definition" "collector" {
  family                   = "${var.name}-collector"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 256
  memory                   = 512
  execution_role_arn       = aws_iam_role.exec.arn
  task_role_arn            = aws_iam_role.collector.arn

  container_definitions = jsonencode([{
    name      = "collector"
    image     = "public.ecr.aws/aws-observability/aws-otel-collector:latest"
    essential = true
    secrets = concat(
      [
        { name = "AOT_CONFIG_CONTENT", valueFrom = aws_ssm_parameter.collector_config.arn },
        { name = "MCO_METRICS_TOKEN", valueFrom = "${local.secret}:MCO_METRICS_TOKEN::" },
      ],
      local.dynatrace_on ? [{ name = "DYNATRACE_API_TOKEN", valueFrom = "${local.secret}:DYNATRACE_API_TOKEN::" }] : [],
    )
    logConfiguration = local.log_cfg
  }])
}

resource "aws_ecs_service" "collector" {
  name            = "collector"
  cluster         = aws_ecs_cluster.city.id
  task_definition = aws_ecs_task_definition.collector.arn
  desired_count   = 1
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = aws_subnet.private[*].id
    security_groups  = [aws_security_group.workers.id]
    assign_public_ip = false
  }

  depends_on = [aws_ecs_service.gateway]
}

# ── Managed Grafana ─────────────────────────────────────────────────────────
# Requires IAM Identity Center enabled in the account (one-time console step).

resource "aws_grafana_workspace" "city" {
  name                     = var.name
  account_access_type      = "CURRENT_ACCOUNT"
  authentication_providers = ["AWS_SSO"]
  permission_type          = "SERVICE_MANAGED"
  role_arn                 = aws_iam_role.grafana.arn
  data_sources             = ["PROMETHEUS", "CLOUDWATCH"]
}

resource "aws_grafana_role_association" "admins" {
  count        = length(var.grafana_admin_sso_user_ids) > 0 ? 1 : 0
  workspace_id = aws_grafana_workspace.city.id
  role         = "ADMIN"
  user_ids     = var.grafana_admin_sso_user_ids
}

# ── The dead-man: a failure domain that is not the city ─────────────────────
# Route53 health checkers probe the public ALB from outside this region's
# control plane. Their metrics live in us-east-1 regardless of where the city
# is, hence the provider alias. If the site goes dark, the alarm fires from
# infrastructure that shares nothing with the gateway - and the phone is the
# RECEIVER, never the checker.

provider "aws" {
  alias  = "use1"
  region = "us-east-1"
}

resource "aws_route53_health_check" "gateway" {
  fqdn              = aws_lb.city.dns_name
  port              = 80
  type              = "HTTP"
  resource_path     = "/healthz"
  request_interval  = 30
  failure_threshold = 3
  tags              = { Name = "${var.name}-deadman" }
}

resource "aws_sns_topic" "alerts" {
  name              = "${var.name}-alerts"
  kms_master_key_id = "alias/aws/sns"
}

resource "aws_sns_topic_subscription" "email" {
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
}

resource "aws_sns_topic_subscription" "sms" {
  count     = var.alert_sms != "" ? 1 : 0
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "sms"
  endpoint  = var.alert_sms
}

# CloudWatch alarms can only notify SNS topics in their own region, and
# Route53 health-check metrics only exist in us-east-1.
resource "aws_sns_topic" "alerts_use1" {
  provider = aws.use1
  name     = "${var.name}-alerts-use1"
}

resource "aws_sns_topic_subscription" "email_use1" {
  provider  = aws.use1
  topic_arn = aws_sns_topic.alerts_use1.arn
  protocol  = "email"
  endpoint  = var.alert_email
}

resource "aws_sns_topic_subscription" "sms_use1" {
  count     = var.alert_sms != "" ? 1 : 0
  provider  = aws.use1
  topic_arn = aws_sns_topic.alerts_use1.arn
  protocol  = "sms"
  endpoint  = var.alert_sms
}

resource "aws_cloudwatch_metric_alarm" "deadman" {
  provider            = aws.use1
  alarm_name          = "${var.name}-site-dark"
  namespace           = "AWS/Route53"
  metric_name         = "HealthCheckStatus"
  statistic           = "Minimum"
  period              = 60
  evaluation_periods  = 3
  threshold           = 1
  comparison_operator = "LessThanThreshold"
  treat_missing_data  = "breaching" # missing = dark; silence must never look healthy
  dimensions          = { HealthCheckId = aws_route53_health_check.gateway.id }
  alarm_actions       = [aws_sns_topic.alerts_use1.arn]
  ok_actions          = [aws_sns_topic.alerts_use1.arn]
  alarm_description   = "The city has not answered /healthz for three minutes. This alarm shares no infrastructure with the gateway."
}
