# ── The core: the ledger's home ─────────────────────────────────────────────
# The gateway speaks LocalStore (SQLite) or the Supabase client - not bare
# Postgres. "postgres" therefore means RDS behind PostgREST, which is the
# surface the Supabase client already understands. "local" means the embedded
# store on EFS, single gateway task. Both are wired; the variable picks.

resource "random_password" "db" {
  count   = var.store_backend == "postgres" ? 1 : 0
  length  = 32
  special = false
}

# PostgREST verifies bearer JWTs with this secret. The gateway entrypoint mints
# SUPABASE_KEY from it at boot (role claim = the DB user), so nothing long-lived
# is hand-signed. See infra/aws/gateway/entrypoint.sh.
resource "random_password" "jwt" {
  count   = var.store_backend == "postgres" ? 1 : 0
  length  = 48
  special = false
}

resource "aws_db_subnet_group" "city" {
  count      = var.store_backend == "postgres" ? 1 : 0
  name       = "${var.name}-db"
  subnet_ids = aws_subnet.private[*].id
}

resource "aws_db_instance" "ledger" {
  count = var.store_backend == "postgres" ? 1 : 0

  identifier        = "${var.name}-ledger"
  engine            = "postgres"
  engine_version    = "16"
  instance_class    = var.db_instance_class
  allocated_storage = 20
  storage_encrypted = true
  kms_key_id        = aws_kms_key.evidence.arn

  db_name  = "bitcadence"
  username = "bitcadence"
  password = random_password.db[0].result

  db_subnet_group_name   = aws_db_subnet_group.city[0].name
  vpc_security_group_ids = [aws_security_group.db[0].id]
  publicly_accessible    = false
  multi_az               = false # a demo; the chaos suite proves recovery, not Multi-AZ

  backup_retention_period = 7
  deletion_protection     = false
  skip_final_snapshot     = true
  apply_immediately       = true
}

# ── Secrets: the utilidor's locked doors ────────────────────────────────────
# ECS injects secrets by TOP-LEVEL JSON key only (secret-arn:KEY::), so every
# value a container needs is a flat key here. Nested objects cannot be injected.

resource "aws_secretsmanager_secret" "core" {
  name                    = "${var.name}/core"
  kms_key_id              = aws_kms_key.evidence.arn
  recovery_window_in_days = 0
}

resource "random_password" "local_token" {
  length  = 40
  special = false
}

resource "random_password" "metrics_token" {
  length  = 32
  special = false
}

resource "random_password" "audit_hmac" {
  length  = 64
  special = false
}

resource "random_password" "worker_token" {
  for_each = toset(var.worker_roles)
  length   = 40
  special  = false
}

locals {
  pg_dsn = var.store_backend == "postgres" ? "postgres://bitcadence:${random_password.db[0].result}@${aws_db_instance.ledger[0].address}:5432/bitcadence" : ""

  worker_token_keys = { for r in var.worker_roles : "WORKER_TOKEN_${upper(r)}" => random_password.worker_token[r].result }
}

resource "aws_secretsmanager_secret_version" "core" {
  secret_id = aws_secretsmanager_secret.core.id
  secret_string = jsonencode(merge(
    {
      MCO_LOCAL_TOKEN    = random_password.local_token.result
      MCO_METRICS_TOKEN  = random_password.metrics_token.result
      MCO_AUDIT_HMAC_KEY = random_password.audit_hmac.result
    },
    local.worker_token_keys,
    var.store_backend == "postgres" ? {
      PGRST_DB_URI = local.pg_dsn
      DATABASE_URL = local.pg_dsn
      JWT_SECRET   = random_password.jwt[0].result
    } : {},
    var.servicenow_instance_url != "" ? {
      SERVICENOW_INSTANCE_URL = var.servicenow_instance_url
      SERVICENOW_USERNAME     = var.servicenow_username
      SERVICENOW_PASSWORD     = var.servicenow_password
    } : {},
    var.dynatrace_base_url != "" ? {
      DYNATRACE_BASE_URL  = var.dynatrace_base_url
      DYNATRACE_API_TOKEN = var.dynatrace_api_token
    } : {},
  ))
}

# ── EFS for the "local" backend ─────────────────────────────────────────────

resource "aws_efs_file_system" "store" {
  count      = var.store_backend == "local" ? 1 : 0
  encrypted  = true
  kms_key_id = aws_kms_key.evidence.arn
  tags       = { Name = "${var.name}-localstore" }
}

resource "aws_efs_mount_target" "store" {
  count           = var.store_backend == "local" ? 2 : 0
  file_system_id  = aws_efs_file_system.store[0].id
  subnet_id       = aws_subnet.private[count.index].id
  security_groups = [aws_security_group.efs[0].id]
}

resource "aws_efs_access_point" "store" {
  count          = var.store_backend == "local" ? 1 : 0
  file_system_id = aws_efs_file_system.store[0].id
  posix_user {
    uid = 1000
    gid = 1000
  }
  root_directory {
    path = "/mco"
    creation_info {
      owner_uid   = 1000
      owner_gid   = 1000
      permissions = "0750"
    }
  }
}
