terraform {
  required_version = ">= 1.10"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.70" }
  }
}
provider "aws" {
  region              = "us-east-1"
  allowed_account_ids = ["314086896527"]
  default_tags { tags = { Project = "bitcadence-lab", ManagedBy = "terraform" } }
}
data "aws_caller_identity" "current" {}
locals {
  account         = data.aws_caller_identity.current.account_id
  prefix          = "bitcadence-lab"
  state_bucket    = "${local.prefix}-state-${local.account}"
  evidence_bucket = "${local.prefix}-evidence-${local.account}"
  repo            = "mastalink/BitCadence"
  branch          = "codex/bitcadence-completion"
  secrets         = toset(["operator", "worker", "reviewer", "tls-ca"])
}
resource "aws_iam_openid_connect_provider" "github" {
  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]
}
resource "aws_s3_bucket" "state" {
  bucket = local.state_bucket
  lifecycle { prevent_destroy = true }
}
resource "aws_s3_bucket_versioning" "state" {
  bucket = aws_s3_bucket.state.id
  versioning_configuration { status = "Enabled" }
}
resource "aws_s3_bucket_server_side_encryption_configuration" "state" {
  bucket = aws_s3_bucket.state.id
  rule {
    apply_server_side_encryption_by_default { sse_algorithm = "AES256" }
  }
}
resource "aws_s3_bucket_public_access_block" "state" {
  bucket                  = aws_s3_bucket.state.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}
