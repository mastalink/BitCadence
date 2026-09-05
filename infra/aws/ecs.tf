# ── The hub and the ring ────────────────────────────────────────────────────
# Gateway (hub) + workers (ring) + conductor (the clock) + collector (the
# greenbelt's eyes), all on Fargate. Images come from ECR; see README for the
# three build-and-push commands.

resource "aws_ecr_repository" "gateway" {
  name                 = "${var.name}/gateway"
  image_tag_mutability = "IMMUTABLE"
  force_delete         = true
}
resource "aws_ecr_repository" "worker" {
  name                 = "${var.name}/worker"
  image_tag_mutability = "IMMUTABLE"
  force_delete         = true
}
resource "aws_ecr_repository" "conductor" {
  name                 = "${var.name}/conductor"
  image_tag_mutability = "IMMUTABLE"
  force_delete         = true
}

resource "aws_cloudwatch_log_group" "city" {
  name              = "/ecs/${var.name}"
  retention_in_days = 30
}

resource "aws_ecs_cluster" "city" {
  name = var.name
  setting {
    name  = "containerInsights"
    value = "enabled"
  }
}

# Internal DNS: gateway.city.local. Workers and the conductor find the hub
# without touching the public ALB.
resource "aws_service_discovery_private_dns_namespace" "city" {
  name = "city.local"
  vpc  = aws_vpc.city.id
}

resource "aws_service_discovery_service" "gateway" {
  name = "gateway"
  dns_config {
    namespace_id   = aws_service_discovery_private_dns_namespace.city.id
    routing_policy = "MULTIVALUE"
    dns_records {
      ttl  = 10
      type = "A"
    }
  }
  health_check_custom_config {
    failure_threshold = 1
  }
}

