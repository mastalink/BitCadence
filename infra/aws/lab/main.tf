terraform {
  required_version = ">= 1.10"
  backend "s3" {
    key          = "lab/terraform.tfstate"
    region       = "us-east-1"
    encrypt      = true
    use_lockfile = true
  }
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.70" }
  }
}
provider "aws" {
  region              = "us-east-1"
  allowed_account_ids = ["314086896527"]
  default_tags { tags = { Project = "bitcadence-lab", ManagedBy = "terraform" } }
}
variable "image_tag" {
  type = string
  validation {
    condition     = can(regex("^[a-f0-9]{40}$", var.image_tag))
    error_message = "Use the exact release commit SHA."
  }
}
variable "expires_at" {
  type        = string
  description = "UTC shutdown time, YYYY-MM-DDTHH:mm:ss; each test session lasts at most two hours."
}
data "aws_caller_identity" "current" {}
data "aws_ssm_parameter" "ami" { name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64" }
data "aws_availability_zones" "available" { state = "available" }
locals {
  prefix   = "bitcadence-lab"
  account  = data.aws_caller_identity.current.account_id
  evidence = "${local.prefix}-evidence-${local.account}"
  image    = "${local.account}.dkr.ecr.us-east-1.amazonaws.com/${local.prefix}:${var.image_tag}"
  nodes    = { hub = "t3.small", worker = "t3.micro", reviewer = "t3.micro" }
}
resource "aws_vpc" "lab" {
  cidr_block           = "10.43.0.0/16"
  enable_dns_support   = true
  enable_dns_hostnames = true
  tags                 = { Name = local.prefix }
}
resource "aws_subnet" "lab" {
  vpc_id                  = aws_vpc.lab.id
  cidr_block              = "10.43.1.0/24"
  availability_zone       = data.aws_availability_zones.available.names[0]
  map_public_ip_on_launch = false
  tags                    = { Name = local.prefix }
}
resource "aws_internet_gateway" "lab" { vpc_id = aws_vpc.lab.id }
resource "aws_route_table" "lab" {
  vpc_id = aws_vpc.lab.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.lab.id
  }
}
resource "aws_route_table_association" "lab" {
  subnet_id      = aws_subnet.lab.id
  route_table_id = aws_route_table.lab.id
}
resource "aws_security_group" "spoke" {
  name        = "${local.prefix}-spoke"
  vpc_id      = aws_vpc.lab.id
  description = "No inbound access; TLS to hub and AWS APIs only"
  egress {
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
  egress {
    from_port   = 18790
    to_port     = 18790
    protocol    = "tcp"
    cidr_blocks = ["10.43.1.10/32"]
  }
}
resource "aws_security_group" "hub" {
  name        = "${local.prefix}-hub"
  vpc_id      = aws_vpc.lab.id
  description = "TLS from worker spokes only; console through SSM tunnel"
  ingress {
    from_port       = 18790
    to_port         = 18790
    protocol        = "tcp"
    security_groups = [aws_security_group.spoke.id]
  }
  egress {
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
}
resource "aws_s3_bucket" "evidence" {
  bucket              = local.evidence
  object_lock_enabled = true
  lifecycle { prevent_destroy = true }
}
resource "aws_s3_bucket_public_access_block" "evidence" {
  bucket                  = aws_s3_bucket.evidence.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}
resource "aws_s3_bucket_versioning" "evidence" {
  bucket = aws_s3_bucket.evidence.id
  versioning_configuration { status = "Enabled" }
}
resource "aws_s3_bucket_server_side_encryption_configuration" "evidence" {
  bucket = aws_s3_bucket.evidence.id
  rule {
    apply_server_side_encryption_by_default { sse_algorithm = "AES256" }
  }
}
resource "aws_s3_bucket_object_lock_configuration" "evidence" {
  bucket = aws_s3_bucket.evidence.id
  rule {
    default_retention {
      mode = "COMPLIANCE"
      days = 1
    }
  }
  depends_on = [aws_s3_bucket_versioning.evidence]
}
resource "aws_s3_bucket_lifecycle_configuration" "evidence" {
  bucket = aws_s3_bucket.evidence.id
  rule {
    id     = "Expire lab evidence after seven days"
    status = "Enabled"
    filter { prefix = "" }
    expiration { days = 7 }
    noncurrent_version_expiration { noncurrent_days = 1 }
    abort_incomplete_multipart_upload { days_after_initiation = 1 }
  }
}
resource "aws_s3_bucket_policy" "evidence" {
  bucket = aws_s3_bucket.evidence.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Deny", Principal = "*", Action = "s3:*", Resource = [aws_s3_bucket.evidence.arn, "${aws_s3_bucket.evidence.arn}/*"], Condition = { Bool = { "aws:SecureTransport" = "false" } } }] })
}
resource "aws_ebs_volume" "hub_data" {
  availability_zone = data.aws_availability_zones.available.names[0]
  size              = 8
  type              = "gp3"
  encrypted         = true
  tags              = { Name = "${local.prefix}-hub-data" }
  lifecycle { prevent_destroy = true }
}
resource "aws_volume_attachment" "hub_data" {
  device_name = "/dev/sdf"
  volume_id   = aws_ebs_volume.hub_data.id
  instance_id = aws_instance.node["hub"].id
}
resource "aws_instance" "node" {
  for_each                             = local.nodes
  ami                                  = data.aws_ssm_parameter.ami.value
  instance_type                        = each.value
  subnet_id                            = aws_subnet.lab.id
  private_ip                           = each.key == "hub" ? "10.43.1.10" : null
  associate_public_ip_address          = true
  vpc_security_group_ids               = [each.key == "hub" ? aws_security_group.hub.id : aws_security_group.spoke.id]
  iam_instance_profile                 = "${local.prefix}-${each.key}"
  instance_initiated_shutdown_behavior = "stop"
  user_data_replace_on_change          = true
  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 1
  }
  credit_specification { cpu_credits = "standard" }
  root_block_device {
    volume_size = each.key == "hub" ? 20 : 12
    volume_type = "gp3"
    encrypted   = true
  }
  user_data   = templatefile("${path.module}/user-data.sh.tftpl", { role = each.key, image = local.image, account = local.account, evidence = local.evidence, data_volume = aws_ebs_volume.hub_data.id })
  tags        = { Name = "${local.prefix}-${each.key}", Role = each.key }
  volume_tags = { Project = local.prefix }
  depends_on  = [aws_route_table_association.lab, aws_s3_bucket_object_lock_configuration.evidence]
  lifecycle { ignore_changes = [ami] }
}
resource "aws_scheduler_schedule" "shutdown" {
  name                         = "${local.prefix}-shutdown"
  schedule_expression          = "at(${var.expires_at})"
  schedule_expression_timezone = "UTC"
  flexible_time_window { mode = "OFF" }
  target {
    arn      = "arn:aws:scheduler:::aws-sdk:ec2:stopInstances"
    role_arn = "arn:aws:iam::${local.account}:role/${local.prefix}-shutdown"
    input    = jsonencode({ InstanceIds = [for node in aws_instance.node : node.id] })
    retry_policy {
      maximum_event_age_in_seconds = 3600
      maximum_retry_attempts       = 3
    }
  }
}
output "instances" { value = { for name, node in aws_instance.node : name => node.id } }
output "evidence_bucket" { value = aws_s3_bucket.evidence.id }
output "shutdown_utc" { value = var.expires_at }
output "console_access" { value = "SSM tunnel to hub port 18789, then http://127.0.0.1:18891/console" }
