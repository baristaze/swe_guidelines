output "log_group_name" {
  description = "The log group every replica of this service writes to."
  value       = aws_cloudwatch_log_group.this.name
}

output "task_definition_arn" {
  description = "The revision the service runs."
  value       = aws_ecs_task_definition.this.arn
}

output "service_name" {
  value = aws_ecs_service.this.name
}

output "task_role_arn" {
  value = aws_iam_role.task.arn
}

output "rollout_gate" {
  description = "Known once this instance's pre-rollout task ran; another instance passes it as rollout_after."
  value       = var.pre_rollout == null ? "" : terraform_data.pre_rollout[0].id
}
