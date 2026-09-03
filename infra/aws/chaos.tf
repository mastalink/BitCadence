# ── Always becoming ─────────────────────────────────────────────────────────
# A city that only works while nobody touches it is an exhibit. These are the
# hands that reach in. FIS pulls the plugs; the scheduler decides when; the
# conductor (infra/aws/conductor) narrates, asserts, and files the evidence.

# 1. Kill a worker mid-job. Proves the lease is reclaimed and the job
#    completes exactly once - or exposes that it does not.
resource "aws_fis_experiment_template" "stop_worker" {
  description = "Stop one cloud worker task while it holds a lease"
  role_arn    = aws_iam_role.fis.arn

  stop_condition {
    source = "none"
  }

  # Pinned to the first role so the conductor can post the job there first and
  # know the task it kills is the one holding the lease.
  target {
    name           = "one-worker"
    resource_type  = "aws:ecs:task"
    selection_mode = "COUNT(1)"
    resource_tag {
      key   = "bc:component"
      value = "worker"
    }
    resource_tag {
      key   = "bc:role"
      value = var.worker_roles[0]
    }
    parameters = {
      cluster = aws_ecs_cluster.city.name
    }
  }

  action {
    name      = "stop"
    action_id = "aws:ecs:stop-task"
    target {
      key   = "Tasks"
      value = "one-worker"
    }
  }

  tags = { Name = "${var.name}-stop-worker" }
}

# 2. Kill the gateway. Proves ECS restarts it, workers reconnect, and the
#    dead-man fires if it stays down.
resource "aws_fis_experiment_template" "stop_gateway" {
  description = "Stop the gateway task"
  role_arn    = aws_iam_role.fis.arn

  stop_condition {
    source = "none"
  }

  target {
    name           = "gateway"
    resource_type  = "aws:ecs:task"
    selection_mode = "ALL"
    resource_tag {
      key   = "bc:component"
      value = "gateway"
    }
    parameters = {
      cluster = aws_ecs_cluster.city.name
    }
  }

  action {
    name      = "stop"
    action_id = "aws:ecs:stop-task"
    target {
      key   = "Tasks"
      value = "gateway"
    }
  }

  tags = { Name = "${var.name}-stop-gateway" }
}

# 3. Partition one private subnet for SIXTEEN minutes - longer than the
#    15-minute stale-lease TTL, so the reclaim provably happens under a real
#    network fault rather than after it heals. The stale-writer replay that
#    follows is done by the conductor with the partitioned worker's token.
resource "aws_fis_experiment_template" "partition" {
  description = "Cut network connectivity to one private subnet for 16 minutes"
  role_arn    = aws_iam_role.fis.arn

  stop_condition {
    source = "none"
  }

  target {
    name           = "worker-subnet"
    resource_type  = "aws:ec2:subnet"
    selection_mode = "ALL"
    resource_arns  = [aws_subnet.private[1].arn]
  }

  action {
    name      = "disrupt"
    action_id = "aws:network:disrupt-connectivity"
    parameter {
      key   = "duration"
      value = "PT16M"
    }
    parameter {
      key   = "scope"
      value = "all"
    }
    target {
      key   = "Subnets"
      value = "worker-subnet"
    }
  }

  tags = { Name = "${var.name}-partition" }
}

# ── The clock ───────────────────────────────────────────────────────────────

resource "aws_scheduler_schedule" "conductor" {
  name                = "${var.name}-conductor"
  schedule_expression = var.chaos_schedule

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_ecs_cluster.city.arn
    role_arn = aws_iam_role.scheduler.arn

    ecs_parameters {
      task_definition_arn = aws_ecs_task_definition.conductor.arn
      launch_type         = "FARGATE"
      task_count          = 1

      network_configuration {
        subnets          = aws_subnet.private[*].id
        security_groups  = [aws_security_group.workers.id]
        assign_public_ip = false
      }
    }
  }
}
