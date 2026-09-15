output "audit_runner_function_name" {
  value = aws_lambda_function.audit.function_name
}

output "audit_runner_role_arn" {
  value = aws_iam_role.audit.arn
}

output "audit_schedule_rule_arn" {
  value = aws_cloudwatch_event_rule.audit.arn
}

output "audit_artifact_prefix" {
  value = "s3://${var.evidence_bucket}/score-audits/"
}

output "deploy_runner_function_name" {
  value = aws_lambda_function.deploy.function_name
}

output "deploy_approval_key_arn" {
  value = aws_kms_key.release_approval.arn
}

output "deploy_approval_prefix" {
  value = "s3://${var.evidence_bucket}/score-deploy-approvals/pending/"
}