resource "aws_s3_bucket_policy" "state" {
  bucket = aws_s3_bucket.state.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Deny", Principal = "*", Action = "s3:*", Resource = [aws_s3_bucket.state.arn, "${aws_s3_bucket.state.arn}/*"], Condition = { Bool = { "aws:SecureTransport" = "false" } } }] })
}
resource "aws_ecr_repository" "lab" {
  name                 = local.prefix
  image_tag_mutability = "IMMUTABLE"
  image_scanning_configuration { scan_on_push = true }
}
resource "aws_ecr_lifecycle_policy" "lab" {
  repository = aws_ecr_repository.lab.name
  policy     = jsonencode({ rules = [{ rulePriority = 1, description = "Expire untagged build layers", selection = { tagStatus = "untagged", countType = "sinceImagePushed", countUnit = "days", countNumber = 1 }, action = { type = "expire" } }] })
}
resource "aws_secretsmanager_secret" "lab" {
  for_each                = local.secrets
  name                    = "${local.prefix}/${each.key}"
  recovery_window_in_days = 7
}
resource "aws_iam_role" "node" {
  for_each           = toset(["hub", "worker", "reviewer"])
  name               = "${local.prefix}-${each.key}"
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow", Principal = { Service = "ec2.amazonaws.com" }, Action = "sts:AssumeRole" }] })
}
resource "aws_iam_instance_profile" "node" {
  for_each = aws_iam_role.node
  name     = each.value.name
  role     = each.value.name
}
resource "aws_iam_role_policy_attachment" "ssm" {
  for_each   = aws_iam_role.node
  role       = each.value.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}
resource "aws_iam_role_policy" "node" {
  for_each = aws_iam_role.node
  name     = "lab-runtime"
  role     = each.value.id
  policy = jsonencode({ Version = "2012-10-17", Statement = concat([
    { Effect = "Allow", Action = ["ecr:GetAuthorizationToken"], Resource = "*" },
    { Effect = "Allow", Action = ["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer", "ecr:BatchCheckLayerAvailability"], Resource = aws_ecr_repository.lab.arn },
    { Effect = "Allow", Action = ["secretsmanager:GetSecretValue"], Resource = each.key == "hub" ? [for s in aws_secretsmanager_secret.lab : s.arn] : [aws_secretsmanager_secret.lab[each.key].arn, aws_secretsmanager_secret.lab["tls-ca"].arn] }
    ], jsondecode(each.key == "hub" ? jsonencode([
      { Effect = "Allow", Action = ["secretsmanager:PutSecretValue"], Resource = [for s in aws_secretsmanager_secret.lab : s.arn] },
      { Effect = "Allow", Action = ["s3:PutObject", "s3:PutObjectRetention", "s3:GetObject", "s3:GetObjectVersion"], Resource = "arn:aws:s3:::${local.evidence_bucket}/*" }
      ]) : jsonencode([
      { Effect = "Allow", Action = ["bedrock:InvokeModel"], Resource = "arn:aws:bedrock:us-east-1::foundation-model/amazon.nova-micro-v1:0" }
  ]))) })
}
resource "aws_iam_role" "shutdown" {
  name               = "${local.prefix}-shutdown"
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow", Principal = { Service = "scheduler.amazonaws.com" }, Action = "sts:AssumeRole", Condition = { StringEquals = { "aws:SourceAccount" = local.account }, ArnLike = { "aws:SourceArn" = "arn:aws:scheduler:us-east-1:${local.account}:schedule-group/default" } } }] })
}
resource "aws_iam_role_policy" "shutdown" {
  role   = aws_iam_role.shutdown.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow", Action = "ec2:StopInstances", Resource = "arn:aws:ec2:us-east-1:${local.account}:instance/*", Condition = { StringEquals = { "ec2:ResourceTag/Project" = local.prefix } } }] })
}
resource "aws_iam_role" "deploy" {
  name                 = "${local.prefix}-deploy"
  max_session_duration = 3600
  assume_role_policy   = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow", Principal = { Federated = aws_iam_openid_connect_provider.github.arn }, Action = "sts:AssumeRoleWithWebIdentity", Condition = { StringEquals = { "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com", "token.actions.githubusercontent.com:sub" = "repo:mastalink@72055896/BitCadence@1245844706:ref:refs/heads/${local.branch}" } } }] })
}
resource "aws_iam_role_policy" "deploy" {
  role = aws_iam_role.deploy.id
  name = "lab-deployment"
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = ["sts:GetCallerIdentity", "ecr:GetAuthorizationToken"], Resource = "*" },
    { Effect = "Allow", Action = ["ec2:Describe*"], Resource = "*", Condition = { StringEquals = { "aws:RequestedRegion" = "us-east-1" } } },
    { Effect = "Allow", Action = ["ec2:CreateVolume", "ec2:CreateVpc", "ec2:CreateSubnet", "ec2:CreateRouteTable", "ec2:CreateInternetGateway", "ec2:CreateSecurityGroup"], Resource = "*", Condition = { StringEquals = { "aws:RequestTag/Project" = local.prefix, "aws:RequestedRegion" = "us-east-1" } } },
    { Effect = "Allow", Action = ["ec2:RunInstances"], Resource = "*", Condition = { StringEquals = { "aws:RequestedRegion" = "us-east-1" }, "StringEqualsIfExists" = { "ec2:InstanceType" = ["t3.small", "t3.micro"] } } },
    { Effect = "Allow", Action = ["ec2:CreateTags"], Resource = "arn:aws:ec2:us-east-1:${local.account}:*/*", Condition = { StringEquals = { "aws:RequestTag/Project" = local.prefix } } },
    { Effect = "Allow", Action = ["ec2:AttachVolume", "ec2:DetachVolume", "ec2:DeleteVolume", "ec2:ModifyVpcAttribute", "ec2:ModifySubnetAttribute", "ec2:ModifyInstanceAttribute", "ec2:ModifyInstanceCreditSpecification", "ec2:AuthorizeSecurityGroupIngress", "ec2:AuthorizeSecurityGroupEgress", "ec2:RevokeSecurityGroupIngress", "ec2:RevokeSecurityGroupEgress", "ec2:CreateRoute", "ec2:DeleteRoute", "ec2:AssociateRouteTable", "ec2:DisassociateRouteTable", "ec2:AttachInternetGateway", "ec2:DetachInternetGateway", "ec2:DeleteVpc", "ec2:DeleteSubnet", "ec2:DeleteRouteTable", "ec2:DeleteInternetGateway", "ec2:DeleteSecurityGroup", "ec2:StartInstances", "ec2:StopInstances", "ec2:TerminateInstances", "ec2:DeleteTags"], Resource = "arn:aws:ec2:us-east-1:${local.account}:*/*", Condition = { StringEquals = { "ec2:ResourceTag/Project" = local.prefix } } },
    { Effect = "Allow", Action = ["iam:GetInstanceProfile", "iam:GetRole"], Resource = ["arn:aws:iam::${local.account}:instance-profile/${local.prefix}-*", "arn:aws:iam::${local.account}:role/${local.prefix}-*"] },
    { Effect = "Allow", Action = "iam:PassRole", Resource = [for r in aws_iam_role.node : r.arn], Condition = { StringEquals = { "iam:PassedToService" = "ec2.amazonaws.com" } } },
    { Effect = "Allow", Action = "iam:PassRole", Resource = aws_iam_role.shutdown.arn, Condition = { StringEquals = { "iam:PassedToService" = "scheduler.amazonaws.com" } } },
    { Effect = "Allow", Action = ["ecr:BatchGetImage", "ecr:BatchCheckLayerAvailability", "ecr:InitiateLayerUpload", "ecr:UploadLayerPart", "ecr:CompleteLayerUpload", "ecr:PutImage", "ecr:DescribeImages"], Resource = aws_ecr_repository.lab.arn },
    { Effect = "Allow", Action = "s3:*", Resource = ["arn:aws:s3:::${local.state_bucket}", "arn:aws:s3:::${local.state_bucket}/lab/*", "arn:aws:s3:::${local.evidence_bucket}", "arn:aws:s3:::${local.evidence_bucket}/*"] },
    { Effect = "Allow", Action = ["scheduler:CreateSchedule", "scheduler:UpdateSchedule", "scheduler:GetSchedule", "scheduler:DeleteSchedule"], Resource = "arn:aws:scheduler:us-east-1:${local.account}:schedule/default/${local.prefix}-*" },
    { Effect = "Allow", Action = ["ssm:GetParameter"], Resource = "arn:aws:ssm:us-east-1::parameter/aws/service/ami-amazon-linux-latest/*" },
    { Effect = "Allow", Action = ["ssm:DescribeInstanceInformation", "ssm:GetCommandInvocation", "ssm:ListCommandInvocations"], Resource = "*" },
    { Effect = "Allow", Action = "ssm:SendCommand", Resource = "arn:aws:ssm:us-east-1::document/AWS-RunShellScript" },
    { Effect = "Allow", Action = "ssm:SendCommand", Resource = "arn:aws:ec2:us-east-1:${local.account}:instance/*", Condition = { StringEquals = { "ssm:resourceTag/Project" = local.prefix } } }
  ] })
}
output "deployment_role" { value = aws_iam_role.deploy.arn }
output "state_bucket" { value = aws_s3_bucket.state.id }
output "image_repository" { value = aws_ecr_repository.lab.repository_url }
