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
