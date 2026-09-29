output "task_definition_arn" {
  description = "The revision a run starts."
  value       = aws_ecs_task_definition.this.arn
}

output "container_name" {
  description = "The container a run's command override names."
  value       = var.name
}

output "log_group_name" {
  value = aws_cloudwatch_log_group.this.name
}
