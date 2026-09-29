output "arn" {
  description = "Set as this environment's role ARN repository variable."
  value       = aws_iam_role.this.arn
}

output "name" {
  value = aws_iam_role.this.name
}