locals {
  gateway_internal_url = "http://gateway.city.local:18789"
  secret               = aws_secretsmanager_secret.core.arn

  log_cfg = {
    logDriver = "awslogs"
    options = {
      awslogs-group         = aws_cloudwatch_log_group.city.name
      awslogs-region        = var.region
      awslogs-stream-prefix = "city"
    }
  }

  # Connector env is only handed to the gateway when a tenant was provided, so
  # an unconfigured pavilion is simply absent rather than erroring.
  connector_secrets = concat(
    var.servicenow_instance_url != "" ? [
      { name = "SERVICENOW_INSTANCE_URL", valueFrom = "${local.secret}:SERVICENOW_INSTANCE_URL::" },
      { name = "SERVICENOW_USERNAME", valueFrom = "${local.secret}:SERVICENOW_USERNAME::" },
      { name = "SERVICENOW_PASSWORD", valueFrom = "${local.secret}:SERVICENOW_PASSWORD::" },
    ] : [],
    var.dynatrace_base_url != "" ? [
      { name = "DYNATRACE_BASE_URL", valueFrom = "${local.secret}:DYNATRACE_BASE_URL::" },
      { name = "DYNATRACE_API_TOKEN", valueFrom = "${local.secret}:DYNATRACE_API_TOKEN::" },
    ] : [],
  )

  # ── Gateway container ──
  gateway_container = {
    name         = "gateway"
    image        = "${aws_ecr_repository.gateway.repository_url}:${var.image_tag}"
    essential    = true
    portMappings = [{ containerPort = 18789, protocol = "tcp" }]
    environment = concat(
      [
        { name = "MCO_STORE_BACKEND", value = var.store_backend },
        { name = "MCO_APPROVER_ROLES", value = "human,admin,operator" },
        { name = "MCO_POLICY_GATED_ROLES", value = "servicenow,dynatrace" },
        { name = "MCO_SYNC_INTERVAL", value = "120" },
        { name = "MCO_LOG_JSON", value = "true" },
        { name = "MCO_EVIDENCE_BUCKET", value = aws_s3_bucket.evidence.bucket },
        { name = "MCO_EVIDENCE_ACK_REQUIRED", value = "true" },
        { name = "MCO_EVIDENCE_RETENTION_DAYS", value = tostring(var.evidence_retention_days) },
        { name = "AWS_REGION", value = var.region },
        { name = "WORKER_ROLES", value = join(",", var.worker_roles) },
      ],
      var.store_backend == "postgres" ? [
        { name = "SUPABASE_URL", value = "http://127.0.0.1:3000" },
        ] : [
        { name = "MCO_LOCAL_STORE_PATH", value = "/mco/local.db" },
      ],
    )
    secrets = concat(
      [
        { name = "MCO_LOCAL_TOKEN", valueFrom = "${local.secret}:MCO_LOCAL_TOKEN::" },
        { name = "MCO_METRICS_TOKEN", valueFrom = "${local.secret}:MCO_METRICS_TOKEN::" },
        { name = "MCO_AUDIT_HMAC_KEY", valueFrom = "${local.secret}:MCO_AUDIT_HMAC_KEY::" },
      ],
      [for r in var.worker_roles : { name = "WORKER_TOKEN_${upper(r)}", valueFrom = "${local.secret}:WORKER_TOKEN_${upper(r)}::" }],
      var.store_backend == "postgres" ? [
        { name = "JWT_SECRET", valueFrom = "${local.secret}:JWT_SECRET::" },
        { name = "DATABASE_URL", valueFrom = "${local.secret}:DATABASE_URL::" },
      ] : [],
      local.connector_secrets,
    )
    mountPoints = var.store_backend == "local" ? [{ sourceVolume = "store", containerPath = "/mco", readOnly = false }] : []
    dependsOn   = var.store_backend == "postgres" ? [{ containerName = "rest", condition = "START" }] : []
    healthCheck = {
      command     = ["CMD-SHELL", "python -c \"import httpx; httpx.get('http://127.0.0.1:18789/healthz', timeout=4).raise_for_status()\""]
      interval    = 30
      timeout     = 5
      retries     = 3
      startPeriod = 60
    }
    logConfiguration = local.log_cfg
  }

  # ── PostgREST + a path-strip proxy, only for the postgres backend ──
  # The Supabase client calls {SUPABASE_URL}/rest/v1/...; PostgREST serves at
  # its root. nginx on :3000 strips the prefix and forwards to PostgREST :3001.
  postgrest_container = {
    name      = "postgrest"
    image     = "postgrest/postgrest:v12.2.3"
    essential = true
    environment = [
      { name = "PGRST_DB_SCHEMAS", value = "public" },
      { name = "PGRST_DB_ANON_ROLE", value = "bitcadence" },
      { name = "PGRST_SERVER_PORT", value = "3001" },
      { name = "PGRST_DB_POOL", value = "10" },
    ]
    secrets = [
      { name = "PGRST_DB_URI", valueFrom = "${local.secret}:PGRST_DB_URI::" },
      { name = "PGRST_JWT_SECRET", valueFrom = "${local.secret}:JWT_SECRET::" },
    ]
    logConfiguration = local.log_cfg
  }

  rest_container = {
    name      = "rest"
    image     = "nginx:1.27-alpine"
    essential = true
    command = ["sh", "-c", <<-EOT
      cat > /etc/nginx/conf.d/default.conf <<'NGX'
      server {
        listen 3000;
        location /rest/v1/ { proxy_pass http://127.0.0.1:3001/; proxy_set_header Host $host; }
        location / { return 404; }
      }
      NGX
      nginx -g 'daemon off;'
    EOT
    ]
    dependsOn        = [{ containerName = "postgrest", condition = "START" }]
    logConfiguration = local.log_cfg
  }

  # HCL cannot unify a 3-tuple of differently-shaped objects with a 1-tuple in
  # a conditional, so each branch is encoded to a string first.
  gateway_containers_json = var.store_backend == "postgres" ? jsonencode([local.gateway_container, local.postgrest_container, local.rest_container]) : jsonencode([local.gateway_container])
}

resource "aws_ecs_task_definition" "gateway" {
  family                   = "${var.name}-gateway"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 1024
  memory                   = 2048
  execution_role_arn       = aws_iam_role.exec.arn
  task_role_arn            = aws_iam_role.gateway.arn
  container_definitions    = local.gateway_containers_json

  dynamic "volume" {
    for_each = var.store_backend == "local" ? [1] : []
    content {
      name = "store"
      efs_volume_configuration {
        file_system_id     = aws_efs_file_system.store[0].id
        transit_encryption = "ENABLED"
        authorization_config {
          access_point_id = aws_efs_access_point.store[0].id
          iam             = "ENABLED"
        }
      }
    }
  }
}

resource "aws_ecs_service" "gateway" {
  name            = "gateway"
  cluster         = aws_ecs_cluster.city.id
  task_definition = aws_ecs_task_definition.gateway.arn
  desired_count   = 1
  launch_type     = "FARGATE"
  propagate_tags  = "SERVICE"
  tags            = { "bc:component" = "gateway" }

  # One gateway task, deliberately: the store is a single writer in both
  # backends today. Replaceable active gateways wait on WS1's fenced reaper.
  deployment_minimum_healthy_percent = 0
  deployment_maximum_percent         = 100

  network_configuration {
    subnets          = aws_subnet.private[*].id
    security_groups  = [aws_security_group.gateway.id]
    assign_public_ip = false
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.gateway.arn
    container_name   = "gateway"
    container_port   = 18789
  }

  service_registries {
    registry_arn = aws_service_discovery_service.gateway.arn
  }

  depends_on = [aws_lb_listener.https]
}

# ── Workers: one Fargate task per role, all Bedrock-backed ──────────────────

resource "aws_ecs_task_definition" "worker" {
  for_each = toset(var.worker_roles)

  family                   = "${var.name}-worker-${each.key}"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 512
  memory                   = 1024
  execution_role_arn       = aws_iam_role.exec.arn
  task_role_arn            = aws_iam_role.worker.arn

  container_definitions = jsonencode([{
    name      = "worker"
    image     = "${aws_ecr_repository.worker.repository_url}:${var.image_tag}"
    essential = true
    environment = [
      { name = "GATEWAY_URL", value = local.gateway_internal_url },
      { name = "ROLE", value = each.key },
      { name = "INSTANCE_ID", value = "${each.key}-cloud-1" },
      { name = "BEDROCK_MODEL_ID", value = var.bedrock_model_id },
      { name = "AWS_REGION", value = var.region },
      { name = "POLL_INTERVAL", value = "5" },
    ]
    secrets = [
      { name = "MCO_AGENT_TOKEN", valueFrom = "${local.secret}:WORKER_TOKEN_${upper(each.key)}::" },
    ]
    logConfiguration = local.log_cfg
  }])
}

resource "aws_ecs_service" "worker" {
  for_each = toset(var.worker_roles)

  name            = "worker-${each.key}"
  cluster         = aws_ecs_cluster.city.id
  task_definition = aws_ecs_task_definition.worker[each.key].arn
  desired_count   = 1
  launch_type     = "FARGATE"
  propagate_tags  = "SERVICE"
  # FIS targets workers by this tag. The gateway does not carry it.
  tags = { "bc:component" = "worker", "bc:role" = each.key }

  network_configuration {
    subnets          = aws_subnet.private[*].id
    security_groups  = [aws_security_group.workers.id]
    assign_public_ip = false
  }

  depends_on = [aws_ecs_service.gateway]
}

# ── Conductor: run by the scheduler, never long-lived ───────────────────────

resource "aws_ecs_task_definition" "conductor" {
  family                   = "${var.name}-conductor"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 512
  memory                   = 1024
  execution_role_arn       = aws_iam_role.exec.arn
  task_role_arn            = aws_iam_role.conductor.arn

  container_definitions = jsonencode([{
    name      = "conductor"
    image     = "${aws_ecr_repository.conductor.repository_url}:${var.image_tag}"
    essential = true
    environment = [
      { name = "GATEWAY_URL", value = local.gateway_internal_url },
      { name = "PUBLIC_URL", value = "https://${var.console_hostname}" },
      { name = "EVIDENCE_BUCKET", value = aws_s3_bucket.evidence.bucket },
      { name = "STATUS_BUCKET", value = aws_s3_bucket.status.bucket },
      { name = "ECS_CLUSTER", value = aws_ecs_cluster.city.name },
      { name = "FIS_STOP_WORKER", value = aws_fis_experiment_template.stop_worker.id },
      { name = "FIS_STOP_GATEWAY", value = aws_fis_experiment_template.stop_gateway.id },
      { name = "FIS_PARTITION", value = aws_fis_experiment_template.partition.id },
      { name = "SNS_TOPIC", value = aws_sns_topic.alerts.arn },
      { name = "WORKER_ROLES", value = join(",", var.worker_roles) },
      { name = "SECRET_ARN", value = aws_secretsmanager_secret.core.arn },
      { name = "STORE_BACKEND", value = var.store_backend },
      { name = "SERVICENOW_ENABLED", value = var.servicenow_instance_url != "" ? "1" : "0" },
      { name = "DYNATRACE_ENABLED", value = var.dynatrace_base_url != "" ? "1" : "0" },
      { name = "AWS_REGION", value = var.region },
    ]
    secrets = concat(
      [
        { name = "MCO_LOCAL_TOKEN", valueFrom = "${local.secret}:MCO_LOCAL_TOKEN::" },
        { name = "MCO_METRICS_TOKEN", valueFrom = "${local.secret}:MCO_METRICS_TOKEN::" },
      ],
      [for r in var.worker_roles : { name = "WORKER_TOKEN_${upper(r)}", valueFrom = "${local.secret}:WORKER_TOKEN_${upper(r)}::" }],
      var.store_backend == "postgres" ? [{ name = "DATABASE_URL", valueFrom = "${local.secret}:DATABASE_URL::" }] : [],
    )
    logConfiguration = local.log_cfg
  }])
}

# ── Public entry: the ALB ───────────────────────────────────────────────────
# Public credentials are accepted only over TLS. DNS may be hosted anywhere.

resource "aws_lb" "city" {
  name               = substr("${var.name}-alb", 0, 32)
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb.id]
  subnets            = aws_subnet.public[*].id
}

resource "aws_lb_target_group" "gateway" {
  name        = substr("${var.name}-gw", 0, 32)
  port        = 18789
  protocol    = "HTTP"
  target_type = "ip"
  vpc_id      = aws_vpc.city.id

  health_check {
    path                = "/readyz"
    interval            = 15
    timeout             = 5
    healthy_threshold   = 2
    unhealthy_threshold = 3
    matcher             = "200"
  }
  deregistration_delay = 10
}

resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.city.arn
  port              = 80
  protocol          = "HTTP"
  default_action {
    type = "redirect"
    redirect {
      port        = "443"
      protocol    = "HTTPS"
      status_code = "HTTP_301"
    }
  }
}

resource "aws_lb_listener" "https" {
  load_balancer_arn = aws_lb.city.arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = var.certificate_arn
  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.gateway.arn
  }
}
