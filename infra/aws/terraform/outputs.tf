output "vpc_id" {
  description = "VPC ID"
  value       = aws_vpc.main.id
}

output "vpc_cidr" {
  description = "VPC CIDR block"
  value       = aws_vpc.main.cidr_block
}

output "public_subnet_ids" {
  description = "Public subnet IDs"
  value       = aws_subnet.public[*].id
}

output "private_subnet_ids" {
  description = "Private subnet IDs"
  value       = aws_subnet.private[*].id
}

output "db_subnet_ids" {
  description = "Database subnet IDs"
  value       = aws_subnet.db[*].id
}

output "ecs_cluster_name" {
  description = "ECS cluster name"
  value       = aws_ecs_cluster.main.name
}

output "ecs_cluster_arn" {
  description = "ECS cluster ARN"
  value       = aws_ecs_cluster.main.arn
}

output "api_service_name" {
  description = "API ECS service name"
  value       = aws_ecs_service.api.name
}

output "api_task_definition_arn" {
  description = "API task definition ARN"
  value       = aws_ecs_task_definition.api.arn
}

output "worker_task_definition_arn" {
  description = "Worker task definition ARN"
  value       = aws_ecs_task_definition.worker.arn
}

output "alb_dns_name" {
  description = "ALB DNS name"
  value       = aws_lb.main.dns_name
}

output "alb_arn" {
  description = "ALB ARN"
  value       = aws_lb.main.arn
}

output "api_target_group_arn" {
  description = "API target group ARN"
  value       = aws_lb_target_group.api.arn
}

output "api_gateway_url" {
  description = "API Gateway invoke URL"
  value       = "https://${aws_apigatewayv2_api.main.id}.execute-api.${var.aws_region}.amazonaws.com"
}

output "api_gateway_id" {
  description = "API Gateway ID"
  value       = aws_apigatewayv2_api.main.id
}

output "database_endpoint" {
  description = "RDS endpoint"
  value       = aws_db_instance.main.endpoint
  sensitive   = true
}

output "database_port" {
  description = "RDS port"
  value       = aws_db_instance.main.port
}

output "secrets_manager_database_url_arn" {
  description = "Secrets Manager ARN for database URL"
  value       = aws_secretsmanager_secret.database_url.arn
}

output "secrets_manager_provider_keys_arn" {
  description = "Secrets Manager ARN for provider keys"
  value       = aws_secretsmanager_secret.provider_keys.arn
}

output "secrets_manager_github_app_arn" {
  description = "Secrets Manager ARN for GitHub app"
  value       = aws_secretsmanager_secret.github_app.arn
}

output "secrets_manager_slack_app_arn" {
  description = "Secrets Manager ARN for Slack app"
  value       = aws_secretsmanager_secret.slack_app.arn
}

output "secrets_manager_discord_app_arn" {
  description = "Secrets Manager ARN for Discord app"
  value       = aws_secretsmanager_secret.discord_app.arn
}

output "secrets_manager_clerk_arn" {
  description = "Secrets Manager ARN for Clerk"
  value       = aws_secretsmanager_secret.clerk.arn
}

output "kms_secrets_key_arn" {
  description = "KMS key ARN for secrets encryption"
  value       = aws_kms_key.secrets.arn
}

output "kms_logs_key_arn" {
  description = "KMS key ARN for logs encryption"
  value       = aws_kms_key.logs.arn
}

output "cloudwatch_dashboard_url" {
  description = "CloudWatch dashboard URL"
  value       = "https://${var.aws_region}.console.aws.amazon.com/cloudwatch/home?region=${var.aws_region}#dashboards:name=${var.project_name}-${var.environment}"
}

output "sns_alerts_topic_arn" {
  description = "SNS topic ARN for alerts"
  value       = aws_sns_topic.alerts.arn
}