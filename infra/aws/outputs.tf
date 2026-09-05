output "console_url" {
  description = "The pedestrian level. Log in with the MCO_LOCAL_TOKEN from Secrets Manager."
  value       = "https://${var.console_hostname}/console"
}

output "console_dns_target" {
  description = "Create a DNS CNAME for console_hostname pointing here."
  value       = aws_lb.city.dns_name
}

output "status_page_url" {
  description = "Public, read-only. Rewritten by the conductor after every chaos run."
  value       = "http://${aws_s3_bucket_website_configuration.status.website_endpoint}"
}

output "grafana_url" {
  value = "https://${aws_grafana_workspace.city.endpoint}"
}

output "evidence_bucket" {
  description = "Object Lock COMPLIANCE. Nothing here can be deleted before retention elapses - by anyone."
  value       = aws_s3_bucket.evidence.bucket
}

output "secret_arn" {
  description = "aws secretsmanager get-secret-value --secret-id <this> --query SecretString --output text | jq ."
  value       = aws_secretsmanager_secret.core.arn
}

output "ecr" {
  value = {
    gateway   = aws_ecr_repository.gateway.repository_url
    worker    = aws_ecr_repository.worker.repository_url
    conductor = aws_ecr_repository.conductor.repository_url
  }
}

output "fis_templates" {
  value = {
    stop_worker  = aws_fis_experiment_template.stop_worker.id
    stop_gateway = aws_fis_experiment_template.stop_gateway.id
    partition    = aws_fis_experiment_template.partition.id
  }
}

output "run_conductor_now" {
  description = "Trigger a chaos run without waiting for the schedule."
  value       = "aws ecs run-task --cluster ${aws_ecs_cluster.city.name} --launch-type FARGATE --task-definition ${aws_ecs_task_definition.conductor.family} --network-configuration 'awsvpcConfiguration={subnets=[${join(",", aws_subnet.private[*].id)}],securityGroups=[${aws_security_group.conductor.id}],assignPublicIp=DISABLED}' --region ${var.region}"
}
